"""Ortak günlükleyici.

Çıktı hem dosyaya (``/tmp/tiha.logs``) hem de standart hata akışına
yazılır. Böylece hem çalışma anında ekrandan, hem de geriye dönük olarak
dosyadan incelenebilir.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import LOG_FILE, ensure_runtime_dirs

_LOGGER_NAME = "tiha"
_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Debug log dosyası - her zaman /tmp'de, program çıkışında silinmez
DEBUG_LOG_FILE = Path("/tmp/tiha.logs")


def get_logger(name: str | None = None) -> logging.Logger:
    """Yapılandırılmış bir :class:`logging.Logger` döndürür.

    Çıktı hem eski log dosyasına (``/var/log/tiha/tiha.log``) hem de
    detaylı debug dosyasına (``/tmp/tiha.logs``) yazılır. Debug dosyası
    program çıkışında silinmez ve sorun ayıklama için kullanılabilir.
    """
    root = logging.getLogger(_LOGGER_NAME)
    if not root.handlers:
        root.setLevel(logging.DEBUG)
        formatter = logging.Formatter(_FORMAT, _DATEFMT)

        # 1. Eski log dosyasına yaz (yalnızca yeterli yetki varsa)
        try:
            ensure_runtime_dirs()
            file_handler = RotatingFileHandler(
                LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            file_handler.setLevel(logging.INFO)  # Normal işlemler
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except PermissionError:
            pass

        # 2. Debug log dosyasına yaz (basit FileHandler, daha robust)
        try:
            # Dosyayı açmayı dene, başarısız olursa yok say
            DEBUG_LOG_FILE.touch(exist_ok=True)
            debug_handler = logging.FileHandler(
                DEBUG_LOG_FILE, mode='a', encoding="utf-8"
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
    logger.info("Detaylı loglar için: cat %s", DEBUG_LOG_FILE)
