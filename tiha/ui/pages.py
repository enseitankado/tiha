"""Sihirbaz sayfa sınıfları: Karşılama, Modül, Özet.

Uzun süren modüller (``streams_output = True``) için ``apply`` ayrı bir
thread'de çalıştırılır ve ``GLib.idle_add`` ile ana thread'e satır satır
ilerleme gönderilir. Böylece GUI bloklanmaz, kullanıcı canlı çıktıyı görür.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..core.board import BoardInfo
from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.undo import Journal, JournalEntry
from . import params as params_schema

log = get_logger(__name__)


# =========================================================================
# Karşılama sayfası
# =========================================================================


class WelcomePage(Gtk.Box):
    def __init__(self, board_info: BoardInfo) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.get_style_context().add_class("tiha-content")
        self.set_margin_top(24)
        self.set_margin_bottom(24)
        self.set_margin_start(40)
        self.set_margin_end(40)

        heading = Gtk.Label(label="Hoş geldiniz", xalign=0)
        heading.get_style_context().add_class("tiha-heading")
        self.pack_start(heading, False, False, 0)

        subtitle = Gtk.Label(
            label=(
                "Aşağıda tespit edilen tahta özeti yer almaktadır. Doğruluğundan "
                "emin olduktan sonra 'İleri' ile adımlara geçin."
            ),
            xalign=0,
        )
        subtitle.get_style_context().add_class("tiha-rationale")
        subtitle.set_line_wrap(True)
        self.pack_start(subtitle, False, False, 0)

        card = Gtk.Grid(column_spacing=24, row_spacing=12)
        card.get_style_context().add_class("tiha-board-card")
        for i, (key, value) in enumerate(board_info.as_rows()):
            k = Gtk.Label(label=key, xalign=0)
            k.get_style_context().add_class("tiha-board-key")
            v = Gtk.Label(label=value, xalign=0)
            v.get_style_context().add_class("tiha-board-value")
            v.set_selectable(True)
            card.attach(k, 0, i, 1, 1)
            card.attach(v, 1, i, 1, 1)

        self.pack_start(card, False, False, 0)

        if board_info.is_vm:
            warning = Gtk.Label(
                label=(
                    "⚠ Sanal makine tespit edildi. TiHA burada çalışır; fakat "
                    "eta-register sanal makinede çalışmayı reddeder. İmaj "
                    "sahaya inmeden önce fiziksel bir tahtada doğrulama yapın."
                ),
                xalign=0,
            )
            warning.set_line_wrap(True)
            warning.get_style_context().add_class("tiha-rationale")
            self.pack_start(warning, False, False, 0)


# =========================================================================
# Modül sayfası
# =========================================================================


class ModulePage(Gtk.Box):
    """Bir modülün ekran gösterimi — açıklama, form, (gerekirse canlı) sonuç."""

    def __init__(self, module: Module, journal: Journal) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.module = module
        self.journal = journal
        self.get_style_context().add_class("tiha-content")
        self.set_margin_top(24)
        self.set_margin_bottom(24)
        self.set_margin_start(40)
        self.set_margin_end(40)
        self._fields: dict[str, Gtk.Widget] = {}
        self._stream_buffer: Gtk.TextBuffer | None = None
        self._applying: bool = False
        self._build()

    # ------------------------------------------------------------------
    # UI kurulumu
    # ------------------------------------------------------------------

    def _build(self) -> None:
        heading = Gtk.Label(label=self.module.title, xalign=0)
        heading.get_style_context().add_class("tiha-heading")
        self.pack_start(heading, False, False, 0)

        rationale = Gtk.Label(label=self.module.rationale, xalign=0)
        rationale.get_style_context().add_class("tiha-rationale")
        rationale.set_line_wrap(True)
        self.pack_start(rationale, False, False, 0)

        preview_text = ""
        try:
            preview_text = self.module.preview() or ""
        except Exception as exc:
            log.warning("preview başarısız %s: %s", self.module.id, exc)
        if preview_text:
            preview = Gtk.Label(label=preview_text, xalign=0)
            preview.get_style_context().add_class("tiha-preview")
            preview.set_line_wrap(True)
            self.pack_start(preview, False, False, 0)

        schema = params_schema.get(self.module.id)
        if schema:
            form = self._build_form(schema)
            self.pack_start(form, False, False, 0)

        # Canlı akış alanı (başlangıçta gizli)
        self.stream_scroll = Gtk.ScrolledWindow()
        self.stream_scroll.set_size_request(-1, 240)
        self.stream_view = Gtk.TextView()
        self.stream_view.set_editable(False)
        self.stream_view.set_cursor_visible(False)
        self.stream_view.set_monospace(True)
        self._stream_buffer = self.stream_view.get_buffer()
        self.stream_scroll.add(self.stream_view)
        self.stream_scroll.set_no_show_all(True)   # show_all gösterdiğinde otomatik açılmasın
        self.pack_start(self.stream_scroll, True, True, 0)

        # Sonuç kutusu
        self.result_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.pack_start(self.result_holder, False, False, 0)

    def _build_form(self, schema: list[dict]) -> Gtk.Grid:
        grid = Gtk.Grid(column_spacing=16, row_spacing=10)
        row_idx = 0
        for field in schema:
            label = Gtk.Label(label=field["label"], xalign=0)
            grid.attach(label, 0, row_idx, 1, 1)
            widget = self._make_field(field)
            widget.set_hexpand(True)
            grid.attach(widget, 1, row_idx, 1, 1)
            self._fields[field["key"]] = widget
            row_idx += 1
            if field.get("help"):
                help_lbl = Gtk.Label(label=field["help"], xalign=0)
                help_lbl.get_style_context().add_class("tiha-rationale")
                help_lbl.set_line_wrap(True)
                grid.attach(help_lbl, 1, row_idx, 1, 1)
                row_idx += 1
        return grid

    def _make_field(self, field: dict) -> Gtk.Widget:
        kind = field.get("type", "text")
        default = field.get("default", "")
        if kind == "textarea":
            tv = Gtk.TextView()
            tv.set_size_request(-1, 120)
            tv.get_buffer().set_text(default)
            scroller = Gtk.ScrolledWindow()
            scroller.add(tv)
            scroller.set_size_request(-1, 120)
            scroller._textview = tv  # type: ignore[attr-defined]
            return scroller
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
            buf = widget._textview.get_buffer()  # type: ignore[attr-defined]
            start, end = buf.get_bounds()
            return buf.get_text(start, end, True)
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
            return   # çift tıklama koruması
        params, missing = self._collect_params()
        if missing:
            self._show_result(ApplyResult(False, "Eksik alanlar: " + ", ".join(missing)))
            return

        self._applying = True
        # Sonuç kutusunu temizle, gerektiğinde akış alanını aç
        for child in self.result_holder.get_children():
            self.result_holder.remove(child)
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
        """Ayrı thread'de modülü çalıştırır, sonuçları ana thread'e iletir."""
        def progress(line: str) -> None:
            GLib.idle_add(self._append_stream_line, line)

        progress_cb = progress if self.module.streams_output else None
        try:
            if progress_cb is not None:
                result = self.module.apply(params, progress=progress_cb)
            else:
                result = self.module.apply(params)
        except Exception as exc:  # savunmacı
            log.exception("Modül uygulanamadı: %s", self.module.id)
            result = ApplyResult(False, f"Beklenmeyen hata: {exc}")

        GLib.idle_add(self._apply_thread_done, result)

    def _append_stream_line(self, line: str) -> bool:
        """GLib.idle_add geri çağrısı — thread-güvenli ekran güncellemesi."""
        if self._stream_buffer is None:
            return False
        end = self._stream_buffer.get_end_iter()
        self._stream_buffer.insert(end, line + "\n")
        # otomatik kaydır
        mark = self._stream_buffer.get_insert()
        self.stream_view.scroll_mark_onscreen(mark)
        return False   # idle_add'de False dönmek kaydı tek seferlik yapar

    def _apply_thread_done(self, result: ApplyResult) -> bool:
        self._applying = False
        entry = JournalEntry.new(self.module.id, self.module.title)
        entry.summary = result.summary
        entry.status = "applied" if result.success else "failed"
        self.journal.record(entry)
        self._show_result(result)
        return False

    # ------------------------------------------------------------------
    # Sonuç gösterimi
    # ------------------------------------------------------------------

    def _show_result(self, result: ApplyResult) -> None:
        for child in self.result_holder.get_children():
            self.result_holder.remove(child)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.get_style_context().add_class(
            "tiha-result-ok" if result.success else "tiha-result-fail"
        )
        lbl = Gtk.Label(label=result.summary, xalign=0)
        lbl.set_line_wrap(True)
        lbl.set_selectable(True)
        box.pack_start(lbl, False, False, 0)

        if result.details:
            d = Gtk.Label(label=result.details, xalign=0)
            d.set_line_wrap(True)
            d.set_selectable(True)
            box.pack_start(d, False, False, 0)

        if result.copyable:
            scroller = Gtk.ScrolledWindow()
            scroller.set_size_request(-1, 160)
            tv = Gtk.TextView()
            tv.set_editable(False)
            tv.set_monospace(True)
            tv.get_buffer().set_text(result.copyable)
            scroller.add(tv)
            box.pack_start(scroller, True, True, 0)

            copy_btn = Gtk.Button(label="Panoya kopyala")
            copy_btn.get_style_context().add_class("tiha-secondary")

            def _copy(_btn: Gtk.Button) -> None:
                from gi.repository import Gdk
                clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
                clip.set_text(result.copyable or "", -1)

            copy_btn.connect("clicked", _copy)
            box.pack_start(copy_btn, False, False, 0)

        if result.success and self.module.undo_supported:
            undo_btn = Gtk.Button(label="Bu adımı geri al")
            undo_btn.get_style_context().add_class("tiha-danger")

            def _undo(_btn: Gtk.Button) -> None:
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

            undo_btn.connect("clicked", _undo)
            box.pack_start(undo_btn, False, False, 0)

        self.result_holder.pack_start(box, False, False, 0)
        self.result_holder.show_all()


# =========================================================================
# Özet sayfası
# =========================================================================


class SummaryPage(Gtk.Box):
    def __init__(self, journal: Journal, modules: list[Module]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.journal = journal
        self.modules = {m.id: m for m in modules}
        self.get_style_context().add_class("tiha-content")
        self.set_margin_top(24)
        self.set_margin_bottom(24)
        self.set_margin_start(40)
        self.set_margin_end(40)

        heading = Gtk.Label(label="Özet", xalign=0)
        heading.get_style_context().add_class("tiha-heading")
        self.pack_start(heading, False, False, 0)

        info = Gtk.Label(
            label=(
                "Aşağıda bu oturumda uygulanmış adımlar listelenmiştir. "
                "Herhangi birini geri almak isterseniz 'Geri Al' düğmesini "
                "kullanabilirsiniz. Listenin altındaki 'Bitir' düğmesiyle "
                "uygulamayı kapatabilirsiniz."
            ),
            xalign=0,
        )
        info.set_line_wrap(True)
        info.get_style_context().add_class("tiha-rationale")
        self.pack_start(info, False, False, 0)

        self.container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.pack_start(self.container, True, True, 0)

        refresh = Gtk.Button(label="Listeyi yenile")
        refresh.get_style_context().add_class("tiha-secondary")
        refresh.connect("clicked", lambda *_: self.refresh())
        self.pack_start(refresh, False, False, 0)

        self.refresh()

    def refresh(self) -> None:
        for child in self.container.get_children():
            self.container.remove(child)

        entries = self.journal.all()
        if not entries:
            empty = Gtk.Label(label="Henüz uygulanmış bir adım yok.", xalign=0)
            empty.get_style_context().add_class("tiha-rationale")
            self.container.pack_start(empty, False, False, 0)
            self.container.show_all()
            return

        for entry in entries:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            lbl = Gtk.Label(
                label=f"[{entry.status}] {entry.title}  —  {entry.summary}",
                xalign=0,
            )
            lbl.set_line_wrap(True)
            row.pack_start(lbl, True, True, 0)

            module = self.modules.get(entry.module_id)
            if entry.status == "applied" and module and module.undo_supported:
                btn = Gtk.Button(label="Geri al")
                btn.get_style_context().add_class("tiha-danger")

                def _undo(_b: Gtk.Button, m: Module = module, e = entry) -> None:
                    try:
                        result = m.undo(e.data)
                    except Exception as exc:
                        result = ApplyResult(False, f"Hata: {exc}")
                    if result.success:
                        self.journal.mark_undone(m.id)
                    self.refresh()

                btn.connect("clicked", _undo)
                row.pack_start(btn, False, False, 0)

            self.container.pack_start(row, False, False, 0)
        self.container.show_all()
