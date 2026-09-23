"""TiHA ana penceresi.

Solda tıklanabilir adım listesi, sağda kaydırılabilir içerik alanı ve
altta aksiyon çubuğu bulunur. Her sayfa ``Gtk.Stack`` içinde yer alır;
Stack ise bir ``Gtk.ScrolledWindow`` içindedir, böylece uzun içerikte
pencere şişmez, kullanıcı sayfayı kaydırabilir ve aksiyon çubuğu ekran
altında sabit kalır.
"""

from __future__ import annotations

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from .. import __version__
from ..core.i18n import t
from ..core.logger import get_logger
from ..core.undo import Journal
from ..core.update_check import (
    CheckResult,
    UpdateInfo,
    check_async as check_update_async,
)
from ..modules import all_modules
from .pages import ModulePage, SummaryPage, WelcomePage

log = get_logger(__name__)

CSS_PATH = Path(__file__).resolve().parents[2] / "data" / "styles.css"
ICON_PATH = Path(__file__).resolve().parents[2] / "data" / "tiha.svg"
# Yüklenemezse (SVG yükleyicisi yoksa) ETAP temasındaki bu simgeye düşülür.
FALLBACK_ICON_NAME = "pardus-image-writer"
# Panel, Alt+Tab ve pencere başlığı farklı boyut ister; her boyutu SVG'den
# ayrı çizmek, tek büyük resmin küçültülmesinden daha keskin sonuç verir.
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


