"""Sihirbaz sayfa sınıfları: Karşılama, Modül, Özet.

Tüm sayfalar ana pencerenin ``Gtk.ScrolledWindow``'u içinde çalışır;
dolayısıyla içerik uzadığında aksiyon çubuğuna taşmaz, kullanıcı
kaydırabilir. Uzun metinler (PIN anahtarı listesi, apt çıktısı, ayrıntı)
yine kendi ``ScrolledWindow``'larında sabit yükseklikte verilir.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from ..core import console
from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.undo import Journal, JournalEntry
from . import params as params_schema

log = get_logger(__name__)


# Yardımcı: içerik sayfasının ortak çerçeve marjları (kompakt ama nefes alan)
_PAGE_MARGIN = 18
_ROW_SPACING = 14
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


_WELCOME_INTRO = (
    "TiHA, Pardus ETAP etkileşimli tahtanızı imaj alınmaya hazırlayan bir "
    "sihirbazdır. Tek tahtada yaptığınız hazırlığı, ortak bir imajdan "
    "geçirip onlarca tahtaya tutarlı biçimde dağıtırsınız — bu yolun "
    "başındaki sıkıcı işleri TiHA sizin yerinize, doğru sırada yapar."
)

_WELCOME_FEATURES_TITLE = "Bu sihirbazda neler bulacaksınız?"

_WELCOME_FEATURES = (
    "•  Sistem güncellemesi — paketleri imaj öncesi günceller; sahaya "
    "çıkmadan en son yamayı alırsınız.",
    "•  Yerel hesap yönetimi — root, etapadmin ve ogretmen parolalarını "
    "bilinçli olarak siz belirler, dilerseniz parolalı girişi tamamen "
    "kapatırsınız.",
    "•  Toplu PIN anahtarı — öğretmenler için anahtarları imaj öncesi "
    "merkezî olarak üretip imaja gömer; her tahtaya tek tek kurulum "
    "yapmaktan kurtulursunuz.",
    "•  Uzaktan bakım — SSH, Samba ve merkezi log ile sınıflara "
    "dağıtılmış tahtalara masanızdan dokunabilirsiniz.",
    "•  Sağlam çalışma — saat senkronu, benzersiz hostname ve güç "
    "yönetimi ile her klon sahada tutarlı, bağımsız ve enerji verimli "
    "kalır.",
    "•  İmaj için sanitize — tekil kimlikleri sıfırlar, kullanılmayan "
    "dosyaları temizler, izleri siler. Son adım: imaj alınmaya hazırsınız.",
)

_WELCOME_FLOW = (
    "Sihirbaz adım adım ilerler. Her adımda ne yapılacağı ve nedeni "
    "açıklanır, onayınız alınır, sonuç gösterilir, gerektiğinde geri "
    "alınır. Hazırsanız soldaki listeden ya da aşağıdaki “İleri” "
    "düğmesiyle başlayın."
)


class WelcomePage(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=_ROW_SPACING)
        self.set_margin_top(_PAGE_MARGIN)
        self.set_margin_bottom(_PAGE_MARGIN)
        self.set_margin_start(_PAGE_MARGIN + 4)
        self.set_margin_end(_PAGE_MARGIN + 4)

        def add_paragraph(text: str) -> None:
            lbl = _wrapping_label(text)
            lbl.set_max_width_chars(110)
            self.pack_start(lbl, False, False, 0)

        heading = _wrapping_label("Hoş geldiniz", klass="tiha-heading")
        self.pack_start(heading, False, False, 0)

        add_paragraph(_WELCOME_INTRO)

        title_lbl = _wrapping_label(_WELCOME_FEATURES_TITLE)
        title_lbl.set_max_width_chars(110)
        title_lbl.set_margin_top(4)
        self.pack_start(title_lbl, False, False, 0)

        for feature in _WELCOME_FEATURES:
            lbl = _wrapping_label(feature)
            lbl.set_max_width_chars(110)
            lbl.set_margin_top(6)
            lbl.set_margin_start(8)
            self.pack_start(lbl, False, False, 0)

        flow_lbl = _wrapping_label(_WELCOME_FLOW)
        flow_lbl.set_max_width_chars(110)
        flow_lbl.set_margin_top(8)
        self.pack_start(flow_lbl, False, False, 0)


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
        self._applying: bool = False
        self._auto_applied: bool = False
        self.post_apply_callback = None  # Set by main_window if needed
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
        heading = _wrapping_label(self.module.title, klass="tiha-heading")
        self.pack_start(heading, False, False, 0)

        rationale = _wrapping_label(self.module.rationale, klass="tiha-rationale")
        self.pack_start(rationale, False, False, 0)

        preview_text = ""
        try:
            preview_text = self.module.preview() or ""
        except Exception as exc:
            log.warning("preview başarısız %s: %s", self.module.id, exc)
        if preview_text:
            # Uzun önizleme → scroll'lu metin kutusu; kısa önizleme → label.
            # Tablo görünümlü (çok satırlı, hizalanmış) önizlemelerde
            # satır kırmıyoruz; yatay kaydırma çubuğu alsın.
            is_tabular = "  ─" in preview_text or "KULLANICI" in preview_text
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

        # Canlı akış alanı (başlangıçta gizli)
        self.stream_scroll = Gtk.ScrolledWindow()
        self.stream_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.stream_scroll.set_min_content_height(220)
        self.stream_scroll.set_max_content_height(240)
        self.stream_view = Gtk.TextView()
        self.stream_view.set_editable(False)
        self.stream_view.set_cursor_visible(False)
        self.stream_view.set_monospace(True)
        self.stream_view.get_style_context().add_class("tiha-stream")
        self._stream_buffer = self.stream_view.get_buffer()
        self.stream_scroll.add(self.stream_view)
        self.stream_scroll.set_no_show_all(True)
        self.pack_start(self.stream_scroll, False, False, 0)

        # Sonuç kutusu
        self.result_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.pack_start(self.result_holder, False, False, 0)

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
                f"ℹ Bu adım daha önce ({when}) bir TiHA oturumunda uygulanmış.\n"
                f"Son durum: {entry.summary}",
                selectable=True,
            ),
            False, False, 0,
        )
        if self.module.undo_supported:
            undo_btn = Gtk.Button(label="Bu adımı geri al")
            undo_btn.get_style_context().add_class("destructive-action")
            undo_btn.connect("clicked", lambda *_: self._undo_clicked())
            banner.pack_start(undo_btn, False, False, 0)
        self.result_holder.pack_start(banner, False, False, 0)

    def _build_form(self, schema: list[dict]) -> Gtk.Grid:
        grid = Gtk.Grid(column_spacing=12, row_spacing=6)
        row_idx = 0
        for field in schema:
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
            widget.set_hexpand(True)
            grid.attach(widget, 1, row_idx, 1, 1)
            self._fields[field["key"]] = widget
            row_idx += 1
            row_widgets: list[Gtk.Widget] = [label, widget]
            if field.get("help"):
                help_lbl = _wrapping_label(field["help"], klass="tiha-rationale")
                grid.attach(help_lbl, 1, row_idx, 1, 1)
                row_idx += 1
                row_widgets.append(help_lbl)

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

        related_fields = field_relationships.get(checkbox_key, [])

        for field_key in related_fields:
            widget = self._fields.get(field_key)
            if widget:
                widget.set_sensitive(is_active)

    def _refresh_preview(self) -> None:
        """Önizleme metnini yeniden üretip aynı widget'a yazar."""
        if self._preview_widget is None:
            return
        try:
            new_text = self.module.preview() or ""
        except Exception as exc:
            log.warning("preview tazelenemedi %s: %s", self.module.id, exc)
            return
        if isinstance(self._preview_widget, Gtk.ScrolledWindow):
            tv = getattr(self._preview_widget, "_textview", None)
            if tv is not None:
                tv.get_buffer().set_text(new_text)
        elif isinstance(self._preview_widget, Gtk.Label):
            self._preview_widget.set_text(new_text)
            _apply_line_spacing(self._preview_widget)

    def _refresh_after_action(self) -> None:
        """Apply / buton işlemi sonrası önizleme + şartlı alan tazeleme."""
        self._refresh_preview()
        self._refresh_conditional_fields()

    def _make_field(self, field: dict) -> Gtk.Widget:
        kind = field.get("type", "text")
        default = field.get("default", "")

        # Remote Syslog modülü için mevcut yapılandırmayı kontrol et ve form alanlarını doldur
        if self.module.id == "m06_remote_syslog":
            try:
                # _parse_config fonksiyonunu modül içinden çağır
                from ..modules.m06_remote_syslog import _parse_config
                config = _parse_config()
                if config:
                    host, port, proto = config
                    if field["key"] == "syslog_host":
                        default = host
                    elif field["key"] == "syslog_port":
                        default = str(port)
                    elif field["key"] == "syslog_proto":
                        default = proto
            except Exception:
                # Hata varsa varsayılan değerleri kullan
                pass

        if kind == "textarea":
            tv = Gtk.TextView()
            tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            buf = tv.get_buffer()
            placeholder = field.get("placeholder")
            if placeholder:
                # Placeholder metnini yerleştir, CSS ile soluk göster.
                # Odaklanıldığında (ve metin hâlâ placeholder ise) temizle;
                # boşsa odak kaybında geri koy.
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
            return spin

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
            btn = Gtk.Button(label=field.get("label", "Button"))
            if field.get("style") == "destructive":
                btn.get_style_context().add_class("destructive-action")

            def on_button_clicked(_btn, action=field.get("action")):
                if action and hasattr(self.module, action):
                    self._run_button_action(action, button=_btn)

            btn.connect("clicked", on_button_clicked)
            return btn

        if kind == "bool":
            checkbox = Gtk.CheckButton()
            # Default değeri kontrol et (string olarak geliyor)
            is_checked = default.lower() in ("true", "1", "yes", "on")
            checkbox.set_active(is_checked)

            # Checkbox değişikliklerini dinle ve ilgili alanları aktif/pasif yap
            def on_checkbox_toggled(cb, field_key=field["key"]):
                self._update_conditional_fields(field_key, cb.get_active())

            checkbox.connect("toggled", on_checkbox_toggled)
            return checkbox

        entry = Gtk.Entry()
        entry.set_text(default)
        if kind == "password":
            entry.set_visibility(False)
            entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
            if field.get("show_toggle", True):
                # Entry sağına göz düğmesi: tıklanınca parolayı göster/gizle.
                entry.set_icon_from_icon_name(
                    Gtk.EntryIconPosition.SECONDARY,
                    "view-reveal-symbolic",
                )
                entry.set_icon_tooltip_text(
                    Gtk.EntryIconPosition.SECONDARY,
                    "Parolayı göster / gizle",
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
        return entry

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
        return widget.get_text()

    def _collect_params(self) -> tuple[dict, list[str]]:
        schema = params_schema.get(self.module.id)
        params: dict = {}
        missing: list[str] = []
        for field in schema:
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
            _wrapping_label("Çalışıyor… Lütfen bekleyin."),
            False, False, 0,
        )
        self.result_holder.pack_start(self._working_row, False, False, 0)
        self.result_holder.show_all()

        # Stream alanını gerçekten görünür kıl ve içini boşalt
        self._stream_buffer.set_text("")
        self.stream_scroll.set_no_show_all(False)
        self.stream_scroll.show_all()

        def progress_callback(text: str) -> None:
            GLib.idle_add(self._append_stream_line, text)

        def worker():
            try:
                action_func = getattr(self.module, action)
                try:
                    result = action_func(progress=progress_callback)
                except TypeError:
                    result = action_func()
                GLib.idle_add(self._on_button_action_complete, result)
            except Exception as exc:
                error_result = ApplyResult(False, f"Button action hatası: {exc}")
                GLib.idle_add(self._on_button_action_complete, error_result)

        self._applying = True
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _on_button_action_complete(self, result: ApplyResult) -> None:
        self._applying = False
        if getattr(self, "_active_button", None) is not None:
            self._active_button.set_sensitive(True)
            self._active_button = None
        self._show_result(result)
        # Buton işlemi sistem durumunu değiştirmiş olabilir — önizlemeyi
        # ve "visible_when" şartlı alanların görünürlüğünü tazele.
        self._refresh_after_action()

    def run_apply(self) -> None:
        if self._applying:
            return
        params, missing = self._collect_params()
        if missing:
            self._show_result(ApplyResult(False, "Eksik alanlar: " + ", ".join(missing)))
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
            _wrapping_label("Uygulanıyor… Bu adım tamamlanana kadar lütfen bekleyin."),
            False, False, 0,
        )
        self.result_holder.pack_start(self._working_row, False, False, 0)
        self.result_holder.show_all()

        if self.module.streams_output:
            self._stream_buffer.set_text("")
            self.stream_scroll.set_no_show_all(False)
            self.stream_scroll.show_all()

        thread = threading.Thread(
            target=self._apply_thread_body,
            args=(params,),
            daemon=True,
        )
        thread.start()

    def _apply_thread_body(self, params: dict) -> None:
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
            result = ApplyResult(False, f"Beklenmeyen hata: {exc}")

        GLib.idle_add(self._apply_thread_done, result)

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
        # "Çalışıyor" göstergesini kaldır (result_holder temizlenecek)
        entry = JournalEntry.new(self.module.id, self.module.title)
        entry.summary = result.summary
        entry.status = "applied" if result.success else "failed"
        # Modülün bıraktığı undo verisini günceye taşı
        entry.data = dict(result.data) if isinstance(result.data, dict) else {}
        self.journal.record(entry)
        # Terminale profesyonel sonuç satırı
        if result.success:
            console.ok(result.summary)
        else:
            console.fail(result.summary)
        self._show_result(result)
        # Apply de sistem durumunu değiştirmiş olabilir — aynı tazelemeyi
        # buradan da çalıştır.
        self._refresh_after_action()
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
            "tiha-result-ok" if result.success else "tiha-result-fail"
        )
        box.pack_start(_wrapping_label(result.summary, selectable=True), False, False, 0)

        if result.details:
            # Uzun ayrıntı → scroll'lu kutu
            if result.details.count("\n") > 6 or len(result.details) > 500:
                box.pack_start(
                    _scrolled_textview(result.details, height=160),
                    False, False, 0,
                )
            else:
                box.pack_start(_wrapping_label(result.details, selectable=True), False, False, 0)

        if result.copyable:
            box.pack_start(
                _scrolled_textview(result.copyable, monospace=True, height=160),
                False, False, 0,
            )
            # Buton satırı: panoya kopyala + dosyaya kaydet
            btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            copy_btn = Gtk.Button(label="Panoya kopyala")
            copy_btn.connect("clicked", lambda *_: self._copy_to_clipboard(result.copyable or ""))
            btn_row.pack_start(copy_btn, False, False, 0)

            save_btn = Gtk.Button(label="Dosyaya kaydet…")
            default_name = self.module.save_filename or f"tiha-{self.module.id}.txt"
            save_btn.connect(
                "clicked",
                lambda *_: self._save_to_file(result.copyable or "", default_name),
            )
            btn_row.pack_start(save_btn, False, False, 0)
            box.pack_start(btn_row, False, False, 0)

        if result.success and self.module.undo_supported:
            undo_btn = Gtk.Button(label="Bu adımı geri al")
            undo_btn.get_style_context().add_class("destructive-action")
            undo_btn.connect("clicked", lambda *_: self._undo_clicked())
            box.pack_start(undo_btn, False, False, 0)

        self.result_holder.pack_start(box, False, False, 0)
        self.result_holder.show_all()

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
            title="Dosyaya kaydet",
            transient_for=self.get_toplevel(),
            action=Gtk.FileChooserAction.SAVE,
        )
        dlg.add_buttons(
            "İptal", Gtk.ResponseType.CANCEL,
            "Kaydet", Gtk.ResponseType.ACCEPT,
        )
        dlg.set_current_name(default_name)
        dlg.set_do_overwrite_confirmation(True)

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
            try:
                Path(path).write_text(text, encoding="utf-8")
                # Dosya root tarafından yazıldı; etapadmin ev dizinindeyse
                # sahipliği etapadmin'e çevir ki kullanıcı kolayca açabilsin.
                try:
                    etap_pw = _pwd.getpwnam("etapadmin")
                    if path.startswith(etap_pw.pw_dir):
                        os.chown(path, etap_pw.pw_uid, etap_pw.pw_gid)
                except (KeyError, OSError):
                    pass
                self._toast(f"Dosyaya kaydedildi: {path}")
            except OSError as exc:
                self._toast(f"Dosya yazılamadı: {exc}", error=True)
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
            self._show_result(ApplyResult(False, "Geri alınacak kayıt bulunamadı."))
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
                text=prompt.get("title", "Onay"),
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
            u_result = ApplyResult(False, f"Geri alma sırasında hata: {exc}")
        if u_result.success:
            self.journal.mark_undone(self.module.id)
            console.undone(self.module.title)
        else:
            console.fail(u_result.summary)
        self._show_result(u_result)


