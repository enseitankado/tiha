"""Arayüz metinleri kataloğu.

Ekranda görünen bütün metinler (başlık, açıklama, önizleme, form etiketi,
sonuç, rapor…) tek bir dosyada, ``tiha/locale/<dil>.toml`` içinde durur.
Kod metnin kendisini değil anahtarını kullanır::

    from ..core.i18n import t
    t("m01.title")
    t("m03.preview.pin_count", done=3, total=5)   # "{done}/{total}"

Dil ``TIHA_LANG`` ortam değişkeniyle seçilir (varsayılan ``tr``). Seçilen
dilde bulunmayan anahtar Türkçe kataloğa düşer; orada da yoksa ekranda
anahtarın kendisi görünür ve log'a uyarı yazılır — eksik metin programı
düşürmez, göze batar.

Yer tutucular Python ``str.format`` sözdizimidir. Yalnızca değişken
verilen çağrılarda biçimlendirme yapılır; o metinlerde düz ``{`` / ``}``
karakteri ``{{`` / ``}}`` yazılmalıdır.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .logger import get_logger

log = get_logger(__name__)

LOCALE_DIR = Path(__file__).resolve().parents[1] / "locale"
DEFAULT_LANG = "tr"

_catalog: dict[str, str] | None = None
_warned: set[str] = set()


def _flatten(table: dict, prefix: str = "") -> dict[str, str]:
    """İç içe TOML tablolarını ``a.b.c`` anahtarlı düz sözlüğe çevirir."""
    flat: dict[str, str] = {}
    for key, value in table.items():
        full = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{full}."))
        else:
            flat[full] = str(value)
    return flat


def _read(lang: str) -> dict[str, str]:
    path = LOCALE_DIR / f"{lang}.toml"
    try:
        with path.open("rb") as fh:
            return _flatten(tomllib.load(fh))
    except FileNotFoundError:
        log.warning("Dil dosyası yok: %s", path)
    except tomllib.TOMLDecodeError as exc:
        log.error("Dil dosyası okunamadı (%s): %s", path, exc)
    return {}


def _load() -> dict[str, str]:
    global _catalog
    if _catalog is None:
        lang = os.environ.get("TIHA_LANG", DEFAULT_LANG).strip() or DEFAULT_LANG
        catalog = _read(DEFAULT_LANG)
        if lang != DEFAULT_LANG:
            catalog.update(_read(lang))
        _catalog = catalog
    return _catalog


def t(key: str, **values: object) -> str:
    """``key`` anahtarının metnini döndürür; ``values`` verilmişse
    yer tutucuları doldurur."""
    text = _load().get(key)
    if text is None:
        if key not in _warned:
            _warned.add(key)
            log.warning("Metin kataloğunda anahtar yok: %s", key)
        return key
    if not values:
        return text
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError) as exc:
        log.warning("Metin biçimlendirilemedi (%s): %s", key, exc)
        return text
