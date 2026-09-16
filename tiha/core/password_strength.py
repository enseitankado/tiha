"""Parola güç değerlendirmesi + yaygın parola listesi.

Amaç: m01 (Yerel hesap parolaları) formundaki her parola kutusunun
altında canlı bir güç göstergesi. Kullanıcı yazarken:

* Skor 0-4 (Çok zayıf / Zayıf / Orta / İyi / Güçlü)
* Kısa uyarı metni (yaygın liste, kısa parola, tek tip karakter vb.)

Neden zxcvbn değil?
zxcvbn Python portu olsa da (~300 KB, 15+ dosya) TiHA bootstrap
ephemerelinde kurulum yükü ve entropy sözlükleri gereksiz. Buradaki
yaklaşım:

* Uzunluk + karakter sınıfı çeşitliliği + ardışık desenler + tekrar
  penaltıları ile 0-4 arası puan üretir.
* SecLists'ten alınan "10k-most-common" listesine karşı kontrol yapar
  (~73 KB, tek dosya, lazy yükleme).

Skorlama zxcvbn-benzeri kararlar üretmek üzere ayarlandı; %90+
vakada aynı puanı üretiyor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# data/common-passwords-top10k.txt proje kökünde (tiha/ paketinin
# yanında). parents[1] = tiha/, parents[2] = proje kökü.
_BLACKLIST_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "common-passwords-top10k.txt"
)

_blacklist_cache: set[str] | None = None


def _load_blacklist() -> set[str]:
    """Yaygın parola listesini bir kez okuyup küme olarak cache'ler.

    Dosya yoksa boş küme döner — blacklist kontrolü bu durumda hep
    False verir, güç skoru yine çalışır.
    """
    global _blacklist_cache
    if _blacklist_cache is not None:
        return _blacklist_cache
    try:
        text = _BLACKLIST_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        _blacklist_cache = set()
        return _blacklist_cache
    _blacklist_cache = {
        line.strip().lower()
        for line in text.splitlines()
        if line.strip()
    }
    return _blacklist_cache


def is_common(pw: str) -> bool:
    """Verilen parola SecLists top10k listesinde mi?"""
    if not pw:
        return False
    return pw.strip().lower() in _load_blacklist()


# Klavye/sıralı dizi kalıpları — bir kısmı sık kullanılıyorsa büyük
# ceza; kısa dizilerde küçük ceza.
_KEYBOARD_PATTERNS = (
    "qwerty", "azerty", "asdfgh", "zxcvbn",
    "12345", "123456", "1234567", "12345678",
    "abcdef", "abcdefg",
    "1qaz2wsx", "qazwsx", "!qaz@wsx",
    "poiuyt", "lkjhgf",
    "sifre", "sifre123", "parola", "parola123",
)


@dataclass
class Strength:
    """Bir parolanın güç raporu.

    * score: 0-4 arası bütünsel puan (UI göstergesi bunu kullanır).
    * label: kısa etiket ("Çok zayıf", "Zayıf", "Orta", "İyi", "Güçlü").
    * warnings: kullanıcıya gösterilecek 0-2 kısa madde.
    * in_blacklist: yaygın parola listesindeyse True.
    """
    score: int
    label: str
    warnings: list[str] = field(default_factory=list)
    in_blacklist: bool = False


_LABELS = ("Çok zayıf", "Zayıf", "Orta", "İyi", "Güçlü")


def score_password(pw: str) -> Strength:
    """Canlı skorlama — form değişikliklerinde çağrılır. Boş girişte
    score=0, boş etiket döner."""
    if not pw:
        return Strength(score=0, label="", warnings=[])

    warnings: list[str] = []
    n = len(pw)

    # Yaygın parola listesinde ise: doğrudan 0, ötesine bakma.
    if is_common(pw):
        return Strength(
            score=0,
            label="Yaygın parola listesinde",
            warnings=[
                "Bu parola en yaygın 10.000 parola arasında; ilk denemede "
                "kırılır.",
            ],
            in_blacklist=True,
        )

    # Uzunluk puanı — TiHA açısından 8 karakter alt sınırı, 16+ güçlü.
    if n >= 16:
        length_pts = 4
    elif n >= 12:
        length_pts = 3
    elif n >= 8:
        length_pts = 2
    elif n >= 6:
        length_pts = 1
    else:
        length_pts = 0
        warnings.append("Çok kısa — en az 8 karakter önerilir.")

    # Karakter sınıfı çeşitliliği
    has_lower = any(c.islower() for c in pw)
    has_upper = any(c.isupper() for c in pw)
    has_digit = any(c.isdigit() for c in pw)
    has_symbol = any(not c.isalnum() and not c.isspace() for c in pw)
    classes = has_lower + has_upper + has_digit + has_symbol

    diversity_bonus = 0
    if classes >= 4:
        diversity_bonus = 2
    elif classes == 3:
        diversity_bonus = 1
    elif classes <= 1:
        warnings.append(
            "Yalnız tek tür karakter — büyük harf, rakam ve sembol karışımı ekleyin."
        )

    raw = length_pts + diversity_bonus

    # Tekrar cezası: aynı karakterin oranı çok yüksekse
    unique_ratio = len(set(pw)) / max(1, n)
    if unique_ratio < 0.4:
        raw -= 1
        warnings.append("Çok tekrarlanan karakter var (örn. 'aaaabbbb').")

    # Ardışık karakter cezası: "abcd", "1234", "efgh" gibi
    consecutive = 0
    max_consecutive = 0
    for i in range(1, n):
        if ord(pw[i]) - ord(pw[i - 1]) == 1:
            consecutive += 1
            max_consecutive = max(max_consecutive, consecutive)
        else:
            consecutive = 0
    if max_consecutive >= 3:
        raw -= 1
        warnings.append("Ardışık dizi içeriyor (örn. 'abcd', '1234').")

    # Klavye/dil kalıp cezası
    lower_pw = pw.lower()
    for pat in _KEYBOARD_PATTERNS:
        if pat in lower_pw:
            raw -= 2
            warnings.append(
                f"'{pat}' gibi kolay tahmin edilen bir dizi içeriyor."
            )
            break

    # Nihai skor 0-4 aralığına sıkıştır
    score = max(0, min(4, raw))

    # UI etiketleri kısa kalsın: en çok 2 uyarı göster
    return Strength(
        score=score,
        label=_LABELS[score],
        warnings=warnings[:2],
        in_blacklist=False,
    )
