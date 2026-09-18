"""Ortak günlükleyici.

Çıktı ``/var/log/tiha/tiha.log`` (bilgi) ve ``/var/log/tiha/tiha-debug.log``
(ayrıntılı) dosyalarına yazılır. İkisi de yalnız etapadmin'e açıktır
(root:etapadmin, 0640; dizin 0750) — bkz. :mod:`tiha.core.private_files`.

Ayrıntılı günlük eskiden ``/tmp/tiha.logs`` adıyla herkese okunur yazılıyordu:
hem bilgi sızdırıyordu hem de root'un sabit adla /tmp'ye yazması, bir
kullanıcının o adı önceden oluşturup root'un yazdığını okumasına açıktı.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import LOG_FILE, LOG_ROOT, ensure_runtime_dirs
from .private_files import protect_system_path

_LOGGER_NAME = "tiha"
_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Ayrıntılı (debug) günlük. Program çıkışında silinmez; boyutu sınırlı.
DEBUG_LOG_FILE = LOG_ROOT / "tiha-debug.log"
# Eski sürümün herkese açık bıraktığı dosya; ilk açılışta silinir.
_LEGACY_DEBUG_LOG = Path("/tmp/tiha.logs")


class _PrivateRotatingFileHandler(RotatingFileHandler):
    """Her açılışta (döndürme sonrası yeni dosya dahil) izni kilitler."""

    def _open(self):
        stream = super()._open()
        protect_system_path(Path(self.baseFilename))
        return stream


def get_logger(name: str | None = None) -> logging.Logger:
    """Yapılandırılmış bir :class:`logging.Logger` döndürür.

    Çıktı hem log dosyasına (``/var/log/tiha/tiha.log``) hem de
    ayrıntılı debug dosyasına (``/var/log/tiha/tiha-debug.log``) yazılır.
    İkisi de yalnız etapadmin'e açıktır.
    """
    root = logging.getLogger(_LOGGER_NAME)
    if not root.handlers:
        root.setLevel(logging.DEBUG)
        formatter = logging.Formatter(_FORMAT, _DATEFMT)

        # 1. Eski log dosyasına yaz (yalnızca yeterli yetki varsa)
        try:
            ensure_runtime_dirs()
            protect_system_path(LOG_ROOT)
            for old in LOG_ROOT.glob("*"):
                protect_system_path(old)
            file_handler = _PrivateRotatingFileHandler(
                LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            file_handler.setLevel(logging.INFO)  # Normal işlemler
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except PermissionError:
            pass

        # 2. Debug log dosyasına yaz (basit FileHandler, daha robust)
        try:
            try:
                if _LEGACY_DEBUG_LOG.is_file() and not _LEGACY_DEBUG_LOG.is_symlink() \
                        and _LEGACY_DEBUG_LOG.stat().st_uid == os.geteuid():
                    _LEGACY_DEBUG_LOG.unlink()
            except OSError:
                pass
            debug_handler = _PrivateRotatingFileHandler(
                DEBUG_LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=2,
                encoding="utf-8",
            )
            debug_handler.setLevel(logging.DEBUG)  # Tüm debug mesajları
            debug_handler.setFormatter(formatter)
            root.addHandler(debug_handler)

            # Program başlangıcında bilgi yaz
            root.info("=== TiHA detaylı debug loglama başlatıldı ===")
            root.info("Debug log dosyası: %s", DEBUG_LOG_FILE)
        except Exception as exc:
            # Debug loglama başarısız olursa sessizce devam et (normal davranış)
            pass

        # 3. Terminal çıktısı (geliştirme için)
        if os.environ.get("TIHA_DEBUG"):
            stream = logging.StreamHandler(sys.stderr)
            stream.setLevel(logging.DEBUG)
            stream.setFormatter(formatter)
            root.addHandler(stream)

    if name and name != _LOGGER_NAME:
        return root.getChild(name[len(_LOGGER_NAME) + 1 :] if name.startswith(_LOGGER_NAME + ".") else name)
    return root


def log_startup_info():
    """Program başlangıcında sistem bilgilerini logla."""
    logger = get_logger()
    logger.info("=== TiHA program başlangıcı ===")
    logger.info("Debug log dosyası: %s", DEBUG_LOG_FILE)
    logger.info("Çalışma dizini: %s", os.getcwd())
    logger.info("Kullanıcı: %s (UID: %d)", os.getenv('USER', 'unknown'), os.getuid())
    logger.info("Python path: %s", sys.executable)


def log_shutdown_info():
    """Program kapanışında bilgi mesajı."""
    logger = get_logger()
    logger.info("=== TiHA program kapanışı ===")
    logger.info("Detaylı loglar için: sudo cat %s", DEBUG_LOG_FILE)
