"""Ortak günlükleyici.

Çıktı hem dosyaya (``/var/log/tiha/tiha.log``) hem de standart hata akışına
yazılır. Böylece hem çalışma anında ekrandan, hem de geriye dönük olarak
dosyadan incelenebilir.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .paths import LOG_FILE, ensure_runtime_dirs

_LOGGER_NAME = "tiha"
_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str | None = None) -> logging.Logger:
    """Yapılandırılmış bir :class:`logging.Logger` döndürür.

    İlk çağrıda kök logger yapılandırılır; sonraki çağrılar aynı
    yapılandırmayı paylaşan alt logger'ları verir.

    :param name: Modül adı (ör. ``"tiha.modules.m01"``). ``None`` verilirse
        kök ``"tiha"`` logger'ı döndürülür.
    """
    root = logging.getLogger(_LOGGER_NAME)
    if not root.handlers:
        root.setLevel(logging.DEBUG)
        formatter = logging.Formatter(_FORMAT, _DATEFMT)

        # Dosyaya yaz — yalnızca yeterli yetki varsa.
        try:
            ensure_runtime_dirs()
            file_handler = RotatingFileHandler(
                LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except PermissionError:
            # Yetki yoksa yalnızca stderr'e devam et; uygulama yine çalışabilsin.
            pass

        # Standart hata akışı — kullanıcıya anlık bilgi için.
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(logging.INFO)
        stream.setFormatter(formatter)
        root.addHandler(stream)

    if name and name != _LOGGER_NAME:
        return root.getChild(name[len(_LOGGER_NAME) + 1 :] if name.startswith(_LOGGER_NAME + ".") else name)
    return root