# =========================================================================
# Özet sayfası
# =========================================================================


class SummaryPage(Gtk.Box):
    def __init__(self, journal: Journal, modules: list[Module]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=_ROW_SPACING)
        self.journal = journal
        self.modules = {m.id: m for m in modules}
        self.set_margin_top(_PAGE_MARGIN)
        self.set_margin_bottom(_PAGE_MARGIN)
        self.set_margin_start(_PAGE_MARGIN + 4)
        self.set_margin_end(_PAGE_MARGIN + 4)

        heading = _wrapping_label("Özet", klass="tiha-heading")
        self.pack_start(heading, False, False, 0)

        info = _wrapping_label(
            "Bu oturumda uygulanan adımlar aşağıda listelenmiştir. "
            "Herhangi birini geri almak isterseniz sağ taraftaki düğmeyi "
            "kullanın. Alttaki 'Bitir' düğmesi uygulamayı kapatır.",
            klass="tiha-rationale",
        )
        self.pack_start(info, False, False, 0)

        self.entries_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.pack_start(self.entries_box, False, False, 0)

        refresh = Gtk.Button(label="Listeyi yenile")
        refresh.connect("clicked", lambda *_: self.refresh())
        self.pack_start(refresh, False, False, 0)

        self.refresh()

    def refresh(self) -> None:
        """Yalnızca mevcut oturumun kayıtlarını, her modül için tek kart
        olarak listeler. ``undone`` kayıtlar net-sıfır etki oldukları için
        gösterilmez; ``applied`` için Geri al, ``failed`` için ayırt edici
        renk gösterilir."""
        for child in self.entries_box.get_children():
            self.entries_box.remove(child)

        latest = self.journal.latest_per_module_in_session()
        # Modülün sihirbaz içindeki sırasıyla dizelim
        order = {m.id: idx for idx, m in enumerate(self.modules.values())}
        entries = sorted(
            (e for e in latest.values() if e.status != "undone"),
            key=lambda e: order.get(e.module_id, 99),
        )

        if not entries:
            empty = _wrapping_label(
                "Bu oturumda henüz uygulanmış (ya da geri alınmamış) bir adım yok.",
                klass="tiha-rationale",
            )
            self.entries_box.pack_start(empty, False, False, 0)
            self.entries_box.show_all()
            return

        status_map = {
            "applied": ("✓", "tiha-summary-ok"),
            "failed":  ("✗", "tiha-summary-fail"),
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
                btn = Gtk.Button(label="Geri al")
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

    def _make_undo_handler(self, module: Module, entry: JournalEntry):
        def _handler(_btn: Gtk.Button) -> None:
            try:
                result = module.undo_with_logging(entry.data)
            except Exception as exc:
                result = ApplyResult(False, f"Hata: {exc}")
            if result.success:
                self.journal.mark_undone(module.id)
            self.refresh()
        return _handler
