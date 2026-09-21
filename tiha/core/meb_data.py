"""MEB okul türleri ve branş listesi — yükleyici ve kullanıcı adı üretici.

Veri kaynağı: ``data/meb_branslar.json``. Dosya paketin data-file olarak
kurulur (pyproject.toml). Kod tek girişten okur, hafızada cache'ler.

Ana kullanımlar:

* ``load_school_types()`` — yüklü okul türü sözlüğünü döner.
* ``branches_for(key)`` — verilen okul türünün branş listesini döner.
* ``branch_to_username(label)`` — bir branş adını Linux'un
  useradd NAME_REGEX kuralına uyan bir hesap adına çevirir. Türkçe
  karakterler ASCII eşleniğine dönüştürülür, küçük harfe indirilir,
  boşluklar alt çizgiye dönüşür, noktalama işaretleri atılır.

Kullanıcı adı örnekleri::

    "Matematik"                    → "matematik"
    "Türk Dili ve Edebiyatı"       → "turk_dili_ve_edebiyati"
    "Din Kültürü ve Ahlak Bilgisi" → "din_kulturu_ve_ahlak_bilgisi"
    "Kur'an-ı Kerim"               → "kuran_i_kerim"
    "Elektrik-Elektronik Teknolojisi" → "elektrik_elektronik_teknolojisi"
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .logger import get_logger

log = get_logger(__name__)

# Paket kökünden data/ dizinine göreli yol. bootstrap.sh imajı /opt/tiha
# altına açtığı için resolve() ile mutlak yol elde ediyoruz.
_DATA_FILE = (
    Path(__file__).resolve().parents[2] / "data" / "meb_branslar.json"
)

# Türkçe → ASCII eşlemesi. useradd NAME_REGEX yalnız [a-z0-9_-] kabul
# eder; bu tabloyla önce diakritik temizleyip sonra kalan karakterleri
# eleyerek uyumlu bir isim üretiyoruz.
_TR_ASCII = str.maketrans({
    "ç": "c", "Ç": "c",
    "ğ": "g", "Ğ": "g",
    "ı": "i", "İ": "i",
    "ö": "o", "Ö": "o",
    "ş": "s", "Ş": "s",
    "ü": "u", "Ü": "u",
    "â": "a", "Â": "a",
    "î": "i", "Î": "i",
    "û": "u", "Û": "u",
})

# useradd NAME_REGEX (Debian): ^[a-z_][a-z0-9_-]{0,30}\$?$
# Baş harf harf/alt çizfi olmalı. Bu uygulamada tümü küçük harfle
# başladığı için sadece uzunluk denetliyoruz.
_MAX_USERNAME_LEN = 30

# Kullanıcı adında yer kaplamayan işlev sözcükleri. Kırpma kalitesini
# artırır; ör. "beden_egitimi_ve_spor" → "beden_egitimi_spor".
_STOPWORDS = {"ve", "ile", "icin", "de", "da", "ki"}


@lru_cache(maxsize=1)
def _load() -> dict:
    """JSON verisini oku ve cache'le. Dosya yoksa boş yapı döner."""
    if not _DATA_FILE.exists():
        log.warning("MEB veri dosyası yok: %s", _DATA_FILE)
        return {"okul_turleri": {}}
    try:
        return json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error("MEB veri dosyası okunamadı (%s): %s", _DATA_FILE, exc)
        return {"okul_turleri": {}}


def load_school_types() -> dict[str, dict]:
    """Okul türü sözlüğünü döner. Her değer ``{label, branslar}``."""
    return _load().get("okul_turleri", {})


def branches_for(school_key: str) -> list[str]:
    """Verilen okul türü anahtarındaki branşların listesi."""
    schools = load_school_types()
    entry = schools.get(school_key)
    if not entry:
        return []
    return list(entry.get("branslar", []))


def school_label(school_key: str) -> str:
    """Okul türünün insan-okunur etiketi. Bilinmiyorsa anahtarı döner."""
    entry = load_school_types().get(school_key)
    return (entry.get("label") if entry else school_key) or school_key


def branch_to_username(label: str) -> str:
    """Branş adını useradd uyumlu bir hesap adına çevirir.

    - Türkçe karakterler ASCII eşleniğine indirilir.
    - Küçük harfe çevrilir.
    - Boşluk ve tire alt çizgiye dönüşür.
    - Kesme işareti, nokta gibi işaretler atılır.
    - Ardışık alt çizgiler tekilleştirilir; baş/son alt çizgi kırpılır.
    - 30 karakteri geçerse kırpılır.
    """
    if not label:
        return ""
    text = label.translate(_TR_ASCII).lower()
    # Boşluk, tire ve /'ları alt çizgiye çevir; sonra harf/rakam/_ dışını at.
    text = re.sub(r"[\s\-\/]+", "_", text)
    text = re.sub(r"[^a-z0-9_]", "", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        return ""
    # Stopword'leri ele — kalan sözcükler daha bilgi yoğun olur.
    words = [w for w in text.split("_") if w and w not in _STOPWORDS]
    text = "_".join(words) if words else text
    if text[0].isdigit():
        text = "b_" + text
    if len(text) <= _MAX_USERNAME_LEN:
        return text
    # Uzunsa: kelime sınırında kırp (kısaltmalar okunaklı olsun).
    parts: list[str] = []
    used = 0
    for word in text.split("_"):
        addition = (1 if parts else 0) + len(word)
        if used + addition > _MAX_USERNAME_LEN:
            break
        parts.append(word)
        used += addition
    if not parts:
        # Tek kelime bile sığmıyorsa sert kırpma.
        return text[:_MAX_USERNAME_LEN]
    return "_".join(parts)


def detect_existing_selection(preferred_school: str = "") -> dict | None:
    """Sistemde açılmış branş hesaplarından formun önceki seçimini çıkarır.

    Her okul türü için, kullanıcı adı sistemde bulunan branşlar sayılır.
    ``preferred_school`` (son uygulamada seçilen okul türü) bu hesaplardan
    en az birini içeriyorsa o seçilir; yoksa en çok eşleşen okul türü,
    eşitlikte veri dosyasında önce gelen seçilir.
    Hiç branş hesabı yoksa ``None`` döner.
    """
    import pwd

    try:
        names = {e.pw_name for e in pwd.getpwall()}
    except OSError:
        return None
    schools = load_school_types()
    matches: dict[str, list[str]] = {}
    for key in schools:
        found = [
            label for label in branches_for(key)
            if branch_to_username(label) in names
        ]
        if found:
            matches[key] = found
    if not matches:
        return None
    if preferred_school in matches:
        best = preferred_school
    else:
        # max() eşitlikte ilkini verir: listede önce gelen (daha yaygın)
        # okul türü seçilir.
        best = max(matches, key=lambda k: len(matches[k]))
    return {"school_type": best, "branches": matches[best]}


def clear_cache() -> None:
    """Test amaçlı: yüklenmiş veriyi unut."""
    _load.cache_clear()
