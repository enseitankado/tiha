"""Yerel hesaplar adımının "Branş hesapları" alanı.

Formda yalnız seçimin özeti durur: okul türü, seçilen branşlar (her biri
✕ ile listeden çıkarılabilen bir etiket) ve seçim penceresini açan düğme.
Okul türü ve branşlar geniş bir pencerede seçilir; orada okul türleri
gruplu bir listede, branşlar aranabilir üç sütunlu kutucuklarda durur.

Alanın değeri m01'in beklediği JSON'dur::

    {"school_type": "...", "branches": [...], "unselected": [...]}

``unselected``: sistemde hesabı olup listede bulunmayan branşlar — m01
uygulamada onları (onay alarak) siler.
"""

from __future__ import annotations

import json
import pwd

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango  # noqa: E402

from ..core.i18n import t  # noqa: E402
from ..core.meb_data import (  # noqa: E402
    all_branch_labels,
    branch_to_username,
    branches_for,
    load_school_types,
    school_group,
    school_label,
)


def _existing_usernames() -> set[str]:
    try:
        return {e.pw_name for e in pwd.getpwall()}
    except OSError:
        return set()


def _label(text: str, *classes: str, wrap: bool = False) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=0)
    if wrap:
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    for klass in classes:
        lbl.get_style_context().add_class(klass)
    return lbl


class BranchAccountsField(Gtk.Box):
    """Form alanı: seçim özeti + seçim penceresi."""

    def __init__(self, default: str = "") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.get_style_context().add_class("tiha-branch-accounts")
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.START)
        self._school = ""
        self._selected: list[str] = []

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._school_lbl = _label("", "tiha-branch-school")
        self._school_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        top.pack_start(self._school_lbl, True, True, 0)
        pick = Gtk.Button(label=t("ui.branch_picker.open_button"))
        pick.connect("clicked", lambda *_: self._open_dialog())
        top.pack_end(pick, False, False, 0)
        self.pack_start(top, False, False, 0)

        self._chips = Gtk.FlowBox()
        self._chips.set_selection_mode(Gtk.SelectionMode.NONE)
        self._chips.set_min_children_per_line(1)
        self._chips.set_max_children_per_line(4)
        self._chips.set_row_spacing(4)
        self._chips.set_column_spacing(4)
        self._chips.set_valign(Gtk.Align.START)
        self.pack_start(self._chips, False, False, 0)

        self._empty_lbl = _label(t("ui.branch_picker.empty"), "tiha-rationale")
        self.pack_start(self._empty_lbl, False, False, 0)

        if default:
            self.set_selection(default)
        else:
            self._refresh()

    # --- değer ------------------------------------------------------------

    def value(self) -> str:
        existing = _existing_usernames()
        chosen = {branch_to_username(b) for b in self._selected}
        unselected = [
            label for label in all_branch_labels()
            if branch_to_username(label) in existing
            and branch_to_username(label) not in chosen
        ]
        if not self._school and not self._selected and not unselected:
            return ""
        return json.dumps(
            {
                "school_type": self._school,
                "branches": list(self._selected),
                "unselected": unselected,
            },
            ensure_ascii=False,
        )

    def set_selection(self, value: str) -> None:
        """Kayıtlı/önceki seçimi yükler. Düz okul türü anahtarı da olur
        (o türün bütün branşları seçilir)."""
        school, branches = "", []
        if value.startswith("{"):
            try:
                data = json.loads(value)
            except ValueError:
                data = {}
            school = data.get("school_type") or ""
            branches = list(data.get("branches") or [])
        elif value in load_school_types():
            school, branches = value, branches_for(value)
        self._school = school if school in load_school_types() else ""
        self._selected = _dedupe(branches)
        self._refresh()

    # --- form görünümü ----------------------------------------------------

    def _refresh(self) -> None:
        if self._school:
            self._school_lbl.set_markup(t(
                "ui.branch_picker.school_line",
                school=_escape(school_label(self._school)),
                count=len(self._selected),
            ))
        else:
            self._school_lbl.set_text(t("ui.branch_picker.no_school"))
        for child in self._chips.get_children():
            self._chips.remove(child)
        existing = _existing_usernames()
        for label in self._selected:
            self._chips.add(self._chip(label, branch_to_username(label) in existing))
        self._chips.set_visible(bool(self._selected))
        self._chips.set_no_show_all(not self._selected)
        self._empty_lbl.set_visible(not self._selected)
        self._empty_lbl.set_no_show_all(bool(self._selected))
        self._chips.show_all()

    def _chip(self, label: str, has_account: bool) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        box.get_style_context().add_class("tiha-chip")
        if not has_account:
            box.get_style_context().add_class("tiha-chip-new")
        text = _label(label)
        text.set_ellipsize(Pango.EllipsizeMode.END)
        text.set_max_width_chars(28)
        box.pack_start(text, True, True, 0)
        uname = branch_to_username(label)
        box.set_tooltip_text(t(
            "ui.branch_picker.chip_tip_existing" if has_account
            else "ui.branch_picker.chip_tip_new",
            user=uname,
        ))
        remove = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        remove.set_relief(Gtk.ReliefStyle.NONE)
        remove.set_tooltip_text(t(
            "ui.branch_picker.remove_tip_existing" if has_account
            else "ui.branch_picker.remove_tip_new",
        ))
        remove.connect("clicked", lambda *_: self._remove(label))
        box.pack_end(remove, False, False, 0)
        return box

    def _remove(self, label: str) -> None:
        self._selected = [b for b in self._selected if b != label]
        self._refresh()

    # --- seçim penceresi --------------------------------------------------

    def _open_dialog(self) -> None:
        dlg = BranchPickerDialog(self.get_toplevel(), self._school, self._selected)
        if dlg.run() == Gtk.ResponseType.OK:
            self._school, self._selected = dlg.result()
            self._refresh()
        dlg.destroy()


