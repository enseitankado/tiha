"""Küçük yardımcılar: komut çalıştırma (bloklu / akışlı), dosya yedekleme,
rastgele parola üretme.

``run_cmd``        — Komutu senkron çalıştırır, tüm çıktıyı toplu döner.
``run_cmd_stream`` — Komutu çalıştırır, her çıktı satırını ``progress`` ile
                     canlı iletir (UI'ın akış olarak göstermesi için).
"""

from __future__ import annotations

import os
import secrets
import shutil
import string
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .logger import get_logger

log = get_logger(__name__)


@dataclass
class CmdResult:
    """Komut çalıştırmanın sonucu. İstisna fırlatmaz, elle kontrol edilir."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_cmd(
    cmd: list[str],
    *,
    check: bool = False,
    input_data: str | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> CmdResult:
    """Blok-eden süreç çalıştırıcı — çıktıyı toplu döner."""
    log.debug("Komut: %s", " ".join(cmd))
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    proc = subprocess.run(
        cmd,
        input=input_data,
        capture_output=True,
        text=True,
        env=merged_env,
        timeout=timeout,
        check=False,
    )
    result = CmdResult(proc.returncode, proc.stdout or "", proc.stderr or "")
    if not result.ok:
        log.debug("Çıktı kodu=%s stderr=%s", proc.returncode, result.stderr.strip())
    if check and not result.ok:
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, output=result.stdout, stderr=result.stderr
        )
    return result


def run_cmd_stream(
    cmd: list[str],
    progress: Callable[[str], None] | None = None,
    *,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> CmdResult:
    """Komutu çalıştırır ve her stdout satırını ``progress``'a iletir.

    ``stderr`` de ``stdout``'a birleştirilir; böylece apt gibi araçların
    uyarı satırları da kullanıcıya gösterilir. Arayüz, ``progress`` geri-
    çağrısını thread-güvenli şekilde (GLib.idle_add) saracaktır.
    """
    log.debug("Akışlı komut: %s", " ".join(cmd))
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=merged_env,
    )
    assert proc.stdout is not None
    collected: list[str] = []
    for raw in proc.stdout:
        line = raw.rstrip()
        collected.append(line)
        if progress:
            try:
                progress(line)
            except Exception as exc:  # UI hatası komutu kesmesin
                log.debug("progress geri çağrısında hata: %s", exc)
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = -1
        collected.append("[ZAMAN AŞIMI]")

    return CmdResult(rc, "\n".join(collected), "")


def backup_file(src: Path, backup_dir: Path) -> Path | None:
    """Bir dosyanın yedeğini alır; yoksa ``None`` döner."""
    if not src.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / src.name
    shutil.copy2(src, dest)
    return dest


def restore_file(backup: Path, dest: Path) -> None:
    """``backup_file`` ile alınan bir yedeği yerine koyar."""
    shutil.copy2(backup, dest)


def random_password(length: int = 32) -> str:
    """Kriptografik olarak güvenli rastgele parola üretir."""
    alphabet = string.ascii_letters + string.digits + "!@#%^&*-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def is_root() -> bool:
    return os.geteuid() == 0


def user_exists(username: str) -> bool:
    import pwd
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False
