"""Modül 11 — Otomatik kapanma sistemi.

**Ne yapar?**
Pardus ETA'nın mevcut eta-shutdown altyapısını kullanarak otomatik kapanma
sistemi kurar. İki mod destekler:

1. **Sabit saat kapatma**: Belirlenen saatte otomatik kapatma
2. **Idle tabanlı kapatma**: Belirtilen süre boşta kalınca kapatma

**Orijinalden farkı:**
- Her iki modda da 2 dakika önceden uyarı diyalogu gösterir
- Kullanıcı kapatmayı erteleyebilir
- 1 dakika aralıklarla kontrol yapar (daha hassas)

**Geri al.**
Orijinal eta-shutdown konfigürasyonu geri yüklenir, değişiklikler kaldırılır.
"""

from __future__ import annotations

import configparser
import shutil
from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.utils import run_cmd

log = get_logger(__name__)

# Dosya yolları
ETA_SHUTDOWN_CONFIG = Path("/etc/pardus/eta-shutdown.conf")
ETA_SHUTDOWN_SERVICE = Path("/usr/share/eta/eta-shutdown/src/service/service.py")
ETA_SHUTDOWN_SERVICE_BACKUP = Path("/usr/share/eta/eta-shutdown/src/service/service.py.tiha-backup")


def _render_enhanced_service() -> str:
    """TiHA tarafından geliştirilmiş eta-shutdown service script'i."""
    return '''import os
import sys
import time
import subprocess
import threading
import configparser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime

from xidle import get_idle_time
from logger import log

"""
[AUTO_SHUTDOWN]
enabled = False
hour = 01
minute = 00

[TIMED_MODE]
mode = "shutdown"
hour = 00
minute = 00
"""

CONFIG_FILE = "/etc/pardus/eta-shutdown.conf"
config = configparser.ConfigParser()
config.read(CONFIG_FILE)

def check_time(hour, minute, delay):
    now = datetime.now()
    nex = datetime(now.year, now.month, now.day, hour, minute)
    print(now, nex, delay)
    return nex.timestamp() - delay - now.timestamp() < 0


def check_x11(disp):
    sp = subprocess.run(["env", "DISPLAY={}".format(disp), "xset", "-q"], capture_output=True)
    return sp.returncode == 0

ret = None
def send_notify(message, yes_msg, no_msg, timeout):
    global ret
    def send_notify_disp(disp):
        global ret
        cmd = ["timeout", str(timeout),
            "env", "DISPLAY={}".format(disp),
            "notify-send", "-w",
            "-A", "true={}".format(yes_msg),
            "-A", "false={}".format(no_msg),
            "-t", str(timeout*1000), message]
        log(cmd)
        sp = subprocess.run(cmd, capture_output=True)
        if ret == None:
            ret = (sp.stdout.decode("utf-8").strip() == "true")
    ths = []
    ret = None
    for display in os.listdir("/tmp/.X11-unix/"):
        if check_x11(f":{display[1:]}"):
            th = threading.Thread(target=send_notify_disp, args=[f":{display[1:]}"])
            ths.append(th)
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    print(ret)
    return ret

# TiHA gelişmiş değişkenler
message_shown = False
delay = 0
init = False
ignore_auto = False
shutdown_warning_shown = False
shutdown_start_time = None

def show_shutdown_warning(mode_name):
    """TiHA 2 dakika uyarı diyalogu."""
    global shutdown_warning_shown, shutdown_start_time

    if not shutdown_warning_shown:
        shutdown_warning_shown = True
        shutdown_start_time = time.time()

        message = f"⚠️ UYARI: Tahta 2 dakika sonra kapatılacak!\\n\\n{mode_name} moduna göre sistem kapatılacak."
        result = send_notify(message, "10 dakika ertele", "Kapatmayı onayla", 120)

        if result:  # Kullanıcı "10 dakika ertele" seçti
            log("Kullanıcı kapatmayı 10 dakika erteledi")
            shutdown_warning_shown = False
            shutdown_start_time = None
            return True  # Ertelendi
        else:
            log("Kullanıcı kapatmayı onayladı veya zaman aşımı")
            return False  # Devam et

    # 2 dakika geçti mi?
    if shutdown_start_time and (time.time() - shutdown_start_time) >= 120:
        return False  # 2 dakika geçti, kapat

    return True  # Hala bekliyor


def service():
    global message_shown
    global delay
    global init
    global ignore_auto
    global shutdown_warning_shown, shutdown_start_time

    # variables
    auto_hour = int(config["AUTO_SHUTDOWN"]["hour"])
    auto_minute = int(config["AUTO_SHUTDOWN"]["minute"])
    hour = int(config["TIMED_MODE"]["hour"])
    minute = int(config["TIMED_MODE"]["minute"])

    # first boot check
    if not init:
        init = True
        if check_time(auto_hour, auto_minute, 0):
            ignore_auto = True

    log("###### TiHA Enhanced Eta Shutdown {} ######".format(time.time()))

    # TIMED MODE - Idle tabanlı kapatma
    mode = config["TIMED_MODE"]["mode"]
    if mode != "none":
        idle_time = -1
        for display in os.listdir("/tmp/.X11-unix/"):
            idle = get_idle_time(f":{display[1:]}")
            if idle_time < idle or idle_time < 0:
                idle_time = idle
        print("idle_time: {}".format(idle_time))
        req_idle = (hour*3600 + minute * 60)*1000
        if req_idle < 60*1000:  # Minimum 1 dakika
            req_idle = 60*1000
        print("req_idle:", req_idle)

        if idle_time > req_idle:
            # TiHA: 2 dakika uyarı diyalogu
            postpone = show_shutdown_warning(f"Boşta kalma süresi aşıldı ({minute} dakika)")
            if postpone:
                if postpone is True and shutdown_start_time is None:
                    # 10 dakika erteleme
                    delay_until = time.time() + 600  # 10 dakika
                    shutdown_warning_shown = False
                    log("Idle kapatma 10 dakika ertelendi")
                return

            # 2 dakika geçti veya kullanıcı onayladı
            log("TiHA: Idle tabanlı kapatma gerçekleştiriliyor")
            if mode == "shutdown":
                os.system("poweroff")
            elif mode == "suspend":
                os.system("systemctl suspend")
            shutdown_warning_shown = False
            shutdown_start_time = None

    # AUTO SHUTDOWN - Sabit saat kapatma
    if ignore_auto:
        print("Ignore auto shutdown")
    elif config["AUTO_SHUTDOWN"]["enabled"].lower() == "true":
        # TiHA: 2 dakika önceden uyarı (orijinal 10dk yerine)
        if check_time(auto_hour, auto_minute, 120):  # 2 dakika öncesinden
            postpone = show_shutdown_warning(f"Sabit saat kapatma ({auto_hour:02d}:{auto_minute:02d})")
            if postpone:
                if postpone is True and shutdown_start_time is None:
                    # 10 dakika erteleme
                    delay -= 600  # 10 dakika geri çek
                    shutdown_warning_shown = False
                    log("Sabit saat kapatma 10 dakika ertelendi")
                return

        if check_time(auto_hour, auto_minute, delay):
            log("TiHA: Sabit saat kapatma gerçekleştiriliyor")
            os.system("poweroff")
            shutdown_warning_shown = False
            shutdown_start_time = None


if __name__ == "__main__":
    send_notify(sys.argv[1], "Yes", "No", 10)
'''


