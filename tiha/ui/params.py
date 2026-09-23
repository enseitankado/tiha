"""Modül başına kullanıcıdan alınacak parametre şemaları.

Alan tipleri: ``text``, ``password``, ``number``, ``textarea``, ``select``,
``bool``, ``spin``, ``button``, ``file``, değeri modülden gelen ve
kullanıcının değiştiremediği ``readonly`` (değerini ``default_from``
sağlar) ve değer taşımayan bölüm başlığı ``heading``. ``bool`` alanı
``enables`` listesi taşıyabilir: kutu işaretsizken listedeki alanlar
pasifleşir. Kısa değerli alanlar (text, password, number, readonly)
satır boyu uzamaz; genişlik karakter cinsinden ``width`` ile verilir.
Ek olarak ``deselects`` (bu kutu işaretlenince
listedeki kutuların işareti kaldırılır) ve simetriği ``selects``
(bu kutu işaretlenince listedeki ön-koşul kutuları da otomatik
işaretlenir) bayrakları da desteklenir.
"""

from __future__ import annotations

from ..core.i18n import t
from ..modules.m16_grub_protection import GRUB_PASSWORD_CHARS

PARAMS_SCHEMA: dict[str, list[dict]] = {
    "m01_initial_passwords": [
        {
            "key": "root_password",
            "label": t("m01.params.root_password.label"),
            "type": "password",
            "show_toggle": True,
            "strength_below": True,
            "required": False,
            "help": t("m01.params.root_password.help"),
        },
        {
            "key": "admin_password",
            "label": t("m01.params.admin_password.label"),
            "type": "password",
            "show_toggle": True,
            "strength_below": True,
            "required": False,
            "help": t("m01.params.admin_password.help"),
        },
        {
            "key": "teacher_password",
            "label": t("m01.params.teacher_password.label"),
            "type": "password",
            "show_toggle": True,
            "strength_below": True,
            "required": False,
            "help": t("m01.params.teacher_password.help"),
        },
        {
            "key": "reserve_count",
            "label": t("m01.params.reserve_count.label"),
            "type": "spin",
            "required": False,
            "default": "0",
            # Sistemde ogretmen01 … ogretmenNN varsa kutu NN ile dolu
            # gelsin; adım yeniden uygulandığında yönetici farkında
            # olmadan yeni hesap açmaz.
            "default_from": "suggested_reserve_count",
            "min": 0,
            "max": 999,
            "step": 1,
            "help": t("m01.params.reserve_count.help"),
        },
        {
            "key": "branch_accounts",
            "label": t("m01.params.branch_accounts.label"),
            "type": "branch_accounts",
            "required": False,
            # Daha önce açılmış branş hesapları ve okul türü işaretli gelsin.
            "default_from": "current_branch_accounts",
            "help": t("m01.params.branch_accounts.help"),
            "help_more": t("m01.params.branch_accounts.help_more"),
            "help_more_label": t("ui.pages.help_more_details"),
        },
        {
            "key": "remove_student",
            "label": t("m01.params.remove_student.label"),
            "type": "button",
            "action": "remove_student_user_action",
            "style": "destructive",
            "help": t("m01.params.remove_student.help"),
        },
        {
            # Toplu PIN adımındaki "Fazladan Hesapları Sil" düğmesinin
            # buradaki aynası. Aynı m03 aksiyonuna delege eder — hem
            # varsayılan dışı hesapları hem karşılığı kalmamış PIN
            # kayıtlarını temizler. Yerel hesaplar akışında da işleve
            # ihtiyaç duyulduğu için burada da erişilebilir olmalı.
            "key": "remove_extra_users",
            "label": t("m03.params.remove_extra_users.label"),
            "label_from": "label_remove_extra_users",
            "type": "button",
            "action": "remove_extra_users_action",
            "style": "destructive",
            "visible_when": "can_remove_extra_users",
            "confirm": {
                "title": t("m03.params.remove_extra_users.confirm_title"),
                "message": t("m03.params.remove_extra_users.confirm_message"),
            },
            "help": t("m03.params.remove_extra_users.help"),
        },
    ],
    "m03_otp_secrets": [
        {
            "key": "teacher_names",
            "label": t("m03.params.teacher_names.label"),
            "type": "textarea",
            "required": False,
            "placeholder": t("m03.params.teacher_names.placeholder"),
            "help": t("m03.params.teacher_names.help"),
        },
        {
            "key": "include_etapadmin",
            "label": t("m03.params.include_etapadmin.label"),
            "type": "bool",
            "required": False,
            "default": "True",
            "help": t("m03.params.include_etapadmin.help"),
        },
        {
            "key": "include_other_teachers",
            "label": t("m03.params.include_other_teachers.label"),
            "type": "bool",
            "required": False,
            "default": "True",
            "help": t("m03.params.include_other_teachers.help"),
        },
        {
            "key": "make_group_pin",
            "label": t("m03.params.make_group_pin.label"),
            "type": "bool",
            "required": False,
            "default": "False",
            # PAM grup-PIN'i yalnız gruba üye kullanıcılara kabul ediyor;
            # öğretmen hesapları her uygulamada gruba alınır.
            "help": t("m03.params.make_group_pin.help"),
        },
        {
            "key": "purge_all_secrets",
            "label": t("m03.params.purge_all_secrets.label"),
            "label_from": "label_purge_all_secrets",
            "type": "button",
            "action": "purge_all_secrets_action",
            "style": "destructive",
            "visible_when": "can_purge_secrets",
            "confirm": {
                "title": t("m03.params.purge_all_secrets.confirm_title"),
                "message": t("m03.params.purge_all_secrets.confirm_message"),
            },
            "help": t("m03.params.purge_all_secrets.help"),
        },
        # "Fazladan hesapları sil" yalnız Yerel hesaplar adımında
        # (hesap işi orada); aksiyon bu modülde kalır, m01 ona delege eder.
    ],
    "m05_samba_share": [
        {
            "key": "samba_user",
            "width": 20,
            "label": t("m05.params.samba_user.label"),
            "type": "text",
            "required": True,
            "default": "root",
        },
        {
            "key": "samba_password",
            "label": t("m05.params.samba_password.label"),
            "type": "password",
            "required": True,
        },
    ],
    "m06_remote_syslog": [
        {
            "key": "syslog_host",
            "label": t("m06.params.syslog_host.label"),
            "type": "text",
            "required": True,
        },
        {
            "key": "syslog_port",
            "label": t("m06.params.syslog_port.label"),
            "type": "number",
            "required": False,
            "default": "514",
        },
        {
            "key": "syslog_proto",
            "label": t("m06.params.syslog_proto.label"),
            "type": "select",
            "required": False,
            "default": "tcp",
            "options": ["tcp", "udp"],
        },
        {
            "key": "log_profile",
            "label": t("m06.params.log_profile.label"),
            "type": "select",
            "required": False,
            "default": t("m06.params.log_profile.opt_bakim"),
            "options": [
                t("m06.params.log_profile.opt_bakim"),
                t("m06.params.log_profile.opt_kapsamli"),
                t("m06.params.log_profile.opt_guvenlik"),
            ],
            "help": t("m06.params.log_profile.help"),
        },
        {
            "key": "install_smart_monitoring",
            "label": t("m06.params.install_smart_monitoring.label"),
            "type": "bool",
            "required": False,
            "default": "True",
            "help": t("m06.params.install_smart_monitoring.help"),
        },
        {
            "key": "install_node_exporter",
            "label": t("m06.params.install_node_exporter.label"),
            "type": "bool",
            "required": False,
            "default": "False",
            "help": t("m06.params.install_node_exporter.help"),
        },
        {
            "key": "node_exporter_listen",
            "label": t("m06.params.node_exporter_listen.label"),
            "type": "text",
            "required": False,
            "default": ":9100",
            "enable_when_field": "install_node_exporter",
            "help": t("m06.params.node_exporter_listen.help"),
        },
        {
            "key": "test_log_server",
            "label": t("m06.params.test_log_server.label"),
            "type": "button",
            "action": "test_log_server_action",
            "help": t("m06.params.test_log_server.help"),
        },
    ],
    "m07_time_sync": [
        {
            "key": "ntp_servers",
            "width": 44,
            "label": t("m07.params.ntp_servers.label"),
            "type": "text",
            "required": False,
            "default": "0.tr.pool.ntp.org 1.tr.pool.ntp.org",
            "help": t("m07.params.ntp_servers.help"),
        },
        {
            "key": "test_ntp_servers",
            "label": t("m07.params.test_ntp_servers.label"),
            "type": "button",
            "action": "test_ntp_servers_action",
            "help": t("m07.params.test_ntp_servers.help"),
        },
        {
            "key": "ntp_fallback",
            "width": 44,
            "label": t("m07.params.ntp_fallback.label"),
            "type": "text",
            "required": False,
            "default": "time.cloudflare.com pool.ntp.org",
        },
        {
            "key": "timezone",
            "width": 20,
            "label": t("m07.params.timezone.label"),
            "type": "text",
            "required": False,
            "default": "Europe/Istanbul",
        },
    ],
    "m08_hostname": [
        {
            "key": "template",
            "width": 20,
            "label": t("m08.params.template.label"),
            "type": "text",
            "required": False,
            "default": "etap-image",
            "help": t("m08.params.template.help"),
        },
        {
            "key": "prefix",
            "width": 16,
            "label": t("m08.params.prefix.label"),
            "type": "text",
            "required": False,
            "default": "etap",
            "help": t("m08.params.prefix.help"),
        },
    ],
    "m11_power_management": [
        {
            "key": "auto_enabled",
            "label": t("m11.params.auto_enabled.label"),
            "type": "bool",
            "required": False,
            "default": "False",
            "default_from": "auto_shutdown_active",
            "help": t("m11.params.auto_enabled.help"),
        },
        {
            "key": "auto_hour",
            "label": t("m11.params.auto_hour.label"),
            "type": "spin",
            "required": False,
            "default": "22",
            "min": 0,
            "max": 23,
            "step": 1,
            "help": t("m11.params.auto_hour.help"),
        },
        {
            "key": "auto_minute",
            "label": t("m11.params.auto_minute.label"),
            "type": "spin",
            "required": False,
            "default": "0",
            "min": 0,
            "max": 59,
            "step": 1,
            "help": t("m11.params.auto_minute.help"),
        },
        {
            "key": "idle_enabled",
            "label": t("m11.params.idle_enabled.label"),
            "type": "bool",
            "required": False,
            "default": "True",
            "default_from": "idle_shutdown_active",
            "help": t("m11.params.idle_enabled.help"),
        },
        {
            "key": "idle_minute",
            "label": t("m11.params.idle_minute.label"),
            "type": "spin",
            "required": False,
            "default": "15",
            "min": 1,
            "max": 180,
            "step": 1,
            "help": t("m11.params.idle_minute.help"),
        },
        {
            "key": "countdown_seconds",
            "label": t("m11.params.countdown_seconds.label"),
            "type": "spin",
            "required": False,
            "default": "120",
            # Yüklü service.py'de zaten bir COUNTDOWN_SECONDS varsa
            # kutu o değerle açılsın — kullanıcı farkında olmadan
            # eski süreyi yeniden yazmaz.
            "default_from": "suggested_countdown_seconds",
            # Uyarı penceresi iki modda da çıkar; ikisi de kapalıyken
            # süre anlamsız olduğu için kutu pasifleşir.
            "enable_when_any": ["auto_enabled", "idle_enabled"],
            "min": 30,
            "max": 600,
            "step": 10,
            "help": t("m11.params.countdown_seconds.help"),
        },
        {
            "key": "exempt_macs",
            "label": t("m11.params.exempt_macs.label"),
            "type": "textarea",
            "required": False,
            "default": "",
            # Yeniden girişte kurulu servisteki liste gelsin.
            "default_from": "current_exempt_macs",
            "placeholder": t("m11.params.exempt_macs.placeholder"),
            # Kapanma modu seçilmemişse muaf tutulacak bir şey yok: kutu pasif.
            "enable_when_any": ["auto_enabled", "idle_enabled"],
            "help": t("m11.params.exempt_macs.help"),
        },
    ],
    "m17_performance": [
        {"label": t("m17.params.heading_session"), "type": "heading"},
        {
            "key": "kill_user_processes",
            # Kutu sistemin durumunu gösterir; işareti kaldırıp uygulamak
            # ayarı kaldırır (m17 apply).
            "default_from": "session_cleanup_active",
            "label": t("m17.params.kill_user_processes.label"),
            "type": "bool",
            "default": "True",
            "help": t("m17.params.kill_user_processes.help"),
        },
        {"label": t("m17.params.heading_light_mode"), "type": "heading"},
        {
            "key": "light_mode_enabled",
            "label": t("m17.params.light_mode_enabled.label"),
            "type": "bool",
            "default": "False",
            "default_from": "light_mode_active",
            "enables": [
                "lm_effects", "lm_compositor", "lm_thumbnails",
                "lm_directory_counts", "lm_app_monitoring",
                "lm_low_resolution", "lm_low_refresh_rate",
            ],
            "help": t("m17.params.light_mode_enabled.help"),
        },
        {
            "key": "lm_effects",
            "label": t("m17.params.lm_effects.label"),
            "type": "bool",
            "default": "True",
            "default_from": "lm_effects_active",
            "help": t("m17.params.lm_effects.help"),
        },
        {
            "key": "lm_compositor",
            "label": t("m17.params.lm_compositor.label"),
            "type": "bool",
            "default": "True",
            "default_from": "lm_compositor_active",
            "help": t("m17.params.lm_compositor.help"),
        },
        {
            "key": "lm_thumbnails",
            "label": t("m17.params.lm_thumbnails.label"),
            "type": "bool",
            "default": "True",
            "default_from": "lm_thumbnails_active",
            "help": t("m17.params.lm_thumbnails.help"),
        },
        {
            "key": "lm_directory_counts",
            "label": t("m17.params.lm_directory_counts.label"),
            "type": "bool",
            "default": "True",
            "default_from": "lm_directory_counts_active",
        },
        {
            "key": "lm_app_monitoring",
            "label": t("m17.params.lm_app_monitoring.label"),
            "type": "bool",
            "default": "True",
            "default_from": "lm_app_monitoring_active",
        },
        {
            "key": "lm_low_resolution",
            "label": t("m17.params.lm_low_resolution.label"),
            "type": "bool",
            "default": "False",
            "default_from": "lm_low_resolution_active",
            "help": t("m17.params.lm_low_resolution.help"),
        },
        {
            "key": "lm_low_refresh_rate",
            "label": t("m17.params.lm_low_refresh_rate.label"),
            "type": "bool",
            "default": "False",
            "default_from": "lm_low_refresh_rate_active",
            "help": t("m17.params.lm_low_refresh_rate.help"),
        },
        {"label": t("m17.params.heading_cursor"), "type": "heading"},
        {
            "key": "cursor_xorg_fix",
            "label": t("m17.params.cursor_xorg_fix.label"),
            # Deneysel; hafif moddan bağımsız. Tahtada kurulu olan seçili
            # gelir, "Kapalı" seçilip uygulanırsa kaldırılır.
            "type": "select",
            "required": False,
            "default": t("m17.params.cursor_xorg_fix.opt_off"),
            "default_from": "current_cursor_xorg_choice",
            "options": [
                t("m17.params.cursor_xorg_fix.opt_off"),
                t("m17.params.cursor_xorg_fix.opt_modesetting"),
                t("m17.params.cursor_xorg_fix.opt_swcursor"),
            ],
            "help": t("m17.params.cursor_xorg_fix.help"),
            "help_folded": True,
        },
        {
            "key": "cursor_refresh_service",
            "label": t("m17.params.cursor_refresh_service.label"),
            "type": "bool",
            "default": "False",
            # Kurulu servis işaretli gelir; işaret kaldırılıp uygulanırsa sökülür.
            "default_from": "cursor_service_active",
            "help": t("m17.params.cursor_refresh_service.help"),
            "help_folded": True,
        },
    ],
    "m14_bios_password": [
        {
            "key": "supervisor_password",
            "width": 16,
            "label": t("m14.params.supervisor_password.label"),
            # Düz metin — kullanıcının ne yazdığını görmesi gerekir
            # (BIOS yalnız BÜYÜK A-Z 0-9 kabul eder; 'I' yasak — '1' ile
            # karışıyor. UI input mask ile zorlanır, apply'da yeniden
            # doğrulanır). Donanım desteklenmiyorsa alan gizlenir.
            "type": "text",
            "required": False,
            "placeholder": t("m14.params.supervisor_password.placeholder"),
            "visible_when": "is_hardware_supported_cached",
            "help": t("m14.params.supervisor_password.help"),
        },
        {
            "key": "protection_mode",
            "label": t("m14.params.protection_mode.label"),
            "type": "select",
            "required": False,
            "default": t("m14.params.protection_mode.opt_setup"),
            "options": [
                t("m14.params.protection_mode.opt_setup"),
                t("m14.params.protection_mode.opt_always"),
            ],
            "visible_when": "is_hardware_supported_cached",
            "help": t("m14.params.protection_mode.help"),
        },
        {
            "key": "read_current",
            "label": t("m14.params.read_current.label"),
            "type": "button",
            "action": "read_current_supervisor_action",
            "visible_when": "is_hardware_supported_cached",
            "help": t("m14.params.read_current.help"),
        },
        {
            "key": "set_local",
            "label": t("m14.params.set_local.label"),
            "type": "button",
            "action": "set_local_supervisor_action",
            "style": "destructive",
            "visible_when": "is_hardware_supported_cached",
            "help": t("m14.params.set_local.help"),
        },
    ],
    "m15_wake_on_lan": [
        {
            "key": "enable_wol_listen",
            # Kutu servisin kurulu olup olmadığını gösterir; işareti
            # kaldırıp uygulamak servisi kaldırır (m15 apply).
            "default_from": "wol_active",
            "label": t("m15.params.enable_wol_listen.label"),
            "type": "bool",
            "required": False,
            "default": "False",
            "help": t("m15.params.enable_wol_listen.help"),
        },
    ],
    "m16_grub_protection": [
        {
            "key": "enable_grub_lock",
            "label": t("m16.params.enable_grub_lock.label"),
            "type": "bool",
            "required": False,
            "default": "False",
            # Kutucuk adıma girildiğinde sistemin gerçek durumunu
            # göstersin: GRUB zaten korumalıysa işaretli açılır.
            "default_from": "lockdown_active",
            "help": t("m16.params.enable_grub_lock.help"),
            "help_more": t("m16.params.enable_grub_lock.help_more"),
        },
        {
            "key": "grub_username",
            "label": t("m16.params.grub_username.label"),
            "type": "readonly",
            "required": False,
            # Tek kaynak modüldeki SUPERUSER sabiti.
            "default_from": "superuser_name",
            "help": t("m16.params.grub_username.help"),
        },
        {
            "key": "grub_password",
            "label": t("m16.params.grub_password.label"),
            "type": "password",
            # GRUB ekranı İngilizce klavyeyle çalışır; yalnız Türkçe ve
            # İngilizce klavyede aynı tuşta olan karakterler yazılabilir.
            "allowed_chars": GRUB_PASSWORD_CHARS,
            "show_toggle": True,
            "required": False,
            "default": "",
            "enable_when_field": "enable_grub_lock",
            "placeholder": t("m16.params.grub_password.placeholder"),
            "hint": t("m16.params.grub_password.hint"),
            "help": t("m16.params.grub_password.help"),
        },
    ],
}


def get(module_id: str) -> list[dict]:
    return PARAMS_SCHEMA.get(module_id, [])
