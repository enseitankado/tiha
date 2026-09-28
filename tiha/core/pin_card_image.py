"""PIN anahtarı kartını telefona uygun dikey PNG olarak çizer.

HTML kâğıttaki "Resim olarak kaydet" çıktısının (telefon düzeni) aynısını
tarayıcı gerektirmeden, cairo + Pango ile üretir. Sıra: başlık, hesap türü,
kullanıcı satırı, uyarı, büyük QR, anahtar (iki satır), kurulum adımları.
Genişlik 400 mantıksal piksel, 3 kat ölçekle ~1200 px.
"""

from __future__ import annotations

import io
import re

import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
import cairo  # noqa: E402
from gi.repository import Pango, PangoCairo  # noqa: E402

from .qr import qr_matrix  # noqa: E402

WIDTH = 400
SCALE = 3
PAD = 18
QR_SIZE = 280

_BORDER = (0.53, 0.53, 0.53)
_BORDER_NEW = (0.18, 0.49, 0.2)
_MUTED = (0.33, 0.33, 0.33)
_TEXT = (0.13, 0.13, 0.13)
_WARN_BG = (0.99, 0.93, 0.92)
_WARN_BORDER = (0.88, 0.64, 0.64)
_WARN_TEXT = (0.54, 0.11, 0.11)
_KEY = (0.1, 0.45, 0.91)


def _html_to_markup(text: str) -> str:
    """Kâğıttaki adım metnini (strong/em/code, &gt;) Pango işaretlemesine
    çevirir; tanınmayan etiketler atılır."""
    text = re.sub(r"<(/?)strong>", r"<\1b>", text)
    text = re.sub(r"<(/?)em>", r"<\1i>", text)
    text = re.sub(r"<(/?)code>", r"<\1tt>", text)
    text = re.sub(r"<(?!/?(b|i|tt)>)[^>]+>", "", text)
    # Çıplak & işaretleri (entity olmayanlar) Pango'yu bozar.
    return re.sub(r"&(?!(amp|lt|gt|quot|apos|#\d+);)", "&amp;", text)


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _Painter:
    def __init__(self, ctx: cairo.Context | None):
        self.ctx = ctx
        self.y = PAD

    def _layout(self, markup: str, size: float, *, bold=False, mono=False,
                width: float = WIDTH - 2 * PAD, align=Pango.Alignment.LEFT):
        ctx = self.ctx or cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
        layout = PangoCairo.create_layout(ctx)
        family = "Ubuntu Mono, monospace" if mono else "Ubuntu, sans-serif"
        desc = Pango.FontDescription.from_string(f"{family} {'Bold ' if bold else ''}{size}")
        layout.set_font_description(desc)
        layout.set_width(int(width * Pango.SCALE))
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_alignment(align)
        layout.set_markup(markup, -1)
        return layout

    def text(self, markup, size, color, *, x=PAD, gap=4, **kw):
        layout = self._layout(markup, size, **kw)
        _w, h = layout.get_pixel_size()
        if self.ctx:
            self.ctx.set_source_rgb(*color)
            self.ctx.move_to(x, self.y)
            PangoCairo.show_layout(self.ctx, layout)
        self.y += h + gap
        return h

    def box(self, x, y, w, h, fill=None, stroke=None, radius=4, line=1.0):
        if not self.ctx:
            return
        c = self.ctx
        c.new_sub_path()
        c.arc(x + w - radius, y + radius, radius, -1.5708, 0)
        c.arc(x + w - radius, y + h - radius, radius, 0, 1.5708)
        c.arc(x + radius, y + h - radius, radius, 1.5708, 3.1416)
        c.arc(x + radius, y + radius, radius, 3.1416, 4.7124)
        c.close_path()
        if fill:
            c.set_source_rgb(*fill)
            c.fill_preserve() if stroke else c.fill()
        if stroke:
            c.set_source_rgb(*stroke)
            c.set_line_width(line)
            c.stroke()