# "Emeği Geçenler" diyaloğundaki bölümler ve satırlar. Her satır:
# (görünen ad, url, lisans, açıklama). URL boşsa ad kalın metin olarak
# çıkar, tıklanamaz.
_CREDITS_SECTIONS: tuple[dict, ...] = (
    {
        "title": t("ui.credits.sections.developer"),
        "items": (
            (
                t("ui.credits.items.ozgur_koca.name"),
                "https://github.com/enseitankado",
                t("ui.credits.items.ozgur_koca.license"),
                t("ui.credits.items.ozgur_koca.desc"),
            ),
            (
                t("ui.credits.items.tankado_com.name"),
                "https://tankado.com",
                t("ui.credits.items.tankado_com.license"),
                t("ui.credits.items.tankado_com.desc"),
            ),
        ),
    },
    {
        "title": t("ui.credits.sections.pardus"),
        "items": (
            (
                t("ui.credits.items.pardus.name"),
                "https://www.pardus.org.tr/",
                t("ui.credits.items.pardus.license"),
                t("ui.credits.items.pardus.desc"),
            ),
            (
                t("ui.credits.items.pardus_etap_kaynaklari.name"),
                "https://github.com/pardus",
                t("ui.credits.items.pardus_etap_kaynaklari.license"),
                t("ui.credits.items.pardus_etap_kaynaklari.desc"),
            ),
            (
                t("ui.credits.items.eta_otp_lock.name"),
                "https://github.com/pardus/eta-otp-lock",
                t("ui.credits.items.eta_otp_lock.license"),
                t("ui.credits.items.eta_otp_lock.desc"),
            ),
            (
                t("ui.credits.items.eta_otp_cli.name"),
                "https://github.com/pardus/eta-otp-cli",
                t("ui.credits.items.eta_otp_cli.license"),
                t("ui.credits.items.eta_otp_cli.desc"),
            ),
            (
                t("ui.credits.items.eta_112.name"),
                "https://github.com/pardus/eta-112",
                t("ui.credits.items.eta_112.license"),
                t("ui.credits.items.eta_112.desc"),
            ),
            (
                t("ui.credits.items.eta_light_mode.name"),
                "https://github.com/pardus/eta-light-mode",
                t("ui.credits.items.eta_light_mode.license"),
                t("ui.credits.items.eta_light_mode.desc"),
            ),
            (
                t("ui.credits.items.ahenk_lider.name"),
                "https://github.com/Pardus-LiderAhenk",
                t("ui.credits.items.ahenk_lider.license"),
                t("ui.credits.items.ahenk_lider.desc"),
            ),
        ),
    },
    {
        "title": t("ui.credits.sections.system_tools"),
        "items": (
            (
                t("ui.credits.items.debian.name"),
                "https://www.debian.org/",
                t("ui.credits.items.debian.license"),
                t("ui.credits.items.debian.desc"),
            ),
            (
                t("ui.credits.items.systemd.name"),
                "https://systemd.io/",
                t("ui.credits.items.systemd.license"),
                t("ui.credits.items.systemd.desc"),
            ),
            (
                t("ui.credits.items.gnu_grub.name"),
                "https://www.gnu.org/software/grub/",
                t("ui.credits.items.gnu_grub.license"),
                t("ui.credits.items.gnu_grub.desc"),
            ),
            (
                t("ui.credits.items.rsyslog.name"),
                "https://www.rsyslog.com/",
                t("ui.credits.items.rsyslog.license"),
                t("ui.credits.items.rsyslog.desc"),
            ),
            (
                t("ui.credits.items.prometheus_node_exporter.name"),
                "https://github.com/prometheus/node_exporter",
                t("ui.credits.items.prometheus_node_exporter.license"),
                t("ui.credits.items.prometheus_node_exporter.desc"),
            ),
            (
                t("ui.credits.items.smartmontools.name"),
                "https://www.smartmontools.org/",
                t("ui.credits.items.smartmontools.license"),
                t("ui.credits.items.smartmontools.desc"),
            ),
            (
                t("ui.credits.items.lm_sensors.name"),
                "https://github.com/lm-sensors/lm-sensors",
                t("ui.credits.items.lm_sensors.license"),
                t("ui.credits.items.lm_sensors.desc"),
            ),
            (
                t("ui.credits.items.ethtool.name"),
                "https://mj.ucw.cz/sw/ethtool/",
                t("ui.credits.items.ethtool.license"),
                t("ui.credits.items.ethtool.desc"),
            ),
        ),
    },
    {
        "title": t("ui.credits.sections.ui_lang"),
        "items": (
            (
                t("ui.credits.items.python_3.name"),
                "https://www.python.org/",
                t("ui.credits.items.python_3.license"),
                t("ui.credits.items.python_3.desc"),
            ),
            (
                t("ui.credits.items.gtk_3.name"),
                "https://www.gtk.org/",
                t("ui.credits.items.gtk_3.license"),
                t("ui.credits.items.gtk_3.desc"),
            ),
            (
                t("ui.credits.items.pygobject.name"),
                "https://pygobject.gnome.org/",
                t("ui.credits.items.pygobject.license"),
                t("ui.credits.items.pygobject.desc"),
            ),
        ),
    },
    {
        "title": t("ui.credits.sections.datasets"),
        "items": (
            (
                t("ui.credits.items.seclists.name"),
                "https://github.com/danielmiessler/SecLists",
                t("ui.credits.items.seclists.license"),
                t("ui.credits.items.seclists.desc"),
            ),
            (
                t("ui.credits.items.zxcvbn.name"),
                "https://github.com/dropbox/zxcvbn",
                t("ui.credits.items.zxcvbn.license"),
                t("ui.credits.items.zxcvbn.desc"),
            ),
        ),
    },
    {
        "title": t("ui.credits.sections.platform"),
        "items": (
            (
                t("ui.credits.items.vestel_faz_2_e_tahta.name"),
                "",
                t("ui.credits.items.vestel_faz_2_e_tahta.license"),
                t("ui.credits.items.vestel_faz_2_e_tahta.desc"),
            ),
            (
                t("ui.credits.items.meb_eba_programi.name"),
                "https://www.eba.gov.tr/",
                t("ui.credits.items.meb_eba_programi.license"),
                t("ui.credits.items.meb_eba_programi.desc"),
            ),
            (
                t("ui.credits.items.ogretmenler_ve_okul_yoneticileri.name"),
                "",
                t("ui.credits.items.ogretmenler_ve_okul_yoneticileri.license"),
                t("ui.credits.items.ogretmenler_ve_okul_yoneticileri.desc"),
            ),
        ),
    },
    {
        "title": t("ui.credits.sections.contact"),
        "items": (
            (
                t("ui.credits.items.kaynak_kodu.name"),
                "https://github.com/enseitankado/tiha",
                t("ui.credits.items.kaynak_kodu.license"),
                t("ui.credits.items.kaynak_kodu.desc"),
            ),
            (
                t("ui.credits.items.hata_bildirimi_ve_oneri.name"),
                "https://github.com/enseitankado/tiha/issues",
                t("ui.credits.items.hata_bildirimi_ve_oneri.license"),
                t("ui.credits.items.hata_bildirimi_ve_oneri.desc"),
            ),
            (
                t("ui.credits.items.e_posta.name"),
                "mailto:ozgur.koca@linux.org.tr",
                t("ui.credits.items.e_posta.license"),
                t("ui.credits.items.e_posta.desc"),
            ),
        ),
    },
)


