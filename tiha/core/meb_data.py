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
    tagged = tagged_branch_labels()
    if not matches:
        if not tagged:
            return None
        return {"school_type": "", "branches": tagged}
    if preferred_school in matches:
        best = preferred_school
    else:
        # max() eşitlikte ilkini verir: listede önce gelen (daha yaygın)
        # okul türü seçilir.
        best = max(matches, key=lambda k: len(matches[k]))
    # Seçilen türün dışında kalan hesaplı branşlar da seçili sayılır:
    # listede görünmeyen bir hesap "listeden çıkarıldı" sayılıp silinmesin.
    branches = list(matches[best])
    seen = {branch_to_username(b) for b in branches}
    for key in matches:
        for label in matches[key]:
            uname = branch_to_username(label)
            if uname not in seen:
                seen.add(uname)
                branches.append(label)
    # Elle eklenmiş (listede olmayan) branş hesapları
    for label in tagged:
        uname = branch_to_username(label)
        if uname not in seen:
            seen.add(uname)
            branches.append(label)
    return {"school_type": best, "branches": branches}


# Branş hesabının GECOS "diğer" alanına yazılan işaret. Listede olmayan
# (elle eklenen) branşların hesapları bununla tanınır; giriş ekranı yalnız
# ilk alanı (görünen adı) gösterir.
BRANCH_GECOS_TAG = "tiha-brans"


def branch_gecos(label: str) -> str:
    """Branş hesabının GECOS değeri: görünen ad + TiHA işareti."""
    clean = label.replace(",", " ").replace(":", " ").strip()
    return f"{clean},,,,{BRANCH_GECOS_TAG}"


def tagged_branch_labels() -> list[str]:
    """TiHA işaretli branş hesaplarının görünen adları (elle eklenenler dahil)."""
    import pwd

    try:
        entries = pwd.getpwall()
    except OSError:
        return []
    out = []
    for e in entries:
        fields = e.pw_gecos.split(",")
        if len(fields) >= 5 and fields[4].strip() == BRANCH_GECOS_TAG:
            label = fields[0].strip()
            if label and branch_to_username(label) == e.pw_name:
                out.append(label)
    return out


def school_group(school_key: str) -> str:
    """Okul türünün grubu (ör. "Temel Eğitim"); veride yoksa boş."""
    entry = load_school_types().get(school_key) or {}
    return entry.get("grup") or ""


def all_branch_labels() -> list[str]:
    """Bütün okul türlerindeki branş adları (kullanıcı adına göre tekil)."""
    out: list[str] = []
    seen: set[str] = set()
    for key in load_school_types():
        for label in branches_for(key):
            uname = branch_to_username(label)
            if uname and uname not in seen:
                seen.add(uname)
                out.append(label)
    return out


