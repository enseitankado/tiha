"""Canlı çıktı (akış) penceresi için anlamlı renklendirme.

Akış penceresi tek bir :class:`Gtk.TextView` + :class:`Gtk.TextBuffer`'dır.
Buraya satır satır düz metin yazılır (apt çıktısı, ahenk-debug raporu vb.).
Bu modül her satırı ciddiyetine göre sınıflandırıp uygun renkli
:class:`Gtk.TextTag` ile ekler; böylece hata/uyarı/başarı satırları bir
bakışta ayırt edilir.

Tasarım ilkeleri:
- **Yeniden kullanılabilir:** sınıflandırma araçtan bağımsızdır; herhangi
  bir akış yayınlayan modül kullanabilir.
- **Tutarlı:** renkler uygulamanın ``data/styles.css`` içindeki ciddiyet
  paletinden gelir (yeşil #1b8a3a, kırmızı #a12525, amber #c97800).
- **Temkinli:** yanlış renklendirmemek için önce araçların açık
  rozetlerine (``[ OK ]``, ``[WARN]``, ``[FAIL]``, ``[INFO]``) bakar;
  yalnız güvenli birkaç genel kalıba (apt'nin ``E:``/``W:`` önekleri,
  ``▸`` bölüm başlığı, ayraç çizgileri) düşer.
- **ANSI'den arınık:** tty olmayan borularda bile araçlar kaçış dizisi
  bırakırsa okunurluğu bozmasın diye temizlenir.
"""

from __future__ import annotations

import re

# TextTag adları (buffer'a install_tags ile eklenir).
TAG_FAIL = "sev_fail"
TAG_WARN = "sev_warn"
TAG_OK = "sev_ok"
TAG_INFO = "sev_info"
TAG_HEAD = "sev_head"
TAG_DIM = "sev_dim"

# Renk paleti — styles.css ciddiyet renkleriyle aynı; açık tema için
# seçilmiştir (akış alanı beyaz zeminlidir).
_TAG_COLORS = {
    TAG_FAIL: "#a12525",   # kırmızı — hata / başarısız
    TAG_WARN: "#c97800",   # amber — uyarı
    TAG_OK:   "#1b8a3a",   # yeşil — başarılı / sağlıklı
    TAG_INFO: "#1c6fb0",   # mavi — bilgi
    TAG_HEAD: "#155a8a",   # koyu mavi — bölüm başlığı (kalın)
    TAG_DIM:  "#8a8a8a",   # gri — ayraç / ikincil
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Terminal renk kaçış dizilerini ('\\x1b[…m') temizler."""
    return _ANSI_RE.sub("", text)


def classify(line: str) -> str | None:
    """Bir satırın ciddiyet TextTag adını döndürür; nötr satırda ``None``.

    Önce araçların açık rozetleri, sonra güvenli genel kalıplar denenir.
    """
    s = strip_ansi(line)
    stripped = s.strip()
    if not stripped:
        return None

    # 1) Açık ciddiyet rozetleri (ahenk-debug ve benzerleri).
    #    Boşluklu/boşluksuz köşeli parantez varyantlarını kapsar.
    if "[FAIL]" in s:
        return TAG_FAIL
    if "[WARN]" in s:
        return TAG_WARN
    if "[ OK ]" in s or "[OK]" in s:
        return TAG_OK
    if "[INFO]" in s:
        return TAG_INFO

    # 2) Bölüm başlığı ve ayraçlar.
    if stripped.startswith("▸"):
        return TAG_HEAD
    # Yalnızca ayraç karakterlerinden oluşan satır (===, ───, ---).
    if len(stripped) >= 3 and set(stripped) <= {"=", "─", "-", "—"}:
        return TAG_DIM

    # 3) Güvenli genel önekler (ör. apt). Satır başındaki "E:" / "W:".
    head = stripped.split(" ", 1)[0]
    if head == "E:":
        return TAG_FAIL
    if head == "W:":
        return TAG_WARN

    return None


def install_tags(buffer) -> None:
    """Renk TextTag'lerini buffer'ın tag tablosuna (bir kez) ekler."""
    table = buffer.get_tag_table()
    for name, color in _TAG_COLORS.items():
        if table.lookup(name) is not None:
            continue
        kwargs = {"foreground": color}
        if name == TAG_HEAD:
            kwargs["weight"] = 700  # Pango.Weight.BOLD
        if name == TAG_DIM:
            kwargs["foreground"] = color
        buffer.create_tag(name, **kwargs)


def append_line(buffer, line: str, *, newline: bool = True) -> None:
    """Satırı ciddiyetine göre renkli olarak buffer'ın sonuna ekler.

    ANSI dizileri temizlenir. ``install_tags`` önceden çağrılmış
    olmalıdır; tag yoksa (ör. çağrılmadıysa) satır yine düz eklenir.
    """
    text = strip_ansi(line)
    if newline:
        text += "\n"
    tag = classify(line)
    end = buffer.get_end_iter()
    if tag and buffer.get_tag_table().lookup(tag) is not None:
        buffer.insert_with_tags_by_name(end, text, tag)
    else:
        buffer.insert(end, text)
