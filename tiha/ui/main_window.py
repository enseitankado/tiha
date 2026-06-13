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

from .. import __version__
from ..core import news_state as ns
from ..core.logger import get_logger
from ..core.undo import Journal
from ..core.update_check import (
    CheckResult,
    UpdateInfo,
    check_async as check_update_async,
    is_newer,
)
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
        # Pardus ETAP 'eta' ikon temasında yer alan resmi imaj-yazıcı
        # simgesi — TiHA'nın amacını (tahta imajı hazırlamak ve
        # diskten diske yazmak) doğrudan çağrıştırır.
        self.set_icon_name("pardus-image-writer")

        self._load_css()

        self.journal = Journal()
        self.modules = all_modules()
        self.pages: list[Gtk.Widget] = []
        self.current_index: int = 0

        self._build_layout()
        self._build_welcome()
        self._build_module_pages()
        self._build_summary()

        # İlk durum ikonlarını çiz (geçmiş oturumlardan kalan applied'ları yansıt)
        self._refresh_sidebar_status()
        self._show_page_index(0)

        # Sürüm kontrolü — tek HTTP isteğiyle hem sidebar rozetini hem
        # (gerekiyorsa) 'Yenilikler' diyaloğunu besler. Ağ hatası sessizce
        # yutulur. news_since None ise news analizi atlanır.
        news_since = self._compute_news_since()
        check_update_async(self._on_check_result, news_since=news_since)

    def _on_export_preset_clicked(self) -> None:
        """Özet sayfasındaki 'Preset dışa aktar' düğmesi — tüm ModulePage'lerin
        last_apply_params'ını toplar, FileChooser ile hedef seçer, JSON yazar."""
        from ..core.preset import export_preset

        collected: dict[str, dict] = {}
        for page in self.pages:
            if isinstance(page, ModulePage) and page.last_apply_params:
                collected[page.module.id] = page.last_apply_params

        if not collected:
            self._info_dialog(
                "Dışa aktarılacak parametre yok",
                "Henüz bu oturumda parametre alan bir modül "
                "uygulanmamış. En az bir adımı uygulayıp tekrar deneyin.",
            )
            return

        dlg = Gtk.FileChooserDialog(
            title="Preset'i kaydet",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dlg.add_buttons(
            "İptal", Gtk.ResponseType.CANCEL,
            "Kaydet", Gtk.ResponseType.ACCEPT,
        )
        dlg.set_current_name("tiha-preset.json")
        dlg.set_do_overwrite_confirmation(True)
        fil = Gtk.FileFilter()
        fil.set_name("JSON dosyaları (*.json)")
        fil.add_pattern("*.json")
        dlg.add_filter(fil)
        try:
            if dlg.run() == Gtk.ResponseType.ACCEPT:
                target = Path(dlg.get_filename())
                try:
                    written = export_preset(collected, target=target)
                    self._info_dialog(
                        "Preset kaydedildi",
                        f"{len(collected)} modülün parametreleri "
                        f"şu dosyaya yazıldı:\n\n{written}\n\n"
                        "Diğer tahtalarda uygulamak için:\n"
                        f"  sudo tiha --preset {written.name} --apply",
                    )
                except Exception as exc:
                    self._info_dialog(
                        "Kayıt başarısız",
                        f"Preset yazılamadı: {exc}",
                        error=True,
                    )
        finally:
            dlg.destroy()

    def _info_dialog(self, title: str, body: str, *, error: bool = False) -> None:
        dlg = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.ERROR if error else Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dlg.format_secondary_text(body)
        dlg.run()
        dlg.destroy()

    def _compute_news_since(self) -> str | None:
        """'Yenilikler' diyaloğunu tetikleyecek baseline'ı döner.

        Üç durumda None döner (diyalog hiç tetiklenmez):
          * Kullanıcı bu bilgisayarda "bir daha gösterme"yi seçmişse.
          * İlk çalıştırma (state yok) — gösterilecek geçmiş yok; state'i
            sessizce mevcut sürüme kurar.
          * En son görülen sürüm = mevcut sürüm (yeni bir şey yok).

        Aksi halde geçmiş sürümü döner; async fetch o baseline'dan
        ``__version__``'e kadar olan release notlarını toplar.
        """
        state = ns.load()
        if state.suppress_news_dialog:
            return None
        if state.last_seen_version is None:
            state.last_seen_version = __version__
            ns.save(state)
            return None
        if not is_newer(__version__, state.last_seen_version):
            return None
        return state.last_seen_version

    def _on_check_result(self, result: CheckResult) -> None:
        """Birleşik kontrol sonucu — UI thread'inde çalışır."""
        if result.update is not None:
            self._apply_update_badge(result.update)
        if result.news_body and result.news_since:
            self._show_news_dialog(
                since_version=result.news_since,
                until_version=__version__,
                body=result.news_body,
            )

    def _apply_update_badge(self, info: UpdateInfo) -> None:
        """Sidebar'daki güncelleme rozetini doldur ve göster."""
        self._update_info = info
        markup = (
            f'🔔 Yeni sürüm: '
            f'<a href="tiha-update">'
            f'v{GLib.markup_escape_text(info.latest_version)}</a>'
        )
        self.update_badge.set_markup(markup)
        if info.newer_count > 1:
            tip = (
                f"Şu an v{info.current_version}. {info.newer_count} sürüm "
                "gerideisin — tıklayınca yenilikleri özet halinde görürsün."
            )
        else:
            tip = (
                f"Şu an v{info.current_version}. Tıklayınca bu sürümde "
                "neler değiştiğini gösterir."
            )
        self.update_badge.set_tooltip_text(tip)
        self.update_badge.show()

    def _show_news_dialog(
        self,
        since_version: str,
        until_version: str,
        body: str,
    ) -> None:
        """Bu bilgisayarda son görülen sürümden bu yana çıkan yenilikleri
        modal bir diyalogda gösterir. "Bir daha gösterme" işaretine göre
        suppress bayrağını günceller; her durumda last_seen_version'ı
        ``until_version``'a yazar."""
        dlg = Gtk.Dialog(
            title="TiHA — Yenilikler",
            transient_for=self,
            modal=True,
        )
        dlg.add_button("Tamam", Gtk.ResponseType.OK)
        dlg.set_default_size(680, 520)

        box = dlg.get_content_area()
        box.set_spacing(10)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        header = Gtk.Label(xalign=0)
        header.set_line_wrap(True)
        header.set_markup(
            "Bu bilgisayarda en son "
            f"<b>v{GLib.markup_escape_text(since_version)}</b> kullanmışsın. "
            "Şimdi "
            f"<b>v{GLib.markup_escape_text(until_version)}</b>'i "
            "çalıştırıyorsun. Aradaki yenilikler:"
        )
        box.pack_start(header, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        tv = Gtk.TextView()
        tv.set_editable(False)
        tv.set_cursor_visible(False)
        tv.set_wrap_mode(Gtk.WrapMode.WORD)
        tv.set_left_margin(10)
        tv.set_right_margin(10)
        tv.set_top_margin(8)
        tv.set_bottom_margin(8)
        tv.get_buffer().set_text(body)
        scrolled.add(tv)
        box.pack_start(scrolled, True, True, 0)

        suppress_check = Gtk.CheckButton(
            label="Bu bilgilendirmeyi bu bilgisayarda bir daha gösterme"
        )
        box.pack_start(suppress_check, False, False, 0)

        dlg.show_all()
        dlg.run()
        suppress = suppress_check.get_active()
        dlg.destroy()

        state = ns.load()
        state.last_seen_version = until_version
        state.suppress_news_dialog = suppress
        ns.save(state)

    def _on_update_badge_link(self, _label, uri: str) -> bool:
        """update_badge'deki linke tıklandığında çalışır.

        Default davranış (tarayıcıyı açmak) yerine inline bir 'Yenilikler'
        diyaloğu göstermek için True döner.
        """
        if uri != "tiha-update":
            return False
        info = getattr(self, "_update_info", None)
        if info is None:
            return False
        self._show_update_notes_dialog(info)
        return True

    def _show_update_notes_dialog(self, info: UpdateInfo) -> None:
        """Kullanıcının sürümünden bu yana çıkan release notlarını gösterir."""
        title = (
            f"Yenilikler — v{info.latest_version}"
            if info.newer_count <= 1
            else f"Yenilikler — son {info.newer_count} sürüm"
        )
        dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dlg.add_button("GitHub'da aç", Gtk.ResponseType.APPLY)
        dlg.add_button("Kapat", Gtk.ResponseType.CLOSE)
        dlg.set_default_size(680, 520)

        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        header = Gtk.Label(xalign=0)
        header.set_markup(
            f"<b>Şu an:</b> v{GLib.markup_escape_text(info.current_version)}  ·  "
            f"<b>Son sürüm:</b> v{GLib.markup_escape_text(info.latest_version)}"
        )
        box.pack_start(header, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        tv = Gtk.TextView()
        tv.set_editable(False)
        tv.set_cursor_visible(False)
        tv.set_wrap_mode(Gtk.WrapMode.WORD)
        tv.set_left_margin(10)
        tv.set_right_margin(10)
        tv.set_top_margin(8)
        tv.set_bottom_margin(8)
        body = info.body.strip() or (
            "Yeni sürüm var ama not yazılmamış. Ayrıntılar için "
            "GitHub'ı açabilirsin."
        )
        tv.get_buffer().set_text(body)
        scrolled.add(tv)
        box.pack_start(scrolled, True, True, 0)

        dlg.show_all()
        response = dlg.run()
        if response == Gtk.ResponseType.APPLY:
            self._open_url_in_user_session(info.html_url)
        dlg.destroy()

    def _open_url_in_user_session(self, url: str) -> None:
        """TiHA root yetkisinde çalışır; xdg-open'ı aktif kullanıcının
        oturumunda spawn'lamak gerekir, yoksa tarayıcı bulunamaz."""
        import subprocess
        try:
            from ..core.utils import _find_active_graphical_session
            env = _find_active_graphical_session()
        except ImportError:
            env = None
        try:
            if env:
                subprocess.Popen(
                    ["sudo", "-u", env["USER"], "env"]
                    + [f"{k}={v}" for k, v in env.items()]
                    + ["xdg-open", url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            else:
                subprocess.Popen(
                    ["xdg-open", url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except OSError as exc:
            log.warning("xdg-open başarısız: %s", exc)

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

        # Güncelleme rozeti — async kontrol sonucu geldiğinde belirir.
        self.update_badge = Gtk.Label(xalign=0)
        self.update_badge.set_max_width_chars(28)
        self.update_badge.set_ellipsize(3)
        self.update_badge.set_track_visited_links(False)
        self.update_badge.set_no_show_all(True)
        self.update_badge.get_style_context().add_class("tiha-update-badge")
        self.update_badge.set_margin_start(12)
        self.update_badge.set_margin_end(12)
        self.update_badge.set_margin_top(4)
        # Tarayıcıya gitmek yerine inline "Yenilikler" diyaloğunu aç.
        self.update_badge.connect("activate-link", self._on_update_badge_link)
        sidebar_outer.pack_start(self.update_badge, False, False, 0)

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

        author_web = Gtk.Label(xalign=0)
        author_web.set_markup(
            '<a href="https://github.com/enseitankado/tiha">'
            'github.com/enseitankado/tiha</a>'
        )
        author_web.set_track_visited_links(False)
        author_web.get_style_context().add_class("tiha-author-web")
        author_web.set_max_width_chars(28)
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
            # Apply tamamlandığında ileri/geri kapısını + sidebar ikonlarını tazele.
            def _after_apply(*_a, _mid=module.id, **_kw):
                self._update_navigation_gate()
                self._refresh_sidebar_status()
            page.post_apply_callback = _after_apply
            self.pages.append(page)
            self.stack.add_named(page, module.id)
            sidebar_label = module.sidebar_title or module.title
            self._add_sidebar_entry(f"{idx}. {sidebar_label}", module_id=module.id)

    def _build_summary(self) -> None:
        page = SummaryPage(
            self.journal, self.modules,
            on_export_preset=self._on_export_preset_clicked,
        )
        self.pages.append(page)
        self.stack.add_named(page, "summary")
        self._add_sidebar_entry("Özet")

    def _add_sidebar_entry(self, label: str, *, module_id: str | None = None) -> None:
        row = Gtk.ListBoxRow()
        row.get_style_context().add_class("tiha-step")
        # Box: durum ikonu (sol) + adım adı (genişler)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        status = Gtk.Label(label="·", xalign=0.5)
        status.get_style_context().add_class("tiha-step-status")
        status.set_size_request(18, -1)
        box.pack_start(status, False, False, 0)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.set_ellipsize(3)  # Pango.EllipsizeMode.END
        box.pack_start(lbl, True, True, 0)
        row.add(box)
        row.show_all()
        self.sidebar_list.add(row)
        # Durum ikonunun referansını sakla — _refresh_sidebar_status günceller.
        if not hasattr(self, "_status_labels"):
            self._status_labels = []
        self._status_labels.append((module_id, status))

    def _refresh_sidebar_status(self) -> None:
        """Journal'a bakarak her sidebar satırının durum ikonunu günceller.
        Welcome / Özet sayfaları için module_id None — boş kalır."""
        latest = self.journal.latest_per_module()
        for module_id, status_lbl in getattr(self, "_status_labels", []):
            if module_id is None:
                continue
            entry = latest.get(module_id)
            ctx = status_lbl.get_style_context()
            for cls in ("tiha-step-status-ok",
                        "tiha-step-status-fail",
                        "tiha-step-status-undone"):
                ctx.remove_class(cls)
            if entry is None:
                status_lbl.set_text("·")
            elif entry.status == "applied":
                status_lbl.set_text("✓")
                ctx.add_class("tiha-step-status-ok")
                status_lbl.set_tooltip_text(f"Uygulandı: {entry.summary}")
            elif entry.status == "failed":
                status_lbl.set_text("⚠")
                ctx.add_class("tiha-step-status-fail")
                status_lbl.set_tooltip_text(f"Hata: {entry.summary}")
            elif entry.status == "undone":
                status_lbl.set_text("↶")
                ctx.add_class("tiha-step-status-undone")
                status_lbl.set_tooltip_text("Geri alındı")
            else:
                status_lbl.set_text("·")

    # ---- Navigasyon ------------------------------------------------------

    def _on_sidebar_row_activated(self, _lb: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if row is None:
            return
        # ``sync_sidebar`` argümanı kaldırıldı: hem klavye/buton hem
        # fare tıklamasıyla gelinen yolda aynı görsel akış (aktif satır
        # CSS sınıfı) uygulansın. ``select_row`` zaten seçili satıra
        # çağrılırsa no-op olur, ekstra etkisi yok.
        self._show_page_index(row.get_index())

    def _show_page_index(self, index: int) -> None:
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
            # Yavaş senkron işleri (apt sorgusu, dpkg-query, ağ
            # indirme...) UI thread'ini bloke etmeden arka planda
            # başlat. Sonuç gelince main_window önizlemeyi + gate'i
            # tazeler. Bu metodu override etmeyen modüller no-op.
            try:
                page.module.prefetch_preview_state(
                    lambda v, p=page: self._on_module_state_ready(p, v)
                )
            except Exception as exc:
                log.debug("prefetch_preview_state hatası (%s): %s",
                          page.module.id, exc)

        # Aktif satır görsel vurgusu — her giriş yolunda (sidebar
        # tıklaması, İleri/Geri, programatik) tutarlı kalsın.
        for i in range(len(self.pages)):
            old_row = self.sidebar_list.get_row_at_index(i)
            if old_row:
                old_row.get_style_context().remove_class("tiha-step-active")
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
        sonuç gelince ``_on_module_state_ready`` ile yeniden tazeleriz.
        """
        page = self.pages[self.current_index] if self.pages else None
        gate_open = True
        if isinstance(page, ModulePage) and page.module.id == "m09_system_update":
            async_fn = getattr(page.module, "pending_update_count_async", None)
            if callable(async_fn):
                try:
                    pending = async_fn(
                        lambda v, p=page: self._on_module_state_ready(p, v)
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

    def _on_module_state_ready(self, page, _value) -> bool:
        """Bir modülün arka plan worker'ı tamamlandığında UI thread'inde
        çağrılır. Kullanıcı hâlâ aynı sayfadaysa önizleme + gate
        tazelenir; başka sayfaya geçmişse sessizce çıkılır."""
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
