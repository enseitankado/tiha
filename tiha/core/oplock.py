"""Aynı tahtada aynı anda tek TiHA işlemi.

Sihirbaz açıkken uzaktan (Ankamon gibi bir yönetim aracından SSH ile)
``tiha -u`` gelirse ikisi aynı dosyalara (günce, PIN dosyası, rsyslog
yapılandırması…) aynı anda yazabilirdi. Uygulama, geri alma ve düğme
eylemleri çalışırken ``/run/lock/tiha.lock`` üzerinde ``flock`` tutulur;
ikinci işlem beklemeden reddedilir ve kilidi kimin tuttuğu söylenir.

Kilit dosyası ``/run`` altında (tmpfs): açılışta silinir, imaja girmez.
Aynı süreç içinde iç içe alınabilir (ör. bir adımın başka bir adımı
çağırması); yalnız dıştaki çağrı dosyayı kilitler.
"""

from __future__ import annotations

import fcntl
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

LOCK_FILE = Path("/run/lock/tiha.lock")

_guard = threading.Lock()
_depth = 0
_fd: int | None = None


class Busy(Exception):
    """Başka bir TiHA işlemi sürüyor; ``holder`` kilidi tutanın tanımı."""

    def __init__(self, holder: str) -> None:
        super().__init__(holder)
        self.holder = holder


def _read_holder(fd: int) -> str:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        return os.read(fd, 512).decode("utf-8", "replace").strip()
    except OSError:
        return ""


@contextmanager
def operation_lock(what: str):
    """``what`` (ör. "uygula: Merkezi log sunucusu") için kilidi alır.

    Kilit tutuluyorsa hemen :class:`Busy` fırlatır.
    """
    global _depth, _fd
    with _guard:
        if _depth == 0:
            fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                holder = _read_holder(fd)
                os.close(fd)
                raise Busy(holder) from None
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, f"pid {os.getpid()} · {stamp} · {what}\n".encode("utf-8"))
            _fd = fd
        _depth += 1
    try:
        yield
    finally:
        with _guard:
            _depth -= 1
            if _depth == 0 and _fd is not None:
                try:
                    os.ftruncate(_fd, 0)
                    fcntl.flock(_fd, fcntl.LOCK_UN)
                finally:
                    os.close(_fd)
                    _fd = None
