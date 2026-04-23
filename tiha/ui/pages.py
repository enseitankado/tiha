"""Sihirbaz sayfa sınıfları: Karşılama, Modül, Özet.

Tüm sayfalar ana pencerenin ``Gtk.ScrolledWindow``'u içinde çalışır;
dolayısıyla içerik uzadığında aksiyon çubuğuna taşmaz, kullanıcı
kaydırabilir. Uzun metinler (OTP listesi, apt çıktısı, ayrıntılı sonuç)
yine kendi ``ScrolledWindow``'larında sabit yükseklikte verilir.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from ..core.board import BoardInfo
from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.undo import Journal, JournalEntry
from . import params as params_schema

log = get_logger(__name__)


# Yardımcı: içerik sayfasının ortak çerçeve marjları (kompakt)
_PAGE_MARGIN = 16
_ROW_SPACING = 8
_LONG_TEXT_HEIGHT = 180  # uzun metin kutularının sabit yüksekliği


def _compact_page() -> Gtk.Box:
    """Her sayfanın dış kutusu — sabit, nispeten dar marjlı."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=_ROW_SPACING)
    box.set_margin_top(_PAGE_MARGIN)
    box.set_margin_bottom(_PAGE_MARGIN)
    box.set_margin_start(_PAGE_MARGIN + 4)
    box.set_margin_end(_PAGE_MARGIN + 4)
    return box


def _wrapping_label(text: str, *, klass: str | None = None, selectable: bool = False) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=0)
    lbl.set_line_wrap(True)
    lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    lbl.set_selectable(selectable)
    if klass:
        lbl.get_style_context().add_class(klass)
    return lbl


def _scrolled_textview(text: str, *, monospace: bool = False,
                       editable: bool = False, height: int = _LONG_TEXT_HEIGHT,
                       css_class: str | None = None) -> Gtk.ScrolledWindow:
    """Kaydırma çubuklu, salt-okunur metin kutusu."""
    tv = Gtk.TextView()
    tv.set_editable(editable)
    tv.set_cursor_visible(editable)
    if monospace:
        tv.set_monospace(True)
    if css_class:
        tv.get_style_context().add_class(css_class)
    tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
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


_WELCOME_BODY = (
    "Bu sihirbaz, sınıfta kullanacağınız Pardus ETAP etkileşimli tahtayı "
    "imaj (disk klonu) alınmaya hazır hâle getirir. Aynı imajdan onlarca "
    "tahtaya kurulum yapılacağı senaryo için tasarlanmıştır.\n\n"
    "Neden ihtiyaç var?\n"
    "  • 65\" dokunmatik ekranda kullanıcı parolası yazılırken, sınıftaki "
    "öğrenciler ekrana baktığından parola ifşa olabiliyor. Bu tahtada "
    "öğretmenin öğrenci olmayan bir ortamda parola oluşturması "
    "senaryosunu kabul etmiyoruz.\n"
    "  • Aynı imajdan çıkan tahtaların merkezi kayıt, hostname, SSH "
    "anahtarı ve NetworkManager profili gibi tekil bilgileri çakışırsa "
    "ciddi sorunlar oluşuyor (ör. eta-register kayıt çakışması).\n\n"
    "TiHA ne yapacak?\n"
    "  1. Donanımın imajlamaya uygunluğunu denetler.\n"
    "  2. Parolaları güvenli rastgele değerle kilitler; root/etapadmin "
    "için sizin tanımlayacağınız güçlü parolaları uygular.\n"
    "  3. Her açılışta genel kullanıcı parolalarını temizleyen bir sistem "
    "servisi kurar (ifşayı etkisizleştirir).\n"
    "  4. Öğretmenler için OTP (6 haneli kod) anahtarları üretir.\n"
    "  5. SSH, Samba, merkezi log iletimi, NTP ve benzersiz hostname "
    "yapılandırır.\n"
    "  6. Sistemi günceller.\n"
    "  7. Son adımda imaj için hijyen (machine-id, SSH host anahtarları, "
    "NetworkManager profilleri, loglar ve önbellekler) uygular.\n\n"
    "Her adımda ne yaptığımızı ve neden yaptığımızı göreceksiniz; "
    "onayınızı aldıktan sonra uygularız ve sonucu ekranda paylaşırız. "
    "Gerektiğinde adımları geri de alabilirsiniz."
)