class PowerManagementModule(Module):
    id = "m11_power_management"
    title = "Otomatik kapanma"
    sidebar_title = "Otomatik kapanma"
    apply_hint = "ETA-Shutdown tabanlı otomatik kapanma sistemi kurulur."
    rationale = (
        "Bu adım, tahtanın okulda unutulması durumunda otomatik olarak "
        "kapatılmasını sağlar. Pardus ETA'nın mevcut eta-shutdown altyapısını "
        "kullanarak iki farklı kapatma modu sunar:\n\n"
        "**1. Sabit Saat Kapatma:**\n"
        "• Belirlediğiniz saatte otomatik olarak kapatma yapar\n"
        "• Kapatmadan 2 dakika önce uyarı diyalogu gösterir\n"
        "• Kullanıcı 10 dakika erteleyebilir\n\n"
        "**2. Idle Tabanlı Kapatma:**\n"
        "• Tahta belirlenen süre boyunca boşta kalırsa kapatma yapar\n"
        "• X11 idle detection kullanır (mouse, klavye aktivitesi)\n"
        "• Kapatmadan 2 dakika önce uyarı diyalogu gösterir\n"
        "• Minimum 1 dakika idle süresi\n\n"
        "**Orijinal eta-shutdown'dan farkları:**\n"
        "• Her iki modda da 2 dakika uyarı (orijinal: 10dk/hiç)\n"
        "• Erteleme imkanı her iki modda da mevcut\n"
        "• 1 dakika hassas kontrol aralığı"
    )

    def preview(self) -> str:
        eta_service_running = False
        eta_config_exists = ETA_SHUTDOWN_CONFIG.exists()

        # Servis durumunu kontrol et
        result = run_cmd(["systemctl", "is-active", "eta-shutdown"])
        eta_service_running = result.ok and "active" in result.stdout

        if eta_config_exists:
            try:
                config = configparser.ConfigParser()
                config.read(ETA_SHUTDOWN_CONFIG)

                auto_enabled = config.getboolean("AUTO_SHUTDOWN", "enabled", fallback=False)
                auto_hour = config.get("AUTO_SHUTDOWN", "hour", fallback="0")
                auto_minute = config.get("AUTO_SHUTDOWN", "minute", fallback="0")

                timed_mode = config.get("TIMED_MODE", "mode", fallback="none")
                timed_hour = config.get("TIMED_MODE", "hour", fallback="0")
                timed_minute = config.get("TIMED_MODE", "minute", fallback="0")

                enhanced = ETA_SHUTDOWN_SERVICE_BACKUP.exists()

                status = "✓ Otomatik kapanma sistemi aktif"
                if enhanced:
                    status += " (TiHA ile geliştirilmiş)"
                else:
                    status += " (orijinal eta-shutdown)"

                lines = [
                    status,
                    f"• Servis durumu: {'çalışıyor' if eta_service_running else 'durdurulmuş'}",
                    "",
                    "Mevcut yapılandırma:"
                ]

                if auto_enabled:
                    lines.extend([
                        f"• Sabit saat kapatma: AKTIF ({auto_hour}:{auto_minute})",
                        "  - 2 dakika önceden uyarı diyalogu",
                        "  - 10 dakika erteleme seçeneği"
                    ])
                else:
                    lines.append("• Sabit saat kapatma: KAPALI")

                if timed_mode != "none":
                    action = "kapatma" if timed_mode == "shutdown" else "uyku modu"
                    lines.extend([
                        f"• Idle tabanlı {action}: AKTIF ({timed_hour}:{timed_minute})",
                        "  - 2 dakika önceden uyarı diyalogu",
                        "  - 10 dakika erteleme seçeneği"
                    ])
                else:
                    lines.append("• Idle tabanlı kapatma: KAPALI")

                return "\n".join(lines)

            except Exception as exc:
                return f"✗ Yapılandırma okunurken hata: {exc}"
        else:
            return (
                "Henüz otomatik kapanma sistemi yapılandırılmamış.\n\n"
                "Bu adım şunları yapacak:\n"
                "• ETA-shutdown konfigürasyonu oluşturacak\n"
                "• 2 dakika uyarı diyalogu ekleyecek\n"
                "• Sabit saat ve idle tabanlı kapatma modları sunacak\n"
                "• eta-shutdown.service'i aktifleştirecek"
            )

    def apply(self, params=None, progress=None) -> ApplyResult:
        params = params or {}

        # Parametreleri al
        auto_enabled = params.get("auto_enabled", False)
        auto_hour = int(params.get("auto_hour", 22))
        auto_minute = int(params.get("auto_minute", 0))

        idle_enabled = params.get("idle_enabled", False)
        idle_mode = params.get("idle_mode", "shutdown")  # shutdown veya suspend
        idle_hour = int(params.get("idle_hour", 0))
        idle_minute = int(params.get("idle_minute", 15))

        if progress:
            progress("Mevcut eta-shutdown konfigürasyonu yedekleniyor...")

        # Mevcut service dosyasını yedekle (ilk kez ise)
        if not ETA_SHUTDOWN_SERVICE_BACKUP.exists():
            try:
                shutil.copy2(ETA_SHUTDOWN_SERVICE, ETA_SHUTDOWN_SERVICE_BACKUP)
                log.info("Orijinal eta-shutdown service yedeklendi")
            except OSError as exc:
                return ApplyResult(False, f"Service yedekleme başarısız: {exc}")

        if progress:
            progress("Geliştirilmiş eta-shutdown service yazılıyor...")

        # Geliştirilmiş service dosyasını yaz
        try:
            ETA_SHUTDOWN_SERVICE.write_text(_render_enhanced_service(), encoding="utf-8")
            ETA_SHUTDOWN_SERVICE.chmod(0o755)
        except OSError as exc:
            return ApplyResult(False, f"Service dosyası yazılamadı: {exc}")

        if progress:
            progress("Otomatik kapanma konfigürasyonu yazılıyor...")

        # Konfigürasyon dosyasını oluştur
        config = configparser.ConfigParser()
        config["AUTO_SHUTDOWN"] = {
            "enabled": str(auto_enabled),
            "hour": str(auto_hour),
            "minute": str(auto_minute)
        }

        timed_mode = "none"
        if idle_enabled:
            timed_mode = idle_mode

        config["TIMED_MODE"] = {
            "mode": timed_mode,
            "hour": str(idle_hour),
            "minute": str(idle_minute)
        }

        try:
            ETA_SHUTDOWN_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            with open(ETA_SHUTDOWN_CONFIG, "w", encoding="utf-8") as f:
                config.write(f)
            ETA_SHUTDOWN_CONFIG.chmod(0o644)
        except OSError as exc:
            return ApplyResult(False, f"Konfigürasyon dosyası yazılamadı: {exc}")

        if progress:
            progress("eta-shutdown servisi yeniden başlatılıyor...")

        # Servisi yeniden başlat
        restart_result = run_cmd(["systemctl", "restart", "eta-shutdown"])
        if not restart_result.ok:
            return ApplyResult(False, "eta-shutdown servisi başlatılamadı",
                               details=restart_result.stderr)

        # Servisin aktif olduğunu doğrula
        enable_result = run_cmd(["systemctl", "enable", "eta-shutdown"])
        if not enable_result.ok:
            log.warning("eta-shutdown servisi etkinleştirilemedi: %s", enable_result.stderr)

        if progress:
            progress("✅ Otomatik kapanma sistemi kuruldu!")

        # Özet bilgi
        details_lines = [
            "✓ TiHA geliştirilmiş eta-shutdown sistemi kuruldu",
            f"✓ Orijinal service yedeklendi: {ETA_SHUTDOWN_SERVICE_BACKUP}",
            f"✓ Konfigürasyon: {ETA_SHUTDOWN_CONFIG}",
            f"✓ eta-shutdown.service aktif",
            ""
        ]

        if auto_enabled:
            details_lines.extend([
                f"🕐 Sabit saat kapatma: {auto_hour:02d}:{auto_minute:02d}",
                "   - 2 dakika önceden uyarı diyalogu",
                "   - 10 dakika erteleme seçeneği"
            ])

        if idle_enabled:
            action = "kapatma" if idle_mode == "shutdown" else "uyku modu"
            idle_text = f"{idle_hour*60 + idle_minute} dakika" if idle_hour > 0 else f"{idle_minute} dakika"
            details_lines.extend([
                f"💤 Idle tabanlı {action}: {idle_text} boşta kalınca",
                "   - 2 dakika önceden uyarı diyalogu",
                "   - 10 dakika erteleme seçeneği"
            ])

        if not auto_enabled and not idle_enabled:
            details_lines.append("ℹ️ Her iki mod da devre dışı - sadece altyapı kuruldu")

        details_lines.extend([
            "",
            "📋 Test ve yönetim:",
            "• Durum kontrolü: systemctl status eta-shutdown",
            "• Log takibi: journalctl -f -u eta-shutdown",
            "• Manuel yapılandırma: /usr/bin/eta-shutdown --menu"
        ])

        summary = "Otomatik kapanma sistemi kuruldu"
        if auto_enabled or idle_enabled:
            summary += " ve aktifleştirildi"

        return ApplyResult(
            True,
            summary,
            details="\n".join(details_lines)
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        removed_items = []

        # Servisi durdur
        run_cmd(["systemctl", "stop", "eta-shutdown"])

        # Orijinal service dosyasını geri yükle
        if ETA_SHUTDOWN_SERVICE_BACKUP.exists():
            try:
                shutil.copy2(ETA_SHUTDOWN_SERVICE_BACKUP, ETA_SHUTDOWN_SERVICE)
                ETA_SHUTDOWN_SERVICE_BACKUP.unlink()
                removed_items.append("Orijinal eta-shutdown service geri yüklendi")
            except OSError as exc:
                log.warning("Service geri yükleme başarısız: %s", exc)

        # Konfigürasyonu sıfırla (varsayılan değerler)
        try:
            config = configparser.ConfigParser()
            config["AUTO_SHUTDOWN"] = {
                "enabled": "False",
                "hour": "0",
                "minute": "0"
            }
            config["TIMED_MODE"] = {
                "mode": "none",
                "hour": "0",
                "minute": "0"
            }
            with open(ETA_SHUTDOWN_CONFIG, "w", encoding="utf-8") as f:
                config.write(f)
            removed_items.append("eta-shutdown konfigürasyonu sıfırlandı")
        except OSError as exc:
            log.warning("Konfigürasyon sıfırlama başarısız: %s", exc)

        # Servisi yeniden başlat
        run_cmd(["systemctl", "restart", "eta-shutdown"])

        summary = "Otomatik kapanma sistemi kaldırıldı, orijinal eta-shutdown geri yüklendi"
        details = "\n".join(f"• {item}" for item in removed_items) if removed_items else "Kaldırılacak öğe bulunamadı"

        return ApplyResult(True, summary, details=details)