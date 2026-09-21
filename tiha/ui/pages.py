"""Sihirbaz sayfa sınıfları: Karşılama, Modül, Özet.

Tüm sayfalar ana pencerenin ``Gtk.ScrolledWindow``'u içinde çalışır;
dolayısıyla içerik uzadığında aksiyon çubuğuna taşmaz, kullanıcı
kaydırabilir. Uzun metinler (PIN anahtarı listesi, apt çıktısı, ayrıntı)
yine kendi ``ScrolledWindow``'larında sabit yükseklikte verilir.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from ..core import console
from ..core.i18n import t
from ..core.logger import get_logger
from ..core.module import ApplyResult, Module

log = get_logger(__name__)
from ..core.undo import Journal, JournalEntry
from . import params as params_schema
from ..core.report_log import REPORT_PARAMS_KEY, ActionLog, redact_params
# Rapor sayfa yüklenirken içe aktarılır: imaj temizliği /tmp'yi (bootstrap
# ile gelen TiHA'nın dizini) boşalttıktan sonra geç içe aktarma bulamaz.
from ..core.report import Report, StepReport, build_report
from ..core.private_files import write_user_file

log = get_logger(__name__)


# Yardımcı: içerik sayfasının ortak çerçeve marjları (kompakt ama nefes alan)
_PAGE_MARGIN = 18
_ROW_SPACING = 14

# Kısa değer alan form kutularının varsayılan genişliği (karakter). Bu
# türler satır boyunca uzamaz, sola yaslanır; alan başına şemadaki
# "width" ile değiştirilir. 0: doğal genişlik (spin, select).
_COMPACT_FIELD_WIDTHS = {
    "text": 28,
    "password": 28,
    "number": 8,
    "readonly": 20,
    "spin": 0,
    "select": 0,
    "button": 0,
}
_LONG_TEXT_HEIGHT = 180  # uzun metin kutularının sabit yüksekliği


def _compact_page() -> Gtk.Box:
    """Her sayfanın dış kutusu — sabit, nispeten dar marjlı."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=_ROW_SPACING)
    box.set_margin_top(_PAGE_MARGIN)
    box.set_margin_bottom(_PAGE_MARGIN)
    box.set_margin_start(_PAGE_MARGIN + 4)
    box.set_margin_end(_PAGE_MARGIN + 4)
    return box


def _apply_line_spacing(label: Gtk.Label, factor: float = 1.35) -> None:
    """Etikete Pango ``line-height`` özniteliği uygular.

    Pango 1.50+ gerekir; eski sürümlerde sessizce vazgeçer. Etiket içinde
    sarılmış uzun metinlerde satırların birbirine yapışmasını önler.
    """
    try:
        if hasattr(Pango, "attr_line_height_new"):
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_line_height_new(factor))
            label.set_attributes(attrs)
    except Exception:
        # Pango çok eskiyse veya öznitelik kabul etmezse görsel sorun yok
        pass


def _count_sentences(text: str) -> int:
    """Cümle sayısını yaklaşık olarak sayar (. ! ? ile biten dizeler).

    Aynı satırdaki birden çok cümleyi ve boş satırları (paragraf ayırıcı)
    hesaba katar. Rationale'ın "kısa" mı "uzun" mu olduğunu belirlemek
    için kullanılır — kesin dilbilimsel sayım değil, hızlı bir yaklaşım.
    """
    if not text:
        return 0
    import re
    parts = [p for p in re.split(r"[.!?]+", text) if p.strip()]
    return len(parts)


