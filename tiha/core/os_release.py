"""Çalışılan Pardus ETAP sürümünün tespiti.

TiHA'nın sürüme bağlı işleri (ör. depo onarımı) yalnızca tanıdığı
sürümlerde yapılır; tanımadığı bir sürümde (ör. ileride çıkacak ETAP 24)
yanlış bir sürümün ayarlarını yazmak yerine hiç dokunmaz ve uyarır.
"""

from __future__ import annotations

from pathlib import Path

OS_RELEASE = Path("/etc/os-release")

# TiHA'nın hazırlandığı ve sürüme bağlı ayarlarını bildiği sürümler
# (VERSION_CODENAME'in "etap-" öneki atılmış hâli).
SUPPORTED_CODENAMES = ("yirmiuc",)


def read_os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = OS_RELEASE.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip().strip('"')
    return values


def release_codename() -> str:
    """"etap-yirmiuc" → "yirmiuc". Okunamazsa boş metin."""
    codename = read_os_release().get("VERSION_CODENAME", "")
    return codename.removeprefix("etap-")


def pretty_name() -> str:
    info = read_os_release()
    return info.get("PRETTY_NAME") or info.get("NAME") or "?"


def is_supported() -> bool:
    return release_codename() in SUPPORTED_CODENAMES