def _paint(ctx: cairo.Context | None, card: dict, labels: dict) -> float:
    """Kartı çizer (ctx None ise yalnız yüksekliği hesaplar)."""
    p = _Painter(ctx)
    inner = WIDTH - 2 * PAD

    title = f"<b>{_escape(card['title'])}</b>"
    if card.get("new"):
        title += f'  <span background="#2e7d32" foreground="#ffffff" size="small"> {_escape(labels["badge_new"])} </span>'
    p.text(title, 15, _TEXT, gap=2)
    if card.get("kind"):
        p.text(f"({_escape(card['kind'])})", 10, _MUTED, gap=2)
    p.text(_escape(card.get("user_line", "")), 10, _MUTED, gap=10)

    if card.get("warn"):
        layout = p._layout(_escape("⚠ " + card["warn"]), 10, width=inner - 20)
        _w, h = layout.get_pixel_size()
        p.box(PAD, p.y, inner, h + 12, fill=_WARN_BG, stroke=_WARN_BORDER)
        if ctx:
            ctx.set_source_rgb(*_WARN_TEXT)
            ctx.move_to(PAD + 10, p.y + 6)
            PangoCairo.show_layout(ctx, layout)
        p.y += h + 12 + 12

    matrix = qr_matrix(card.get("qr_data", "")) if card.get("qr_data") else None
    if matrix:
        x0 = (WIDTH - QR_SIZE) / 2
        p.box(x0 - 6, p.y - 6, QR_SIZE + 12, QR_SIZE + 12, fill=(1, 1, 1), stroke=(0.87, 0.87, 0.87))
        if ctx:
            quiet = 2
            n = len(matrix) + 2 * quiet
            cell = QR_SIZE / n
            ctx.set_source_rgb(0, 0, 0)
            for yy, row in enumerate(matrix):
                for xx, dark in enumerate(row):
                    if dark:
                        ctx.rectangle(x0 + (xx + quiet) * cell, p.y + (yy + quiet) * cell,
                                      cell + 0.02, cell + 0.02)
            ctx.fill()
        p.y += QR_SIZE + 10
        p.text(_escape(labels["qr_label"]), 9, _MUTED, gap=12, align=Pango.Alignment.CENTER)

    p.text(_escape(labels["secret_label"]), 9, _MUTED, gap=4, align=Pango.Alignment.CENTER)
    groups = card.get("key", "").split()
    half = (len(groups) + 1) // 2
    key_text = " ".join(groups[:half]) + ("\n" + " ".join(groups[half:]) if groups[half:] else "")
    layout = p._layout(_escape(key_text), 16, bold=True, mono=True, align=Pango.Alignment.CENTER)
    _w, h = layout.get_pixel_size()
    p.box(PAD + 30, p.y, inner - 60, h + 12, fill=(1, 1, 1), stroke=(0.87, 0.87, 0.87))
    if ctx:
        ctx.set_source_rgb(*_KEY)
        ctx.move_to(PAD, p.y + 6)
        PangoCairo.show_layout(ctx, layout)
    p.y += h + 12 + 14

    for i, step in enumerate(card.get("steps", []), start=1):
        top = p.y
        p.text(f"{i}.", 10.5, _TEXT, gap=0)
        p.y = top
        p.text(_html_to_markup(step), 10.5, _TEXT, x=PAD + 20, width=inner - 20, gap=5)

    return p.y + PAD


def render_card_png(card: dict, labels: dict) -> bytes:
    """Kart sözlüğünden PNG baytları üretir.

    ``card``: title, kind, new, user_line, warn, key, steps, qr_data.
    ``labels``: badge_new, qr_label, secret_label (katalog metinleri).
    """
    height = _paint(None, card, labels)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, WIDTH * SCALE, int(height * SCALE) + 1)
    ctx = cairo.Context(surface)
    ctx.scale(SCALE, SCALE)
    ctx.set_source_rgb(1, 1, 1)
    ctx.paint()
    border = _BORDER_NEW if card.get("new") else _BORDER
    painter = _Painter(ctx)
    painter.box(4, 4, WIDTH - 8, height - 8, stroke=border, radius=8, line=1.5)
    _paint(ctx, card, labels)
    buf = io.BytesIO()
    surface.write_to_png(buf)
    return buf.getvalue()
