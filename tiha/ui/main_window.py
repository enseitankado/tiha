"""TiHA ana penceresi.

Solda tıklanabilir adım listesi, sağda kaydırılabilir içerik alanı ve
altta aksiyon çubuğu bulunur. Her sayfa ``Gtk.Stack`` içinde yer alır;
Stack ise bir ``Gtk.ScrolledWindow`` içindedir, böylece uzun içerikte
pencere şişmez, kullanıcı sayfayı kaydırabilir ve aksiyon çubuğu ekran
altında sabit kalır.
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
    """Ana pencere — eta stilinde kompakt ve dokunmatik-uyumlu."""

    # Pardus ETAP ekranları genellikle 1920x1080 dokunmatik paneller;
    # pencere onun %60'ı kadar açılır, kullanıcı isterse büyütür.
    DEFAULT_WIDTH = 1100
    DEFAULT_HEIGHT = 720
    MIN_WIDTH = 840
    MIN_HEIGHT = 560

    def __init__(self) -> None:
        super().__init__(title="TiHA — Tahta İmaj Hazırlık Aracı")
        self.get_style_context().add_class("tiha")
        self.set_default_size(self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT)
        self.set_size_request(self.MIN_WIDTH, self.MIN_HEIGHT)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name("preferences-system")

        self._load_css()

        self.journal = Journal()
        self.board_info = board.detect()
        self.modules = all_modules()
        self.pages: list[Gtk.Widget] = []
        self.current_index: int = 0

        self._build_layout()
        self._build_welcome()
        self._build_module_pages()
        self._build_summary()

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
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.add(paned)

        # --- Sol: adım listesi (kaydırılabilir, tıklanabilir) ---
        sidebar_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_outer.get_style_context().add_class("tiha-sidebar")
        sidebar_outer.set_size_request(240, -1)

        title = Gtk.Label(label="TiHA", xalign=0)
        title.get_style_context().add_class("tiha-sidebar-title")
        subtitle = Gtk.Label(label="Tahta İmaj Hazırlık Aracı", xalign=0)
        subtitle.get_style_context().add_class("tiha-sidebar-subtitle")
        sidebar_outer.pack_start(title, False, False, 0)
        sidebar_outer.pack_start(subtitle, False, False, 0)

        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.sidebar_list = Gtk.ListBox()
        self.sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.sidebar_list.connect("row-activated", self._on_sidebar_row_activated)
        sidebar_scroll.add(self.sidebar_list)
        sidebar_outer.pack_start(sidebar_scroll, True, True, 0)

        paned.pack1(sidebar_outer, resize=False, shrink=False)

        # --- Sağ: Stack (ScrolledWindow içinde) + aksiyon çubuğu ---
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(180)

        self.content_scroll = Gtk.ScrolledWindow()
        self.content_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.content_scroll.add(self.stack)
        right.pack_start(self.content_scroll, True, True, 0)

        # Aksiyon çubuğu
        self.action_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.action_bar.get_style_context().add_class("tiha-actions")

        self.btn_back = Gtk.Button(label="◀  Geri")
        self.btn_back.connect("clicked", self._on_back)
        self.action_bar.pack_start(self.btn_back, False, False, 0)

        spacer = Gtk.Box()
        self.action_bar.pack_start(spacer, True, True, 0)

        self.btn_apply = Gtk.Button(label="Uygula")
        self.btn_apply.get_style_context().add_class("suggested-action")
        self.btn_apply.connect("clicked", self._on_apply)
        self.action_bar.pack_start(self.btn_apply, False, False, 0)

        self.btn_next = Gtk.Button(label="İleri  ▶")
        self.btn_next.connect("clicked", self._on_next)
        self.action_bar.pack_start(self.btn_next, False, False, 0)

        right.pack_start(self.action_bar, False, False, 0)

        paned.pack2(right, resize=True, shrink=False)
        paned.set_position(240)

    def _build_welcome(self) -> None:
        page = WelcomePage(self.board_info)
        self.pages.append(page)
        self.stack.add_named(page, "welcome")
        self._add_sidebar_entry("Hoş geldiniz")

    def _build_module_pages(self) -> None:
        # Karşılama bir adım değildir; modüller 1'den başlayarak numaralandırılır.
        for idx, module in enumerate(self.modules, start=1):
            page = ModulePage(module, self.journal)
            self.pages.append(page)
            self.stack.add_named(page, module.id)
            self._add_sidebar_entry(f"{idx}. {module.title}")

    def _build_summary(self) -> None:
        page = SummaryPage(self.journal, self.modules)
        self.pages.append(page)
        self.stack.add_named(page, "summary")
        self._add_sidebar_entry("Özet")

    def _add_sidebar_entry(self, label: str) -> None:
        row = Gtk.ListBoxRow()
        row.get_style_context().add_class("tiha-step")
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.set_ellipsize(3)  # Pango.EllipsizeMode.END
        row.add(lbl)
        row.show_all()
        self.sidebar_list.add(row)

    # ---- Navigasyon ------------------------------------------------------

    def _on_sidebar_row_activated(self, _lb: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if row is None:
            return
        self._show_page_index(row.get_index(), sync_sidebar=False)

    def _show_page_index(self, index: int, *, sync_sidebar: bool = True) -> None:
        index = max(0, min(index, len(self.pages) - 1))
        self.current_index = index
        self.stack.set_visible_child(self.pages[index])

        # İçerik scroll'u en başa çek
        adj = self.content_scroll.get_vadjustment()
        if adj:
            adj.set_value(0)

        # Özet sayfası her açılışta güncellensin
        page = self.pages[index]
        if isinstance(page, SummaryPage):
            page.refresh()

        if sync_sidebar:
            row = self.sidebar_list.get_row_at_index(index)
            if row is not None:
                self.sidebar_list.select_row(row)

        # Aksiyon çubuğu görünürlüğü
        is_module = isinstance(page, ModulePage)
        self.btn_apply.set_visible(is_module)

        # Özet sayfasında "Bitir" gösterelim
        is_last = index >= len(self.pages) - 1
        self.btn_next.set_label("Bitir" if is_last else "İleri  ▶")

    def _on_back(self, _btn: Gtk.Button) -> None:
        if self.current_index == 0:
            return
        self._show_page_index(self.current_index - 1)

    def _on_next(self, _btn: Gtk.Button) -> None:
        if self.current_index >= len(self.pages) - 1:
            Gtk.main_quit()
            return
        self._show_page_index(self.current_index + 1)

    def _on_apply(self, _btn: Gtk.Button) -> None:
        page = self.pages[self.current_index]
        if isinstance(page, ModulePage):
            page.run_apply()