def _as_checked(value: object) -> bool:
    """Şema varsayılanı metin ("True"), ``default_from`` sağlayıcısı gerçek
    bool döndürür; onay kutusu ikisini de anlamalı."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _report_meta() -> list[tuple[str, str]]:
    """Yazdırılabilir raporun başlık altı bilgileri."""
    import socket
    from datetime import datetime

    from .. import __version__

    return [
        (t("core.report.html.meta_date"), datetime.now().strftime("%d.%m.%Y %H:%M")),
        (t("core.report.html.meta_host"), socket.gethostname()),
        (t("core.report.html.meta_version"), __version__),
    ]


def _widget_text(widget: Gtk.Widget | None) -> str:
    """Sayı/metin alanının karşılaştırılabilir değeri."""
    if isinstance(widget, Gtk.SpinButton):
        return str(int(round(widget.get_value())))
    if isinstance(widget, Gtk.Entry):
        return widget.get_text()
    tv = getattr(widget, "_textview", None)
    if isinstance(tv, Gtk.TextView):
        if tv.get_style_context().has_class("tiha-placeholder"):
            return ""
        buf = tv.get_buffer()
        start, end = buf.get_bounds()
        return buf.get_text(start, end, True)
    return ""


def _set_textarea_text(widget: Gtk.Widget, text: str) -> None:
    """Çok satırlı alana metin yazar; boşsa yer tutucu geri gelir."""
    tv = widget._textview  # type: ignore[attr-defined]
    placeholder = getattr(widget, "_placeholder", None)
    ctx = tv.get_style_context()
    if not text and placeholder:
        tv.get_buffer().set_text(placeholder)
        ctx.add_class("tiha-placeholder")
    else:
        tv.get_buffer().set_text(text)
        ctx.remove_class("tiha-placeholder")


def _no_focus_labels(widget: Gtk.Widget) -> None:
    """Kapsayıcıdaki bütün etiketlerin klavye odağı almasını kapatır."""
    if isinstance(widget, Gtk.Label):
        widget.set_can_focus(False)
    elif isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            _no_focus_labels(child)


def _wrapping_label(text: str, *, klass: str | None = None, selectable: bool = False) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=0)
    lbl.set_line_wrap(True)
    lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    lbl.set_selectable(selectable)
    if klass:
        lbl.get_style_context().add_class(klass)
    _apply_line_spacing(lbl)
    return lbl


def _scrolled_textview(text: str, *, monospace: bool = False,
                       editable: bool = False, height: int = _LONG_TEXT_HEIGHT,
                       css_class: str | None = None,
                       wrap: bool = True) -> Gtk.ScrolledWindow:
    """Kaydırma çubuklu, salt-okunur metin kutusu.

    ``wrap=False`` tablo benzeri hizalanmış (monospace) içerik için
    yatay kaydırmaya izin verir; sütunlar hizalı kalır.
    """
    tv = Gtk.TextView()
    tv.set_editable(editable)
    tv.set_cursor_visible(editable)
    if monospace:
        tv.set_monospace(True)
    if css_class:
        tv.get_style_context().add_class(css_class)
    tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR if wrap else Gtk.WrapMode.NONE)
    tv.set_pixels_above_lines(2)
    tv.set_pixels_below_lines(2)
    tv.get_buffer().set_text(text)
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroller.set_min_content_height(height)
    scroller.set_max_content_height(height)
    scroller.add(tv)
    scroller._textview = tv  # type: ignore[attr-defined]
    return scroller


# =========================================================================
# Karşılama sayfası
# =========================================================================


_WELCOME_INTRO = t("ui.welcome.intro")

_WELCOME_FEATURES_TITLE = t("ui.welcome.features_title")

# (başlık, açıklama) çiftleri — hoşgeldiniz sayfasında iki sütunlu bir
# Grid ile render edilir; böylece açıklamaların ilk harfi aynı hizada
# başlar. Başlıklar sol menüdeki adlarla birebir aynı ve aynı sırada.
_WELCOME_FEATURES: tuple[tuple[str, str], ...] = (
    (t("m09.sidebar_title"), t("ui.welcome.features.m09")),
    (t("m01.sidebar_title"), t("ui.welcome.features.m01")),
    (t("m02.sidebar_title"), t("ui.welcome.features.m02")),
    (t("m03.sidebar_title"), t("ui.welcome.features.m03")),
    (t("m13.sidebar_title"), t("ui.welcome.features.m13")),
    (t("m04.sidebar_title"), t("ui.welcome.features.m04")),
    (t("m05.title"), t("ui.welcome.features.m05")),
    (t("m06.sidebar_title"), t("ui.welcome.features.m06")),
    (t("m07.title"), t("ui.welcome.features.m07")),
    (t("m08.sidebar_title"), t("ui.welcome.features.m08")),
    (t("m11.sidebar_title"), t("ui.welcome.features.m11")),
    (t("m15.sidebar_title"), t("ui.welcome.features.m15")),
    (t("m17.sidebar_title"), t("ui.welcome.features.m17")),
    (t("m12.sidebar_title"), t("ui.welcome.features.m12")),
    (t("m14.sidebar_title"), t("ui.welcome.features.m14")),
    (t("m16.sidebar_title"), t("ui.welcome.features.m16")),
    (t("m10.title"), t("ui.welcome.features.m10")),
)

_WELCOME_FLOW = t("ui.welcome.flow")

# Proje deposu (Hoşgeldiniz sayfasında tıklanabilir satır olarak gösterilir).
_WELCOME_REPO_URL = "https://github.com/enseitankado/tiha"
_WELCOME_REPO_LABEL = t("ui.welcome.repo_label")


class WelcomePage(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=_ROW_SPACING)
        self.get_style_context().add_class("tiha-welcome")
        self.set_margin_top(_PAGE_MARGIN)
        self.set_margin_bottom(_PAGE_MARGIN)
        self.set_margin_start(_PAGE_MARGIN + 4)
        self.set_margin_end(_PAGE_MARGIN + 4)

        def add_paragraph(text: str) -> None:
            lbl = _wrapping_label(text)
            lbl.set_max_width_chars(110)
            self.pack_start(lbl, False, False, 0)

        heading = _wrapping_label(t("ui.welcome.heading"), klass="tiha-heading")
        self.pack_start(heading, False, False, 0)

        from ..core.os_release import is_supported, pretty_name
        if not is_supported():
            self.pack_start(
                _wrapping_label(
                    t("core.os_release.unsupported", name=pretty_name()),
                    klass="tiha-experimental-banner",
                ),
                False, False, 0,
            )

        add_paragraph(_WELCOME_INTRO)

        title_lbl = _wrapping_label(_WELCOME_FEATURES_TITLE, klass="tiha-form-section")
        title_lbl.set_max_width_chars(110)
        title_lbl.set_margin_top(4)
        self.pack_start(title_lbl, False, False, 0)

        # Features iki sütunlu Grid — sol sütun madde başlığı (kalın),
        # sağ sütun açıklama; tüm açıklamaların ilk harfi aynı x'ten
        # başlar. Satırlar arası boşluk 0.
        features_grid = Gtk.Grid()
        features_grid.set_row_spacing(2)
        features_grid.set_column_spacing(14)
        features_grid.set_margin_start(8)
        for row_idx, (title, description) in enumerate(_WELCOME_FEATURES):
            bullet = Gtk.Label(label="•", xalign=0)
            title_lbl = Gtk.Label(xalign=0)
            title_lbl.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
            desc_lbl = _wrapping_label(description)
            desc_lbl.set_hexpand(True)
            desc_lbl.set_max_width_chars(80)
            features_grid.attach(bullet,    0, row_idx, 1, 1)
            features_grid.attach(title_lbl, 1, row_idx, 1, 1)
            features_grid.attach(desc_lbl,  2, row_idx, 1, 1)
        self.pack_start(features_grid, False, False, 0)

        flow_lbl = _wrapping_label(_WELCOME_FLOW)
        flow_lbl.set_max_width_chars(110)
        flow_lbl.set_margin_top(8)
        self.pack_start(flow_lbl, False, False, 0)

        # GitHub depo bağlantısı — tıklanabilir.
        repo_lbl = Gtk.Label(xalign=0)
        repo_lbl.set_markup(
            f'<a href="{GLib.markup_escape_text(_WELCOME_REPO_URL)}">'
            f'{GLib.markup_escape_text(_WELCOME_REPO_LABEL)}</a>'
        )
        repo_lbl.set_use_markup(True)
        repo_lbl.set_track_visited_links(False)
        repo_lbl.set_margin_top(12)
        repo_lbl.set_max_width_chars(110)
        self.pack_start(repo_lbl, False, False, 0)


# =========================================================================
# Modül sayfası
# =========================================================================


class ModulePage(Gtk.Box):
    """Bir modülün ekran gösterimi — açıklama, form, (gerekirse canlı) sonuç."""

    def __init__(self, module: Module, journal: Journal) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=_ROW_SPACING)
        self.module = module
        self.journal = journal
        self.set_margin_top(_PAGE_MARGIN)
        self.set_margin_bottom(_PAGE_MARGIN)
        self.set_margin_start(_PAGE_MARGIN + 4)
        self.set_margin_end(_PAGE_MARGIN + 4)
        self._fields: dict[str, Gtk.Widget] = {}
        self._stream_buffer: Gtk.TextBuffer | None = None
        # Canlı çıktı modali ve parçaları — ilk akışta kurulur.
        self._stream_dialog: Gtk.Dialog | None = None
        self._stream_status: Gtk.Label | None = None
        self._stream_spinner: Gtk.Spinner | None = None
        self._stream_close_btn: Gtk.Button | None = None
        self._applying: bool = False
        self._auto_applied: bool = False
        self.post_apply_callback = None  # Set by main_window if needed
        # Bu modülün son apply çağrısında kullanılan parametreler; preset
        # export için main_window tarafından okunur. None: hiç uygulanmadı.
        self.last_apply_params: dict | None = None
        # Önizleme widget'ı + şartlı (visible_when) alanların widget grupları:
        # Apply / buton işlemi sonrası tazelemek için saklanır.
        self._preview_widget: Gtk.Widget | None = None
        self._conditional_field_widgets: dict[str, list[Gtk.Widget]] = {}
        self._build()
        # Önceki oturumda uygulanmış mı? Varsa "geri al" banner'ı göster.
        self._show_previous_apply_banner()

    # ------------------------------------------------------------------
    # UI kurulumu
    # ------------------------------------------------------------------

    def _build(self) -> None:
        # Rationale 3 cümleden uzunsa: başlığın sağına yuvarlak "?" düğmesi;
        # tıklanınca rationale ayrı bir pencerede açılır, sayfa kaymaz. 3 ve
        # altı ise rationale sayfada hemen görünür — düğme yok.
        rationale_text = (self.module.rationale or "").strip()
        sentence_count = _count_sentences(rationale_text)
        is_long_rationale = sentence_count > 3 and not getattr(
            self.module, "rationale_inline", False,
        )

        heading_lbl = _wrapping_label(self.module.title, klass="tiha-heading")
        if is_long_rationale:
            heading_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            heading_row.pack_start(heading_lbl, False, False, 0)
            help_btn = Gtk.Button(label="?")
            help_btn.get_style_context().add_class("tiha-help-btn")
            help_btn.set_tooltip_text(t("ui.pages.rationale_toggle_tip"))
            help_btn.set_valign(Gtk.Align.CENTER)
            help_btn.connect("clicked", lambda *_: self._show_rationale_dialog())
            heading_row.pack_start(help_btn, False, False, 0)
            self.pack_start(heading_row, False, False, 0)
        else:
            self.pack_start(heading_lbl, False, False, 0)

        if self.module.experimental:
            banner = _wrapping_label(
                t("ui.pages.experimental_banner"),
                klass="tiha-experimental-banner",
            )
            self.pack_start(banner, False, False, 0)

        # Modülün o anki duruma göre verdiği vurgulu not (ör. SSH adımında
        # "root parolası tanımlı değil"). Sayfa her açıldığında tazelenir.
        self._notice_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.pack_start(self._notice_holder, False, False, 0)
        self._refresh_notice()

        if not is_long_rationale:
            self.pack_start(self._rationale_box(), False, False, 0)

        preview_text = ""
        try:
            preview_text = self.module.preview() or ""
        except Exception as exc:
            log.warning("preview başarısız %s: %s", self.module.id, exc)
        if preview_text:
            # Uzun önizleme → scroll'lu metin kutusu; kısa önizleme → label.
            # Modülünde `preview_tabular = True` bildirilmedikçe uzun
            # metinler word-wrap yapılır; böylece yatay kaydırma çubuğu
            # gereksiz yere oluşmaz (Türkçe uzun cümleler için önemli).
            is_tabular = bool(getattr(self.module, "preview_tabular", False))
            if preview_text.count("\n") > 6 or len(preview_text) > 500:
                self._preview_widget = _scrolled_textview(
                    preview_text, monospace=True,
                    height=180, css_class="tiha-preview",
                    wrap=not is_tabular,
                )
                self.pack_start(self._preview_widget, False, False, 0)
            else:
                self._preview_widget = _wrapping_label(
                    preview_text, klass="tiha-preview", selectable=True,
                )
                self.pack_start(self._preview_widget, False, False, 0)

        schema = params_schema.get(self.module.id)
        if schema:
            form = self._build_form(schema)
            self.pack_start(form, False, False, 0)

        # Canlı akış artık sayfada değil, geniş bir modalda gösterilir
        # (bkz. _open_stream_dialog). Sayfada yer tutmasına gerek yok.
        self.stream_view: Gtk.TextView | None = None

        # Sonuç kutusu
        self.result_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.pack_start(self.result_holder, False, False, 0)

        # Modül-özel ek bağlantılar (sol hizalı, link görünümlü)
        for link in getattr(self.module, "extra_links", []) or []:
            self.pack_start(self._make_action_link(link), False, False, 0)

    def _make_action_link(self, link: dict) -> Gtk.Widget:
        """Sol hizalı, mavi altı çizili tıklanabilir bir bağlantı üretir."""
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_halign(Gtk.Align.START)
        btn.get_style_context().add_class("tiha-action-link")
        lbl = Gtk.Label()
        lbl.set_markup(f"<u>{GLib.markup_escape_text(link['label'])}</u>")
        lbl.set_xalign(0)
        btn.add(lbl)
        action = link.get("action")
        btn.connect(
            "clicked",
            lambda b, a=action: self._run_button_action(a, button=b) if a else None,
        )
        return btn

    def _show_previous_apply_banner(self) -> None:
        """Journal'da önceki bir oturumdan kalma 'applied' kayıt varsa
        bilgilendirme + 'Bu adımı geri al' düğmesi göster. Sihirbaz'ın
        mevcut oturumunda yeni bir uygulama yapılınca result_holder
        temizlenip bu banner gider."""
        entry = self.journal.last_applied(self.module.id)
        if entry is None:
            return
        # Mevcut oturumda eklenmişse banner gösterme — normal akış zaten
        # _show_result üzerinden yönetiliyor.
        if entry.timestamp >= self.journal.session_start:
            return

        from datetime import datetime
        try:
            when = datetime.fromisoformat(entry.timestamp).strftime("%d.%m.%Y %H:%M")
        except (TypeError, ValueError):
            when = entry.timestamp

        banner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        banner.get_style_context().add_class("tiha-prev-banner")
        banner.pack_start(
            _wrapping_label(
                t("ui.pages.previous_apply", when=when, summary=entry.summary),
                selectable=True,
            ),
            False, False, 0,
        )
        if self.module.undo_supported:
            undo_btn = Gtk.Button(label=t("ui.pages.undo_step"))
            undo_btn.get_style_context().add_class("destructive-action")
            undo_btn.connect("clicked", lambda *_: self._undo_clicked())
            banner.pack_start(undo_btn, False, False, 0)
        self.result_holder.pack_start(banner, False, False, 0)

    def _build_form(self, schema: list[dict]) -> Gtk.Grid:
        if not hasattr(self, "_auto_values"):
            self._auto_values: dict[str, str] = {}
        self._toggle_rows: dict[str, list[Gtk.Widget]] = {}
        grid = Gtk.Grid(column_spacing=10, row_spacing=4)
        grid.get_style_context().add_class("tiha-form")
        row_idx = 0
        for field in schema:
            # Bölüm başlığı: değer taşımaz, iki sütuna yayılan kalın etiket.
            if field.get("type") == "heading":
                section = _wrapping_label(field["label"], klass="tiha-form-section")
                grid.attach(section, 0, row_idx, 2, 1)
                row_idx += 1
                continue

            # Şartlı görünürlük: visible_when bir modül methodunu işaret
            # ediyorsa, başlangıç görünürlüğünü ondan al. Widget'lar
            # daima oluşturulur ve _conditional_field_widgets'ta saklanır;
            # böylece sonraki bir buton/apply işleminden sonra durum
            # değişirse görünürlük tazelenebilir.
            gate = field.get("visible_when")
            initial_visible = True
            if gate:
                gate_fn = getattr(self.module, gate, None)
                if callable(gate_fn):
                    initial_visible = bool(gate_fn())

            label = _wrapping_label(field["label"])
            grid.attach(label, 0, row_idx, 1, 1)
            widget = self._make_field(field)
            kind = field.get("type", "text")
            if kind in _COMPACT_FIELD_WIDTHS:
                # Kısa değer alan kutular satır boyu uzamasın: sola yaslı,
                # içeriğe göre makul genişlik (şemada "width" ile ayarlanır).
                widget.set_hexpand(False)
                widget.set_halign(Gtk.Align.START)
                entry = getattr(widget, "_entry", widget)
                chars = field.get("width", _COMPACT_FIELD_WIDTHS[kind])
                if chars and isinstance(entry, Gtk.Entry) and not isinstance(entry, Gtk.SpinButton):
                    entry.set_width_chars(chars)
            else:
                widget.set_hexpand(True)
            grid.attach(widget, 1, row_idx, 1, 1)
            self._fields[field["key"]] = widget
            if field.get("default_from") and field.get("type") != "bool":
                # Sistemden okunarak doldurulan değer; kullanıcı bunu
                # değiştirirse tazeleme onun değerine dokunmaz.
                self._auto_values[field["key"]] = _widget_text(widget)
            row_idx += 1
            row_widgets: list[Gtk.Widget] = [label, widget]
            if field.get("help"):
                help_lbl = _wrapping_label(field["help"], klass="tiha-rationale")
                # Kutular daraldı; sütunu sayfa genişliğine yardım metni yayar.
                help_lbl.set_hexpand(True)
                help_widget: Gtk.Widget = help_lbl
                if field.get("help_folded"):
                    # Uzun ve nadiren gereken açıklama: kapalı başlayan bir
                    # katlayıcının içinde durur, form kısa görünür.
                    expander = Gtk.Expander(label=t("ui.pages.help_expander"))
                    expander.set_expanded(False)
                    expander.add(help_lbl)
                    expander.get_style_context().add_class("tiha-rationale")
                    help_widget = expander
                grid.attach(help_widget, 1, row_idx, 1, 1)
                row_idx += 1
                row_widgets.append(help_widget)
            if field.get("help_more"):
                # Yardımın ilk kısmı açıkta, uzun devamı (neden/nasıl)
                # kapalı başlayan bir katlayıcıda.
                more_lbl = _wrapping_label(field["help_more"], klass="tiha-rationale")
                more_lbl.set_hexpand(True)
                more = Gtk.Expander(label=t("ui.pages.help_more_expander"))
                more.set_expanded(False)
                more.add(more_lbl)
                more.get_style_context().add_class("tiha-rationale")
                grid.attach(more, 1, row_idx, 1, 1)
                row_idx += 1
                row_widgets.append(more)

            if field.get("visible_when_any"):
                # Görünürlüğü onay kutularına bağlı satır; ilk durum aşağıdaki
                # _update_conditional_fields turunda ayarlanır.
                self._toggle_rows[field["key"]] = row_widgets
                for w in row_widgets:
                    w.set_no_show_all(True)
            if gate:
                self._conditional_field_widgets[field["key"]] = row_widgets
                if not initial_visible:
                    for w in row_widgets:
                        w.set_no_show_all(True)
                        w.set_visible(False)

        # Checkbox'ların başlangıç durumuna göre ilgili alanları ayarla
        for field in schema:
            if field.get("type") == "bool":
                checkbox_key = field["key"]
                widget = self._fields.get(checkbox_key)
                if widget and hasattr(widget, 'get_active'):
                    self._update_conditional_fields(checkbox_key, widget.get_active())

        return grid

    def _refresh_dynamic_defaults(self, *, bools_only: bool = False) -> None:
        """``default_from``'lu alanları sistemin güncel durumundan tazeler.

        Form sayfaları uygulama açılışında bir kez kuruluyor. Bir buton
        işlemi sistemi değiştirdiğinde (ör. fazladan hesaplar silindi,
        anahtarlar boşaltıldı) kutuda işlem öncesinin sayısı kalıyordu.

        Yalnız buton işlemlerinden sonra çağrılır; sayfa her açılışta
        çağrılsaydı kullanıcının elle girdiği değeri silerdi.

        ``bools_only`` yalnız onay kutularını tazeler. Uygula/geri al
        sonrası bu biçimde çağrılır: "özellik açık mı" kutusu sistemin
        yeni hâlini göstermeli, ama kullanıcının girdiği sayı ve metinler
        (kapanma saati, boşta süresi, geri sayım) tercihi olarak kalmalı.
        """
        schema = params_schema.get(self.module.id) or []
        for field in schema:
            source = field.get("default_from")
            if not source:
                continue
            is_bool = field.get("type") == "bool"
            if bools_only and not is_bool:
                continue
            provider = getattr(self.module, source, None)
            if not callable(provider):
                continue
            try:
                value = provider()
            except Exception as exc:
                log.warning("default_from tazelenemedi (%s): %s", source, exc)
                continue
            self._apply_default_value(field["key"], field.get("type", "text"), value)

    def _apply_default_value(self, key: str, kind: str, value) -> None:
        """Sistemden okunan değeri alana yazar. Onay kutusu hep güncellenir
        (sistem durumudur); sayı/metin alanı kullanıcı değiştirdiyse ona
        dokunulmaz — ör. "yedek hesap sayısı" 20 yapılıp alakasız bir düğmeye
        basılınca eski değere dönüyordu."""
        widget = self._fields.get(key)
        if widget is None:
            return
        if kind == "bool":
            if hasattr(widget, "set_active"):
                widget.set_active(_as_checked(value))
            return
        if not hasattr(self, "_auto_values"):
            self._auto_values = {}
        auto = self._auto_values.get(key)
        if auto is not None and _widget_text(widget) != auto:
            return
        if isinstance(widget, Gtk.SpinButton):
            widget.set_value(float(value or 0))
        elif isinstance(widget, Gtk.Entry):
            widget.set_text("" if value is None else str(value))
        elif isinstance(getattr(widget, "_textview", None), Gtk.TextView):
            _set_textarea_text(widget, "" if value is None else str(value))
        else:
            return
        self._auto_values[key] = _widget_text(widget)

    def _refresh_state_checkboxes(self) -> None:
        """Uygula/geri al sonrası "durum" kutularını sistemden tazeler.

        Sayısal ve metin alanlarına dokunmaz; onlar kullanıcının tercihi.
        """
        self._refresh_dynamic_defaults(bools_only=True)

    def _refresh_conditional_fields(self) -> None:
        """``visible_when``'lı alanların görünürlüğünü tazeler.

        Apply ya da buton işlemi durumu değiştirmiş olabilir (ör. fazladan
        hesap silindi → "Fazladan Hesapları Sil" düğmesi gizlensin).
        """
        schema = params_schema.get(self.module.id) or []
        for field in schema:
            gate = field.get("visible_when")
            if not gate:
                continue
            gate_fn = getattr(self.module, gate, None)
            visible = bool(callable(gate_fn) and gate_fn())
            for w in self._conditional_field_widgets.get(field["key"], ()):
                w.set_no_show_all(not visible)
                w.set_visible(visible)

    def _update_conditional_fields(self, checkbox_key: str, is_active: bool) -> None:
        """Checkbox durumuna göre ilgili alanları etkinleştir/pasifleştir."""

        # Güç yönetimi modülü için checkbox-field ilişkilerini tanımla
        field_relationships = {
            "auto_enabled": ["auto_hour", "auto_minute"],
            "idle_enabled": ["idle_minute"]
        }

        related_fields = list(field_relationships.get(checkbox_key, []))
        # Şemada bir kutu ``enables`` listesi taşıyorsa, işaretsizken o
        # alanlar pasifleşir (ör. m17: hafif mod kapalıyken alt ayarları).
        for field in params_schema.get(self.module.id) or []:
            if field.get("key") == checkbox_key:
                related_fields += field.get("enables", [])

        for field_key in related_fields:
            widget = self._fields.get(field_key)
            if widget:
                widget.set_sensitive(is_active)

        # Genel şema mekanizması: bir alanın schema'sında
        # ``enable_when_field: "<checkbox_key>"`` varsa, ilgili checkbox
        # durumuna göre widget'ın sensitive halini senkronla. Böylece
        # metrik izleme checkbox'ı pasifken "Metrik dinleme adresi"
        # kutusu da pasif görünür.
        schema = params_schema.get(self.module.id) or []
        for f in schema:
            if f.get("enable_when_field") != checkbox_key:
                continue
            widget = self._fields.get(f["key"])
            if widget is not None:
                widget.set_sensitive(is_active)

        # ``enable_when_any: [kutu, kutu, …]``: listedeki kutulardan en az biri
        # işaretliyse alan etkin (ör. m11 geri sayım süresi: sabit saat ya da
        # boşta kapanma açıkken anlamlı).
        for f in schema:
            sources = f.get("enable_when_any") or []
            if checkbox_key not in sources:
                continue
            widget = self._fields.get(f["key"])
            if widget is None:
                continue
            any_on = any(
                getattr(self._fields.get(k), "get_active", lambda: False)()
                for k in sources
            )
            widget.set_sensitive(any_on)

        # ``visible_when_any: [kutu, kutu, …]``: listedeki kutulardan biri
        # işaretliyken satır görünür (ör. m11 muaf tahtalar listesi).
        for f in schema:
            sources = f.get("visible_when_any") or []
            if checkbox_key not in sources:
                continue
            any_on = any(
                getattr(self._fields.get(k), "get_active", lambda: False)()
                for k in sources
            )
            for w in getattr(self, "_toggle_rows", {}).get(f["key"], ()):
                w.set_no_show_all(not any_on)
                if any_on:
                    w.show_all()
                else:
                    w.set_visible(False)

    def _refresh_preview(self) -> None:
        """Önizleme metnini yeniden üretip aynı widget'a yazar."""
        if self._preview_widget is None:
            return
        try:
            new_text = self.module.preview() or ""
        except Exception as exc:
            log.warning("preview tazelenemedi %s: %s", self.module.id, exc)
            return
        self._set_preview_text(new_text)

    def _set_preview_text(self, new_text: str) -> None:
        if self._preview_widget is None:
            return
        if isinstance(self._preview_widget, Gtk.ScrolledWindow):
            tv = getattr(self._preview_widget, "_textview", None)
            if tv is not None:
                tv.get_buffer().set_text(new_text)
        elif isinstance(self._preview_widget, Gtk.Label):
            self._preview_widget.set_text(new_text)

    def _update_form_fields(self, config_values: dict) -> None:
        """Form alanlarını verilen config değerleriyle güncelle."""
        for key, value in config_values.items():
            widget = self._fields.get(key)
            if widget is None:
                continue

            # Widget tipine göre değer ataması
            try:
                if hasattr(widget, 'set_active'):  # CheckBox
                    is_checked = str(value).lower() in ("true", "1", "yes", "on")
                    widget.set_active(is_checked)
                    # Checkbox değişikliklerini tetikle (conditional fields için)
                    self._update_conditional_fields(key, is_checked)
                elif hasattr(widget, 'set_value'):  # SpinButton
                    widget.set_value(float(value))
                elif hasattr(widget, 'set_text'):  # Entry
                    widget.set_text(str(value))
            except Exception as exc:
                log.warning("Form field güncelleme hatası %s: %s", key, exc)
            _apply_line_spacing(self._preview_widget)

    def _refresh_after_action(self) -> None:
        """Apply / buton işlemi sonrası önizleme + şartlı alan + dinamik
        button etiketi tazeleme."""
        self.apply_view_state(self.collect_view_state())

    # --- Sayfa açılışı: ağır hesap arka planda, yazma ana iş parçacığında ---
    # Sayfa geçişinde modül önizlemesi, şartlı alan denetimleri ve düğme
    # etiketleri alt süreç çalıştırabiliyor (dconf, apt, dpkg…). Bunlar ana
    # iş parçacığında yapılınca arayüz 1-2 sn donuyordu. collect_view_state
    # GTK'ya dokunmadan hesaplar (arka planda çağrılabilir);
    # apply_view_state sonucu widget'lara yazar (ana iş parçacığında).

    def collect_view_state(self) -> dict:
        state: dict = {}

        def safe(label, fn):
            try:
                return fn()
            except Exception as exc:
                log.warning("%s başarısız %s: %s", label, self.module.id, exc)
                return None

        state["notice"] = safe("notice", self.module.notice)
        if self._preview_widget is not None:
            state["preview"] = safe("preview", self.module.preview)
        schema = params_schema.get(self.module.id) or []
        gates: dict[str, bool] = {}
        labels: dict[str, str] = {}
        for field in schema:
            gate = field.get("visible_when")
            if gate:
                fn = getattr(self.module, gate, None)
                gates[field["key"]] = bool(callable(fn) and safe(gate, fn))
            source = field.get("label_from")
            if source:
                fn = getattr(self.module, source, None)
                if callable(fn):
                    value = safe(source, fn)
                    if isinstance(value, str) and value:
                        labels[field["key"]] = value
        state["gates"] = gates
        state["labels"] = labels
        # Sistemden dolan alanlar (default_from): sayfaya her geçişte tahtanın
        # o anki durumunu göstersin (Özet'ten geri alma ya da TiHA dışında
        # yapılan değişiklik eski durumda kalmasın).
        defaults: dict[str, tuple[str, object]] = {}
        for field in schema:
            source = field.get("default_from")
            if not source:
                continue
            fn = getattr(self.module, source, None)
            if callable(fn):
                defaults[field["key"]] = (field.get("type", "text"), safe(source, fn))
        state["defaults"] = defaults
        # Otomatik kapanma: form her açılışta güncel eta-shutdown ayarını
        # göstersin (ETA Zamanlı Kapatma arayüzünden değişmiş olabilir).
        if self.module.id == "m11_power_management":
            fn = getattr(self.module, "get_current_config", None)
            if callable(fn):
                state["config"] = safe("get_current_config", fn)
        return state

    def apply_view_state(self, state: dict | None) -> None:
        state = state or {}
        # Uyarı şeridi
        for child in self._notice_holder.get_children():
            self._notice_holder.remove(child)
        notice = state.get("notice")
        if notice:
            kind, text = notice
            klass = "tiha-experimental-banner" if kind == "warning" else "tiha-prev-banner"
            self._notice_holder.pack_start(_wrapping_label(text, klass=klass), False, False, 0)
            self._notice_holder.show_all()
        # Önizleme
        if "preview" in state and state["preview"] is not None:
            self._set_preview_text(state["preview"] or "")
        # Şartlı alanlar
        for key, visible in (state.get("gates") or {}).items():
            for w in self._conditional_field_widgets.get(key, ()):
                w.set_no_show_all(not visible)
                w.set_visible(visible)
        # Düğme etiketleri
        for key, label in (state.get("labels") or {}).items():
            widget = self._fields.get(key)
            if isinstance(widget, Gtk.Button):
                widget.set_label(label)
        if state.get("config"):
            self._update_form_fields(state["config"])
        for key, (kind, value) in (state.get("defaults") or {}).items():
            if value is not None:
                self._apply_default_value(key, kind, value)

    def _rationale_box(self) -> Gtk.Box:
        """Adım açıklaması + (varsa) teknik belge bağlantısı. Kısa
        açıklamalarda sayfaya, uzunlarda "?" penceresine konur."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        text = (self.module.rationale or "").strip()
        box.pack_start(_wrapping_label(text, klass="tiha-rationale"), False, False, 0)

        # İsteğe bağlı: adıma ait teknik belge / algoritma şeması linki.
        # Emoji kullanılmıyor — sadece linkin kendisi.
        if self.module.doc_url:
            label = self.module.doc_label or t("ui.pages.doc_link_default")
            doc_lbl = Gtk.Label(xalign=0)
            doc_lbl.set_markup(
                f'<a href="{GLib.markup_escape_text(self.module.doc_url)}">'
                f'{GLib.markup_escape_text(label)}</a>'
            )
            doc_lbl.set_use_markup(True)
            doc_lbl.set_selectable(False)
            doc_lbl.set_track_visited_links(False)
            doc_lbl.get_style_context().add_class("tiha-rationale")
            box.pack_start(doc_lbl, False, False, 0)
        return box

    def _show_rationale_dialog(self) -> None:
        """Uzun adım açıklamasını ayrı, kaydırılabilir bir pencerede gösterir."""
        dlg = Gtk.Dialog(
            title=self.module.title,
            transient_for=self.get_toplevel(),
            modal=True,
            destroy_with_parent=True,
        )
        dlg.add_button(t("ui.main.close"), Gtk.ResponseType.CLOSE)
        dlg.set_default_size(680, 520)

        content = dlg.get_content_area()
        heading = _wrapping_label(self.module.title, klass="tiha-heading")
        heading.set_margin_top(14)
        heading.set_margin_start(18)
        heading.set_margin_end(18)
        content.pack_start(heading, False, False, 0)

        body = self._rationale_box()
        body.get_style_context().add_class("tiha-rationale-dialog")
        body.set_margin_top(8)
        body.set_margin_bottom(14)
        body.set_margin_start(18)
        body.set_margin_end(18)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.add(body)
        content.pack_start(scrolled, True, True, 0)

        dlg.show_all()
        dlg.run()
        dlg.destroy()

    def _refresh_notice(self) -> None:
        for child in self._notice_holder.get_children():
            self._notice_holder.remove(child)
        try:
            notice = self.module.notice()
        except Exception as exc:
            log.warning("notice başarısız %s: %s", self.module.id, exc)
            notice = None
        if not notice:
            return
        kind, text = notice
        klass = "tiha-experimental-banner" if kind == "warning" else "tiha-prev-banner"
        self._notice_holder.pack_start(_wrapping_label(text, klass=klass), False, False, 0)
        self._notice_holder.show_all()

    def _refresh_button_labels(self) -> None:
        """Her button widget'ının `label_from` provider'ı varsa etiketini
        modülün metodundan güncel değere göre yeniden hesaplar. Etiketler
        adımın her aksiyonundan sonra güncel kalır."""
        for key, widget in list(self._fields.items()):
            if not isinstance(widget, Gtk.Button):
                continue
            provider_name = getattr(widget, "_label_from", None)
            if not provider_name:
                continue
            provider = getattr(self.module, provider_name, None)
            if not callable(provider):
                continue
            try:
                new_label = provider()
            except Exception as exc:
                log.warning(
                    "label_from tazeleme başarısız (%s): %s",
                    provider_name, exc,
                )
                continue
            if isinstance(new_label, str) and new_label:
                widget.set_label(new_label)

    def _make_field(self, field: dict) -> Gtk.Widget:
        kind = field.get("type", "text")
        default = field.get("default", "")

        # "default_from" bir modül methodunu işaret ediyorsa varsayılanı
        # oradan al. Sistemin o anki durumuna göre dolu gelmesi gereken
        # alanlar için (ör. m03 yedek hesap sayısı) gerekir.
        source = field.get("default_from")
        if source:
            provider = getattr(self.module, source, None)
            if callable(provider):
                try:
                    value = provider()
                except Exception as exc:
                    log.warning("default_from başarısız (%s): %s", source, exc)
                else:
                    if value is not None:
                        default = str(value)

        # Remote Syslog modülü için mevcut yapılandırmayı okuyup formu
        # ön-doldur. _parse_config dict döner: {"host","port","proto","profile"}.
        if self.module.id == "m06_remote_syslog":
            try:
                from ..modules.m06_remote_syslog import (
                    _parse_config, LOG_PROFILES,
                )
                config = _parse_config()
                if config:
                    if field["key"] == "syslog_host":
                        default = config.get("host", "")
                    elif field["key"] == "syslog_port":
                        default = str(config.get("port", 514))
                    elif field["key"] == "syslog_proto":
                        default = config.get("proto", "udp")
                    elif field["key"] == "log_profile":
                        prof_key = config.get("profile", "bakim")
                        prof = LOG_PROFILES.get(prof_key)
                        if prof:
                            default = prof["label"]
            except Exception:
                pass

        # BIOS yönetici parolası — adıma girişte gösterme. Kullanıcı
        # "Mevcut yönetici parolasını oku" düğmesine basınca asenkron
        # action sonucu kutuya yazılır (bkz. _on_button_action_complete).

        if kind == "textarea":
            tv = Gtk.TextView()
            tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            buf = tv.get_buffer()
            placeholder = field.get("placeholder")
            if placeholder:
                # Placeholder metnini yerleştir, CSS ile soluk göster.
                # Odaklanıldığında (ve metin hâlâ placeholder ise) temizle;
                # boşsa odak kaybında geri koy. Sistemden dolu gelen değer
                # (default_from) varsa yer tutucu yerine o görünür.
                if default:
                    buf.set_text(default)
                else:
                    buf.set_text(placeholder)
                    tv.get_style_context().add_class("tiha-placeholder")

                def on_focus_in(_widget, _event, _ph=placeholder):
                    start, end = buf.get_bounds()
                    if buf.get_text(start, end, True) == _ph:
                        buf.set_text("")
                        tv.get_style_context().remove_class("tiha-placeholder")
                    return False

                def on_focus_out(_widget, _event, _ph=placeholder):
                    start, end = buf.get_bounds()
                    if not buf.get_text(start, end, True).strip():
                        buf.set_text(_ph)
                        tv.get_style_context().add_class("tiha-placeholder")
                    return False

                tv.connect("focus-in-event", on_focus_in)
                tv.connect("focus-out-event", on_focus_out)
            elif default:
                buf.set_text(default)

            scroller = Gtk.ScrolledWindow()
            scroller.add(tv)
            scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scroller.set_min_content_height(110)
            scroller.set_max_content_height(150)
            scroller.get_style_context().add_class("tiha-textarea")
            scroller._textview = tv  # type: ignore[attr-defined]
            scroller._placeholder = placeholder  # type: ignore[attr-defined]
            return scroller

        if kind == "spin":
            lower = float(field.get("min", 0))
            upper = float(field.get("max", 100))
            step = float(field.get("step", 1))
            value = float(default or lower)
            adj = Gtk.Adjustment(
                value=value, lower=lower, upper=upper,
                step_increment=step, page_increment=step * 5,
            )
            spin = Gtk.SpinButton()
            spin.set_adjustment(adj)
            spin.set_numeric(True)
            spin.set_digits(0)
            # Not: m03'te "ortak PIN" kutusu eskiden yedek hesap sayısı
            # 0 iken pasifleştiriliyordu. Artık grup üyeliği yedek
            # hesaplara bağlı değil (listedeki öğretmenler de gruba
            # giriyor), dolayısıyla ortak PIN her durumda anlamlı ve
            # kutu her zaman seçilebilir.
            return spin

        if kind == "readonly":
            # Değeri modül belirler (sabit ya da ``default_from``);
            # kullanıcı yalnızca görür ve kopyalayabilir, değiştiremez.
            # Örn. m16'da GRUB'ın soracağı superuser adı.
            entry = Gtk.Entry()
            entry.set_text(default)
            entry.set_editable(False)
            entry.set_hexpand(True)
            entry.get_style_context().add_class("tiha-readonly")
            return entry

        if kind == "select":
            combo = Gtk.ComboBoxText()
            for opt in field.get("options", []):
                combo.append_text(opt)
            idx = 0
            if default in field.get("options", []):
                idx = field["options"].index(default)
            combo.set_active(idx)
            return combo

        if kind == "button":
            initial_label = field.get("label", "Button")
            # Etiket dinamikse (label_from), initial label'ı da o metottan al.
            label_from = field.get("label_from")
            if label_from:
                provider = getattr(self.module, label_from, None)
                if callable(provider):
                    try:
                        dyn = provider()
                        if isinstance(dyn, str) and dyn:
                            initial_label = dyn
                    except Exception as exc:
                        log.warning(
                            "label_from başarısız (%s): %s", label_from, exc,
                        )
            btn = Gtk.Button(label=initial_label)
            if field.get("style") == "destructive":
                btn.get_style_context().add_class("destructive-action")
            # Refresh mekanizması için provider referansını widget'a bağla.
            if label_from:
                btn._label_from = label_from  # type: ignore[attr-defined]

            def on_button_clicked(_btn, action=field.get("action"),
                                  confirm=field.get("confirm")):
                # Geri alınamaz butonlar şemada bir "confirm" bloğu
                # taşır; onay alınmadan action çalıştırılmaz.
                if confirm and not self._confirm_action(confirm):
                    return
                if action and hasattr(self.module, action):
                    self._run_button_action(action, button=_btn)

            btn.connect("clicked", on_button_clicked)
            return btn

        if kind == "bool":
            checkbox = Gtk.CheckButton()
            checkbox.set_active(_as_checked(default))

            # Checkbox değişikliklerini dinle ve ilgili alanları aktif/pasif yap
            def on_checkbox_toggled(cb, field_key=field["key"],
                                    deselects=tuple(field.get("deselects", ())),
                                    selects=tuple(field.get("selects", ()))):
                # Bu kutu seçildiğinde:
                #   * `deselects` listesindeki çelişkili kutuların işareti
                #     kaldırılır (tek yönlü: geri işaretlemek kullanıcının
                #     tercihidir).
                #   * `selects` listesindeki gerekli-ön-koşul kutuları
                #     otomatik işaretlenir. Bu, teknik olarak ancak beraber
                #     çalışan kombinasyonların (ör. grup PIN'i + gruba
                #     üyelik) UI'da eşleşik görünmesi için.
                if cb.get_active():
                    for other_key in deselects:
                        other = self._fields.get(other_key)
                        if isinstance(other, Gtk.CheckButton) and other.get_active():
                            other.set_active(False)
                    for other_key in selects:
                        other = self._fields.get(other_key)
                        if isinstance(other, Gtk.CheckButton) and not other.get_active():
                            other.set_active(True)
                self._update_conditional_fields(field_key, cb.get_active())

            checkbox.connect("toggled", on_checkbox_toggled)
            return checkbox

        if kind == "file":
            # HBox: dosya yolu girilen Entry + sağında "Göz at…" düğmesi.
            # FileChooserDialog seçimi Entry'ye yazar. Manuel path yazımı
            # da serbest — kullanıcı isterse tarayıcı açmadan girer.
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            entry = Gtk.Entry()
            entry.set_text(default)
            entry.set_hexpand(True)
            placeholder = field.get("placeholder")
            if placeholder:
                entry.set_placeholder_text(placeholder)
            box.pack_start(entry, True, True, 0)

            browse_btn = Gtk.Button(label=t("ui.pages.browse"))

            def on_browse(_btn, _entry=entry, _field=field):
                dlg = Gtk.FileChooserDialog(
                    title=t("ui.pages.choose_file_title", label=_field.get("label", "")),
                    transient_for=self.get_toplevel(),
                    action=Gtk.FileChooserAction.OPEN,
                )
                dlg.add_buttons(
                    t("ui.main.cancel"), Gtk.ResponseType.CANCEL,
                    t("ui.pages.choose"), Gtk.ResponseType.ACCEPT,
                )
                # Başlangıç klasörü seçimi.
                # Mevcut entry değeri varsa ilk konum olarak aç; aksi
                # hâlde TiHA sudo/pkexec ile root context'te çalıştığı
                # için default cwd / /root oluyor → kullanıcı kendi
                # dosyalarını göremiyor. Aktif grafik oturumdaki user'ın
                # HOME'una (varsa Masaüstü/Desktop alt klasörüne)
                # yönlendiriyoruz.
                from pathlib import Path as _P
                current = _entry.get_text().strip()
                started_at_user_dir = False
                if current:
                    cp = _P(current).expanduser()
                    if cp.is_file():
                        dlg.set_filename(str(cp))
                        started_at_user_dir = True
                    elif cp.parent.is_dir():
                        dlg.set_current_folder(str(cp.parent))
                        started_at_user_dir = True
                if not started_at_user_dir:
                    # Aktif kullanıcı oturumundan HOME al, Masaüstü tercih et
                    try:
                        from ..core.utils import _find_active_graphical_session
                        env = _find_active_graphical_session()
                    except ImportError:
                        env = None
                    user_home = _P(env["HOME"]) if env else None
                    candidates = []
                    if user_home:
                        candidates.extend([
                            user_home / "Masaüstü",   # Türkçe (Pardus)
                            user_home / "Desktop",
                            user_home / "Belgeler",
                            user_home / "Documents",
                            user_home / "İndirilenler",
                            user_home / "Downloads",
                            user_home,
                        ])
                    for cand in candidates:
                        if cand and cand.is_dir():
                            dlg.set_current_folder(str(cand))
                            break
                # Filtreler. ÖNEMLİ: "Tüm dosyalar" filter'ını VARSAYILAN
                # olarak set ediyoruz. Sudo / xdg-desktop-portal-kde
                # bağlamında GTK image filter'ları (add_pixbuf_formats,
                # add_pattern, add_mime_type, add_custom) güvenilir
                # çalışmıyor — dosyalar listede gizli kalıyor. Default'u
                # "Tüm dosyalar" yapıp kullanıcının PNG'yi rahatlıkla
                # görmesini sağlıyoruz; istek hâlinde filter combobox'tan
                # "Görsel dosyaları"na geçilebilir. Uzantı kontrolü zaten
                # apply tarafında yapılıyor.
                any_fil = Gtk.FileFilter()
                any_fil.set_name(t("ui.pages.filter_all_files"))
                any_fil.add_pattern("*")
                dlg.add_filter(any_fil)
                dlg.set_filter(any_fil)  # default aktif

                # Yardımcı "Görsel dosyaları" filter'ı — kullanıcı ister
                # ve düzgün çalışırsa kullansın diye eklenir.
                img_fil = Gtk.FileFilter()
                img_fil.set_name(t("ui.pages.filter_images"))
                img_fil.add_pixbuf_formats()
                dlg.add_filter(img_fil)
                try:
                    if dlg.run() == Gtk.ResponseType.ACCEPT:
                        fname = dlg.get_filename()
                        if fname:
                            _entry.set_text(fname)
                finally:
                    dlg.destroy()

            browse_btn.connect("clicked", on_browse)
            box.pack_start(browse_btn, False, False, 0)
            # _field_value Entry'ye erişebilmek için referansı sakla
            box._entry = entry  # type: ignore[attr-defined]
            return box

        entry = Gtk.Entry()
        entry.set_text(default)
        # BIOS yönetici parolası — yalnızca İngilizce BÜYÜK harf (A-Z) ve
        # rakam (0-9) girilebilsin. 'I' harfi okunabilirlik için yasak
        # ('1' ile karışıyor). Türkçe karakterler ve küçük harfler
        # reddedilir; küçük harf girildiyse büyütülür.
        if (self.module.id == "m14_bios_password"
                and field.get("key") == "supervisor_password"):
            entry.set_max_length(12)

            def _filter_insert(e, text, length, position):
                cleaned = "".join(
                    ch for ch in text.upper()
                    if ch != "I" and (("A" <= ch <= "Z") or ("0" <= ch <= "9"))
                )
                if cleaned != text:
                    # Default handler'ı block edip temiz metni elle ekle
                    # (signal recursion engellenir).
                    e.handler_block(handler_id[0])
                    pos = e.get_position()
                    e.insert_text(cleaned, pos)
                    e.set_position(pos + len(cleaned))
                    e.handler_unblock(handler_id[0])
                    e.stop_emission_by_name("insert-text")

            handler_id = [0]
            handler_id[0] = entry.connect("insert-text", _filter_insert)
        if field.get("allowed_chars"):
            self._attach_char_filter(entry, field["allowed_chars"])
        if field.get("placeholder"):
            entry.set_placeholder_text(field["placeholder"])
        if field.get("hint"):
            # Kutunun içinde kısa ipucu (placeholder), üzerine gelince tam
            # açıklama (tooltip).
            entry.set_tooltip_text(field["hint"])
        if kind == "password":
            # Parolalar varsayılan olarak görünür: tahtada dokunmatik ekran
            # klavyesiyle yazarken yanlış girilen karakter ancak böyle fark
            # edilir. Göz düğmesi gizlemek için hâlâ duruyor.
            entry.set_visibility(True)
            entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
            if field.get("show_toggle", True):
                # Entry sağına göz düğmesi: tıklanınca parolayı göster/gizle.
                entry.set_icon_from_icon_name(
                    Gtk.EntryIconPosition.SECONDARY,
                    "view-conceal-symbolic",
                )
                entry.set_icon_tooltip_text(
                    Gtk.EntryIconPosition.SECONDARY,
                    t("ui.pages.password_toggle_tip"),
                )
                entry.set_icon_activatable(Gtk.EntryIconPosition.SECONDARY, True)

                def on_icon_press(_entry, _pos, _event, e=entry):
                    visible = not e.get_visibility()
                    e.set_visibility(visible)
                    e.set_icon_from_icon_name(
                        Gtk.EntryIconPosition.SECONDARY,
                        "view-conceal-symbolic" if visible else "view-reveal-symbolic",
                    )

                entry.connect("icon-press", on_icon_press)
        if kind == "number":
            entry.set_input_purpose(Gtk.InputPurpose.DIGITS)

        # Parola alanının altına canlı güç göstergesi
        # (şemada "strength_below": True ise). Kullanıcı yazarken
        # score_password çağrılır, etiket renklenir. UI thread'ini
        # bloklamamak için skorlama basit tutuldu (blacklist
        # lookup O(1), skor O(n)).
        if field.get("strength_below"):
            from ..core.password_strength import score_password

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.pack_start(entry, False, False, 0)
            strength_lbl = Gtk.Label(xalign=0)
            strength_lbl.set_use_markup(True)
            strength_lbl.set_line_wrap(True)
            strength_lbl.get_style_context().add_class("tiha-rationale")
            # Boşken hem show_all'a takılmasın hem de dikey yer kaplayıp
            # yardım metnini parola kutusundan uzağa itmesin: default'ta
            # gizli; parola yazıldığında görünür olur.
            strength_lbl.set_no_show_all(True)
            box.pack_start(strength_lbl, False, False, 0)

            def _update(_e, lbl=strength_lbl):
                s = score_password(entry.get_text())
                if not entry.get_text():
                    lbl.set_markup("")
                    lbl.set_visible(False)
                    return
                colors = ("#c62828", "#e65100", "#f9a825",
                          "#2e7d32", "#1b5e20")
                color = colors[s.score]
                main = (
                    f'<span foreground="{color}"><b>'
                    + GLib.markup_escape_text(
                        t("ui.pages.strength", label=s.label, score=s.score)
                    )
                    + '</b></span>'
                )
                if s.warnings:
                    warn = GLib.markup_escape_text(" ".join(s.warnings))
                    lbl.set_markup(f'{main}  <small>{warn}</small>')
                else:
                    lbl.set_markup(main)
                lbl.set_visible(True)

            entry.connect("changed", _update)
            _update(entry)  # ilk render (boş → gizli etiket)

            box._entry = entry  # type: ignore[attr-defined]
            return box
        return entry

    def _attach_char_filter(self, entry: Gtk.Entry, allowed: str) -> None:
        """Kutuya yalnız ``allowed`` (regex karakter sınıfı içeriği, ör.
        "A-Za-z0-9") karakterlerinin yazılmasına izin verir. Reddedilen
        tuşta kutunun solunda birkaç saniye uyarı simgesi belirir; üzerine
        gelince nedeni okunur (şemadaki yardım metni ayrıntıyı anlatır)."""
        bad = re.compile(f"[^{allowed}]")
        handler_id = [0]
        hide_timer = [0]

        def _hide_warning() -> bool:
            entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, None)
            hide_timer[0] = 0
            return False

        def _filter(e, text, _length, _position):
            cleaned = bad.sub("", text)
            if cleaned == text:
                return
            e.handler_block(handler_id[0])
            pos = e.get_position()
            if cleaned:
                e.insert_text(cleaned, pos)
                e.set_position(pos + len(cleaned))
            e.handler_unblock(handler_id[0])
            e.stop_emission_by_name("insert-text")
            e.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "dialog-warning-symbolic")
            e.set_icon_tooltip_text(
                Gtk.EntryIconPosition.PRIMARY, t("ui.pages.char_rejected"),
            )
            if hide_timer[0]:
                GLib.source_remove(hide_timer[0])
            hide_timer[0] = GLib.timeout_add_seconds(4, _hide_warning)

        handler_id[0] = entry.connect("insert-text", _filter)

    def _field_value(self, key: str, field: dict) -> str:
        widget = self._fields[key]
        kind = field.get("type", "text")
        if kind == "textarea":
            tv = widget._textview  # type: ignore[attr-defined]
            # Placeholder hâlâ etkin mi?
            if tv.get_style_context().has_class("tiha-placeholder"):
                return ""
            buf = tv.get_buffer()
            start, end = buf.get_bounds()
            return buf.get_text(start, end, True)
        if kind == "spin":
            return str(int(widget.get_value()))
        if kind == "select":
            return widget.get_active_text() or ""
        if kind == "button":
            return ""  # Buttons don't have values
        if kind == "bool":
            return str(widget.get_active())  # True/False → "True"/"False"
        if kind == "file":
            # _make_field bunu HBox yaptı; içindeki Entry'e referans tuttuk
            return widget._entry.get_text()  # type: ignore[attr-defined]
        # strength_below ile sarmalanmış password kutusu — inner entry
        # box._entry olarak saklandı.
        if isinstance(widget, Gtk.Box) and hasattr(widget, "_entry"):
            return widget._entry.get_text()  # type: ignore[attr-defined]
        return widget.get_text()

    def _collect_params(self) -> tuple[dict, list[str]]:
        schema = params_schema.get(self.module.id)
        params: dict = {}
        missing: list[str] = []
        for field in schema:
            if field.get("type") == "heading":
                continue
            key = field["key"]
            widget = self._fields.get(key)
            if widget is None:
                continue
            # Şartlı görünürlüğü kapatılmış alanlar parametre olarak
            # iletilmez; gerekli olarak işaretlense bile uyarı vermeyiz.
            if not widget.get_visible():
                continue
            value = self._field_value(key, field).strip()
            if field.get("required") and not value:
                missing.append(field["label"])
            params[key] = value
        return params, missing

    # ------------------------------------------------------------------
    # Apply akışı — thread'li + canlı çıktı
    # ------------------------------------------------------------------

    def _record_action(self, result: ApplyResult) -> None:
        """Düğme eylemini kalıcı eylem kaydına yazar (Özet raporu okur).

        Günceye yazılmaz: düğme eylemi geri alınabilir bir adım değil.
        """
        ctx = getattr(self, "_action_ctx", None)
        self._action_ctx = None
        if not ctx:
            return
        action, label, params = ctx
        try:
            ActionLog().record(
                module_id=self.module.id,
                title=self.module.title,
                action=action,
                label=label,
                success=bool(result.success),
                summary=result.summary or "",
                params=params,
                data=result.data if isinstance(result.data, dict) else {},
            )
        except Exception as exc:  # rapor kaydı işlemi asla bozmasın
            log.warning("Eylem kaydı yazılamadı: %s", exc)

    def _run_button_action(self, action: str, button: Gtk.Button | None = None) -> None:
        """Button action'ını canlı çıktı ve görsel geri bildirimle çalıştırır."""
        if self._applying:
            return

        # Tıklanan butonu çift tıklamaya karşı pasifleştir
        self._active_button = button
        if button is not None:
            button.set_sensitive(False)

        # result_holder'ı temizle ve "Çalışıyor…" satırı ekle
        for child in self.result_holder.get_children():
            self.result_holder.remove(child)
        self._working_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        spinner = Gtk.Spinner()
        spinner.start()
        self._working_row.pack_start(spinner, False, False, 0)
        self._working_row.pack_start(
            _wrapping_label(t("ui.pages.working_row")),
            False, False, 0,
        )
        self.result_holder.pack_start(self._working_row, False, False, 0)
        self.result_holder.show_all()

        # Konsola da adım satırı düş — apply yolunda olduğu gibi. Buton
        # işlemleri (hesap silme, anahtar silme) sistemi kalıcı olarak
        # değiştiriyor; terminal dökümünde izi kalmalı.
        label = button.get_label() if button is not None else ""
        console.step(t("ui.pages.console_action", title=self.module.title, label=label) if label else self.module.title)

        # Canlı çıktıyı modalda göster
        self._open_stream_dialog(t("ui.pages.stream_title_running", title=self.module.title))

        def progress_callback(text: str) -> None:
            GLib.idle_add(self._append_stream_line, text)

        # Form değerlerini ANA thread'de topla. Button action'ları (örn. m14
        # "Bu makinenin BIOS parolasını ayarla") parola/koruma gibi alanlara
        # ihtiyaç duyar; bunlar geçmezse action params'ı boş görür ve yanlış
        # davranır (m14 boş parolayı "temizle" niyeti sayar). GTK widget'larına
        # worker thread'inden erişmek güvenli olmadığı için burada toplarız.
        params, _missing = self._collect_params()
        # Eylem kaydı (Özet raporu) için tamamlanınca kullanılır.
        self._action_ctx = (action, label, dict(params))

        def worker():
            try:
                action_func = getattr(self.module, action)
                try:
                    result = action_func(params=params, progress=progress_callback)
                except TypeError:
                    # Parametre kabul etmeyen eski action imzaları için
                    # zarif geri dönüş.
                    try:
                        result = action_func(progress=progress_callback)
                    except TypeError:
                        result = action_func()
                GLib.idle_add(self._on_button_action_complete, result)
            except Exception as exc:
                error_result = ApplyResult(False, t("ui.pages.action_error", error=exc))
                GLib.idle_add(self._on_button_action_complete, error_result)

        self._applying = True
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _on_button_action_complete(self, result: ApplyResult) -> None:
        self._record_action(result)
        self._finish_stream_dialog(
            result.summary,
            result.success,
            details=result.details or "",
            copyable=result.copyable or "",
        )
        self._applying = False
        if getattr(self, "_active_button", None) is not None:
            self._active_button.set_sensitive(True)
            self._active_button = None
        # m14: action sonucundan parola ve/veya koruma modunu form'a yansıt.
        if self.module.id == "m14_bios_password" and isinstance(result.data, dict):
            if "supervisor_password" in result.data:
                entry = self._fields.get("supervisor_password")
                if isinstance(entry, Gtk.Entry):
                    entry.set_text(result.data.get("supervisor_password") or "")
            prot = result.data.get("protection_mode")
            if prot in ("always", "setup"):
                combo = self._fields.get("protection_mode")
                if isinstance(combo, Gtk.ComboBoxText):
                    # params.py'daki option sırası: [0]=setup, [1]=always
                    combo.set_active(0 if prot == "setup" else 1)
        # Terminale sonuç satırı — apply yolundaki davranışın aynısı.
        if result.success:
            console.ok(result.summary)
        else:
            console.fail(result.summary)
        self._show_result(result)
        if result.warning:
            self._show_warning_dialog(result.warning)
        # Buton işlemi sistem durumunu değiştirmiş olabilir — önizlemeyi
        # ve "visible_when" şartlı alanların görünürlüğünü tazele.
        self._refresh_after_action()
        # Sistemden okunan varsayılanları da tazele: hesaplar silindiyse
        # "yedek hesap sayısı" kutusunda eski sayı kalmasın.
        self._refresh_dynamic_defaults()

    def run_apply(self) -> None:
        if self._applying:
            return
        params, missing = self._collect_params()
        if missing:
            self._show_result(ApplyResult(False, t("ui.pages.missing_fields", fields=", ".join(missing))))
            return

        self._applying = True
        # Terminale profesyonel satır (son kullanıcı içindir)
        console.step(self.module.title)
        for child in self.result_holder.get_children():
            self.result_holder.remove(child)

        # Kullanıcıya "çalışıyor" geri bildirimi: spinner + metin.
        self._working_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        spinner = Gtk.Spinner()
        spinner.start()
        self._working_row.pack_start(spinner, False, False, 0)
        self._working_row.pack_start(
            _wrapping_label(t("ui.pages.applying_row")),
            False, False, 0,
        )
        self.result_holder.pack_start(self._working_row, False, False, 0)
        self.result_holder.show_all()

        # Tüm adımlarda: canlı çıktı / sonuç için modal aç. Modül akış
        # yayınlamıyorsa modal boş kalır ve iş bitince özet + detay
        # oraya yazılır. Kapat düğmesi iş bitene kadar pasif kalır.
        self._open_stream_dialog(t("ui.pages.stream_title_applying", title=self.module.title))

        thread = threading.Thread(
            target=self._apply_thread_body,
            args=(params,),
            daemon=True,
        )
        thread.start()

    def _apply_thread_body(self, params: dict) -> None:
        # Preset export için son apply parametrelerini sakla.
        self.last_apply_params = dict(params)

        def progress(line: str) -> None:
            GLib.idle_add(self._append_stream_line, line)

        progress_cb = progress if self.module.streams_output else None
        try:
            if progress_cb is not None:
                result = self.module.apply_with_logging(params, progress=progress_cb)
            else:
                result = self.module.apply_with_logging(params)
        except Exception as exc:
            log.exception("Modül uygulanamadı: %s", self.module.id)
            result = ApplyResult(False, t("ui.pages.unexpected_error", error=exc))

        GLib.idle_add(self._apply_thread_done, result)

    # ---------------- Canlı çıktı modali ----------------
    # Çıktı eskiden sayfanın içindeki ~220 px'lik bir alana akıyordu;
    # uzun apt/useradd çıktılarında okunmuyor ve sayfayı kaydırmak
    # gerekiyordu. Artık adım uygulanırken geniş bir modal açılıp akış
    # oraya yazılır; iş bitene kadar kapatılamaz.

    def _open_stream_dialog(self, title: str) -> None:
        """Canlı çıktı modalını açar (varsa yeniden kullanır)."""
        if self._stream_dialog is not None:
            self._stream_buffer.set_text("")
            self._stream_status.set_text(t("ui.pages.stream_working"))
            self._stream_spinner.start()
            self._stream_close_btn.set_sensitive(False)
            self._stream_dialog.present()
            return

        dlg = Gtk.Dialog(
            title=title,
            transient_for=self.get_toplevel(),
            modal=True,
        )
        dlg.set_default_size(920, 620)
        dlg.set_resizable(True)

        content = dlg.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._stream_spinner = Gtk.Spinner()
        self._stream_spinner.start()
        status_row.pack_start(self._stream_spinner, False, False, 0)
        self._stream_status = Gtk.Label(label=t("ui.pages.stream_working"))
        self._stream_status.set_xalign(0.0)
        self._stream_status.set_line_wrap(True)
        status_row.pack_start(self._stream_status, True, True, 0)
        content.pack_start(status_row, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self.stream_view = Gtk.TextView()
        self.stream_view.set_editable(False)
        self.stream_view.set_cursor_visible(False)
        self.stream_view.set_monospace(True)
        self.stream_view.get_style_context().add_class("tiha-stream")
        self._stream_buffer = self.stream_view.get_buffer()
        scroll.add(self.stream_view)
        content.pack_start(scroll, True, True, 0)

        self._stream_close_btn = dlg.add_button(t("ui.main.close"), Gtk.ResponseType.CLOSE)
        self._stream_close_btn.set_sensitive(False)

        def on_response(_dlg, _response):
            # İş sürerken kapatmaya izin vermiyoruz.
            if self._applying:
                return
            _dlg.hide()

        def on_delete(_dlg, _event):
            return self._applying  # True: kapanmayı engelle

        dlg.connect("response", on_response)
        dlg.connect("delete-event", on_delete)

        self._stream_dialog = dlg
        dlg.show_all()

    def _finish_stream_dialog(
        self,
        summary: str,
        success: bool,
        *,
        details: str = "",
        copyable: str = "",
    ) -> None:
        """Akış bittiğinde modalı kapatılabilir hâle getirir.

        Modül akış yayınlamadıysa (ya da yaydıysa bile), sonuç
        özeti + varsa ayrıntı ve kopyalanabilir rapor modal'ın metin
        alanına da eklenir; kullanıcı modalı kapatmadan önce tüm
        çıktıyı orada görsün, gerekirse kopyalasın diye.
        """
        if self._stream_dialog is None:
            return
        self._stream_spinner.stop()
        self._stream_status.set_text(
            t("ui.pages.stream_done", summary=summary) if success
            else t("ui.pages.stream_failed", summary=summary)
        )

        if self._stream_buffer is not None:
            end = self._stream_buffer.get_end_iter()
            start = self._stream_buffer.get_start_iter()
            has_content = self._stream_buffer.get_char_count() > 0
            report = "\n\n".join(
                part.strip() for part in (details, copyable) if part and part.strip()
            )
            trailing = t("ui.pages.stream_result_trailing", summary=summary)
            if report:
                trailing += "\n" + report + "\n"
            if not has_content:
                # Akış yayınlanmadıysa baştan yaz — çirkin ayraç olmasın.
                self._stream_buffer.set_text(
                    t("ui.pages.stream_result", summary=summary)
                    + (("\n" + report + "\n") if report else "")
                )
            else:
                self._stream_buffer.insert(end, trailing)
            # En alta kaydır
            end = self._stream_buffer.get_end_iter()
            mark = self._stream_buffer.get_insert()
            self._stream_buffer.place_cursor(end)
            if self.stream_view is not None:
                self.stream_view.scroll_mark_onscreen(mark)

        self._stream_close_btn.set_sensitive(True)
        self._stream_close_btn.grab_focus()

    def _append_stream_line(self, line: str) -> bool:
        if self._stream_buffer is None:
            return False
        end = self._stream_buffer.get_end_iter()
        self._stream_buffer.insert(end, line + "\n")
        mark = self._stream_buffer.get_insert()
        self.stream_view.scroll_mark_onscreen(mark)
        return False

    def _apply_thread_done(self, result: ApplyResult) -> bool:
        self._applying = False
        self._finish_stream_dialog(
            result.summary,
            result.success or result.not_applicable,
            details=result.details or "",
            copyable=result.copyable or "",
        )
        # "Çalışıyor" göstergesini kaldır (result_holder temizlenecek)
        entry = JournalEntry.new(self.module.id, self.module.title)
        entry.summary = result.summary
        entry.status = (
            "skipped" if result.not_applicable
            else "applied" if result.success else "failed"
        )
        # Modülün bıraktığı undo verisini günceye taşı
        entry.data = dict(result.data) if isinstance(result.data, dict) else {}
        # Özet raporu hangi seçeneklerle uygulandığını bilsin (parolalar
        # maskelenir; yalnız "girildi mi" bilgisi kalır).
        entry.data[REPORT_PARAMS_KEY] = redact_params(
            self.module.id, self.last_apply_params,
        )
        self.journal.record(entry)
        # Terminale profesyonel sonuç satırı
        if result.success:
            console.ok(result.summary)
        elif result.not_applicable:
            console.note(result.summary)
        else:
            console.fail(result.summary)
        self._show_result(result)
        if result.warning:
            # "Mutlaka uyarılsın": sayfadaki blok kaydırılıp kaçırılabilir,
            # modal kaçırılamaz.
            self._show_warning_dialog(result.warning)
        # Apply de sistem durumunu değiştirmiş olabilir — aynı tazelemeyi
        # buradan da çalıştır.
        self._refresh_after_action()
        # "Özellik açık mı" kutuları yeni durumu göstersin; kullanıcının
        # girdiği sayı/metin alanlarına dokunulmaz.
        self._refresh_state_checkboxes()
        if self.post_apply_callback is not None:
            try:
                self.post_apply_callback(result)
            except Exception as exc:
                log.debug("post_apply_callback hatası: %s", exc)
        # Modül "tamamlandı" sinyalini özellikle popup ile vermek istiyorsa
        if result.success and getattr(self.module, "popup_on_success", False):
            self._toast(result.summary)
        return False

    # ------------------------------------------------------------------
    # Sonuç gösterimi
    # ------------------------------------------------------------------

    def _show_result(self, result: ApplyResult) -> None:
        for child in self.result_holder.get_children():
            self.result_holder.remove(child)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.get_style_context().add_class(
            "tiha-result-ok" if result.success
            else "tiha-prev-banner" if result.not_applicable
            else "tiha-result-fail"
        )
        box.pack_start(_wrapping_label(result.summary, selectable=True), False, False, 0)

        # Kaçırılmaması gereken uyarı: hem sayfada vurgulu bir blok, hem
        # de aşağıda modal bir diyalog. Sonuç kutusu başarı renginde
        # olduğu için uyarı kendi sınıfıyla ayrışır.
        if result.warning:
            warn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            warn_box.get_style_context().add_class("tiha-result-fail")
            warn_box.pack_start(
                _wrapping_label(t("ui.pages.warning_line", warning=result.warning), selectable=True),
                False, False, 0,
            )
            box.pack_start(warn_box, False, False, 0)

        # Ayrıntı ve kopyalanabilir rapor TEK alanda gösterilir. Ayrı
        # kutular hâlindeyken aynı bilgiler (üretilen/korunan sayıları)
        # iki kez görünüyordu; birleşik metin ayrıntının bulunduğu
        # yerde, yani sonuç kutusunun hemen altında durur.
        report = "\n\n".join(
            part for part in (result.details, result.copyable) if part
        )
        if report:
            # Rapor hizalı çerçeveler içeriyorsa monospace şart.
            if result.copyable:
                box.pack_start(
                    _scrolled_textview(report, monospace=True, height=260),
                    False, False, 0,
                )
            elif report.count("\n") > 6 or len(report) > 500:
                box.pack_start(
                    _scrolled_textview(report, height=160),
                    False, False, 0,
                )
            else:
                box.pack_start(_wrapping_label(report, selectable=True), False, False, 0)

        if result.copyable:
            # Buton satırı: panoya kopyala + dosyaya kaydet
            btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            copy_btn = Gtk.Button(label=t("ui.pages.copy_clipboard"))
            # Ekranda görünen birleşik metni kopyalar.
            copy_btn.connect("clicked", lambda *_: self._copy_to_clipboard(report))
            btn_row.pack_start(copy_btn, False, False, 0)

            save_btn = Gtk.Button(label=t("ui.pages.save_to_file"))
            # Modül kaydedilecek içeriği ayrıca verdiyse (ör. m03'ün
            # yazdırılabilir HTML kâğıdı) ekrandaki metin yerine onu
            # kaydediyoruz; dosya adının uzantısı biçimi belirler.
            save_text = (
                result.save_payload
                if result.save_payload is not None
                else (result.copyable or "")
            )
            default_name = (
                result.save_filename
                or self.module.save_filename
                or f"tiha-{self.module.id}.txt"
            )
            save_btn.connect(
                "clicked",
                lambda *_: self._save_to_file(save_text, default_name),
            )
            btn_row.pack_start(save_btn, False, False, 0)
            box.pack_start(btn_row, False, False, 0)

        # "Bu adımı geri al" düğmesi yalnızca günlükte HÂLÂ geri
        # alınabilir (applied statüsünde) bir kayıt varsa gösterilir.
        # Aksi hâlde başarılı bir undo sonrası bile düğme yeniden çizilip
        # tıklanınca "kayıt bulunamadı" hatasına yol açıyordu.
        if (
            result.success
            and self.module.undo_supported
            and self.journal.last_applied(self.module.id) is not None
        ):
            undo_btn = Gtk.Button(label=t("ui.pages.undo_step"))
            undo_btn.get_style_context().add_class("destructive-action")
            undo_btn.connect("clicked", lambda *_: self._undo_clicked())
            box.pack_start(undo_btn, False, False, 0)

        if not result.success and not result.not_applicable:
            report_btn = Gtk.Button(label=t("ui.pages.report_bug"))
            report_btn.set_tooltip_text(t("ui.pages.report_bug_tip"))
            report_btn.connect("clicked", lambda *_: self._report_failure(result))
            box.pack_start(report_btn, False, False, 0)

        self.result_holder.pack_start(box, False, False, 0)
        self.result_holder.show_all()

    def _report_failure(self, result: ApplyResult) -> None:
        """Adım başarısız olduğunda kullanıcıya GitHub Issue ön-doldurulmuş
        bir URL açar. Body'de: adım id, sürüm, özet, detay ve log son
        satırları. Anonimleştirme ipucu olarak parolayı andıran satırlar
        sansürlenir."""
        import subprocess
        import urllib.parse
        from datetime import datetime
        from .. import __version__
        from ..core.paths import LOG_FILE

        # Son 50 satır log
        log_tail = ""
        try:
            if LOG_FILE.exists():
                lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
                # Parola/secret andıran satırları sansürle
                sanitized = []
                for ln in lines[-50:]:
                    low = ln.lower()
                    if any(s in low for s in ("password", "parola", "secret", "smbpasswd")):
                        sanitized.append(t("ui.pages.issue.masked_line"))
                    else:
                        sanitized.append(ln)
                log_tail = "\n".join(sanitized)
        except OSError:
            log_tail = t("ui.pages.issue.log_unreadable")

        body = t(
            "ui.pages.issue.head",
            module_id=self.module.id, title=self.module.title,
            version=__version__,
            date=datetime.now().isoformat(timespec="seconds"),
            summary=result.summary,
        )
        if result.details:
            body += t("ui.pages.issue.details", details=result.details[:1500])
        body += t("ui.pages.issue.log", log_tail=log_tail[-4000:])
        title = f"[{self.module.id}] {result.summary[:80]}"

        url = (
            "https://github.com/enseitankado/tiha/issues/new?"
            + urllib.parse.urlencode({
                "title": title,
                "body": body,
                "labels": "bug",
            })
        )

        # TiHA root yetkisiyle çalışır; xdg-open root'un (boş) session'ında
        # tarayıcı bulamaz, sessiz fail eder. Aktif kullanıcı oturumunda
        # xdg-open çalıştırmamız lazım — m14'tekiyle aynı pattern.
        spawned = False
        try:
            from ..core.utils import _find_active_graphical_session
            env = _find_active_graphical_session()
            if env:
                subprocess.Popen(
                    ["sudo", "-u", env["USER"], "env"]
                    + [f"{k}={v}" for k, v in env.items()]
                    + ["xdg-open", url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                spawned = True
        except (ImportError, OSError):
            pass

        # Fallback: doğrudan xdg-open (root oturumu varsa çalışır)
        if not spawned:
            try:
                subprocess.Popen(["xdg-open", url],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                spawned = True
            except OSError:
                pass

        # Yine de açılamadıysa kullanıcıya URL'i göster — kopyalayabilsin
        if not spawned:
            dlg = Gtk.MessageDialog(
                transient_for=self.get_toplevel(), modal=True,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text=t("ui.pages.browser_failed"),
            )
            dlg.format_secondary_text(t("ui.pages.browser_failed_body", url=url))
            dlg.run()
            dlg.destroy()

    def _copy_to_clipboard(self, text: str) -> None:
        from gi.repository import Gdk
        clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clip.set_text(text, -1)

    def _save_to_file(self, text: str, default_name: str) -> None:
        """Sonuç içeriğini kullanıcının seçtiği bir dosyaya yazar.

        Varsayılan olarak etapadmin'in ev dizinindeki 'Masaüstü' (ya
        da yoksa ev dizini) açılır. Root olarak yazılan dosya sonra
        etapadmin'e chown'lanır ki kullanıcı açabilsin.
        """
        import os
        import pwd as _pwd
        from pathlib import Path

        dlg = Gtk.FileChooserDialog(
            title=t("ui.pages.save_dialog_title"),
            transient_for=self.get_toplevel(),
            action=Gtk.FileChooserAction.SAVE,
        )
        dlg.add_buttons(
            t("ui.main.cancel"), Gtk.ResponseType.CANCEL,
            t("ui.main.save"), Gtk.ResponseType.ACCEPT,
        )
        dlg.set_current_name(default_name)
        dlg.set_do_overwrite_confirmation(True)

        # Biçim, önerilen dosya adının uzantısından gelir. Diyaloğu o
        # uzantıyla sınırlıyoruz; kullanıcı adı değiştirse de uzantı
        # aşağıda geri eklenir, böylece dosya her zaman beklenen
        # biçimde açılır (ör. HTML kâğıt tarayıcıda).
        forced_suffix = Path(default_name).suffix
        if forced_suffix:
            html_filter = Gtk.FileFilter()
            label = forced_suffix.lstrip(".").upper()
            html_filter.set_name(t("ui.pages.file_filter", kind=label, suffix=forced_suffix))
            html_filter.add_pattern(f"*{forced_suffix}")
            dlg.add_filter(html_filter)

        # Etapadmin ev dizinini varsayılan konum yap
        try:
            etap_home = _pwd.getpwnam("etapadmin").pw_dir
            for candidate in ("Masaüstü", "Desktop", ""):
                folder = os.path.join(etap_home, candidate) if candidate else etap_home
                if os.path.isdir(folder):
                    dlg.set_current_folder(folder)
                    break
        except KeyError:
            pass

        response = dlg.run()
        if response == Gtk.ResponseType.ACCEPT:
            path = dlg.get_filename()
            if forced_suffix and not path.lower().endswith(forced_suffix.lower()):
                path += forced_suffix
            try:
                # Kaydedilen içerik gizli olabilir (PIN anahtarları,
                # parolalar): dosya etapadmin'e ait, 0600 yazılır. Eskiden
                # 0600 ama sahibi root kalıyordu; etapadmin kendi
                # kaydettiği dosyayı açamıyordu.
                write_user_file(Path(path), text)
                # Dosya root tarafından yazıldı; etapadmin ev dizinindeyse
                # sahipliği etapadmin'e çevir ki kullanıcı kolayca açabilsin.
                try:
                    etap_pw = _pwd.getpwnam("etapadmin")
                    if path.startswith(etap_pw.pw_dir):
                        os.chown(path, etap_pw.pw_uid, etap_pw.pw_gid)
                except (KeyError, OSError):
                    pass
                self._toast(t("ui.pages.saved_to", path=path))
            except OSError as exc:
                self._toast(t("ui.pages.save_failed", error=exc), error=True)
        dlg.destroy()

    def _confirm_action(self, spec: dict) -> bool:
        """Geri alınamaz bir işlem öncesi evet/hayır onayı ister.

        ``spec`` şemadan gelir: ``title`` ve ``message``. Varsayılan
        yanıt "Hayır" — yanlışlıkla Enter'a basmak işlemi başlatmaz.
        """
        dlg = Gtk.MessageDialog(
            transient_for=self.get_toplevel(),
            modal=True,
            destroy_with_parent=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text=spec.get("title", t("ui.pages.confirm_default_title")),
        )
        message = spec.get("message")
        if message:
            dlg.format_secondary_text(message)
        dlg.set_default_response(Gtk.ResponseType.NO)
        response = dlg.run()
        dlg.destroy()
        return response == Gtk.ResponseType.YES

    def _show_warning_dialog(self, message: str) -> None:
        """Kullanıcının kaçırmaması gereken uyarıyı modal olarak gösterir."""
        dlg = Gtk.MessageDialog(
            transient_for=self.get_toplevel(),
            modal=True,
            destroy_with_parent=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK,
            text=t("ui.pages.warning_dialog_title"),
        )
        dlg.format_secondary_text(message)
        dlg.run()
        dlg.destroy()

    def _toast(self, message: str, error: bool = False) -> None:
        """Küçük bir bilgi diyaloğu göster."""
        dlg = Gtk.MessageDialog(
            transient_for=self.get_toplevel(),
            modal=True,
            destroy_with_parent=True,
            message_type=Gtk.MessageType.ERROR if error else Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=message,
        )
        dlg.run()
        dlg.destroy()

    def _undo_clicked(self) -> None:
        entry = self.journal.last_applied(self.module.id)
        if not entry:
            self._show_result(ApplyResult(False, t("ui.pages.undo_nothing")))
            return

        # Modül geri alma öncesi bir onay istiyor mu?
        undo_params: dict | None = None
        prompt = self.module.pre_undo_prompt(entry.data)
        if prompt:
            dlg = Gtk.MessageDialog(
                transient_for=self.get_toplevel(),
                modal=True,
                destroy_with_parent=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.YES_NO,
                text=prompt.get("title", t("ui.pages.undo_prompt_default_title")),
            )
            dlg.format_secondary_text(prompt.get("message", ""))
            response = dlg.run()
            dlg.destroy()
            if response == Gtk.ResponseType.YES:
                undo_params = prompt.get("yes_params", {})
            elif response == Gtk.ResponseType.NO:
                undo_params = prompt.get("no_params", {})
            else:
                return  # İptal

        try:
            u_result = self.module.undo_with_logging(entry.data, undo_params)
        except Exception as exc:
            u_result = ApplyResult(False, t("ui.pages.undo_error", error=exc))
        if u_result.success:
            self.journal.mark_undone(self.module.id)
            console.undone(self.module.title)
        else:
            console.fail(u_result.summary)
        self._show_result(u_result)
        # Geri alma sistemi eski hâline döndürdü: önizleme, şartlı alanlar
        # ve "özellik açık mı" kutuları bunu yansıtmalıydı — yansıtmıyordu.
        # Sayı/metin alanları kullanıcının tercihi olduğu için korunur.
        self._refresh_after_action()
        self._refresh_state_checkboxes()


# =========================================================================
# Özet sayfası
# =========================================================================


class SummaryPage(Gtk.Box):
    def __init__(
        self,
        journal: Journal,
        modules: list[Module],
        *,
        on_export_preset=None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=_ROW_SPACING)
        self.journal = journal
        self.modules = {m.id: m for m in modules}
        self.on_export_preset = on_export_preset
        self.set_margin_top(_PAGE_MARGIN)
        self.set_margin_bottom(_PAGE_MARGIN)
        self.set_margin_start(_PAGE_MARGIN + 4)
        self.set_margin_end(_PAGE_MARGIN + 4)

        self.get_style_context().add_class("tiha-summary")

        heading = _wrapping_label(t("ui.summary.heading"), klass="tiha-heading")
        self.pack_start(heading, False, False, 0)

        # "Bu imajda neler yaptınız" raporu: giriş, adımlar arası uyarılar,
        # kapanış ve dışa aktarma düğmeleri. Ayrıntılar aşağıdaki üç
        # katlanır grupta. refresh() her açılışta yeniden kurar.
        self.report_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.pack_start(self.report_box, False, False, 0)
        self._report: Report | None = None

        # Üç katlanır grup: Yapılanlar, Özet (adım kartları + geri alma),
        # Kontrol et (klon tahtada denenecekler). Varsayılan kapalı;
        # expander'lar bir kez kurulur, refresh() yalnız içlerini
        # yenilediği için açık/kapalı durumları korunur.
        self.done_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.done_exp = self._group_expander(self.done_box)
        self.pack_start(self.done_exp, False, False, 0)

        summary_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.summary_exp = self._group_expander(summary_box)
        self.pack_start(self.summary_exp, False, False, 0)

        self.tests_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.tests_exp = self._group_expander(self.tests_box)
        self.pack_start(self.tests_exp, False, False, 0)

        info = _wrapping_label(
            t("ui.summary.undo_info"),
            klass="tiha-rationale",
        )
        summary_box.pack_start(info, False, False, 0)

        self.entries_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        summary_box.pack_start(self.entries_box, False, False, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        refresh = Gtk.Button(label=t("ui.summary.refresh"))
        refresh.connect("clicked", lambda *_: self.refresh())
        btn_row.pack_start(refresh, False, False, 0)

        if self.on_export_preset is not None:
            export_btn = Gtk.Button(label=t("ui.summary.export_preset"))
            export_btn.set_tooltip_text(t("ui.summary.export_preset_tip"))
            export_btn.connect("clicked", lambda *_: self.on_export_preset())
            btn_row.pack_start(export_btn, False, False, 0)

        summary_box.pack_start(btn_row, False, False, 0)

        self.refresh()

    @staticmethod
    def _group_expander(child: Gtk.Widget) -> Gtk.Expander:
        """Özet sayfasındaki katlanır grup: kalın başlık, kapalı başlar."""
        exp = Gtk.Expander()
        exp.set_expanded(False)
        exp.set_margin_top(6)
        title = Gtk.Label(xalign=0)
        title.get_style_context().add_class("tiha-summary-group")
        exp.set_label_widget(title)
        child.set_margin_top(6)
        child.set_margin_start(18)
        exp.add(child)
        return exp

    @staticmethod
    def _set_group_title(exp: Gtk.Expander, title: str, detail: str) -> None:
        exp.get_label_widget().set_text(
            t("ui.summary.group_title", title=title, detail=detail) if detail else title
        )

    def collect_view_state(self):
        """Raporu arka planda kurar (GTK'ya dokunmaz)."""
        try:
            return build_report(list(self.modules.values()), journal=self.journal)
        except Exception as exc:
            return exc

    def apply_view_state(self, state) -> None:
        self.refresh(report=state)

    def refresh(self, report=None) -> None:
        """Tüm geçmiş kayıtlar arasından her modül için en son durumu
        gösterir. Hangi oturumda uygulandığına bakılmaksızın, son durumu
        ``applied`` olan adımlar Geri al düğmesiyle birlikte listelenir;
        ``undone`` net-sıfır etki olduğu için gizlenir; ``failed`` ayırt
        edici renkle (geri al düğmesiz) gösterilir."""
        self._render_report(report)
        for child in self.entries_box.get_children():
            self.entries_box.remove(child)

        latest = self.journal.latest_per_module()
        # Modülün sihirbaz içindeki sırasıyla dizelim
        order = {m.id: idx for idx, m in enumerate(self.modules.values())}
        entries = sorted(
            (e for e in latest.values() if e.status != "undone"),
            key=lambda e: order.get(e.module_id, 99),
        )

        undoable = sum(
            1 for e in entries
            if e.status == "applied"
            and (m := self.modules.get(e.module_id)) is not None
            and m.undo_supported
        )
        self._set_group_title(
            self.summary_exp, t("ui.summary.group_undo"),
            t("ui.summary.group_undo_detail", count=len(entries), undoable=undoable)
            if entries else "",
        )

        if not entries:
            empty = _wrapping_label(
                t("ui.summary.undo_empty"),
                klass="tiha-rationale",
            )
            self.entries_box.pack_start(empty, False, False, 0)
            self.entries_box.show_all()
            return

        status_map = {
            "applied": ("✓", "tiha-summary-ok"),
            "failed":  ("✗", "tiha-summary-fail"),
            "skipped": ("–", "tiha-summary-undone"),
        }

        for entry in entries:
            sym, css = status_map.get(entry.status, ("?", ""))

            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            card.get_style_context().add_class("tiha-summary-card")
            if css:
                card.get_style_context().add_class(css)
            card.set_margin_bottom(4)

            head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

            sym_lbl = Gtk.Label(label=sym, xalign=0)
            sym_lbl.get_style_context().add_class("tiha-summary-sym")
            sym_lbl.set_size_request(24, -1)
            head.pack_start(sym_lbl, False, False, 0)

            title_lbl = _wrapping_label(entry.title)
            title_lbl.get_style_context().add_class("tiha-summary-title")
            head.pack_start(title_lbl, True, True, 0)

            module = self.modules.get(entry.module_id)
            if entry.status == "applied" and module and module.undo_supported:
                btn = Gtk.Button(label=t("ui.summary.undo"))
                btn.get_style_context().add_class("destructive-action")
                btn.set_valign(Gtk.Align.CENTER)
                btn.connect("clicked", self._make_undo_handler(module, entry))
                head.pack_end(btn, False, False, 0)

            card.pack_start(head, False, False, 0)

            if entry.summary:
                desc = _wrapping_label(entry.summary, klass="tiha-summary-desc")
                desc.set_margin_start(34)
                desc.set_margin_end(6)
                card.pack_start(desc, False, False, 0)

            self.entries_box.pack_start(card, False, False, 0)

        self.entries_box.show_all()

    # --- "Bu imajda neler yaptınız" raporu ---------------------------------

    def _render_report(self, report=None) -> None:
        for box in (self.report_box, self.done_box, self.tests_box):
            for child in box.get_children():
                box.remove(child)
        try:
            # Rapor arka planda kurulduysa hazır gelir; hata da nesne olarak.
            if isinstance(report, Exception):
                raise report
            if report is None:
                report = build_report(list(self.modules.values()), journal=self.journal)
        except Exception as exc:  # rapor hatası Özet sayfasını düşürmesin
            log.warning("Özet raporu kurulamadı: %s", exc)
            self.report_box.pack_start(
                _wrapping_label(t("ui.summary.report_failed", error=exc), klass="tiha-rationale"),
                False, False, 0,
            )
            self.report_box.show_all()
            self.done_exp.hide()
            self.tests_exp.hide()
            return
        self._report = report

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.get_style_context().add_class("tiha-report")
        card.pack_start(
            _wrapping_label(t("ui.summary.report_title"), klass="tiha-section-title"),
            False, False, 0,
        )
        card.pack_start(_wrapping_label(report.intro, selectable=True), False, False, 0)

        # Gruplar içerik varsa görünür; set_no_show_all ile üst sayfanın
        # show_all çağrısı gizli grubu yeniden açmasın.
        for exp in (self.done_exp, self.tests_exp):
            exp.set_no_show_all(report.is_empty)
            exp.set_visible(not report.is_empty)

        if not report.is_empty:
            self.done_box.pack_start(self._report_steps(report.steps), False, False, 0)
            self._set_group_title(
                self.done_exp, t("ui.summary.group_done"),
                t("ui.summary.group_done_detail", count=len(report.steps)),
            )
            tests_box = self._report_tests(report)
            self.tests_box.pack_start(tests_box, False, False, 0)
            n_tests = sum(len(s.tests) for s in report.steps) + len(report.general_tests)
            self._set_group_title(
                self.tests_exp, t("ui.summary.group_tests"),
                t("ui.summary.group_tests_detail", count=n_tests),
            )
            _no_focus_labels(self.done_box)
            _no_focus_labels(self.tests_box)
            self.done_box.show_all()
            self.tests_box.show_all()

            if report.warnings:
                card.pack_start(self._report_warnings(report.warnings), False, False, 0)

            closing = _wrapping_label(report.closing, selectable=True)
            closing.get_style_context().add_class("tiha-report-closing")
            card.pack_start(closing, False, False, 0)

            buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            copy_btn = Gtk.Button(label=t("ui.summary.copy_report"))
            copy_btn.set_tooltip_text(t("ui.summary.copy_report_tip"))
            copy_btn.connect("clicked", lambda *_: self._copy_report())
            buttons.pack_start(copy_btn, False, False, 0)
            save_btn = Gtk.Button(label=t("ui.summary.save_report"))
            save_btn.connect("clicked", lambda *_: self._save_report())
            buttons.pack_start(save_btn, False, False, 0)
            card.pack_start(buttons, False, False, 0)

        self.report_box.pack_start(card, False, False, 0)
        # Seçilebilir etiketler odak alınca bütün metni seçiyor (sayfa açılışta
        # mavi vurgulu görünüyordu). Fareyle seçim yine çalışır.
        _no_focus_labels(card)
        self.report_box.show_all()

    def _bullets(self, lines: list[str], mark: str, klass: str | None = None) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for line in lines:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            sym = Gtk.Label(label=mark, xalign=0, yalign=0)
            sym.set_size_request(16, -1)
            row.pack_start(sym, False, False, 0)
            lbl = _wrapping_label(line, selectable=True)
            if klass:
                lbl.get_style_context().add_class(klass)
            row.pack_start(lbl, True, True, 0)
            box.pack_start(row, False, False, 0)
        return box

    def _report_steps(self, steps: list[StepReport]) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for step in steps:
            title = step.title
            if step.failed:
                title = t("ui.summary.step_failed", title=title)
            elif step.skipped:
                title = t("ui.summary.step_skipped", title=title)
            elif (step.experimental
                  and t("ui.summary.experimental_marker") not in title.lower()):
                title = t("ui.summary.step_experimental", title=title)
            head = _wrapping_label(title, klass="tiha-summary-title")
            if step.failed:
                head.get_style_context().add_class("tiha-report-failed")
            box.pack_start(head, False, False, 0)
            items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            items.set_margin_start(12)
            items.pack_start(self._bullets(step.done, "•"), False, False, 0)
            if step.notes:
                items.pack_start(
                    self._bullets(step.notes, "⚠", "tiha-report-note"), False, False, 0,
                )
            box.pack_start(items, False, False, 0)
        return box

    def _report_warnings(self, warnings: list[str]) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.get_style_context().add_class("tiha-report-warnings")
        box.pack_start(
            _wrapping_label(t("ui.summary.warnings_title"), klass="tiha-report-subtitle"),
            False, False, 0,
        )
        box.pack_start(self._bullets(warnings, "⚠"), False, False, 0)
        return box

    def _report_tests(self, report: Report) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.pack_start(
            _wrapping_label(
                t("ui.summary.tests_intro"),
                klass="tiha-rationale",
            ),
            False, False, 0,
        )
        groups = [(s.title, s.tests) for s in report.steps if s.tests]
        if report.general_tests:
            groups.append((t("ui.summary.tests_general"), report.general_tests))
        for title, tests in groups:
            box.pack_start(_wrapping_label(title, klass="tiha-summary-title"), False, False, 0)
            items = self._bullets(tests, "☐")
            items.set_margin_start(12)
            box.pack_start(items, False, False, 0)
        return box

    def _copy_report(self) -> None:
        if self._report is None:
            return
        clipboard = Gtk.Clipboard.get_default(self.get_display())
        clipboard.set_text(self._report.to_text(), -1)
        clipboard.store()

    def _save_report(self) -> None:
        if self._report is None:
            return
        dlg = Gtk.FileChooserDialog(
            title=t("ui.summary.save_report_title"),
            parent=self.get_toplevel() if isinstance(self.get_toplevel(), Gtk.Window) else None,
            action=Gtk.FileChooserAction.SAVE,
        )
        dlg.add_buttons(t("ui.main.cancel"), Gtk.ResponseType.CANCEL, t("ui.main.save"), Gtk.ResponseType.ACCEPT)
        dlg.set_current_name("tiha-imaj-raporu.html")
        dlg.set_do_overwrite_confirmation(True)
        html_filter = Gtk.FileFilter()
        html_filter.set_name("HTML")
        html_filter.add_pattern("*.html")
        dlg.add_filter(html_filter)
        try:
            if dlg.run() == Gtk.ResponseType.ACCEPT:
                path = Path(dlg.get_filename())
                if path.suffix.lower() not in (".html", ".htm"):
                    path = path.with_suffix(".html")
                try:
                    write_user_file(path, self._report.to_html(_report_meta()))
                except OSError as exc:
                    log.warning("Rapor kaydedilemedi: %s", exc)
        finally:
            dlg.destroy()

    def _make_undo_handler(self, module: Module, entry: JournalEntry):
        def _handler(_btn: Gtk.Button) -> None:
            try:
                result = module.undo_with_logging(entry.data)
            except Exception as exc:
                result = ApplyResult(False, t("ui.summary.undo_error", error=exc))
            if result.success:
                self.journal.mark_undone(module.id)
            self.refresh()
        return _handler
