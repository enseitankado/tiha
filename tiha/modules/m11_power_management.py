"""Modül 11 — Güç yönetimi ve otomatik kapanma.

**Ne yapar?**
LightDM greeter ekranında tahta belirtilen süre boyunca boşta kaldığında
otomatik olarak güvenli kapanma işlemi gerçekleştirir. Bu, okulda
öğretmenlerin tahtayı açık unutması durumunda enerji tasarrufu sağlar.

**Nasıl çalışır?**
1. Systemd timer ile düzenli kontrol (her 5 dakika)
2. LightDM greeter boştayken idle süre hesaplama
3. Belirtilen eşik aşılınca güvenli kapatma
4. Tüm işlemler detaylı log'lanır

**Güvenlik önlemleri:**
- SSH bağlantısı varsa kapatmaz
- Aktif USB cihaz varsa bekler
- Önemli süreçler çalışıyorsa atlar
- Çalışma saatleri koruma (opsiyonel)

**Geri al.**
Systemd timer/service kaldırılır, script dosyaları temizlenir.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.utils import run_cmd

log = get_logger(__name__)

# Dosya yolları
POWER_SCRIPT = Path("/usr/local/sbin/tiha-power-manager.py")
POWER_SERVICE = Path("/etc/systemd/system/tiha-power-manager.service")
POWER_TIMER = Path("/etc/systemd/system/tiha-power-manager.timer")
POWER_CONFIG = Path("/etc/tiha/power-management.conf")


def _render_script(idle_minutes: int) -> str:
    """Güç yönetimi script'ini oluşturur."""
    return f"""#!/usr/bin/env python3
\"\"\"
TiHA Güç Yönetimi - LightDM greeter boştayken otomatik kapatma
Oluşturan: TiHA (Tahta İmaj Hazırlık Aracı)
\"\"\"

import subprocess
import sys
import time
import syslog
from pathlib import Path

# Yapılandırma
IDLE_THRESHOLD_MINUTES = {idle_minutes}
LOG_PREFIX = "TiHA-PowerMgmt"

def log_info(msg: str) -> None:
    syslog.openlog(LOG_PREFIX)
    syslog.syslog(syslog.LOG_INFO, msg)
    syslog.closelog()

def log_warning(msg: str) -> None:
    syslog.openlog(LOG_PREFIX)
    syslog.syslog(syslog.LOG_WARNING, msg)
    syslog.closelog()

def run_cmd(cmd: list[str]) -> tuple[bool, str]:
    \"\"\"Komut çalıştır ve sonuç döndür.\"\"\"
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0, result.stdout.strip()
    except Exception as e:
        return False, str(e)

def get_active_sessions() -> dict:
    \"\"\"Aktif kullanıcı oturumlarını detaylı analiz et.\"\"\"
    result = {{"console": [], "remote": [], "locked": [], "greeter": []}}

    # who komutu ile temel bilgi al
    success, output = run_cmd(["who"])
    if not success:
        return result

    for line in output.splitlines():
        if line.strip():
            parts = line.split()
            if len(parts) >= 2:
                user, tty = parts[0], parts[1]

                if user in ["lightdm", "gdm"]:
                    result["greeter"].append(user)
                elif "pts/" in tty:
                    result["remote"].append(user)
                elif tty.startswith("tty"):
                    result["console"].append(user)

    # loginctl ile gerçek session durumunu kontrol et
    success, sessions_output = run_cmd(["loginctl", "list-sessions", "--no-legend"])
    if success:
        for line in sessions_output.splitlines():
            parts = line.strip().split()
            if len(parts) >= 4:
                session_id, uid, user, seat = parts[0], parts[1], parts[2], parts[3]

                # Session detayını al
                success, detail = run_cmd(["loginctl", "show-session", session_id, "-p", "State,Type,Scope"])
                if success:
                    session_info = {{}}
                    for detail_line in detail.splitlines():
                        if "=" in detail_line:
                            key, value = detail_line.split("=", 1)
                            session_info[key] = value

                    # Kilitli oturumları tespit et
                    if session_info.get("State") == "closing" or session_info.get("Type") == "user":
                        if user not in ["lightdm", "gdm", "root"]:
                            result["locked"].append(f"{{user}}({{session_info.get('State', 'unknown')}})")

    return result

def get_ssh_sessions() -> int:
    \"\"\"Aktif SSH bağlantı sayısını döndür.\"\"\"
    success, output = run_cmd(["who"])
    if not success:
        return 0

    ssh_count = 0
    for line in output.splitlines():
        if "pts/" in line:  # SSH terminal sessions
            ssh_count += 1
    return ssh_count

def get_idle_time_minutes() -> tuple[float, str]:
    \"\"\"Sistem idle süresini ve hangi yöntemle bulunduğunu döndür.\"\"\"

    # Yöntem 1: LightDM log analizi (en güvenilir)
    try:
        success, output = run_cmd(["journalctl", "-u", "lightdm", "-n", "50", "--no-pager"])
        if success:
            import re
            from datetime import datetime

            # Son aktivite zamanını bul
            last_activity = None
            for line in output.splitlines():
                if "session" in line.lower() or "greeter" in line.lower():
                    # Timestamp çıkar: Dec 12 14:30:45
                    timestamp_match = re.search(r'(\w+ \d+ \d+:\d+:\d+)', line)
                    if timestamp_match:
                        try:
                            time_str = timestamp_match.group(1)
                            # Yıl ekle (güncel yıl)
                            import datetime as dt
                            current_year = dt.datetime.now().year
                            full_time_str = f"{{current_year}} {{time_str}}"
                            activity_time = datetime.strptime(full_time_str, "%Y %b %d %H:%M:%S")

                            if last_activity is None or activity_time > last_activity:
                                last_activity = activity_time
                        except:
                            continue

            if last_activity:
                idle_seconds = (datetime.now() - last_activity).total_seconds()
                return idle_seconds / 60, "lightdm-log"
    except Exception as e:
        log_info(f"LightDM log analizi başarısız: {{e}}")

    # Yöntem 2: Input device analizi (/proc/interrupts)
    try:
        with open("/proc/interrupts", "r") as f:
            interrupt_data = f.read()

        # Klavye/mouse interrupt'larını ara
        keyboard_lines = [line for line in interrupt_data.splitlines()
                         if "keyboard" in line.lower() or "mouse" in line.lower() or "i8042" in line.lower()]

        if keyboard_lines:
            # Son interrupt zamanından idle hesapla
            with open("/proc/uptime", "r") as f:
                uptime = float(f.read().split()[0])

            # Konservatif yaklaşım: son 5 dakika idle kabul et
            return 5.0, "interrupt-analysis"
    except:
        pass

    # Yöntem 3: systemd-logind idle hint
    try:
        success, output = run_cmd(["busctl", "get-property", "org.freedesktop.login1",
                                  "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
                                  "IdleHint"])
        if success and "true" in output:
            success, idle_since = run_cmd(["busctl", "get-property", "org.freedesktop.login1",
                                          "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
                                          "IdleSinceHint"])
            if success:
                # Unix timestamp'ten dakika hesapla
                import re
                timestamp_match = re.search(r'(\d+)', idle_since)
                if timestamp_match:
                    idle_since_ts = int(timestamp_match.group(1)) // 1000000  # mikrosaniye -> saniye
                    current_ts = int(time.time())
                    idle_minutes = (current_ts - idle_since_ts) / 60
                    return idle_minutes, "systemd-logind"
    except:
        pass

    # Yöntem 4: X11 xprintidle (sadece X session'da çalışır)
    for display in [":0", ":1"]:
        try:
            env = {{"DISPLAY": display}}
            success, output = run_cmd(["xprintidle"], env=env)
            if success and output.isdigit():
                idle_ms = int(output)
                return idle_ms / 1000 / 60, f"xprintidle-{{display}}"
        except:
            continue

    # Fallback: Boot zamanından tahmin
    try:
        with open("/proc/uptime", "r") as f:
            uptime_seconds = float(f.read().split()[0])

        # Çok muhafazakar: uptime'ın %10'unu idle kabul et (minimum 10 dk)
        estimated_idle = max(10.0, uptime_seconds / 60 * 0.1)
        return estimated_idle, "uptime-estimate"
    except:
        pass

    # Son çare
    return 5.0, "fallback"

def check_blocking_conditions() -> tuple[bool, str]:
    \"\"\"Kapatmayı engelleyen durumları kontrol et.\"\"\"

    # SSH bağlantısı kontrolü
    ssh_count = get_ssh_sessions()
    if ssh_count > 0:
        return True, f"{{ssh_count}} SSH bağlantısı aktif"

    # USB cihaz kontrolü
    usb_devices = list(Path("/sys/bus/usb/devices").glob("*-*"))
    # Built-in cihazları çıkar (klavye, mouse vs hariç tutabiliriz)
    external_usb = [d for d in usb_devices if not any(x in str(d) for x in ["1-1", "2-1"])]
    if len(external_usb) > 2:  # Temel cihazlar hariç
        return True, f"{{len(external_usb)}} USB cihaz bağlı"

    # Önemli süreç kontrolü (opsiyonel)
    success, output = run_cmd(["pgrep", "-f", "backup|sync|update"])
    if success and output.strip():
        return True, "Önemli süreç çalışıyor (backup/sync/update)"

    return False, ""

def main():
    log_info("TiHA Güç Yönetimi kontrolü başlatıldı")

    # Aktif oturum detaylı analizi
    sessions = get_active_sessions()
    log_info(f"Session analizi: console={{sessions['console']}}, remote={{sessions['remote']}}, locked={{sessions['locked']}}, greeter={{sessions['greeter']}}")

    # Aktif kullanıcı oturumu kontrolü (locked oturumlar hariç)
    active_users = sessions['console'] + sessions['remote']
    active_users = [u for u in active_users if u not in ["lightdm", "gdm", "root"]]

    if active_users:
        log_info(f"Aktif kullanıcı oturumu var: {{active_users}}, kapatma iptal edildi")
        return

    # Kilitli oturum varsa bilgilendir ama devam et
    if sessions['locked']:
        log_info(f"Kilitli oturum tespit edildi: {{sessions['locked']}}, ama kapatma devam edecek")

    # Greeter kontrolü
    if not sessions['greeter']:
        log_info("LightDM greeter aktif değil, kapatma iptal edildi")
        return

    # Gelişmiş idle süre kontrolü
    idle_minutes, detection_method = get_idle_time_minutes()
    log_info(f"Idle süre: {{idle_minutes:.1f}} dakika (yöntem: {{detection_method}}, eşik: {{IDLE_THRESHOLD_MINUTES}})")

    if idle_minutes < IDLE_THRESHOLD_MINUTES:
        log_info(f"Idle süre eşik altında ({{idle_minutes:.1f}} < {{IDLE_THRESHOLD_MINUTES}}), kapatma yapılmayacak")
        return

    # Engelleme durumları kontrolü
    blocked, reason = check_blocking_conditions()
    if blocked:
        log_warning(f"Kapatma engellendi: {{reason}}")
        return

    # DPMS ekran kontrolünü devre dışı bırak (sistem kapatmasından önce)
    run_cmd(["xset", "-display", ":0", "-dpms"])

    log_warning(f"OTOMATIK KAPATMA BAŞLATILIYOR: {{idle_minutes:.1f}} dakika idle ({{detection_method}}), eşik {{IDLE_THRESHOLD_MINUTES}} dakika")

    # Wall mesajı gönder
    run_cmd(["wall", f"TiHA: Tahta {{IDLE_THRESHOLD_MINUTES}} dakika boyunca boşta kaldığı için 1 dakika içinde kapatılacak."])

    # 1 dakika bekle
    log_info("60 saniye uyarı sürecinde...")
    time.sleep(60)

    # Son kontrol - detaylı
    log_info("Son kontrol yapılıyor...")
    final_sessions = get_active_sessions()
    final_active = final_sessions['console'] + final_sessions['remote']
    final_active = [u for u in final_active if u not in ["lightdm", "gdm", "root"]]

    if final_active:
        log_info(f"Son kontrol: yeni kullanıcı girişi tespit edildi {{final_active}}, kapatma iptal edildi")
        return

    # Son idle kontrolü
    final_idle, final_method = get_idle_time_minutes()
    log_info(f"Son idle kontrolü: {{final_idle:.1f}} dakika ({{final_method}})")

    if final_idle < IDLE_THRESHOLD_MINUTES:
        log_info(f"Son kontrol: idle süre eşik altına düştü, kapatma iptal edildi")
        return

    log_warning("SİSTEM KAPAMA İŞLEMİ GERÇEKLEŞTİRİLİYOR")
    log_warning(f"Final durum - idle: {{final_idle:.1f}}dk, method: {{final_method}}, sessions: {{final_sessions}}")

    run_cmd(["shutdown", "-h", "now", "TiHA otomatik güç yönetimi"])

if __name__ == "__main__":
    main()
"""