def _result_is_off(module, entry) -> bool:
    """Günlük kaydı "uygulandı" ama özelliği kapalı mı bıraktı?"""
    if module is None:
        return False
    try:
        return bool(module.result_is_off(entry.data))
    except Exception:
        return False


class TiHAWindow(Gtk.Window):
    """Ana pencere — eta stilinde kompakt ve dokunmatik-uyumlu."""

    # Pardus ETAP ekranları genellikle 1920x1080 dokunmatik paneller;
    # pencere onun %60'ı kadar açılır, kullanıcı isterse büyütür.
    # 17 adım sığdığı için sidebar'da kaydırma çubuğu oluşmasın diye
    # yükseklik biraz artırıldı; genişlik dokunmadı.
    DEFAULT_WIDTH = 1100
    DEFAULT_HEIGHT = 820
    MIN_WIDTH = 840
    MIN_HEIGHT = 640

    def __init__(self) -> None:
        super().__init__(title=t("ui.main.window_title"))
        self.get_style_context().add_class("tiha")
        self.set_default_size(self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT)
        self.set_size_request(self.MIN_WIDTH, self.MIN_HEIGHT)
        self.set_position(Gtk.WindowPosition.CENTER)
        self._load_icon()

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
        # Geri alma (adım sayfasından ya da Özet'ten) işareti kaldırmıyordu:
        # tazeleme yalnız uygula sonrasına bağlıydı. Artık kayıttaki her
        # değişiklik sol menüyü ve ileri/geri kapısını tazeler.
        self.journal.listeners.append(self._on_journal_changed)
        self._show_page_index(0)

        # Sürüm kontrolü — çalışan koddan daha yeni bir release var mı diye
        # bakıp sidebar rozetini besler. Ağ hatası sessizce yutulur.
        check_update_async(self._on_check_result)

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
                t("ui.main.export_empty_title"),
                t("ui.main.export_empty_body"),
            )
            return

        dlg = Gtk.FileChooserDialog(
            title=t("ui.main.export_dialog_title"),
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dlg.add_buttons(
            t("ui.main.cancel"), Gtk.ResponseType.CANCEL,
            t("ui.main.save"), Gtk.ResponseType.ACCEPT,
        )
        dlg.set_current_name("tiha-preset.json")
        dlg.set_do_overwrite_confirmation(True)
        fil = Gtk.FileFilter()
        fil.set_name(t("ui.main.json_filter"))
        fil.add_pattern("*.json")
        dlg.add_filter(fil)
        try:
            if dlg.run() == Gtk.ResponseType.ACCEPT:
                target = Path(dlg.get_filename())
                try:
                    written = export_preset(collected, target=target)
                    self._info_dialog(
                        t("ui.main.export_done_title"),
                        t("ui.main.export_done_body", count=len(collected),
                          path=written, name=written.name),
                    )
                except Exception as exc:
                    self._info_dialog(
                        t("ui.main.export_failed_title"),
                        t("ui.main.export_failed_body", error=exc),
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

    def _on_check_result(self, result: CheckResult) -> None:
        """Sürüm kontrolü sonucu — UI thread'inde çalışır."""
        if result.update is not None:
            self._apply_update_badge(result.update)

    def _apply_update_badge(self, info: UpdateInfo) -> None:
        """Sidebar'daki güncelleme rozetini doldur ve göster."""
        self._update_info = info
        markup = t(
            "ui.main.update_badge",
            version=GLib.markup_escape_text(info.latest_version),
        )
        self.update_badge.set_markup(markup)
        if info.newer_count > 1:
            tip = t(
                "ui.main.update_tip_many",
                current=info.current_version, count=info.newer_count,
            )
        else:
            tip = t("ui.main.update_tip_one", current=info.current_version)
        self.update_badge.set_tooltip_text(tip)
        self.update_badge.show()

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
            t("ui.main.notes_title_one", version=info.latest_version)
            if info.newer_count <= 1
            else t("ui.main.notes_title_many", count=info.newer_count)
        )
        dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dlg.add_button(t("ui.main.open_github"), Gtk.ResponseType.APPLY)
        dlg.add_button(t("ui.main.close"), Gtk.ResponseType.CLOSE)
        dlg.set_default_size(680, 520)

        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        header = Gtk.Label(xalign=0)
        header.set_markup(t(
            "ui.main.notes_header",
            current=GLib.markup_escape_text(info.current_version),
            latest=GLib.markup_escape_text(info.latest_version),
        ))
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
        body = info.body.strip() or t("ui.main.notes_empty")
        tv.get_buffer().set_text(body)
        scrolled.add(tv)
        box.pack_start(scrolled, True, True, 0)

        dlg.show_all()
        response = dlg.run()
        if response == Gtk.ResponseType.APPLY:
            self._open_url_in_user_session(info.html_url)
        dlg.destroy()

    def _show_credits_dialog(self) -> None:
        """TiHA'nın omzunda durduğu açık kaynak projeler, veri kümeleri
        ve ekipler için teşekkür diyaloğu — bölümlü, ızgara tabanlı,
        her satırda lisans rozetiyle."""
        dlg = Gtk.Dialog(title=t("ui.credits.title"), transient_for=self, modal=True)
        dlg.add_button(t("ui.main.close"), Gtk.ResponseType.CLOSE)
        dlg.set_default_size(820, 660)

        content = dlg.get_content_area()
        content.set_spacing(0)
        content.set_margin_top(0)
        content.set_margin_bottom(0)
        content.set_margin_start(0)
        content.set_margin_end(0)

        # --- Başlık bandı ---
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        header.set_margin_top(18)
        header.set_margin_bottom(14)
        header.set_margin_start(22)
        header.set_margin_end(22)

        title_lbl = Gtk.Label(xalign=0)
        title_lbl.set_markup(
            '<span size="xx-large" weight="bold">'
            f'{GLib.markup_escape_text(t("ui.credits.title"))}</span>'
        )
        header.pack_start(title_lbl, False, False, 0)

        subtitle = Gtk.Label(xalign=0)
        subtitle.set_line_wrap(True)
        subtitle.set_max_width_chars(96)
        subtitle.set_markup(
            '<span foreground="#4b5563">'
            f'{GLib.markup_escape_text(t("ui.credits.subtitle"))}</span>'
        )
        header.pack_start(subtitle, False, False, 0)
        content.pack_start(header, False, False, 0)

        # İnce ayırıcı çizgi
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        content.pack_start(sep, False, False, 0)

        # --- Bölümler için ScrolledWindow ---
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_shadow_type(Gtk.ShadowType.NONE)
        scrolled.set_vexpand(True)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        body.set_margin_top(18)
        body.set_margin_bottom(20)
        body.set_margin_start(22)
        body.set_margin_end(22)

        for sect in _CREDITS_SECTIONS:
            body.pack_start(self._credits_section(sect), False, False, 0)

        scrolled.add(body)
        content.pack_start(scrolled, True, True, 0)

        dlg.show_all()
        dlg.run()
        dlg.destroy()

    def _credits_section(self, section: dict) -> Gtk.Widget:
        """Bir bölümü (başlık + üç sütunlu ızgara) render eder."""
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        head = Gtk.Label(xalign=0)
        head.set_markup(
            f'<span size="large" weight="bold">'
            f'{GLib.markup_escape_text(section["title"])}</span>'
        )
        wrap.pack_start(head, False, False, 0)

        # İnce alt çizgi
        thin = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        thin.set_margin_bottom(4)
        wrap.pack_start(thin, False, False, 0)

        grid = Gtk.Grid()
        grid.set_column_spacing(16)
        grid.set_row_spacing(8)
        for row_idx, (name, url, license_txt, desc) in enumerate(section["items"]):
            # 0. sütun: ad — varsa bağlantı, yoksa kalın metin
            name_lbl = Gtk.Label(xalign=0)
            name_lbl.set_use_markup(True)
            name_lbl.set_track_visited_links(False)
            name_lbl.set_valign(Gtk.Align.START)
            # Tüm adlar aynı font, aynı punto — Pango'nun standart link
            # stili (mavi + altı çizili). Bold/span sarmalı yok, böylece
            # eta-otp-lock ile Vestel Faz 2 aynı görünür.
            if url:
                name_lbl.set_markup(
                    f'<a href="{GLib.markup_escape_text(url)}">'
                    f'{GLib.markup_escape_text(name)}</a>'
                )
                name_lbl.connect("activate-link", self._on_credits_link)
            else:
                name_lbl.set_markup(GLib.markup_escape_text(name))
            grid.attach(name_lbl, 0, row_idx, 1, 1)

            # 1. sütun: lisans rozeti — küçük, monospace, gri arka planlı
            lic_lbl = Gtk.Label(xalign=0)
            lic_lbl.set_valign(Gtk.Align.START)
            lic_lbl.set_markup(
                f'<span background="#eef2f7" foreground="#334155" '
                f'font_desc="Monospace 9"> '
                f'{GLib.markup_escape_text(license_txt)}'
                f' </span>'
            )
            grid.attach(lic_lbl, 1, row_idx, 1, 1)

            # 2. sütun: açıklama — sarılabilen ana metin
            desc_lbl = Gtk.Label(xalign=0)
            desc_lbl.set_line_wrap(True)
            desc_lbl.set_max_width_chars(56)
            desc_lbl.set_hexpand(True)
            desc_lbl.set_valign(Gtk.Align.START)
            desc_lbl.set_markup(GLib.markup_escape_text(desc))
            grid.attach(desc_lbl, 2, row_idx, 1, 1)

        wrap.pack_start(grid, False, False, 0)
        return wrap

    def _on_credits_link(self, _label, uri: str) -> bool:
        """Credits diyaloğundaki bağlantıları kullanıcının X oturumundaki
        tarayıcıda açar (TiHA root ile çalışır — xdg-open doğrudan
        işlemez)."""
        self._open_url_in_user_session(uri)
        return True  # Gtk'nun kendi xdg-open denemesini bastır.

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

    def _load_icon(self) -> None:
        """TiHA'nın kendi simgesi (data/tiha.svg): yapılandırılıp klonlanan
        tahta destesi. TiHA kurulmadan, indirildiği dizinden çalıştığı için
        simge tema adıyla değil dosyadan yüklenir."""
        try:
            pixbufs = [
                GdkPixbuf.Pixbuf.new_from_file_at_size(str(ICON_PATH), size, size)
                for size in ICON_SIZES
            ]
        except GLib.Error as exc:
            log.warning("Uygulama simgesi yüklenemedi (%s): %s", ICON_PATH, exc)
            self.set_icon_name(FALLBACK_ICON_NAME)
            return
        self.set_icon_list(pixbufs)
        # Diyaloglar (hakkında, uyarılar) da aynı simgeyi taşısın.
        Gtk.Window.set_default_icon_list(pixbufs)

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

        title = Gtk.Label(label=t("ui.main.sidebar_title"), xalign=0)
        title.get_style_context().add_class("tiha-sidebar-title")
        subtitle = Gtk.Label(label=t("ui.main.sidebar_subtitle"), xalign=0)
        subtitle.get_style_context().add_class("tiha-sidebar-subtitle")
        # Güncelleme rozeti "TiHA" yazısının sağında, aynı taban çizgisinde;
        # yeni sürüm yoksa gizli. Async kontrol sonucu geldiğinde dolar.
        self.update_badge = Gtk.Label(xalign=0)
        self.update_badge.set_track_visited_links(False)
        self.update_badge.set_no_show_all(True)
        self.update_badge.get_style_context().add_class("tiha-update-badge")
        self.update_badge.set_valign(Gtk.Align.BASELINE)
        # Tarayıcıya gitmek yerine inline "Yenilikler" diyaloğunu aç.
        self.update_badge.connect("activate-link", self._on_update_badge_link)
        title.set_valign(Gtk.Align.BASELINE)
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        title_row.pack_start(title, False, False, 0)
        title_row.pack_start(self.update_badge, False, False, 0)

        title_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title_col.pack_start(title_row, False, False, 0)
        title_col.pack_start(subtitle, False, False, 0)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header.pack_start(title_col, True, True, 0)
        sidebar_outer.pack_start(header, False, False, 0)

        # Simge, iki satırlık metnin (TiHA + alt başlık) toplam yüksekliğini
        # geçmesin diye boyutu fontun gerçek satır yüksekliğinden hesaplanır.
        text_px = (
            title.get_layout().get_pixel_size()[1]
            + subtitle.get_layout().get_pixel_size()[1]
        )
        icon_px = max(24, min(48, text_px or 36))
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(
                str(ICON_PATH), icon_px, icon_px,
            )
        except GLib.Error as exc:
            log.warning("Kenar çubuğu simgesi yüklenemedi: %s", exc)
        else:
            icon = Gtk.Image.new_from_pixbuf(pixbuf)
            icon.get_style_context().add_class("tiha-sidebar-icon")
            # Metin bloğunun CSS dolgularıyla aynı hizada dursun: üstte
            # başlığın 8px, altta alt başlığın 10px dolgusu.
            icon.set_valign(Gtk.Align.START)
            icon.set_margin_top(8)
            icon.set_margin_start(12)
            header.pack_start(icon, False, False, 0)
            header.reorder_child(icon, 0)

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

        # "Emeği Geçenler" — TiHA'nın omuzunda durduğu açık kaynak projeler
        # ve topluluklara referans. Tıklanınca modal diyalog açılır.
        credits_lbl = Gtk.Label(xalign=0)
        credits_lbl.set_markup(
            '<a href="tiha:credits">'
            f'{GLib.markup_escape_text(t("ui.credits.link"))}</a>'
        )
        credits_lbl.set_use_markup(True)
        credits_lbl.set_track_visited_links(False)
        credits_lbl.get_style_context().add_class("tiha-author-web")
        credits_lbl.connect(
            "activate-link", lambda *_: (self._show_credits_dialog(), True)[1],
        )
        author_box.pack_start(credits_lbl, False, False, 0)

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

        # Sayfa hazırlanırken görünen "Yükleniyor…" katmanı. Kaydırılabilir
        # alanın DIŞINDA durur: sayfalar ortak bir Stack'te olduğu için orada
        # en uzun sayfanın yüksekliğine göre ortalanır, görünmezdi.
        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        loading.set_halign(Gtk.Align.CENTER)
        loading.set_valign(Gtk.Align.CENTER)
        self.loading_spinner = Gtk.Spinner()
        self.loading_spinner.set_size_request(32, 32)
        loading.pack_start(self.loading_spinner, False, False, 0)
        loading_lbl = Gtk.Label(label=t("ui.main.loading"))
        loading_lbl.get_style_context().add_class("tiha-rationale")
        loading.pack_start(loading_lbl, False, False, 0)

        self.content_stack = Gtk.Stack()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_transition_duration(120)
        self.content_stack.add_named(self.content_scroll, "content")
        self.content_stack.add_named(loading, "loading")
        self.content_stack.set_visible_child_name("content")
        right.pack_start(self.content_stack, True, True, 0)
        # Gezinme belirteci: üst üste tıklamalarda yalnız son sayfanın
        # hazırlanan durumu uygulanır.
        self._nav_token = 0
        self._loading_timer = 0

        # Aksiyon çubuğu
        self.action_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.action_bar.get_style_context().add_class("tiha-actions")

        self.btn_back = Gtk.Button(label=t("ui.main.btn_back"))
        self.btn_back.connect("clicked", self._on_back)
        self.action_bar.pack_start(self.btn_back, False, False, 0)

        # Apply öncesi ne olacağını anlatan ipucu
        self.lbl_apply_hint = Gtk.Label(label="", xalign=1)
        self.lbl_apply_hint.set_line_wrap(True)
        self.lbl_apply_hint.set_max_width_chars(80)
        self.lbl_apply_hint.set_no_show_all(True)
        self.lbl_apply_hint.get_style_context().add_class("tiha-apply-hint")
        self.action_bar.pack_start(self.lbl_apply_hint, True, True, 0)

        self.btn_apply = Gtk.Button(label=t("ui.main.btn_apply"))
        self.btn_apply.get_style_context().add_class("suggested-action")
        self.btn_apply.set_no_show_all(True)
        self.btn_apply.connect("clicked", self._on_apply)
        self.action_bar.pack_start(self.btn_apply, False, False, 0)

        self.btn_next = Gtk.Button(label=t("ui.main.btn_next"))
        self.btn_next.connect("clicked", self._on_next)
        self.action_bar.pack_start(self.btn_next, False, False, 0)

        # Uzun ipucu iki-üç satıra sarınca düğmeler onunla birlikte
        # dikeyde uzamasın; ortada doğal boylarında dursunlar.
        for btn in (self.btn_back, self.btn_apply, self.btn_next):
            btn.set_valign(Gtk.Align.CENTER)

        right.pack_start(self.action_bar, False, False, 0)

        paned.pack2(right, resize=True, shrink=False)
        paned.set_position(240)

    def _build_welcome(self) -> None:
        page = WelcomePage()
        self.pages.append(page)
        self.stack.add_named(page, "welcome")
        self._add_sidebar_entry(t("ui.main.sidebar_welcome"))

    def _build_module_pages(self) -> None:
        # Karşılama bir adım değildir; modüller 1'den başlayarak numaralandırılır.
        for idx, module in enumerate(self.modules, start=1):
            try:
                page = ModulePage(module, self.journal)
            except Exception as exc:
                # Bir adımın sayfasındaki hata bütün sihirbazı düşürmesin:
                # o adımın yerine hatayı anlatan bir sayfa konur, diğer
                # adımlar kullanılmaya devam eder.
                log.exception("Adım sayfası kurulamadı: %s", module.id)
                page = self._broken_page(module, exc)
            else:
                # Apply tamamlandığında ileri/geri kapısını + sidebar ikonlarını tazele.
                def _after_apply(*_a, _mid=module.id, **_kw):
                    self._update_navigation_gate()
                    self._refresh_sidebar_status()
                page.post_apply_callback = _after_apply
            self.pages.append(page)
            self.stack.add_named(page, module.id)
            sidebar_label = module.sidebar_title or module.title
            self._add_sidebar_entry(f"{idx}. {sidebar_label}", module_id=module.id)

    @staticmethod
    def _broken_page(module, exc: Exception) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(18)
        box.set_margin_start(22)
        box.set_margin_end(22)
        heading = Gtk.Label(label=module.title, xalign=0)
        heading.get_style_context().add_class("tiha-heading")
        box.pack_start(heading, False, False, 0)
        msg = Gtk.Label(
            label=t("ui.main.page_failed", error=f"{type(exc).__name__}: {exc}"),
            xalign=0,
        )
        msg.set_line_wrap(True)
        msg.set_selectable(True)
        msg.get_style_context().add_class("tiha-experimental-banner")
        box.pack_start(msg, False, False, 0)
        return box

    def _build_summary(self) -> None:
        page = SummaryPage(
            self.journal, self.modules,
            on_export_preset=self._on_export_preset_clicked,
        )
        self.pages.append(page)
        self.stack.add_named(page, "summary")
        self._add_sidebar_entry(t("ui.main.sidebar_summary"))

    def _add_sidebar_entry(self, label: str, *, module_id: str | None = None) -> None:
        row = Gtk.ListBoxRow()
        row.get_style_context().add_class("tiha-step")
        # Box: durum ikonu (sol) + adım adı (genişler)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        # Sidebar durum ikonu: sadece uygulanan (✓) ve hatalı (⚠) adımlarda
        # simge çıksın; bekleyen / geri alınan adımlarda boş dursun.
        status = Gtk.Label(label="", xalign=0.5)
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

    def _on_journal_changed(self) -> None:
        # Kayıt arka plan iş parçacığından da yazılabilir; GTK işi ana döngüde.
        def _do() -> bool:
            self._update_navigation_gate()
            self._refresh_sidebar_status()
            return False
        GLib.idle_add(_do)

    def _refresh_sidebar_status(self) -> None:
        """Journal'a bakarak her sidebar satırının durum ikonunu günceller.
        Welcome / Özet sayfaları için module_id None — boş kalır."""
        latest = self.journal.latest_per_module()
        by_id = {m.id: m for m in self.modules}
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
                status_lbl.set_text("")
                status_lbl.set_tooltip_text("")
            elif entry.status == "applied" and _result_is_off(by_id.get(module_id), entry):
                # Kutular boş bırakılıp uygulandı: özellik kapalı, işaret yok.
                status_lbl.set_text("")
                status_lbl.set_tooltip_text(t("ui.main.status_off", summary=entry.summary))
            elif entry.status == "applied":
                status_lbl.set_text("✓")
                ctx.add_class("tiha-step-status-ok")
                status_lbl.set_tooltip_text(t("ui.main.status_applied", summary=entry.summary))
            elif entry.status == "failed":
                status_lbl.set_text("⚠")
                ctx.add_class("tiha-step-status-fail")
                status_lbl.set_tooltip_text(t("ui.main.status_failed", summary=entry.summary))
            elif entry.status == "skipped":
                # Bu tahtada uygulanamaz (ör. donanım desteklenmiyor):
                # hata değil, simge yok; nedeni ipucunda.
                status_lbl.set_text("")
                status_lbl.set_tooltip_text(t("ui.main.status_skipped", summary=entry.summary))
            elif entry.status == "undone":
                status_lbl.set_text("")
                status_lbl.set_tooltip_text("")
            else:
                status_lbl.set_text("")

    # ---- Navigasyon ------------------------------------------------------

    def _on_sidebar_row_activated(self, _lb: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if row is None:
            return
        # ``sync_sidebar`` argümanı kaldırıldı: hem klavye/buton hem
        # fare tıklamasıyla gelinen yolda aynı görsel akış (aktif satır
        # CSS sınıfı) uygulansın. ``select_row`` zaten seçili satıra
        # çağrılırsa no-op olur, ekstra etkisi yok.
        self._show_page_index(row.get_index())

    # Sayfa durumu bu süreden uzun sürerse "Yükleniyor…" gösterilir; hızlı
    # sayfalarda gösterge yanıp sönmesin.
    LOADING_DELAY_MS = 120

    def _show_page_index(self, index: int) -> None:
        """Sayfaya geçer.

        Tıklanan satır ve alt çubuk HEMEN güncellenir. Sayfanın durumu
        (önizleme, şartlı alanlar, Özet raporu…) arka plan iş parçacığında
        hesaplanır; kısa sürede bitmezse içerik alanında ortalanmış
        "Yükleniyor…" görünür, hazır olunca içerik basılır. Eskiden bütün
        bu iş ana iş parçacığında yapılıyor, arayüz 1-2 sn donuyordu ve
        tıklama hiç algılanmamış gibi görünüyordu.
        """
        index = max(0, min(index, len(self.pages) - 1))
        self.current_index = index
        page = self.pages[index]
        self._nav_token += 1
        token = self._nav_token

        # 1) Anında geri bildirim: seçili satır + aksiyon çubuğu
        self._mark_sidebar_row(index)
        self._update_action_bar(page, index)

        # 2) Ağır durum hesabı arka planda
        self._cancel_loading_timer()
        collect = getattr(page, "collect_view_state", None)
        if not callable(collect):
            self._present_page(page, token, None)
            return
        self._loading_timer = GLib.timeout_add(
            self.LOADING_DELAY_MS, self._show_loading, token,
        )

        def worker() -> None:
            try:
                state = collect()
            except Exception as exc:  # hesap hatası sayfayı düşürmesin
                log.warning("Sayfa durumu hazırlanamadı: %s", exc)
                state = None
            GLib.idle_add(self._present_page, page, token, state)

        threading.Thread(target=worker, daemon=True).start()

    def _show_loading(self, token: int) -> bool:
        self._loading_timer = 0
        if token == self._nav_token:
            self.loading_spinner.start()
            self.content_stack.set_visible_child_name("loading")
        return False

    def _cancel_loading_timer(self) -> None:
        if self._loading_timer:
            GLib.source_remove(self._loading_timer)
            self._loading_timer = 0

    def _present_page(self, page, token: int, state) -> bool:
        """Hazırlanan durumu sayfaya yazar ve sayfayı gösterir."""
        if token != self._nav_token:
            return False  # kullanıcı bu arada başka adıma geçti
        self._cancel_loading_timer()
        apply_state = getattr(page, "apply_view_state", None)
        if callable(apply_state) and state is not None:
            try:
                apply_state(state)
            except Exception as exc:
                log.warning("Sayfa durumu uygulanamadı: %s", exc)
        self.stack.set_visible_child(page)
        # İçerik scroll'u en başa çek
        adj = self.content_scroll.get_vadjustment()
        if adj:
            adj.set_value(0)
        self.content_stack.set_visible_child_name("content")
        self.loading_spinner.stop()

        if isinstance(page, ModulePage):
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
            # Auto-apply modüllerini (salt-okunur) bir kez kendi tetikle
            if page.module.auto_apply and not page._auto_applied:
                page._auto_applied = True
                GLib.idle_add(page.run_apply)

        self._update_navigation_gate()
        return False

    def _mark_sidebar_row(self, index: int) -> None:
        """Aktif satır görsel vurgusu — her giriş yolunda (sidebar
        tıklaması, İleri/Geri, programatik) tutarlı kalsın."""
        for i in range(len(self.pages)):
            old_row = self.sidebar_list.get_row_at_index(i)
            if old_row:
                old_row.get_style_context().remove_class("tiha-step-active")
        row = self.sidebar_list.get_row_at_index(index)
        if row is not None:
            self.sidebar_list.select_row(row)
            row.get_style_context().add_class("tiha-step-active")

    def _update_action_bar(self, page, index: int) -> None:
        # Aksiyon çubuğu görünürlüğü (Apply ve ipucu yalnızca manuel modüllerde)
        is_module = isinstance(page, ModulePage)
        show_apply = is_module and not page.module.auto_apply
        self.btn_apply.set_visible(show_apply)
        hint = page.module.apply_hint if is_module else ""
        self.lbl_apply_hint.set_text(t("ui.main.apply_hint", hint=hint) if (show_apply and hint) else "")
        self.lbl_apply_hint.set_visible(bool(show_apply and hint))
        # Özet sayfasında "Bitir" gösterelim
        is_last = index >= len(self.pages) - 1
        self.btn_next.set_label(t("ui.main.btn_finish") if is_last else t("ui.main.btn_next"))

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