# Branş adlarının İngilizce karşılıkları (yalnız dosya adı üretmek için).
# Eksik olanlar transliterasyona düşer. Tekrarları tekil kullanıcı adına
# göre eşleyip yönetiyoruz; hem "Matematik" hem "İlköğretim Matematik"
# tek girdi.
BRANCH_EN: dict[str, str] = {
    "Matematik": "mathematics",
    "Türkçe": "turkish",
    "Fen Bilimleri": "science",
    "Sosyal Bilgiler": "social-studies",
    "İngilizce": "english",
    "Almanca": "german",
    "Fransızca": "french",
    "Arapça": "arabic",
    "Rusça": "russian",
    "İspanyolca": "spanish",
    "İtalyanca": "italian",
    "Çince": "chinese",
    "Japonca": "japanese",
    "Korece": "korean",
    "Farsça": "persian",
    "Yaşayan Diller ve Lehçeler": "living-languages",
    "Din Kültürü ve Ahlak Bilgisi": "religious-culture",
    "İmam-Hatip Lisesi Meslek Dersleri": "imam-hatip-vocational",
    "Türk Dili ve Edebiyatı": "turkish-literature",
    "Fizik": "physics",
    "Kimya": "chemistry",
    "Biyoloji": "biology",
    "Tarih": "history",
    "Coğrafya": "geography",
    "Felsefe": "philosophy",
    "Psikoloji": "psychology",
    "Sağlık Bilgisi": "health-education",
    "Beden Eğitimi": "physical-education",
    "Beden Eğitimi ve Spor": "physical-education",
    "Görsel Sanatlar": "visual-arts",
    "Müzik": "music",
    "Teknoloji ve Tasarım": "technology-design",
    "Bilişim Teknolojileri": "informatics",
    "Bilgisayar ve Öğretim Teknolojileri": "computer-instruction-tech",
    "Sınıf Öğretmenliği": "primary-classroom",
    "Okul Öncesi": "preschool",
    "Çocuk Gelişimi ve Eğitimi": "child-development",
    "Özel Eğitim": "special-education",
    "Rehberlik": "guidance-counseling",
    "Zihinsel Engelliler Sınıf Öğretmenliği": "special-ed-intellectual",
    "İşitme Engelliler Sınıf Öğretmenliği": "special-ed-hearing",
    "Görme Engelliler Sınıf Öğretmenliği": "special-ed-visual",
    "Ortopedik Engelliler Sınıf Öğretmenliği": "special-ed-orthopedic",
    "Muhasebe ve Finansman": "accounting-finance",
    "Büro Yönetimi": "office-management",
    "Pazarlama ve Perakende": "marketing-retail",
    "Halkla İlişkiler ve Organizasyon Hizmetleri": "public-relations",
    "Adalet": "justice",
    "Grafik ve Fotoğraf": "graphic-photo",
    "Radyo Televizyon": "radio-tv",
    "El Sanatları Teknolojisi": "handicrafts",
    "Tekstil Teknolojisi": "textile",
    "Giyim Üretim Teknolojisi": "clothing-production",
    "Yiyecek İçecek Hizmetleri": "food-beverage",
    "Konaklama ve Seyahat Hizmetleri": "hospitality-travel",
    "Elektrik-Elektronik Teknolojisi": "electrical-electronics",
    "Endüstriyel Otomasyon Teknolojileri": "industrial-automation",
    "Makine Teknolojisi": "mechanical",
    "Metal Teknolojisi": "metalworking",
    "Motorlu Araçlar Teknolojisi": "motor-vehicles",
    "Mobilya ve İç Mekan Tasarımı": "furniture-interior",
    "İnşaat Teknolojisi": "construction",
    "Tesisat Teknolojisi ve İklimlendirme": "hvac-plumbing",
    "Gıda Teknolojisi": "food-technology",
    "Kimya Teknolojisi": "chemistry-technology",
    "Ayakkabı ve Saraciye Teknolojisi": "footwear-leather",
    "Denizcilik": "maritime",
    "Havacılık": "aviation",
    "Raylı Sistemler Teknolojisi": "rail-systems",
    "Ulaştırma Hizmetleri": "transportation",
    "Sağlık Hizmetleri": "health-services",
    "Anestezi ve Reanimasyon": "anesthesia",
    "Radyoloji": "radiology",
    "Tıbbi Laboratuvar": "medical-laboratory",
    "Tıbbi Sekreterlik": "medical-secretary",
    "Hemşire Yardımcılığı": "nursing-assistant",
    "Ebe Yardımcılığı": "midwifery-assistant",
    "Sağlık Bakım Teknisyenliği": "healthcare-technician",
    "Acil Sağlık Hizmetleri": "emergency-medical",
    "Optisyenlik": "optician",
    "Diş Protez Teknisyenliği": "dental-prosthesis",
    "Ortopedik Protez ve Ortez": "orthopedic-prosthesis",
    "Anatomi ve Fizyoloji": "anatomy-physiology",
    "Osmanlı Türkçesi": "ottoman-turkish",
    "İkinci Yabancı Dil": "second-foreign-language",
    "Türkiye Cumhuriyeti İnkılap Tarihi": "turkish-revolution-history",
    "Sosyoloji": "sociology",
    "Mantık": "logic",
    "Astronomi ve Uzay Bilimleri": "astronomy",
    "Sağlık Bilgisi ve Trafik Kültürü": "health-traffic",
    "Kur'an-ı Kerim": "quran",
    "Tefsir": "tafsir",
    "Hadis": "hadith",
    "Fıkıh": "fiqh",
    "Akaid ve Kelam": "aqidah-kalam",
    "Siyer": "siyer",
    "Hitabet ve Mesleki Uygulama": "rhetoric-practice",
    "Resim": "painting",
    "Heykel": "sculpture",
    "Grafik Tasarım": "graphic-design",
    "Fotoğraf": "photography",
    "Seramik": "ceramics",
    "Sanat Tarihi": "art-history",
    "Piyano": "piano",
    "Yaylı Çalgılar": "strings",
    "Nefesli Çalgılar": "winds",
    "Vurmalı Çalgılar": "percussion",
    "Ses Eğitimi": "voice",
    "Türk Halk Müziği": "turkish-folk-music",
    "Türk Sanat Müziği": "turkish-classical-music",
    "Spor Anatomisi ve Fizyolojisi": "sports-anatomy",
    "Atletizm": "athletics",
    "Basketbol": "basketball",
    "Voleybol": "volleyball",
    "Futbol": "football",
    "Yüzme": "swimming",
    "Cimnastik": "gymnastics",
    "Takım Sporları": "team-sports",
    "Bireysel Sporlar": "individual-sports",
    "Sağlıklı Yaşam ve Beslenme": "healthy-nutrition",
    "Eğlence Hizmetleri": "entertainment",
    "Aile ve Tüketici Hizmetleri": "family-consumer",
    "Hayvan Yetiştiriciliği ve Sağlığı": "animal-husbandry",
    "Laboratuvar Hizmetleri": "laboratory-services",
    "Tarım": "agriculture",
    "Matbaa Teknolojisi": "printing",
    "Plastik Teknolojisi": "plastics",
}


def branch_english_slug(label: str) -> str:
    """Branş adının dosya adında kullanılacak İngilizce (a-z0-9-) sürümü.

    Eşleme tablosunda yoksa Türkçe → ASCII transliterasyon + '-' ayracı.
    Kimliğin dosya adı olarak taşınması amaçlıdır, çeviri değil.
    """
    if not label:
        return "brans"
    en = BRANCH_EN.get(label.strip())
    if en:
        return en
    # Fallback: transliterasyon, alt çizgi yerine tire.
    slug = branch_to_username(label).replace("_", "-")
    return slug or "brans"


def clear_cache() -> None:
    """Test amaçlı: yüklenmiş veriyi unut."""
    _load.cache_clear()