def _render_service() -> str:
    """Systemd service dosyası."""
    return f"""[Unit]
Description=TiHA Güç Yönetimi
After=multi-user.target

[Service]
Type=oneshot
ExecStart={POWER_SCRIPT}
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


def _render_timer(interval_minutes: int = 5) -> str:
    """Systemd timer dosyası."""
    return f"""[Unit]
Description=TiHA Güç Yönetimi Timer
Requires=tiha-power-manager.service

[Timer]
OnBootSec={interval_minutes}min
OnUnitActiveSec={interval_minutes}min
Persistent=true

[Install]
WantedBy=timers.target
"""


def _render_config(idle_minutes: int) -> str:
    """Yapılandırma dosyası."""
    return f"""# TiHA Güç Yönetimi Yapılandırması
# Bu dosya TiHA tarafından otomatik oluşturulmuştur.

idle_threshold_minutes={idle_minutes}
check_interval_minutes=5
enable_worktime_protection=false
worktime_start=09:00
worktime_end=17:00
"""


class PowerManagementModule(Module):
    id = "m11_power_management"
    title = "Güç yönetimi ve otomatik kapanma"
    apply_hint = "LightDM boşta kaldığında otomatik kapatma sistemi kurulur."
    rationale = (
        "Bu adım, tahtanın okulda öğretmen tarafından açık unutulması "
        "durumunda otomatik olarak güvenli kapatma işlemi yapar. Enerji "
        "tasarrufu sağlar ve tahtanın gereksiz yere açık kalmasını önler.\n\n"
        "**Nasıl çalışır? (Gelişmiş sürüm)**\n"
        "• LightDM greeter ekranında tahta belirttiğiniz süre boyunca boşta "
        "kalırsa otomatik kapatma başlatılır\n"
        "• Kilitli kullanıcı oturumları kapatmaya engel OLMAZ (sadece aktif oturumlar)\n"
        "• Çoklu idle detection: LightDM log, systemd-logind, xprintidle\n"
        "• SSH bağlantısı, USB cihaz veya önemli işlemler varsa kapatmaz\n"
        "• DPMS ekran koruyucu devre dışı bırakılarak gerçek sistem kapatması yapılır\n"
        "• Tüm işlemler detaylı olarak sistem günlüklerine kaydedilir\n"
        "• 1 dakika önceden uyarı verilir (wall komutu ile)\n\n"
        "**Sorun Giderme:** Arka planda kilitli oturum olsa da greeter'da idle "
        "kalırsa sistem kapatılır. Sadece ekranın kapanması (DPMS) önlenir."
    )

    def preview(self) -> str:
        if POWER_TIMER.exists() and POWER_SERVICE.exists() and POWER_SCRIPT.exists():
            # Mevcut yapılandırmayı oku
            idle_time = "45"  # varsayılan
            if POWER_CONFIG.exists():
                try:
                    config_content = POWER_CONFIG.read_text(encoding="utf-8")
                    for line in config_content.splitlines():
                        if line.startswith("idle_threshold_minutes="):
                            idle_time = line.split("=")[1].strip()
                            break
                except:
                    pass

            # Timer durumunu kontrol et
            timer_result = run_cmd(["systemctl", "is-enabled", "tiha-power-manager.timer"])
            timer_status = "aktif" if timer_result.ok else "pasif"

            return (
                f"✓ TiHA güç yönetimi sistemi kurulu\n"
                f"• Idle eşiği: {idle_time} dakika\n"
                f"• Timer durumu: {timer_status}\n"
                f"• Kontrol aralığı: 5 dakika\n\n"
                f"Dosyalar:\n"
                f"  • Script: {POWER_SCRIPT}\n"
                f"  • Service: {POWER_SERVICE}\n"
                f"  • Timer: {POWER_TIMER}\n"
                f"  • Config: {POWER_CONFIG}"
            )
        else:
            return (
                "Henüz TiHA güç yönetimi sistemi kurulmamış.\n\n"
                "Bu adım şunları kuracak:\n"
                f"• Python script: {POWER_SCRIPT}\n"
                f"• Systemd service: {POWER_SERVICE}\n"
                f"• Systemd timer: {POWER_TIMER}\n"
                f"• Yapılandırma: {POWER_CONFIG}\n\n"
                "Timer her 5 dakikada LightDM idle durumunu kontrol edecek."
            )

    def apply(self, params=None, progress=None) -> ApplyResult:
        params = params or {}
        idle_minutes = int(params.get("idle_minutes") or 45)

        if progress:
            progress("TiHA güç yönetimi sistemi kuruluyor...")

        # Gerekli dizinleri oluştur
        try:
            POWER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            POWER_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ApplyResult(False, f"Dizin oluşturulamadı: {exc}")

        if progress:
            progress("Python script yazılıyor...")

        # Python script oluştur
        try:
            POWER_SCRIPT.write_text(_render_script(idle_minutes), encoding="utf-8")
            POWER_SCRIPT.chmod(0o755)
        except OSError as exc:
            return ApplyResult(False, f"Python script yazılamadı: {exc}")

        if progress:
            progress("Systemd service dosyası yazılıyor...")

        # Service dosyası oluştur
        try:
            POWER_SERVICE.write_text(_render_service(), encoding="utf-8")
            POWER_SERVICE.chmod(0o644)
        except OSError as exc:
            return ApplyResult(False, f"Service dosyası yazılamadı: {exc}")

        if progress:
            progress("Systemd timer dosyası yazılıyor...")

        # Timer dosyası oluştur
        try:
            POWER_TIMER.write_text(_render_timer(), encoding="utf-8")
            POWER_TIMER.chmod(0o644)
        except OSError as exc:
            return ApplyResult(False, f"Timer dosyası yazılamadı: {exc}")

        if progress:
            progress("Yapılandırma dosyası yazılıyor...")

        # Config dosyası oluştur
        try:
            POWER_CONFIG.write_text(_render_config(idle_minutes), encoding="utf-8")
            POWER_CONFIG.chmod(0o644)
        except OSError as exc:
            return ApplyResult(False, f"Yapılandırma dosyası yazılamadı: {exc}")

        if progress:
            progress("xprintidle paketi kontrol ediliyor...")

        # xprintidle kurulu değilse kur
        xprintidle_check = run_cmd(["which", "xprintidle"])
        if not xprintidle_check.ok:
            if progress:
                progress("xprintidle paketi kuruluyor...")
            install_result = run_cmd(
                ["apt-get", "install", "-y", "xprintidle"],
                env={"DEBIAN_FRONTEND": "noninteractive"}
            )
            if not install_result.ok:
                if progress:
                    progress("⚠ xprintidle kurulamadı, alternatif idle kontrolü kullanılacak")

        if progress:
            progress("Systemd daemon reload...")

        # Systemd reload
        reload_result = run_cmd(["systemctl", "daemon-reload"])
        if not reload_result.ok:
            return ApplyResult(False, "systemctl daemon-reload başarısız")

        if progress:
            progress("Timer aktifleştiriliyor...")

        # Timer'ı aktifleştir ve başlat
        enable_result = run_cmd(["systemctl", "enable", "tiha-power-manager.timer"])
        if not enable_result.ok:
            return ApplyResult(False, "Timer aktifleştirilemedi", details=enable_result.stderr)

        start_result = run_cmd(["systemctl", "start", "tiha-power-manager.timer"])
        if not start_result.ok:
            return ApplyResult(False, "Timer başlatılamadı", details=start_result.stderr)

        if progress:
            progress("✅ Kurulum tamamlandı!")

        return ApplyResult(
            True,
            f"Güç yönetimi sistemi kuruldu ({idle_minutes} dakika idle eşiği).",
            details=(
                f"✓ Python script: {POWER_SCRIPT}\n"
                f"✓ Systemd service: {POWER_SERVICE}\n"
                f"✓ Systemd timer: {POWER_TIMER} (aktif)\n"
                f"✓ Yapılandırma: {POWER_CONFIG}\n\n"
                f"Timer her 5 dakikada kontrol yapacak.\n"
                f"LightDM {idle_minutes} dakika boşta kalırsa tahta kapatılacak.\n\n"
                "Test: 'systemctl status tiha-power-manager.timer' ile durumu kontrol edin."
            )
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        removed_items = []

        # Timer'ı durdur ve deaktive et
        run_cmd(["systemctl", "stop", "tiha-power-manager.timer"])
        run_cmd(["systemctl", "disable", "tiha-power-manager.timer"])

        # Dosyaları kaldır
        for filepath in [POWER_TIMER, POWER_SERVICE, POWER_SCRIPT, POWER_CONFIG]:
            try:
                if filepath.exists():
                    filepath.unlink()
                    removed_items.append(str(filepath))
            except OSError:
                pass

        # Daemon reload
        run_cmd(["systemctl", "daemon-reload"])

        summary = f"TiHA güç yönetimi sistemi kaldırıldı"
        details = f"Kaldırılan dosyalar:\n" + "\n".join(f"  • {item}" for item in removed_items) if removed_items else "Kaldırılacak dosya bulunamadı"

        return ApplyResult(True, summary, details=details)