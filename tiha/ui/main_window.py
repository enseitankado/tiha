"""TiHA ana penceresi.

Soldaki adım listesi ve sağdaki içerik panelinden oluşur. Sayfalar bir
``Gtk.Stack`` içinde yer alır: önce karşılama ekranı, ardından her modül
için bir sayfa, en sonunda özet sayfası.
"""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from ..core import board
from ..core.logger import get_logger
from ..core.undo import Journal
from ..modules import all_modules
from .pages import ModulePage, SummaryPage, WelcomePage

log = get_logger(__name__)

CSS_PATH = Path(__file__).resolve().parents[2] / "data" / "styles.css"


class TiHAWindow(Gtk.Window):
    """Ana pencere."""

    def __init__(self) -> None:
        super().__init__(title="TiHA — Tahta İmaj Hazırlık Aracı")
        self.get_style_context().add_class("tiha")
        self.set_default_size(1280, 800)
        self.set_position(Gtk.WindowPosition.CENTER)

        self._load_css()

        self.journal = Journal()
        self.board_info = board.detect()
        self.modules = all_modules()
        self.pages: list[Gtk.Widget] = []

        self._build_layout()
        self._build_welcome()
        self._build_module_pages()
        self._build_summary()

        # Başlangıç
        self._show_page_index(0)

    # ---- Kurulum yardımcıları -------------------------------------------

    def _load_css(self) -> None:
        if not CSS_PATH.exists():
            log.warning("CSS dosyası bulunamadı: %s", CSS_PATH)
            return
        provider = Gtk.CssProvider()
        try:
            provider.load_from_path(str(CSS_PATH))
        except Exception as exc:
            log.warning("CSS yüklenemedi: %s", exc)
            return
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_layout(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.add(outer)

        # --- Sol: kenar çubuğu ---
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar.get_style_context().add_class("tiha-sidebar")
        title = Gtk.Label(label="TiHA")
        title.get_style_context().add_class("tiha-sidebar-title")
        title.set_xalign(0)
        subtitle = Gtk.Label(label="Tahta İmaj Hazırlık Aracı")
        subtitle.get_style_context().add_class("tiha-sidebar-subtitle")
        subtitle.set_xalign(0)
        sidebar.pack_start(title, False, False, 0)
        sidebar.pack_start(subtitle, False, False, 0)

        self.sidebar_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar.pack_start(self.sidebar_list, True, True, 0)
        outer.pack_start(sidebar, False, False, 0)

        # --- Sağ: içerik + aksiyon çubuğu ---
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.pack_start(right, True, True, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(200)
        right.pack_start(self.stack, True, True, 0)

        # Aksiyon çubuğu
        self.action_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.action_bar.get_style_context().add_class("tiha-actions")

        self.btn_back = Gtk.Button(label="Geri")
        self.btn_back.get_style_context().add_class("tiha-secondary")
        self.btn_back.connect("clicked", self._on_back)
        self.action_bar.pack_start(self.btn_back, False, False, 0)

        spacer = Gtk.Box()
        self.action_bar.pack_start(spacer, True, True, 0)

        self.btn_skip = Gtk.Button(label="Atla")
        self.btn_skip.get_style_context().add_class("tiha-secondary")
        self.btn_skip.connect("clicked", self._on_next)
        self.action_bar.pack_start(self.btn_skip, False, False, 0)

        self.btn_apply = Gtk.Button(label="Uygula")
        self.btn_apply.get_style_context().add_class("tiha-primary")
        self.btn_apply.connect("clicked", self._on_apply)
        self.action_bar.pack_start(self.btn_apply, False, False, 0)

        self.btn_next = Gtk.Button(label="İleri")
        self.btn_next.get_style_context().add_class("tiha-primary")
        self.btn_next.connect("clicked", self._on_next)
        self.action_bar.pack_start(self.btn_next, False, False, 0)

        right.pack_start(self.action_bar, False, False, 0)

    def _build_welcome(self) -> None:
        page = WelcomePage(self.board_info)
        self.pages.append(page)
        self.stack.add_named(page, "welcome")
        self._add_sidebar_entry("Başlangıç")

    def _build_module_pages(self) -> None:
        for module in self.modules:
            page = ModulePage(module, self.journal)
            self.pages.append(page)
            self.stack.add_named(page, module.id)
            self._add_sidebar_entry(module.title)

    def _build_summary(self) -> None:
        page = SummaryPage(self.journal, self.modules)
        self.pages.append(page)
        self.stack.add_named(page, "summary")
        self._add_sidebar_entry("Özet")

    def _add_sidebar_entry(self, label: str) -> None:
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.get_style_context().add_class("tiha-step")
        self.sidebar_list.pack_start(lbl, False, False, 0)

    # ---- Navigasyon ------------------------------------------------------

    def _show_page_index(self, index: int) -> None:
        index = max(0, min(index, len(self.pages) - 1))
        self.current_index = index
        self.stack.set_visible_child(self.pages[index])
        # Kenar çubuğu işaretleri
        children = self.sidebar_list.get_children()
        for i, child in enumerate(children):
            ctx = child.get_style_context()
            for cls in ("tiha-step-active", "tiha-step-done", "tiha-step-failed"):
                ctx.remove_class(cls)
            if i == index:
                ctx.add_class("tiha-step-active")

        # Aksiyon çubuğu görünürlüğü
        page = self.pages[index]
        is_module = isinstance(page, ModulePage)
        self.btn_apply.set_visible(is_module)
        self.btn_skip.set_visible(is_module)
        self.btn_next.set_visible(not is_module or False)
        self.btn_next.set_visible(True)

    def _on_back(self, _btn: Gtk.Button) -> None:
        self._show_page_index(self.current_index - 1)

    def _on_next(self, _btn: Gtk.Button) -> None:
        if self.current_index >= len(self.pages) - 1:
            Gtk.main_quit()
            return
        self._show_page_index(self.current_index + 1)

    def _on_apply(self, _btn: Gtk.Button) -> None:
        page = self.pages[self.current_index]
        if not isinstance(page, ModulePage):
            return
        page.run_apply()
