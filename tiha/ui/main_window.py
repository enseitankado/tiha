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
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

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

        # Author bilgisi
        author_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        author_box.set_margin_start(12)
        author_box.set_margin_end(12)
        author_box.set_margin_bottom(8)

        author_name = Gtk.Label(label="Özgür Koca", xalign=0)
        author_name.get_style_context().add_class("tiha-author-name")
        author_name.set_max_width_chars(20)
        author_name.set_ellipsize(3)  # Pango.EllipsizeMode.END
        author_box.pack_start(author_name, False, False, 0)

        author_web = Gtk.Label(label="ozgurkoca.com", xalign=0)
        author_web.get_style_context().add_class("tiha-author-web")
        author_web.set_max_width_chars(20)
        author_web.set_ellipsize(3)
        author_box.pack_start(author_web, False, False, 0)

        author_email = Gtk.Label(label="ozgur.koca@linux.org.tr", xalign=0)
        author_email.get_style_context().add_class("tiha-author-email")
        author_email.set_max_width_chars(20)
        author_email.set_ellipsize(3)
        author_box.pack_start(author_email, False, False, 0)

        sidebar_outer.pack_start(author_box, False, False, 0)

        paned.pack1(sidebar_outer, resize=False, shrink=False)

        # --- Sağ: Stack (ScrolledWindow içinde) + aksiyon çubuğu ---
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(180)

        self.content_scroll = Gtk.ScrolledWindow()
        self.content_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # Üstte gezinen (overlay) kaydırma çubuğu — içerik sığdığında
        # yer kaplamaz, görünmez. Tema/Pardus 'eta' dış kaydırma çubuğu
        # zorlamasın diye açıkça etkinleştiriyoruz.
        self.content_scroll.set_overlay_scrolling(True)
        self.content_scroll.add(self.stack)
        right.pack_start(self.content_scroll, True, True, 0)

        # Aksiyon çubuğu
        self.action_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.action_bar.get_style_context().add_class("tiha-actions")

        self.btn_back = Gtk.Button(label="◀  Geri")
        self.btn_back.connect("clicked", self._on_back)
        self.action_bar.pack_start(self.btn_back, False, False, 0)

        # Apply öncesi ne olacağını anlatan ipucu
        self.lbl_apply_hint = Gtk.Label(label="", xalign=1)
        self.lbl_apply_hint.set_line_wrap(True)
        self.lbl_apply_hint.set_max_width_chars(80)
        self.lbl_apply_hint.set_no_show_all(True)
        self.lbl_apply_hint.get_style_context().add_class("tiha-apply-hint")
        self.action_bar.pack_start(self.lbl_apply_hint, True, True, 0)

        self.btn_apply = Gtk.Button(label="Uygula")
        self.btn_apply.get_style_context().add_class("suggested-action")
        self.btn_apply.set_no_show_all(True)
        self.btn_apply.connect("clicked", self._on_apply)
        self.action_bar.pack_start(self.btn_apply, False, False, 0)

        self.btn_next = Gtk.Button(label="İleri  ▶")
        self.btn_next.connect("clicked", self._on_next)
        self.action_bar.pack_start(self.btn_next, False, False, 0)

        right.pack_start(self.action_bar, False, False, 0)

        paned.pack2(right, resize=True, shrink=False)
        paned.set_position(240)

    def _build_welcome(self) -> None:
        page = WelcomePage()
        self.pages.append(page)
        self.stack.add_named(page, "welcome")
        self._add_sidebar_entry("Hoş geldiniz")

    def _build_module_pages(self) -> None:
        # Karşılama bir adım değildir; modüller 1'den başlayarak numaralandırılır.
        for idx, module in enumerate(self.modules, start=1):
            page = ModulePage(module, self.journal)
            # Apply tamamlandığında ileri/geri kapısını yeniden değerlendir
            page.post_apply_callback = lambda *_a, **_kw: self._update_navigation_gate()
            self.pages.append(page)
            self.stack.add_named(page, module.id)
            sidebar_label = module.sidebar_title or module.title
            self._add_sidebar_entry(f"{idx}. {sidebar_label}")

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
        page = self.pages[index]
        self.stack.set_visible_child(page)

        # İçerik scroll'u en başa çek
        adj = self.content_scroll.get_vadjustment()
        if adj:
            adj.set_value(0)

        # Özet sayfası her açılışta güncellensin
        if isinstance(page, SummaryPage):
            page.refresh()

        # OTP modülü sayfası her açılışta canlı veri ile güncellensin
        if hasattr(page, 'module') and page.module.id == "m03_otp_secrets":
            page._refresh_preview()

        # Güç yönetimi modülü her açılışta güncel eta-shutdown config'ini okuysun
        if hasattr(page, 'module') and page.module.id == "m11_power_management":
            page._refresh_preview()
            # Form alanlarını da güncel config'e göre doldur
            if hasattr(page.module, 'get_current_config'):
                current_config = page.module.get_current_config()
                if current_config:
                    page._update_form_fields(current_config)

        # Modül sayfaları açıldığında önizleme/şartlı alanları sistemin
        # güncel durumuna göre tazele. (Sayfalar uygulama başlangıcında
        # bir kez kuruluyor; bu olmadan rapor kutusu o anki anlık değil
        # uygulama açılış anının görüntüsü olarak kalırdı.)
        if isinstance(page, ModulePage):
            page._refresh_after_action()

        if sync_sidebar:
            # Tüm adımlardan active sınıfını kaldır
            for i in range(len(self.pages)):
                old_row = self.sidebar_list.get_row_at_index(i)
                if old_row:
                    old_row.get_style_context().remove_class("tiha-step-active")

            # Aktif adıma active sınıfını ekle
            row = self.sidebar_list.get_row_at_index(index)
            if row is not None:
                self.sidebar_list.select_row(row)
                row.get_style_context().add_class("tiha-step-active")

        # Aksiyon çubuğu görünürlüğü (Apply ve ipucu yalnızca manuel modüllerde)
        is_module = isinstance(page, ModulePage)
        show_apply = is_module and not page.module.auto_apply
        self.btn_apply.set_visible(show_apply)
        hint = page.module.apply_hint if is_module else ""
        self.lbl_apply_hint.set_text(f"Uygulandığında: {hint}" if (show_apply and hint) else "")
        self.lbl_apply_hint.set_visible(bool(show_apply and hint))

        # Auto-apply modüllerini (salt-okunur) bir kez kendi tetikle
        if is_module and page.module.auto_apply and not page._auto_applied:
            page._auto_applied = True
            GLib.idle_add(page.run_apply)

        # Özet sayfasında "Bitir" gösterelim
        is_last = index >= len(self.pages) - 1
        self.btn_next.set_label("Bitir" if is_last else "İleri  ▶")

        self._update_navigation_gate()

    def _update_navigation_gate(self) -> None:
        """Mevcut sayfadaki kurallara göre İleri düğmesini etkin/pasif tutar.

        Kural: sistem güncellemesi sayfasında bekleyen yükseltme varsa
        İleri pasifleşir; kullanıcı önce Uygula çalıştırmalı (ya da sol
        listeden başka adıma geçmeli). Diğer tüm sayfalarda İleri serbesttir.
        Sol listeden navigasyon hiçbir zaman engellenmez.

        m09 için ``apt-get -s -q full-upgrade`` ~3 sn senkron sürer;
        UI'yı bloke etmemek için modülün async API'sini kullanıyoruz:
        cache varsa hemen değer döner, yoksa -1 (bilinmiyor) döner ve
        sonuç gelince geri çağrımla yeniden tazeleriz.
        """
        page = self.pages[self.current_index] if self.pages else None
        gate_open = True
        if isinstance(page, ModulePage) and page.module.id == "m09_system_update":
            async_fn = getattr(page.module, "pending_update_count_async", None)
            if callable(async_fn):
                try:
                    pending = async_fn(
                        lambda v, p=page: self._on_pending_update_ready(p, v)
                    )
                except Exception as exc:
                    log.debug("pending_update_count_async hatası: %s", exc)
                    pending = -1
            else:
                pending = -1
            # pending > 0 → bekleyen yükseltme var → İleri kapalı
            # pending == 0 → güncel → İleri açık
            # pending < 0 → bilinmiyor (kontrol ediliyor) → İleri açık (fail-open)
            if pending > 0:
                gate_open = False
        self.btn_next.set_sensitive(gate_open)

    def _on_pending_update_ready(self, page, _value: int) -> bool:
        """m09'un arka plan worker'ı bittiğinde UI thread'inde çağrılır.
        Eğer kullanıcı hâlâ aynı sayfadaysa önizleme + gate tazelenir."""
        if not self.pages:
            return False
        current = self.pages[self.current_index] if self.current_index < len(self.pages) else None
        if current is not page:
            return False
        if isinstance(page, ModulePage):
            page._refresh_preview()
        self._update_navigation_gate()
        return False  # GLib.idle_add tek seferlik olsun

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
