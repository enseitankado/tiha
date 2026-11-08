"""Sihirbaz sayfasının yerleşim kuralları — GTK'sız, tek kaynak.

Sihirbaz (``ui/pages.py``) formu bu sabitlerle çizer; makine arayüzü
(``tiha --sema``) aynı değerleri ``presentation`` bölümünde yayımlar ki
bir yönetim aracı (ör. Ankamon) formu görsel olarak benzer çizebilsin.
Bir değer burada değişince ikisi birlikte değişir.

Renk ve yazı biçimleri ``data/styles.css``'ten okunur (:func:`css_tokens`);
orada değişen bir renk de şemaya kendiliğinden yansır.
"""

from __future__ import annotations

import re
from pathlib import Path

# Form ad sütununun genişliği (karakter).
FORM_LABEL_CHARS = 26
# Uzun alan açıklamaları sayfa yerine "?" balonunda gösterilir; satır sayısı
# bu genişlikte (piksel, varsayılan pencerede açıklama sütunu) ölçülür.
HELP_COLUMN_WIDTH = 580
# Bundan fazla satır tutan açıklama balona taşınır.
HELP_BUBBLE_OVER_LINES = 2
# Kısa değer alan form kutularının varsayılan genişliği (karakter). Bu türler
# satır boyunca uzamaz, sola yaslanır; alan başına şemadaki "width" ile
# değiştirilir. 0: doğal genişlik. Burada olmayan türler satırı doldurur.
COMPACT_FIELD_WIDTHS = {
    "text": 28,
    "password": 28,
    "number": 8,
    "readonly": 20,
    "spin": 0,
    "select": 0,
    "button": 0,
}
# Uzun metin kutularının (çok satırlı alan, önizleme) yüksekliği (piksel).
LONG_TEXT_HEIGHT = 180
# Adım açıklaması bundan çok cümleyse sayfada değil, başlığın yanındaki "?"
# düğmesinin açtığı pencerede gösterilir (modül rationale_inline demedikçe).
RATIONALE_INLINE_MAX_SENTENCES = 3
# Canlı çıktı penceresinin varsayılan boyutu (piksel; ekrana sığdırılır).
STREAM_DEFAULT_SIZE = (920, 620)
# Form ızgarası aralıkları (piksel).
FORM_COLUMN_SPACING = 10
FORM_ROW_SPACING = 4
PAGE_MARGIN = 18
PAGE_ROW_SPACING = 14

# Açıklama metinlerindeki işaretler.
ANKAMON_URL = "https://github.com/enseitankado/ankamon"
LINK_COLOR = "#1c6fb0"
WARNING_TEXT_COLOR = "#c62828"
HELP_MARKERS = {
    "[[ankamon]]": {"text": "Ankamon", "link": ANKAMON_URL, "color": LINK_COLOR},
    "[[dikkat]]…[[/dikkat]]": {"color": WARNING_TEXT_COLOR,
                               "meaning": "aradaki metin kırmızı uyarı"},
}

# Sol menüde adım durumunun işareti: durum (core.undo.step_state) →
# (işaret, styles.css sınıfı, ipucu metninin katalog anahtarı; {summary}).
SIDEBAR_MARKS = {
    "applied": ("✓", "tiha-step-status-ok", "ui.main.status_applied"),
    "off": ("", "", "ui.main.status_off"),
    "failed": ("⚠", "tiha-step-status-fail", "ui.main.status_failed"),
    "skipped": ("", "", "ui.main.status_skipped"),
    "undone": ("", "", ""),
    "none": ("", "", ""),
}

# Parola gücü göstergesinin renkleri, puan 0 (çok zayıf) … 4 (güçlü).
STRENGTH_COLORS = ("#c62828", "#e65100", "#f9a825", "#2e7d32", "#1b5e20")

# Şema türü → arayüz öğesi.
WIDGETS = {
    "bool": "checkbox",
    "text": "entry",
    "password": "password_entry",
    "number": "entry_numeric",
    "spin": "spin_button",
    "select": "dropdown",
    "textarea": "multiline_text",
    "heading": "section_heading",
    "readonly": "readonly_entry",
    "branch_accounts": "branch_picker",
    "file": "file_chooser",
    "button": "button",
}


