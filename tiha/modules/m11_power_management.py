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
- Aktif oturumu systemd-logind ile saptar; kullanıcı login değilse
  uyarı LightDM greeter ekranında da gösterilir

**Geri al.**
Orijinal eta-shutdown konfigürasyonu geri yüklenir, değişiklikler kaldırılır.
"""

from __future__ import annotations

import configparser
import shutil
import subprocess
from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.privilege import invoking_username
from ..core.utils import run_cmd, screen_blank_seconds

# Geri sayım diyalogunun görünebilmesi için ekran-blank ile idle eşiği
# arasında olması gereken minimum güvenlik payı (saniye):
#   60s — servisin OnUnitActiveSec poll periyodu
#   30s — kullanıcının diyalogu görüp tepki vermesi için ek buffer
_BLANK_SAFETY_SEC = 90

log = get_logger(__name__)

# Dosya yolları
ETA_SHUTDOWN_CONFIG = Path("/etc/pardus/eta-shutdown.conf")
ETA_SHUTDOWN_SERVICE = Path("/usr/share/eta/eta-shutdown/src/service/service.py")
ETA_SHUTDOWN_SERVICE_BACKUP = Path("/usr/share/eta/eta-shutdown/src/service/service.py.tiha-backup")
# TiHA tarafından kurulan, kullanıcı oturumunda görünen GUI geri sayım penceresi
COUNTDOWN_SCRIPT = Path("/usr/local/sbin/tiha-shutdown-countdown.py")


def _render_countdown_script() -> str:
    """Kullanıcı oturumunda gösterilen GTK geri sayım penceresi.

    Çağrı:  tiha-shutdown-countdown.py "<mod açıklaması>" <saniye>
    Exit kodu:  0 = kapatmaya devam et (zaman aşımı veya "Şimdi kapat")
                1 = ertelendi (kullanıcı "10 dakika ertele" tıkladı)
    """
    return '''#!/usr/bin/env python3
"""TiHA otomatik kapanma geri sayım penceresi."""
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

mode_name = sys.argv[1] if len(sys.argv) > 1 else "Otomatik kapatma"
total_seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 120


class CountdownWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="Otomatik Kapatma Uyarısı")
        self.set_keep_above(True)
        self.set_position(Gtk.WindowPosition.CENTER_ALWAYS)
        self.set_default_size(440, 240)
        self.set_resizable(False)
        self.set_border_width(20)
        self.set_skip_taskbar_hint(False)

        self.remaining = total_seconds
        self.exit_code = 0

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.add(vbox)

        title = Gtk.Label()
        title.set_markup('<span size="15000" weight="bold">Tahta kapatılacak</span>')
        vbox.pack_start(title, False, False, 0)

        reason = Gtk.Label(label=mode_name)
        reason.set_line_wrap(True)
        reason.set_justify(Gtk.Justification.CENTER)
        vbox.pack_start(reason, False, False, 0)

        self.timer_label = Gtk.Label()
        self._update_label()
        vbox.pack_start(self.timer_label, True, True, 0)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_box.set_halign(Gtk.Align.CENTER)

        postpone_btn = Gtk.Button(label="10 dakika ertele")
        postpone_btn.connect("clicked", self._on_postpone)
        btn_box.pack_start(postpone_btn, False, False, 0)

        shutdown_btn = Gtk.Button(label="Şimdi kapat")
        shutdown_btn.get_style_context().add_class("destructive-action")
        shutdown_btn.connect("clicked", self._on_shutdown_now)
        btn_box.pack_start(shutdown_btn, False, False, 0)

        vbox.pack_end(btn_box, False, False, 0)

        self.connect("destroy", self._quit)
        GLib.timeout_add(1000, self._tick)

    def _fmt(self, s):
        return "{}:{:02d}".format(s // 60, s % 60)

    def _update_label(self):
        self.timer_label.set_markup(
            '<span size="42000" weight="bold">{}</span>'.format(self._fmt(self.remaining))
        )

    def _tick(self):
        self.remaining -= 1
        if self.remaining <= 0:
            self.exit_code = 0
            self._quit()
            return False
        self._update_label()
        return True

    def _on_postpone(self, _btn):
        self.exit_code = 1
        self._quit()

    def _on_shutdown_now(self, _btn):
        self.exit_code = 0
        self._quit()

    def _quit(self, *_args):
        Gtk.main_quit()


win = CountdownWindow()
win.show_all()
Gtk.main()
sys.exit(win.exit_code)
'''


def _render_enhanced_service() -> str:
    """TiHA tarafından geliştirilmiş eta-shutdown service script'i.

    Orijinal eta-shutdown ``service.py`` dosyası bu içerikle değiştirilir.
    `main.py` (orijinal) her 60 saniyede ``service()`` fonksiyonunu çağırır.
    Aynı systemd unit ve aynı /etc/pardus/eta-shutdown.conf'u kullanır;
    yalnızca davranış (GUI geri sayım + erteleme) bu modülde tanımlanır.
    """
    return '''import os
import pwd
import sys
import time
import subprocess
import configparser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime

from xidle import get_idle_time
from logger import log

CONFIG_FILE = "/etc/pardus/eta-shutdown.conf"
COUNTDOWN_SCRIPT = "/usr/local/sbin/tiha-shutdown-countdown.py"
COUNTDOWN_SECONDS = 120  # 2 dakika

config = configparser.ConfigParser()
config.read(CONFIG_FILE)


def check_time(hour, minute, delay):
    now = datetime.now()
    nex = datetime(now.year, now.month, now.day, hour, minute)
    return nex.timestamp() - delay - now.timestamp() < 0


def find_display_target():
    """O an ekrandaki aktif grafik oturumu döndürür.

    Sonuç: (username, env_dict, kind) ya da None.
      kind: "user"    — UID >= 1000 olan normal kullanıcı oturumu
            "greeter" — LightDM greeter (kimse login değil ya da user-switch)

    Önce systemd-logind ile aktif (ekrandaki) grafik oturumu sorulur; bu
    başarısızsa eski /run/user taramasına düşülür.
    """
    target = _find_target_via_logind()
    if target:
        return target
    return _find_target_via_runuser()


def _find_target_via_logind():
    """systemd-logind ile Active=yes olan grafik oturumu döndürür."""
    try:
        list_res = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception as exc:
        log("loginctl list-sessions failed: {}".format(exc))
        return None
    if list_res.returncode != 0:
        return None

    session_ids = []
    for line in list_res.stdout.splitlines():
        parts = line.split()
        if parts:
            session_ids.append(parts[0])

    for sid in session_ids:
        try:
            show_res = subprocess.run(
                ["loginctl", "show-session", sid,
                 "--property=Active", "--property=Class", "--property=Type",
                 "--property=Name", "--property=User", "--property=Display"],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:
            continue
        if show_res.returncode != 0:
            continue
        props = {}
        for kv in show_res.stdout.splitlines():
            if "=" in kv:
                k, v = kv.split("=", 1)
                props[k] = v
        if props.get("Active") != "yes":
            continue
        if props.get("Type") not in ("x11", "wayland", "mir"):
            continue

        klass = props.get("Class", "user")
        username = props.get("Name", "")
        display = props.get("Display") or ":0"
        try:
            uid = int(props.get("User", "0"))
        except ValueError:
            uid = 0

        if klass == "user" and uid >= 1000 and username:
            try:
                pw = pwd.getpwuid(uid)
                home_dir = pw.pw_dir
            except KeyError:
                home_dir = "/home/{}".format(username)
            runtime_dir = "/run/user/{}".format(uid)
            xauth_candidates = [
                "{}/.Xauthority".format(home_dir),
                "{}/gdm/Xauthority".format(runtime_dir),
                "/var/run/lightdm/{}/xauthority".format(username),
                "/run/lightdm/{}/xauthority".format(username),
            ]
            xauth = next((p for p in xauth_candidates if os.path.exists(p)), "")
            env = {
                "DISPLAY": display,
                "XAUTHORITY": xauth,
                "XDG_RUNTIME_DIR": runtime_dir,
                "DBUS_SESSION_BUS_ADDRESS": "unix:path={}/bus".format(runtime_dir),
                "HOME": home_dir,
                "USER": username,
                "LOGNAME": username,
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            }
            return username, env, "user"

        if klass == "greeter":
            greeter_user = username or "lightdm"
            xauth_candidates = [
                "/var/lib/lightdm-data/lightdm/.Xauthority",
                "/var/lib/lightdm/.Xauthority",
                "/run/lightdm/root/{}".format(display),
                "/var/run/lightdm/root/{}".format(display),
            ]
            xauth = next((p for p in xauth_candidates if os.path.exists(p)), "")
            env = {
                "DISPLAY": display,
                "XAUTHORITY": xauth,
                "HOME": "/var/lib/lightdm",
                "USER": greeter_user,
                "LOGNAME": greeter_user,
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            }
            return greeter_user, env, "greeter"

    return None


def _find_target_via_runuser():
    """Eski fallback: /run/user altında UID>=1000 + xset -q ile X oturumu bul."""
    try:
        for entry in os.listdir("/run/user"):
            try:
                uid = int(entry)
            except ValueError:
                continue
            if uid < 1000:
                continue
            try:
                pw = pwd.getpwuid(uid)
            except KeyError:
                continue
            runtime_dir = "/run/user/{}".format(uid)
            xauth_candidates = [
                "{}/.Xauthority".format(pw.pw_dir),
                "{}/gdm/Xauthority".format(runtime_dir),
                "/var/run/lightdm/{}/xauthority".format(pw.pw_name),
                "/run/lightdm/{}/xauthority".format(pw.pw_name),
            ]
            xauth = next((p for p in xauth_candidates if os.path.exists(p)), "")
            env = {
                "DISPLAY": os.environ.get("DISPLAY", ":0"),
                "XAUTHORITY": xauth,
                "XDG_RUNTIME_DIR": runtime_dir,
                "DBUS_SESSION_BUS_ADDRESS": "unix:path={}/bus".format(runtime_dir),
                "HOME": pw.pw_dir,
                "USER": pw.pw_name,
                "LOGNAME": pw.pw_name,
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            }
            try:
                rc = subprocess.run(
                    ["sudo", "-u", pw.pw_name, "env"]
                    + ["{}={}".format(k, v) for k, v in env.items()]
                    + ["xset", "-q"],
                    capture_output=True,
                    timeout=5,
                ).returncode
                if rc == 0:
                    return pw.pw_name, env, "user"
            except Exception:
                continue
    except OSError:
        pass
    return None


def wake_screen(username, env):
    """Ekranı güç tasarrufundan çıkar (countdown penceresi görülebilsin).

    Hem oturum içi hem LightDM greeter X sunucusunda çalışır. ``xset
    dpms force on`` monitörü uyandırır; ``xset s reset`` aktif ekran
    koruyucunun idle sayacını sıfırlar. Hatalar log'a düşer ama
    countdown gösterimini bloklamaz.
    """
    base = (
        ["sudo", "-u", username, "env"]
        + ["{}={}".format(k, v) for k, v in env.items()]
    )
    for args in (["xset", "dpms", "force", "on"], ["xset", "s", "reset"]):
        try:
            subprocess.run(
                base + args, timeout=5, check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            log("TiHA wake_screen: {} hata: {}".format(args, exc))


def show_countdown_dialog(mode_name):
    """Aktif grafik oturumda GTK geri sayım penceresi çalıştırır.

    Hedef oturum: önce login olmuş kullanıcı; yoksa LightDM greeter.
    Pencereyi açmadan hemen önce ekran güç tasarrufundan çıkarılır, böylece
    monitör kapalıyken bile diyalog kullanıcının görüş alanına gelir.

    Dönüş:
      "postpone" → kullanıcı erteledi (10 dakika)
      "proceed"  → süre doldu veya kullanıcı "Şimdi kapat" dedi
      "fallback" → GUI çalıştırılamadı (gösterilebilir oturum yok, vb.)
    """
    target = find_display_target()
    if not target:
        log("TiHA countdown: gösterilebilir aktif grafik oturum yok")
        return "fallback"

    username, env, kind = target
    if not os.path.exists(COUNTDOWN_SCRIPT):
        log("TiHA countdown: GUI script yok: {}".format(COUNTDOWN_SCRIPT))
        return "fallback"

    log("TiHA countdown: hedef={}/{} display={}".format(
        username, kind, env.get("DISPLAY")))
    # Ekran güç tasarrufundaysa monitörü uyandır — diyalog karanlıkta açılmasın.
    wake_screen(username, env)
    cmd = (
        ["sudo", "-u", username, "env"]
        + ["{}={}".format(k, v) for k, v in env.items()]
        + ["python3", COUNTDOWN_SCRIPT, mode_name, str(COUNTDOWN_SECONDS)]
    )
    try:
        result = subprocess.run(cmd, timeout=COUNTDOWN_SECONDS + 15)
    except subprocess.TimeoutExpired:
        log("TiHA countdown: GUI zaman aşımına uğradı, kapatmaya devam")
        return "proceed"
    except Exception as exc:
        log("TiHA countdown: GUI başlatılamadı: {}".format(exc))
        return "fallback"

    if result.returncode == 1:
        return "postpone"
    return "proceed"


def wait_or_proceed(mode_name):
    """Geri sayım göster; başarısızsa eski davranış (2 dk bekle, kapat).

    Eski 'send_notify' temelli balon zaten görünmediği için fallback
    yalnızca süreyi tüketir; sonunda True döner (kapatmaya devam).
    """
    outcome = show_countdown_dialog(mode_name)
    if outcome == "postpone":
        return False  # kapatma iptal/ertelendi
    if outcome == "proceed":
        return True
    # fallback: 2 dakika bekleyip kapat (mevcut davranışla uyumlu)
    log("TiHA countdown: fallback — {} sn bekleniyor".format(COUNTDOWN_SECONDS))
    time.sleep(COUNTDOWN_SECONDS)
    return True


# State
init = False
ignore_auto = False
postpone_until = 0.0  # bu zamana kadar uyarı suspended


def service():
    global init, ignore_auto, postpone_until

    # Config'i her döngüde tazele (ETA Zamanlı Kapatma GUI'sinden gelen
    # değişiklikleri yakalamak için)
    config.read(CONFIG_FILE)

    auto_hour = int(config["AUTO_SHUTDOWN"]["hour"])
    auto_minute = int(config["AUTO_SHUTDOWN"]["minute"])
    hour = int(config["TIMED_MODE"]["hour"])
    minute = int(config["TIMED_MODE"]["minute"])

    # İlk açılışta sabit saat geçmişse o günü atla
    if not init:
        init = True
        if check_time(auto_hour, auto_minute, 0):
            ignore_auto = True

    log("###### TiHA Enhanced Eta Shutdown {} ######".format(time.time()))

    # Erteleme aktifse hiçbir kapatma tetiklenmesin
    if time.time() < postpone_until:
        log("TiHA: erteleme aktif, {:.0f} sn kaldı".format(postpone_until - time.time()))
        return

    # ---- TIMED MODE — Idle tabanlı kapatma ----
    mode = config["TIMED_MODE"]["mode"]
    if mode != "none":
        idle_time = -1
        for display in [":0", ":1", ":10", ":11"]:
            try:
                idle = get_idle_time(display)
                if idle > 0 and (idle > idle_time or idle_time < 0):
                    idle_time = idle
            except Exception:
                continue
        req_idle = (hour * 3600 + minute * 60) * 1000
        if req_idle < 60 * 1000:
            req_idle = 60 * 1000
        log("idle_time={} req_idle={}".format(idle_time, req_idle))

        if idle_time > req_idle:
            proceed = wait_or_proceed(
                "Boşta kalma süresi aşıldı ({} dakika).".format(minute)
            )
            if not proceed:
                postpone_until = time.time() + 600
                log("Idle kapatma 10 dakika ertelendi")
                return
            log("TiHA: Idle tabanlı kapatma gerçekleştiriliyor")
            if mode == "shutdown":
                os.system("poweroff")
            elif mode == "suspend":
                os.system("systemctl suspend")
            return

    # ---- AUTO SHUTDOWN — Sabit saat kapatma ----
    if ignore_auto:
        return
    if config["AUTO_SHUTDOWN"]["enabled"].lower() != "true":
        return

    # Geri sayım penceresini hedef saatten 2 dk önce aç
    if check_time(auto_hour, auto_minute, COUNTDOWN_SECONDS):
        if not check_time(auto_hour, auto_minute, 0):
            proceed = wait_or_proceed(
                "Sabit saat kapatma ({:02d}:{:02d}).".format(auto_hour, auto_minute)
            )
            if not proceed:
                postpone_until = time.time() + 600
                log("Sabit saat kapatma 10 dakika ertelendi")
                return
            log("TiHA: Sabit saat kapatma gerçekleştiriliyor")
            os.system("poweroff")
'''


class PowerManagementModule(Module):
    id = "m11_power_management"
    title = "Otomatik kapanma"
    sidebar_title = "Otomatik kapanma"
    apply_hint = "ETA-Shutdown tabanlı otomatik kapanma sistemi kurulur."
    rationale = (
        "Tahtanın unutulması durumunda otomatik kapatma sistemi kurar. "
        "Belirlenen saatte veya tahta boşta kaldığında otomatik olarak "
        "kapatma işlemi yapılır. Her iki modda da 2 dakika önceden uyarı "
        "diyalogu gösterilir ve kapatma 10 dakika ertelenebilir. "
        "Kullanıcı login değilse uyarı LightDM greeter ekranında "
        "gösterilir."
    )
    extra_links = [
        {"label": "ETA Zamanlı Kapatma'yı aç", "action": "launch_eta_shutdown_gui_action"},
    ]

    def preview(self) -> str:
        """Her açılışta güncel eta-shutdown config'ini okuyan dinamik preview."""

        # Güncel tarih/saat bilgisi ekle
        import datetime
        current_time = datetime.datetime.now().strftime("%H:%M")

        eta_service_running = False
        eta_config_exists = ETA_SHUTDOWN_CONFIG.exists()

        # Servis durumunu her seferinde kontrol et
        result = run_cmd(["systemctl", "is-active", "eta-shutdown"])
        eta_service_running = result.ok and "active" in result.stdout

        if eta_config_exists:
            try:
                # Config'i her seferinde yeniden oku
                config = configparser.ConfigParser()
                config.read(ETA_SHUTDOWN_CONFIG)

                auto_enabled = config.getboolean("AUTO_SHUTDOWN", "enabled", fallback=False)
                auto_hour = config.get("AUTO_SHUTDOWN", "hour", fallback="0")
                auto_minute = config.get("AUTO_SHUTDOWN", "minute", fallback="0")

                timed_mode = config.get("TIMED_MODE", "mode", fallback="none")
                timed_minute = config.get("TIMED_MODE", "minute", fallback="0")

                enhanced = ETA_SHUTDOWN_SERVICE_BACKUP.exists()

                # Dinamik durum başlığı
                status = f"🔄 Otomatik kapanma sistemi (güncellendi {current_time})"
                if enhanced:
                    status += "\n✓ TiHA gelişmiş sürüm aktif"
                else:
                    status += "\n⚠️ Orijinal eta-shutdown kullanımda"

                lines = [
                    status,
                    f"• Servis durumu: {'🟢 çalışıyor' if eta_service_running else '🔴 durdurulmuş'}",
                    "",
                    "📋 Mevcut yapılandırma:"
                ]

                if auto_enabled:
                    # Sabit saat kapatmaya ne kadar kaldığını hesapla
                    try:
                        from datetime import datetime, time
                        now = datetime.now()
                        shutdown_time = datetime.combine(now.date(), time(int(auto_hour), int(auto_minute)))
                        if shutdown_time < now:
                            shutdown_time = shutdown_time.replace(day=now.day + 1)
                        time_diff = shutdown_time - now
                        hours, remainder = divmod(time_diff.seconds, 3600)
                        minutes, _ = divmod(remainder, 60)
                        countdown = f" ({hours}s {minutes}dk kaldı)" if time_diff.days == 0 else ""
                    except:
                        countdown = ""

                    lines.extend([
                        f"🕐 Sabit saat kapatma: AKTİF {auto_hour.zfill(2)}:{auto_minute.zfill(2)}{countdown}",
                        "   - 2 dakika önceden uyarı diyalogu",
                        "   - 10 dakika erteleme seçeneği"
                    ])
                else:
                    lines.append("🕐 Sabit saat kapatma: KAPALI")

                if timed_mode != "none":
                    lines.extend([
                        f"💤 Idle tabanlı kapatma: AKTİF ({timed_minute} dakika)",
                        "   - X11 idle detection (mouse, klavye)",
                        "   - 2 dakika önceden uyarı diyalogu",
                        "   - 10 dakika erteleme seçeneği"
                    ])
                else:
                    lines.append("💤 Idle tabanlı kapatma: KAPALI")

                # Config dosyası son değişiklik zamanı
                try:
                    import os
                    mtime = os.path.getmtime(ETA_SHUTDOWN_CONFIG)
                    mtime_str = datetime.datetime.fromtimestamp(mtime).strftime("%H:%M")
                    lines.extend([
                        "",
                        f"📄 Config son güncelleme: {mtime_str}"
                    ])
                except:
                    pass

                return "\n".join(lines)

            except Exception as exc:
                return f"✗ Yapılandırma okunurken hata: {exc}\n🔄 Sayfa yeniden yüklendiğinde tekrar denenecek"
        else:
            return (
                f"⚙️ Henüz yapılandırılmamış (kontrol: {current_time})\n\n"
                "Bu adım şunları yapacak:\n"
                "• ETA-shutdown konfigürasyonu oluşturacak\n"
                "• 2 dakika uyarı diyalogu ekleyecek\n"
                "• Sabit saat ve idle tabanlı kapatma modları sunacak\n"
                "• eta-shutdown.service'i aktifleştirecek\n\n"
                "🔄 Adım her açılışta güncel durumu kontrol eder"
            )

    def apply(self, params=None, progress=None) -> ApplyResult:
        params = params or {}

        # Parametreleri al
        auto_enabled = str(params.get("auto_enabled", "False")).lower() == "true"
        auto_hour = int(params.get("auto_hour", 22))
        auto_minute = int(params.get("auto_minute", 0))

        idle_enabled = str(params.get("idle_enabled", "True")).lower() == "true"
        idle_minute = int(params.get("idle_minute", 15))

        # Ekran-blank ile idle kapanma süresinin çakışma kontrolü (yumuşak uyarı).
        # Geri sayım diyalogu idle_threshold anında doğar; doğum anında ekran
        # açık olmalı, yoksa kullanıcı 2 dk'lık erteleme penceresini hiç görmez.
        blank_warning: str | None = None
        if idle_enabled:
            blank_sec = screen_blank_seconds()
            if blank_sec is not None:
                max_idle_min = max(0, (blank_sec - _BLANK_SAFETY_SEC) // 60)
                if idle_minute > max_idle_min:
                    blank_warning = (
                        f"⚠️ UYARI: Sistemde ekran enerjisi yaklaşık "
                        f"{blank_sec // 60} dk idle sonra kesiliyor; "
                        f"seçtiğiniz idle kapatma süresi ({idle_minute} dk) "
                        f"bundan uzun olduğu için 2 dk'lık geri sayım/erteleme "
                        f"diyalogu kararmış ekranda görünmeyebilir. "
                        f"Önerilen üst sınır: {max_idle_min} dk. Daha uzun bir "
                        "süre istiyorsanız ekran-blank süresini Sistem "
                        "Ayarları → Güç'ten yükseltin."
                    )
                    log.warning(blank_warning)
                    if progress:
                        progress(blank_warning)

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
            progress("Geri sayım penceresi (GUI) kuruluyor...")

        # Kullanıcı oturumunda gösterilecek GTK geri sayım penceresini kur
        try:
            COUNTDOWN_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
            COUNTDOWN_SCRIPT.write_text(_render_countdown_script(), encoding="utf-8")
            COUNTDOWN_SCRIPT.chmod(0o755)
        except OSError as exc:
            return ApplyResult(False, f"Geri sayım scripti yazılamadı: {exc}")

        if progress:
            progress("Otomatik kapanma konfigürasyonu yazılıyor...")

        # Konfigürasyon dosyasını oluştur
        config = configparser.ConfigParser()
        config["AUTO_SHUTDOWN"] = {
            "enabled": str(auto_enabled),
            "hour": str(auto_hour),
            "minute": str(auto_minute)
        }

        timed_mode = "shutdown" if idle_enabled else "none"

        config["TIMED_MODE"] = {
            "mode": timed_mode,
            "hour": "0",
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
            details_lines.extend([
                f"💤 Idle tabanlı kapatma: {idle_minute} dakika boşta kalınca",
                "   - 2 dakika önceden uyarı diyalogu",
                "   - 10 dakika erteleme seçeneği"
            ])
            if blank_warning:
                details_lines.extend(["", blank_warning])

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

        # Geri sayım penceresi scriptini kaldır
        if COUNTDOWN_SCRIPT.exists():
            try:
                COUNTDOWN_SCRIPT.unlink()
                removed_items.append("Geri sayım penceresi scripti kaldırıldı")
            except OSError as exc:
                log.warning("Geri sayım scripti silinemedi: %s", exc)

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

    def launch_eta_shutdown_gui_action(self, params: dict | None = None) -> ApplyResult:
        """ETA Zamanlı Kapatma GUI'sini kullanıcının X oturumunda açar."""
        binary = Path("/usr/bin/eta-shutdown")
        if not binary.exists():
            return ApplyResult(
                False,
                "ETA Zamanlı Kapatma uygulaması bulunamadı.",
                details=f"{binary} mevcut değil; eta-shutdown paketi kurulu mu?",
            )

        user = invoking_username()
        try:
            subprocess.Popen(
                ["sudo", "-u", user, "env",
                 "DISPLAY=:0",
                 f"XAUTHORITY=/home/{user}/.Xauthority",
                 str(binary)],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return ApplyResult(
                False,
                "ETA Zamanlı Kapatma başlatılamadı.",
                details=str(exc),
            )

        return ApplyResult(
            True,
            f"ETA Zamanlı Kapatma '{user}' oturumunda açıldı.",
            details="Yapılandırmayı kaydedip kapattığınızda 10. adımdaki bilgi yenilenecek.",
        )

    def get_current_config(self) -> dict:
        """Mevcut eta-shutdown config'ini okuyup form parametreleri döndürür."""
        if not ETA_SHUTDOWN_CONFIG.exists():
            return {}

        try:
            config = configparser.ConfigParser()
            config.read(ETA_SHUTDOWN_CONFIG)

            auto_enabled = config.getboolean("AUTO_SHUTDOWN", "enabled", fallback=False)
            auto_hour = config.getint("AUTO_SHUTDOWN", "hour", fallback=22)
            auto_minute = config.getint("AUTO_SHUTDOWN", "minute", fallback=0)

            timed_mode = config.get("TIMED_MODE", "mode", fallback="none")
            timed_minute = config.getint("TIMED_MODE", "minute", fallback=15)

            return {
                "auto_enabled": str(auto_enabled),
                "auto_hour": str(auto_hour),
                "auto_minute": str(auto_minute),
                "idle_enabled": str(timed_mode != "none"),
                "idle_minute": str(timed_minute)
            }
        except Exception as exc:
            log.warning("Config okuma hatası: %s", exc)
            return {}