class BranchPickerDialog(Gtk.Dialog):
    """Okul türü + branş seçimi için geniş pencere."""

    def __init__(self, parent: Gtk.Widget, school: str, selected: list[str]) -> None:
        super().__init__(
            title=t("ui.branch_picker.dialog_title"),
            transient_for=parent if isinstance(parent, Gtk.Window) else None,
            modal=True,
            destroy_with_parent=True,
        )
        self.add_button(t("ui.branch_picker.cancel"), Gtk.ResponseType.CANCEL)
        ok = self.add_button(t("ui.branch_picker.confirm"), Gtk.ResponseType.OK)
        ok.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)
        self.set_default_size(1000, 680)
        self.get_style_context().add_class("tiha-branch-dialog")

        self._schools = load_school_types()
        self._keys = list(self._schools)
        self._school = school if school in self._schools else ""
        self._working: list[str] = _dedupe(selected)
        self._existing = _existing_usernames()
        self._checks: list[Gtk.CheckButton] = []
        self._rebuilding = False
        # Liste ilk gösterilirken ilk satırı kendiliğinden seçer; kurulum
        # bitene kadar bu seçimler yok sayılır.
        self._ready = False

        area = self.get_content_area()
        area.set_spacing(8)
        for side in ("top", "bottom", "start", "end"):
            getattr(area, f"set_margin_{side}")(12)
        self._search = Gtk.SearchEntry()
        area.pack_start(
            _label(t("ui.branch_picker.intro"), "tiha-rationale", wrap=True),
            False, False, 0,
        )

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(320)
        area.pack_start(paned, True, True, 0)

        # Sol: gruplu okul türleri
        self._school_list = Gtk.ListBox()
        self._school_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._school_list.set_header_func(self._school_header)
        for key in self._keys:
            row = Gtk.ListBoxRow()
            row._key = key  # type: ignore[attr-defined]
            lbl = _label(self._schools[key].get("label") or key, wrap=True)
            lbl.set_margin_top(4)
            lbl.set_margin_bottom(4)
            lbl.set_margin_start(8)
            lbl.set_margin_end(8)
            row.add(lbl)
            self._school_list.add(row)
        self._school_list.connect("row-selected", self._on_school_selected)
        left = Gtk.ScrolledWindow()
        left.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        left.add(self._school_list)
        left.set_size_request(260, -1)
        paned.pack1(left, False, False)

        # Sağ: branşlar
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        right.set_margin_start(10)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._title = _label("", "tiha-branch-dialog-title")
        self._title.set_ellipsize(Pango.EllipsizeMode.END)
        head.pack_start(self._title, True, True, 0)
        self._count = _label("", "tiha-rationale")
        head.pack_end(self._count, False, False, 0)
        right.pack_start(head, False, False, 0)

        tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._search.set_placeholder_text(t("ui.branch_picker.search"))
        self._search.connect("search-changed", lambda *_: (
            self._branches.invalidate_filter(), self._extra_branches.invalidate_filter()))
        tools.pack_start(self._search, True, True, 0)
        for text, active in ((t("ui.branch_picker.select_all"), True),
                             (t("ui.branch_picker.select_none"), False)):
            btn = Gtk.Button(label=text)
            btn.connect("clicked", lambda _b, a=active: self._set_all(a))
            tools.pack_start(btn, False, False, 0)
        right.pack_start(tools, False, False, 0)

        # Okul türünün branşları + (varsa) başka türden seçili olanlar
        self._branches = self._make_flow()
        self._extra_branches = self._make_flow()
        self._extra_title = _label(t("ui.branch_picker.other_school_title"), "tiha-branch-group")
        self._extra_title.set_margin_top(10)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        inner.pack_start(self._branches, False, False, 0)
        inner.pack_start(self._extra_title, False, False, 0)
        inner.pack_start(self._extra_branches, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.add(inner)
        right.pack_start(scroll, True, True, 0)

        self._placeholder = _label(t("ui.branch_picker.pick_school"), "tiha-rationale", wrap=True)
        right.pack_start(self._placeholder, False, False, 0)

        right.pack_start(
            _label(t("ui.branch_picker.legend"), "tiha-rationale", wrap=True),
            False, False, 0,
        )
        paned.pack2(right, True, False)

        self.show_all()
        self._search.grab_focus()
        self._school_list.unselect_all()
        for row in self._school_list.get_children():
            if row._key == self._school:  # type: ignore[attr-defined]
                self._school_list.select_row(row)
                break
        self._ready = True
        self._rebuild()

    def _make_flow(self) -> Gtk.FlowBox:
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_min_children_per_line(3)
        flow.set_max_children_per_line(3)
        flow.set_homogeneous(True)
        flow.set_row_spacing(2)
        flow.set_column_spacing(12)
        flow.set_valign(Gtk.Align.START)
        flow.set_filter_func(self._filter)
        return flow

    # --- okul türleri -----------------------------------------------------

    def _school_header(self, row: Gtk.ListBoxRow, before: Gtk.ListBoxRow | None) -> None:
        group = school_group(row._key)  # type: ignore[attr-defined]
        prev = school_group(before._key) if before else None  # type: ignore[attr-defined]
        if group and group != prev:
            hdr = _label(group, "tiha-branch-group")
            hdr.set_margin_top(8 if before else 2)
            hdr.set_margin_start(6)
            hdr.set_margin_bottom(2)
            row.set_header(hdr)
        else:
            row.set_header(None)

    def _on_school_selected(self, _lb, row: Gtk.ListBoxRow | None) -> None:
        if row is None or not self._ready:
            return
        self._school = row._key  # type: ignore[attr-defined]
        self._rebuild()

    # --- branşlar ---------------------------------------------------------

    def _labels_to_show(self) -> tuple[list[str], list[str]]:
        """(okul türünün branşları, bu türde olmayan ama seçili olanlar)"""
        own = branches_for(self._school) if self._school else []
        own_users = {branch_to_username(b) for b in own}
        extra = [b for b in self._working if branch_to_username(b) not in own_users]
        return own, extra

    def _rebuild(self) -> None:
        self._rebuilding = True
        for flow in (self._branches, self._extra_branches):
            for child in flow.get_children():
                flow.remove(child)
        self._checks = []
        own, extra = self._labels_to_show()
        chosen = {branch_to_username(b) for b in self._working}
        for label in own + extra:
            uname = branch_to_username(label)
            has_account = uname in self._existing
            cb = Gtk.CheckButton(label=label)
            child = cb.get_child()
            if isinstance(child, Gtk.Label):
                child.set_line_wrap(True)
                child.set_xalign(0)
            if has_account:
                cb.get_style_context().add_class("tiha-branch-has-account")
            cb.set_tooltip_text(t(
                "ui.branch_picker.check_tip_existing" if has_account
                else "ui.branch_picker.check_tip_new",
                user=uname,
            ))
            cb.set_active(uname in chosen)
            cb._branch_label = label  # type: ignore[attr-defined]
            cb.connect("toggled", self._on_toggled)
            (self._extra_branches if label in extra else self._branches).add(cb)
            self._checks.append(cb)
        self._branches.show_all()
        self._extra_branches.show_all()
        self._branches.set_visible(bool(own))
        self._extra_branches.set_visible(bool(extra))
        self._extra_title.set_visible(bool(extra))
        self._placeholder.set_visible(not self._school)
        self._title.set_text(
            school_label(self._school) if self._school else t("ui.branch_picker.no_school")
        )
        self._rebuilding = False
        self._update_count()

    def _on_toggled(self, cb: Gtk.CheckButton) -> None:
        if self._rebuilding:
            return
        label = cb._branch_label  # type: ignore[attr-defined]
        uname = branch_to_username(label)
        self._working = [b for b in self._working if branch_to_username(b) != uname]
        if cb.get_active():
            self._working.append(label)
        self._update_count()

    def _set_all(self, active: bool) -> None:
        # Arama süzgecinden geçen (görünen) kutular değişir.
        for cb in self._checks:
            parent = cb.get_parent()
            if parent is not None and parent.get_child_visible():
                cb.set_active(active)

    def _filter(self, child: Gtk.FlowBoxChild) -> bool:
        query = self._search.get_text().strip().casefold()
        if not query:
            return True
        cb = child.get_child()
        label = getattr(cb, "_branch_label", "")
        return query in label.casefold() or query in branch_to_username(label)

    def _update_count(self) -> None:
        self._count.set_text(t("ui.branch_picker.count", count=len(self._working)))

    def result(self) -> tuple[str, list[str]]:
        """Onaylanan (okul türü, branşlar); okul türünün sırası korunur."""
        own, extra = self._labels_to_show()
        chosen = {branch_to_username(b) for b in self._working}
        ordered = [b for b in own if branch_to_username(b) in chosen] + extra
        return self._school, _dedupe(ordered)


def _dedupe(labels: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for label in labels:
        uname = branch_to_username(label)
        if uname and uname not in seen:
            seen.add(uname)
            out.append(label)
    return out


def _escape(text: str) -> str:
    from gi.repository import GLib
    return GLib.markup_escape_text(text)