def count_sentences(text: str) -> int:
    """Cümle sayısını yaklaşık sayar (. ! ? ile biten dizeler)."""
    if not text:
        return 0
    return len([p for p in re.split(r"[.!?]+", text) if p.strip()])


def rationale_mode(module) -> str:
    """"inline": açıklama sayfada; "dialog": başlığın yanında "?" düğmesi."""
    long = count_sentences((module.rationale or "").strip()) > RATIONALE_INLINE_MAX_SENTENCES
    return "dialog" if long and not getattr(module, "rationale_inline", False) else "inline"


# --- styles.css'ten renk ve yazı biçimi ------------------------------------------

STYLES_CSS = Path(__file__).resolve().parents[2] / "data" / "styles.css"

# Rol adı → styles.css seçicisi.
STYLE_ROLES = {
    "heading": ".tiha-heading",
    "rationale": ".tiha-rationale",
    "form_section": ".tiha-form-section",
    "banner_info": ".tiha-prev-banner",
    "banner_warning": ".tiha-experimental-banner",
    "apply_hint": ".tiha-apply-hint",
    "result_ok": ".tiha-result-ok",
    "result_fail": ".tiha-result-fail",
    "preview": ".tiha-preview",
    "preview_box": ".tiha-preview-box",
    "help_button": ".tiha-help-btn",
    "help_dot": ".tiha-form button.tiha-help-dot",
    "step_active": "row.tiha-step-active",
    "step_done": "row.tiha-step-done",
    "step_failed": "row.tiha-step-failed",
    "step_status": ".tiha-step-status",
    "step_status_ok": ".tiha-step-status-ok",
    "step_status_fail": ".tiha-step-status-fail",
    "sidebar": ".tiha-sidebar",
}

_ALPHA = re.compile(r"alpha\(\s*#([0-9a-fA-F]{6})\s*,\s*([0-9.]+)\s*\)")
_THEME = re.compile(r"@theme_([a-z_]+)")
_ALPHA_VAR = re.compile(r"alpha\(\s*(var\(--[a-z-]+\))\s*,\s*([0-9.]+)\s*\)")


def _css_value(value: str) -> str:
    """GTK'ya özgü değerleri CSS'e çevirir: alpha(#rrggbb, a) → rgba(…);
    tema rengi @theme_fg_color → var(--theme-fg-color); tema rengiyle
    saydamlık → color-mix(in srgb, var(--…) %, transparent)."""
    def rgba(m: re.Match) -> str:
        h = m.group(1)
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return f"rgba({r}, {g}, {b}, {m.group(2)})"

    value = _ALPHA.sub(rgba, value)
    value = _THEME.sub(lambda m: "var(--theme-" + m.group(1).replace("_", "-") + ")", value)
    # Tema rengiyle saydamlık: alpha(var(--…), a) → color-mix(…)
    return _ALPHA_VAR.sub(
        lambda m: f"color-mix(in srgb, {m.group(1)} {float(m.group(2)) * 100:g}%, transparent)",
        value)


def css_tokens(path: Path = STYLES_CSS) -> dict[str, dict[str, str]]:
    """``STYLE_ROLES``'taki seçicilerin özelliklerini sözlük olarak döner.

    Tema renkleri ``var(--theme-…)`` olarak kalır (sistem temasından gelir;
    ``fg-color``, ``selected-bg-color`` …); okuyan taraf kendi karşılığını
    koyar. Dosya okunamazsa boş sözlük.
    """
    try:
        css = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    except OSError:
        return {}
    blocks: dict[str, dict[str, str]] = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        props = {}
        for decl in body.split(";"):
            name, sep, value = decl.partition(":")
            if sep and name.strip():
                props[name.strip()] = _css_value(value.strip())
        for sel in selectors.split(","):
            sel = " ".join(sel.split())
            if sel:
                blocks.setdefault(sel, {}).update(props)
    return {role: blocks[sel] for role, sel in STYLE_ROLES.items() if sel in blocks}