class WelcomePage(Gtk.Box):
    def __init__(self, board_info: BoardInfo) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=_ROW_SPACING)
        self.set_margin_top(_PAGE_MARGIN)
        self.set_margin_bottom(_PAGE_MARGIN)
        self.set_margin_start(_PAGE_MARGIN + 4)
        self.set_margin_end(_PAGE_MARGIN + 4)

        heading = _wrapping_label("Hoş geldiniz", klass="tiha-heading")
        self.pack_start(heading, False, False, 0)

        # Açıklama metni (kompakt)
        desc = _wrapping_label(_WELCOME_BODY)
        desc.set_max_width_chars(100)
        self.pack_start(desc, False, False, 0)

        # Ayırıcı
        self.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 6)

        # Tahta bilgisi kartı
        card_title = _wrapping_label("Tespit edilen tahta", klass="tiha-heading")
        self.pack_start(card_title, False, False, 0)

        card = Gtk.Grid(column_spacing=18, row_spacing=4)
        card.get_style_context().add_class("tiha-board-card")
        for i, (key, value) in enumerate(board_info.as_rows()):
            k = _wrapping_label(key, klass="tiha-board-key")
            v = _wrapping_label(value, klass="tiha-board-value", selectable=True)
            card.attach(k, 0, i, 1, 1)
            card.attach(v, 1, i, 1, 1)
        self.pack_start(card, False, False, 0)

        if board_info.is_vm:
            warn = _wrapping_label(
                "⚠ Sanal makine tespit edildi. TiHA burada çalışır; fakat "
                "eta-register sanal makinede çalışmayı reddeder. İmaj sahaya "
                "inmeden önce bir fiziksel tahtada mutlaka test edin.",
                klass="tiha-rationale",
            )
            self.pack_start(warn, False, False, 0)


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
        self._build()

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
            # Uzun önizleme → scroll'lu metin kutusu; kısa önizleme → label
            if preview_text.count("\n") > 6 or len(preview_text) > 500:
                self.pack_start(_scrolled_textview(preview_text, monospace=True,
                                                   height=120, css_class="tiha-preview"),
                                False, False, 0)
            else:
                p = _wrapping_label(preview_text, klass="tiha-preview", selectable=True)
                self.pack_start(p, False, False, 0)

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
        self.result_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.pack_start(self.result_holder, False, False, 0)

    def _build_form(self, schema: list[dict]) -> Gtk.Grid:
        grid = Gtk.Grid(column_spacing=12, row_spacing=6)
        row_idx = 0
        for field in schema:
            label = _wrapping_label(field["label"])
            grid.attach(label, 0, row_idx, 1, 1)
            widget = self._make_field(field)
            widget.set_hexpand(True)
            grid.attach(widget, 1, row_idx, 1, 1)
            self._fields[field["key"]] = widget
            row_idx += 1
            if field.get("help"):
                help_lbl = _wrapping_label(field["help"], klass="tiha-rationale")
                grid.attach(help_lbl, 1, row_idx, 1, 1)
                row_idx += 1
        return grid

    def _make_field(self, field: dict) -> Gtk.Widget:
        kind = field.get("type", "text")
        default = field.get("default", "")

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

        entry = Gtk.Entry()
        entry.set_text(default)
        if kind == "password":
            entry.set_visibility(False)
            entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
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
        return widget.get_text()

    def _collect_params(self) -> tuple[dict, list[str]]:
        schema = params_schema.get(self.module.id)
        params: dict = {}
        missing: list[str] = []
        for field in schema:
            key = field["key"]
            value = self._field_value(key, field).strip()
            if field.get("required") and not value:
                missing.append(field["label"])
            params[key] = value
        return params, missing

    # ------------------------------------------------------------------
    # Apply akışı — thread'li + canlı çıktı
    # ------------------------------------------------------------------

    def run_apply(self) -> None:
        if self._applying:
            return
        params, missing = self._collect_params()
        if missing:
            self._show_result(ApplyResult(False, "Eksik alanlar: " + ", ".join(missing)))
            return

        self._applying = True
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
                result = self.module.apply(params, progress=progress_cb)
            else:
                result = self.module.apply(params)
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
        self._show_result(result)
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
            copy_btn = Gtk.Button(label="Panoya kopyala")
            copy_btn.connect("clicked", lambda *_: self._copy_to_clipboard(result.copyable or ""))
            box.pack_start(copy_btn, False, False, 0)

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

    def _undo_clicked(self) -> None:
        entry = self.journal.last_applied(self.module.id)
        if not entry:
            self._show_result(ApplyResult(False, "Geri alınacak kayıt bulunamadı."))
            return
        try:
            u_result = self.module.undo(entry.data)
        except Exception as exc:
            u_result = ApplyResult(False, f"Geri alma sırasında hata: {exc}")
        if u_result.success:
            self.journal.mark_undone(self.module.id)
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
        for child in self.entries_box.get_children():
            self.entries_box.remove(child)

        entries = self.journal.all()
        if not entries:
            empty = _wrapping_label("Henüz uygulanmış bir adım yok.", klass="tiha-rationale")
            self.entries_box.pack_start(empty, False, False, 0)
            self.entries_box.show_all()
            return

        for entry in entries:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            status_sym = {"applied": "✓", "failed": "✗", "undone": "↶"}.get(entry.status, "?")
            lbl = _wrapping_label(f"{status_sym}  {entry.title}  —  {entry.summary}")
            row.pack_start(lbl, True, True, 0)

            module = self.modules.get(entry.module_id)
            if entry.status == "applied" and module and module.undo_supported:
                btn = Gtk.Button(label="Geri al")
                btn.get_style_context().add_class("destructive-action")
                btn.connect("clicked", self._make_undo_handler(module, entry))
                row.pack_start(btn, False, False, 0)

            self.entries_box.pack_start(row, False, False, 0)
        self.entries_box.show_all()

    def _make_undo_handler(self, module: Module, entry: JournalEntry):
        def _handler(_btn: Gtk.Button) -> None:
            try:
                result = module.undo(entry.data)
            except Exception as exc:
                result = ApplyResult(False, f"Hata: {exc}")
            if result.success:
                self.journal.mark_undone(module.id)
            self.refresh()
        return _handler
