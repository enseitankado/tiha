"""Modül 11 — Otomatik kapanma sistemi.

**Ne yapar?**
Pardus ETA'nın mevcut eta-shutdown altyapısını kullanarak otomatik kapanma
sistemi kurar. İki mod destekler:

1. **Sabit saat kapatma**: Belirlenen saatte otomatik kapatma
2. **Kullanılmadığında kapatma**: Belirtilen süre boşta kalınca kapatma

İki moddan biri seçiliyken MAC adresi listesi girilebilir. Klon bu
tahtalardan birinde açılırsa servis ilk turunda yapılandırmayı bir kez
paketin varsayılanına (iki mod da kapalı) döndürür; tahta TiHA'nın kapanma
ayarı hiç yapılmamış gibi davranır, yerel kullanıcı ETA Zamanlı Kapatma ile
kendi ayarını yapabilir.

**Orijinalden farkı:**
- Her iki modda da uyarı diyalogu gösterir (varsayılan 2 dakika,
  form kutusundan ayarlanabilir)
- Kullanıcı kapatmayı erteleyebilir
- 1 dakika aralıklarla kontrol yapar (daha hassas)
- Aktif oturumu systemd-logind ile saptar; kullanıcı login değilse
  uyarı LightDM greeter ekranında da gösterilir

**Geri al.**
Orijinal eta-shutdown konfigürasyonu geri yüklenir, değişiklikler kaldırılır.
"""

from __future__ import annotations

import ast
import configparser
import re
import shutil
import subprocess
from pathlib import Path

from ..core.i18n import t
from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.privilege import invoking_username
from ..core.utils import run_cmd, screen_blank_seconds
from ..core.utils import _find_active_graphical_session

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
# Muaf tahtada yapılandırmanın varsayılana döndürüldüğü MAC adresleri.
# İçerik makineye özel: imajla gelen dosya başka bir tahtanın adresini
# taşıdığı için klonda yeniden değerlendirme yapılır.
EXEMPT_MARKER = Path("/var/lib/tiha/shutdown-exempt.done")


# --- Otomatik kapanmanın uygulanmayacağı tahtalar (MAC listesi) ------------

_MAC_SPLIT = re.compile(r"[\s,;]+")


def parse_mac_list(text: str | None) -> tuple[list[str], list[str]]:
    """Serbest metni MAC adreslerine ayırır.

    Ayraç: satır sonu, boşluk, virgül ya da noktalı virgül. Adres
    ``aa:bb:cc:dd:ee:ff``, ``AA-BB-…``, ``aabb.ccdd.eeff`` ya da ayraçsız
    12 onaltılık hane olabilir. Dönüş: (geçerli ve tekilleştirilmiş
    ``aa:bb:…`` listesi, geçersiz parçalar).
    """
    valid: list[str] = []
    invalid: list[str] = []
    for token in _MAC_SPLIT.split(text or ""):
        if not token:
            continue
        digits = re.sub(r"[:\-.]", "", token).lower()
        if not re.fullmatch(r"[0-9a-f]{12}", digits):
            invalid.append(token)
            continue
        mac = ":".join(digits[i:i + 2] for i in range(0, 12, 2))
        if mac not in valid:
            valid.append(mac)
    return valid, invalid


def _local_macs() -> set[str]:
    """Bu tahtanın ağ kartlarının MAC adresleri (anlık ve kalıcı)."""
    macs: set[str] = set()
    net = Path("/sys/class/net")
    try:
        ifaces = [p.name for p in net.iterdir()]
    except OSError:
        return macs
    for iface in ifaces:
        if iface == "lo":
            continue
        try:
            macs.add((net / iface / "address").read_text().strip().lower())
        except OSError:
            pass
        # Kablosuz kart rastgele MAC kullanıyorsa kalıcı adres ethtool'da.
        if not shutil.which("ethtool"):
            continue
        r = run_cmd(["ethtool", "-P", iface], timeout=5)
        if r.ok and r.stdout.strip():
            macs.add(r.stdout.strip().rsplit(" ", 1)[-1].lower())
    macs.discard("00:00:00:00:00:00")
    return macs


def _render_countdown_script() -> str:
    """Kullanıcı oturumunda gösterilen GTK geri sayım penceresi.

    Çağrı:  tiha-shutdown-countdown.py "<mod açıklaması>" <saniye>
    Exit kodu:  0 = kapatmaya devam et (zaman aşımı veya "Şimdi kapat")
                1 = ertelendi (kullanıcı "10 dakika ertele" tıkladı)

    Pencerede görünen metinler katalogdan gelir; betik üretilirken
    ``__T_*__`` işaretlerinin yerine Python metin sabiti olarak yazılır.
    """
    template = '''#!/usr/bin/env python3
"""TiHA otomatik kapanma geri sayım penceresi."""
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

mode_name = sys.argv[1] if len(sys.argv) > 1 else __T_DEFAULT_MODE__
total_seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 120


class CountdownWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title=__T_WINDOW_TITLE__)
        self.set_keep_above(True)
        self.set_position(Gtk.WindowPosition.CENTER_ALWAYS)
        self.set_default_size(440, 240)
        self.set_resizable(False)
        self.set_border_width(20)
        self.set_skip_taskbar_hint(False)

        self.total_seconds = total_seconds
        self.remaining = total_seconds
        self.exit_code = 0
        # Pencerenin sağ üstteki X ile kapatılması kapatmayı tetiklemesin —
        # bunun yerine sayaç sıfırdan başlasın. delete-event True dönerek
        # yok etmeyi engelliyoruz.
        self.connect("delete-event", self._on_delete_event)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.add(vbox)

        title = Gtk.Label()
        title.set_markup('<span size="15000" weight="bold">{}</span>'.format(
            GLib.markup_escape_text(__T_HEADLINE__)))
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

        postpone_btn = Gtk.Button(label=__T_POSTPONE__)
        postpone_btn.connect("clicked", self._on_postpone)
        btn_box.pack_start(postpone_btn, False, False, 0)

        shutdown_btn = Gtk.Button(label=__T_SHUTDOWN_NOW__)
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

    def _on_delete_event(self, _win, _event):
        # Sağ üst X: kullanıcı "buradayım" diyor.
        # 1) X11 idle sayacını sıfırla (xset ile best-effort) — böylece
        #    ana servis bir sonraki tetiklemede idle'ı sıfırdan sayar.
        # 2) exit_code=1 (postpone) ile kapan — servis 10 dk boyunca
        #    yeni popup açmaz; kullanıcı bu süre içinde aktifse zaten
        #    idle eşiği aşılmaz.
        try:
            import subprocess as _sp
            _sp.run(["xset", "s", "reset"],
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, timeout=2)
            _sp.run(["xset", "dpms", "force", "on"],
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, timeout=2)
        except Exception:
            pass
        self.exit_code = 1
        self._quit()
        return False  # yok etmeye izin ver (pencere kapansın)

    def _quit(self, *_args):
        Gtk.main_quit()


win = CountdownWindow()
win.show_all()
Gtk.main()
sys.exit(win.exit_code)
'''
    texts = {
        "__T_DEFAULT_MODE__": t("m11.countdown.default_mode"),
        "__T_WINDOW_TITLE__": t("m11.countdown.window_title"),
        "__T_HEADLINE__": t("m11.countdown.headline"),
        "__T_POSTPONE__": t("m11.countdown.postpone"),
        "__T_SHUTDOWN_NOW__": t("m11.countdown.shutdown_now"),
    }
    for token, text in texts.items():
        template = template.replace(token, repr(text))
    return template


DEFAULT_COUNTDOWN_SECONDS = 120


def _render_enhanced_service(
    countdown_seconds: int = DEFAULT_COUNTDOWN_SECONDS,
    exempt_macs: list[str] | tuple[str, ...] = (),
) -> str:
    """TiHA tarafından geliştirilmiş eta-shutdown service script'i.

    Orijinal eta-shutdown ``service.py`` dosyası bu içerikle değiştirilir.
    `main.py` (orijinal) her 60 saniyede ``service()`` fonksiyonunu çağırır.
    Aynı systemd unit ve aynı /etc/pardus/eta-shutdown.conf'u kullanır;
    yalnızca davranış (GUI geri sayım + erteleme) bu modülde tanımlanır.

    ``countdown_seconds`` — kapanmadan önce ekranda beklenecek uyarı
    süresi (saniye). Adımın form kutusundan gelir. Template metnindeki
    varsayılan ``COUNTDOWN_SECONDS = 120`` satırı bu değere göre
    değiştirilir.

    ``exempt_macs`` — otomatik kapanmanın uygulanmayacağı tahtaların MAC
    adresleri (``parse_mac_list`` çıktısı); ``EXEMPT_MACS`` satırına yazılır.
    """
    template = '''import os
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
COUNTDOWN_SECONDS = 120
# Otomatik kapanmanın uygulanmayacağı tahtalar (TiHA'da girilen MAC
# adresleri). Klon bunlardan birinde açılırsa yapılandırma bir kez paketin
# varsayılanına döner; sonra tahtadaki kullanıcı ETA Zamanlı Kapatma ile
# kendi ayarını yapabilir.
EXEMPT_MACS = []
EXEMPT_MARKER = "/var/lib/tiha/shutdown-exempt.done"

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


def local_macs():
    """Bu tahtanın ağ kartlarının MAC adresleri (anlık ve kalıcı)."""
    macs = set()
    try:
        ifaces = os.listdir("/sys/class/net")
    except OSError:
        return macs
    for iface in ifaces:
        if iface == "lo":
            continue
        try:
            with open("/sys/class/net/{}/address".format(iface)) as f:
                macs.add(f.read().strip().lower())
        except OSError:
            pass
        try:
            out = subprocess.run(
                ["ethtool", "-P", iface],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if out:
                macs.add(out.rsplit(" ", 1)[-1].lower())
        except Exception:
            pass
    macs.discard("00:00:00:00:00:00")
    return macs


def apply_exemption():
    """Tahta muaf listesindeyse yapılandırmayı bir kez varsayılana döndürür.

    Dönüş True ise servis bu tahtada kapanma yapmamalı (varsayılana dönüş
    yazılamadı). İşaret dosyası tahtanın kendi adresini taşır; imajla gelen
    dosya klonu etkilemez, yerel ayar sonraki açılışlarda korunur.
    """
    if not EXEMPT_MACS:
        return False
    matched = sorted(local_macs() & set(EXEMPT_MACS))
    if not matched:
        return False
    try:
        with open(EXEMPT_MARKER) as f:
            done = f.read().split()
    except OSError:
        done = []
    if any(mac in done for mac in matched):
        return False
    defaults = configparser.ConfigParser()
    defaults["AUTO_SHUTDOWN"] = {"enabled": "False", "hour": "0", "minute": "0"}
    defaults["TIMED_MODE"] = {"mode": "none", "hour": "0", "minute": "5"}
    try:
        with open(CONFIG_FILE, "w") as f:
            defaults.write(f)
        os.makedirs(os.path.dirname(EXEMPT_MARKER), exist_ok=True)
        with open(EXEMPT_MARKER, "w") as f:
            f.write("\\n".join(matched) + "\\n")
    except OSError as exc:
        log("TiHA: muaf tahta yapılandırması yazılamadı: {}".format(exc))
        return True
    log("TiHA: bu tahta ({}) otomatik kapanmadan muaf; yapılandırma "
        "varsayılana döndü".format(", ".join(matched)))
    return False


# State
init = False
ignore_auto = False
postpone_until = 0.0  # bu zamana kadar uyarı suspended
exempt_checked = False
exempt_block = False


def service():
    global init, ignore_auto, postpone_until, exempt_checked, exempt_block

    # Muaf tahta denetimi yapılandırma okunmadan önce, servis başına bir kez.
    if not exempt_checked:
        exempt_checked = True
        try:
            exempt_block = apply_exemption()
        except Exception as exc:
            log("TiHA: muaf tahta denetimi başarısız: {}".format(exc))
    if exempt_block:
        return

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

    # ---- TIMED MODE — Kullanılmadığında kapatma ----
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
                __T_REASON_IDLE__.format(minutes=minute)
            )
            if not proceed:
                postpone_until = time.time() + 600
                log("Kullanılmadığında kapatma 10 dakika ertelendi")
                return
            log("TiHA: Kullanılmadığında kapatma gerçekleştiriliyor")
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
                __T_REASON_FIXED__.format(
                    time="{:02d}:{:02d}".format(auto_hour, auto_minute))
            )
            if not proceed:
                postpone_until = time.time() + 600
                log("Sabit saat kapatma 10 dakika ertelendi")
                return
            log("TiHA: Sabit saat kapatma gerçekleştiriliyor")
            os.system("poweroff")
'''
    # Geri sayım penceresinde görünen mod açıklamaları. Yer tutucular
    # betik çalışırken doldurulur; burada olduğu gibi bırakılır.
    texts = {
        "__T_REASON_IDLE__": t("m11.countdown.reason_idle", minutes="{minutes}"),
        "__T_REASON_FIXED__": t("m11.countdown.reason_fixed", time="{time}"),
    }
    for token, text in texts.items():
        template = template.replace(token, repr(text))
    template = template.replace(
        "EXEMPT_MACS = []", f"EXEMPT_MACS = {list(exempt_macs)!r}", 1,
    )
    return template.replace(
        f"COUNTDOWN_SECONDS = {DEFAULT_COUNTDOWN_SECONDS}",
        f"COUNTDOWN_SECONDS = {int(countdown_seconds)}",
        1,
    )


def _current_exempt_macs() -> list[str]:
    """Yüklü service.py'deki muaf tahta listesi (TiHA sürümü değilse boş)."""
    try:
        txt = ETA_SHUTDOWN_SERVICE.read_text(encoding="utf-8")
    except OSError:
        return []
    m = re.search(r"^EXEMPT_MACS = (\[.*\])$", txt, re.MULTILINE)
    if not m:
        return []
    try:
        value = ast.literal_eval(m.group(1))
    except (ValueError, SyntaxError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


def _current_countdown_seconds() -> int:
    """Yüklü service.py dosyasından mevcut geri sayım süresini oku."""
    import re as _re
    try:
        txt = ETA_SHUTDOWN_SERVICE.read_text(encoding="utf-8")
    except OSError:
        return DEFAULT_COUNTDOWN_SECONDS
    m = _re.search(r"COUNTDOWN_SECONDS\s*=\s*(\d+)", txt)
    return int(m.group(1)) if m else DEFAULT_COUNTDOWN_SECONDS


# ---------------------------------------------------------------------------
# Şu anki güç ayarlarını okuma — preview'ün en üstündeki bilgi bloğu.
# Sistemi değiştirmez; yalnızca xset / gsettings / logind kaynaklarını okur.
# ---------------------------------------------------------------------------


def _fmt_seconds(sec: int | None) -> str:
    if sec is None:
        return t("m11.fmt.unreadable")
    if sec <= 0:
        return t("m11.fmt.off")
    if sec >= 60 and sec % 60 == 0:
        return t("m11.fmt.minutes", minutes=sec // 60)
    if sec >= 60:
        return t("m11.fmt.minutes_seconds", minutes=sec // 60, seconds=sec % 60)
    return t("m11.fmt.seconds", seconds=sec)


def _fmt_microseconds(usec: int | None) -> str:
    if usec is None:
        return t("m11.fmt.unreadable")
    if usec <= 0:
        return t("m11.fmt.off")
    return _fmt_seconds(usec // 1_000_000)


def _read_current_power_settings() -> list[tuple[str, str]]:
    """Sistemin şu anki güç yönetimi ayarlarını okuyup (etiket, değer)
    çiftleri döndürür. Kaynak sırası:

    * ``xset q`` — X11 DPMS zaman aşımları (Standby / Suspend / Off)
    * ``gsettings`` — Cinnamon/GNOME 'sleep-display' ve 'sleep-inactive'
    * ``systemctl show systemd-logind`` — IdleAction, IdleActionUSec,
      HandleLidSwitch, HandlePowerKey
    """
    import re as _re

    rows: list[tuple[str, str]] = []

    env = _find_active_graphical_session()
    sudo_env = (
        ["sudo", "-u", env["USER"], "env"]
        + [f"{k}={v}" for k, v in env.items()]
        if env else None
    )

    # --- X11 DPMS ---
    if sudo_env:
        r = run_cmd(sudo_env + ["xset", "q"], timeout=5)
        if r.ok:
            standby = suspend = off = None
            saver_timeout = None
            for m in _re.finditer(r"\b(Standby|Suspend|Off):\s*(\d+)", r.stdout):
                v = int(m.group(2))
                if m.group(1) == "Standby":
                    standby = v
                elif m.group(1) == "Suspend":
                    suspend = v
                else:
                    off = v
            # Screen Saver bloğu birden fazla satır — 'timeout:' değerini
            # başlıktan sonra ilk gördüğünde al. DOTALL şart.
            m = _re.search(r"Screen Saver:.*?timeout:\s*(\d+)", r.stdout, _re.DOTALL)
            if m:
                saver_timeout = int(m.group(1))
            dpms_on = "DPMS is Enabled" in r.stdout
            rows.append((
                t("m11.power.x11_saver"),
                _fmt_seconds(saver_timeout) if saver_timeout is not None
                else t("m11.fmt.unreadable"),
            ))
            rows.append((
                t("m11.power.x11_dpms"),
                (
                    f"{_fmt_seconds(standby)} / "
                    f"{_fmt_seconds(suspend)} / "
                    f"{_fmt_seconds(off)}"
                    + ("" if dpms_on else t("m11.power.dpms_disabled"))
                ),
            ))

    # --- Cinnamon / GNOME gsettings ---
    if sudo_env:
        schemas_r = run_cmd(sudo_env + ["gsettings", "list-schemas"], timeout=5)
        loaded = set(schemas_r.stdout.split()) if schemas_r.ok else set()
        candidate_schemas = [
            ("Cinnamon", "org.cinnamon.settings-daemon.plugins.power"),
            ("GNOME", "org.gnome.settings-daemon.plugins.power"),
        ]
        for env_label, schema in candidate_schemas:
            if schema not in loaded:
                continue
            def _read(key: str) -> str | None:
                r = run_cmd(sudo_env + ["gsettings", "get", schema, key], timeout=5)
                if not r.ok:
                    return None
                return r.stdout.strip().replace("uint32 ", "").strip("'")
            display_ac = _read("sleep-display-ac")
            display_batt = _read("sleep-display-battery")
            inactive_ac = _read("sleep-inactive-ac-timeout")
            inactive_ac_type = _read("sleep-inactive-ac-type")
            inactive_batt = _read("sleep-inactive-battery-timeout")
            inactive_batt_type = _read("sleep-inactive-battery-type")

            def _sec(s: str | None) -> int | None:
                if s is None:
                    return None
                try:
                    return int(s)
                except ValueError:
                    return None

            if display_ac is not None or display_batt is not None:
                rows.append((
                    t("m11.power.display_sleep", desktop=env_label),
                    f"{_fmt_seconds(_sec(display_ac))} / {_fmt_seconds(_sec(display_batt))}",
                ))
            if inactive_ac is not None or inactive_batt is not None:
                ac_txt = _fmt_seconds(_sec(inactive_ac))
                batt_txt = _fmt_seconds(_sec(inactive_batt))
                types = f" ({inactive_ac_type or '-'} / {inactive_batt_type or '-'})"
                rows.append((
                    t("m11.power.inactive_sleep", desktop=env_label),
                    f"{ac_txt} / {batt_txt}{types}",
                ))

    # --- systemd-logind ---
    r = run_cmd(
        ["systemctl", "show", "systemd-logind",
         "--property=IdleAction",
         "--property=IdleActionUSec",
         "--property=HandleLidSwitch",
         "--property=HandleLidSwitchDocked",
         "--property=HandlePowerKey"],
        timeout=5,
    )
    if r.ok and r.stdout.strip():
        props: dict[str, str] = {}
        for line in r.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()
        idle_action = props.get("IdleAction", "")
        idle_usec_raw = props.get("IdleActionUSec", "")
        try:
            idle_usec = int(idle_usec_raw)
        except ValueError:
            idle_usec = None
        if idle_action:
            rows.append((
                t("m11.power.logind_idle"),
                f"{idle_action}, {_fmt_microseconds(idle_usec)}",
            ))
        lid = props.get("HandleLidSwitch")
        if lid:
            rows.append((t("m11.power.logind_lid"), lid))
        power = props.get("HandlePowerKey")
        if power:
            rows.append((t("m11.power.logind_power_key"), power))

    if not rows:
        rows.append((
            t("m11.power.source_label"),
            t("m11.power.unavailable"),
        ))
    return rows


class PowerManagementModule(Module):
    id = "m11_power_management"
    title = t("m11.title")
    sidebar_title = t("m11.sidebar_title")
    apply_hint = t("m11.apply_hint")
    rationale = t("m11.rationale")
    extra_links = [
        {"label": t("m11.link_eta_shutdown"), "action": "launch_eta_shutdown_gui_action"},
    ]

    def preview(self) -> str:
        """m08 stiliyle hizalı key-value + girintili dash liste."""
        import datetime

        current_time = datetime.datetime.now().strftime("%H:%M")
        eta_config_exists = ETA_SHUTDOWN_CONFIG.exists()
        result = run_cmd(["systemctl", "is-active", "eta-shutdown"])
        eta_service_running = result.ok and "active" in result.stdout

        # Şu anki güç ayarları — mevcut sistemden okunuyor, TiHA yönetimi
        # dışındaki her şey (Cinnamon, GNOME, X11 DPMS, logind) burada
        # görünsün ki bu adım hangi çakışmalara girebileceğini kullanıcı
        # önden görsün.
        power_rows = _read_current_power_settings()
        power_label_width = max((len(label) for label, _ in power_rows), default=0)
        power_block: list[str] = []
        power_block.append(t("m11.preview.power_header"))
        for label, value in power_rows:
            power_block.append(f"  {label.ljust(power_label_width)} : {value}")
        power_block.append("")

        if not eta_config_exists:
            lines = power_block + [
                t("m11.preview.status_unconfigured", time=current_time),
                "",
                t("m11.preview.unconfigured_plan"),
            ]
            return "\n".join(lines)

        try:
            config = configparser.ConfigParser()
            config.read(ETA_SHUTDOWN_CONFIG)
            auto_enabled = config.getboolean("AUTO_SHUTDOWN", "enabled", fallback=False)
            auto_hour = config.get("AUTO_SHUTDOWN", "hour", fallback="0")
            auto_minute = config.get("AUTO_SHUTDOWN", "minute", fallback="0")
            timed_mode = config.get("TIMED_MODE", "mode", fallback="none")
            timed_minute = config.get("TIMED_MODE", "minute", fallback="0")
            enhanced = ETA_SHUTDOWN_SERVICE_BACKUP.exists()
        except Exception as exc:
            return "\n".join(power_block) + t("m11.preview.config_error", error=exc)

        lines: list[str] = list(power_block)
        lines.append(t(
            "m11.preview.status",
            state=t("m11.preview.state_enhanced") if enhanced
            else t("m11.preview.state_original"),
            time=current_time,
        ))
        lines.append(t(
            "m11.preview.service",
            state=t("m11.preview.service_running") if eta_service_running
            else t("m11.preview.service_stopped"),
        ))

        # Sabit saat
        if auto_enabled:
            countdown = ""
            try:
                from datetime import datetime as _dt, time as _t
                now = _dt.now()
                shutdown_time = _dt.combine(
                    now.date(), _t(int(auto_hour), int(auto_minute))
                )
                if shutdown_time < now:
                    shutdown_time = shutdown_time.replace(day=now.day + 1)
                time_diff = shutdown_time - now
                hours, remainder = divmod(time_diff.seconds, 3600)
                minutes, _ = divmod(remainder, 60)
                if time_diff.days == 0:
                    countdown = t("m11.preview.time_left", hours=hours, minutes=minutes)
            except Exception:
                pass
            lines.append(t(
                "m11.preview.fixed_on",
                time=f"{auto_hour.zfill(2)}:{auto_minute.zfill(2)}",
                countdown=countdown,
            ))
        else:
            lines.append(t("m11.preview.fixed_off"))

        # Kullanılmadığında kapatma
        if timed_mode != "none":
            lines.append(t("m11.preview.idle_on", minutes=timed_minute))
        else:
            lines.append(t("m11.preview.idle_off"))

        exempt = _current_exempt_macs()
        if exempt:
            lines.append(t("m11.preview.exempt", count=len(exempt)))
            if set(exempt) & _local_macs():
                lines.append(t("m11.preview.exempt_this_board"))

        # Config son değişiklik
        try:
            import os as _os
            mtime = _os.path.getmtime(ETA_SHUTDOWN_CONFIG)
            mtime_str = datetime.datetime.fromtimestamp(mtime).strftime("%H:%M")
            lines.append(t("m11.preview.config_updated", time=mtime_str))
        except OSError:
            pass

        current_countdown = _current_countdown_seconds()
        if current_countdown % 60 == 0:
            countdown_label = t("m11.preview.duration_minutes", minutes=current_countdown // 60)
        else:
            countdown_label = t("m11.preview.duration_seconds", seconds=current_countdown)
        lines.append(t("m11.preview.countdown", duration=countdown_label))
        lines.append("")
        lines.append(t("m11.preview.before_shutdown", duration=countdown_label))
        return "\n".join(lines)

    def current_exempt_macs(self) -> str:
        """"Muaf tahtalar" kutusunun açılışta görüneceği liste."""
        return "\n".join(_current_exempt_macs())

    def suggested_countdown_seconds(self) -> int:
        """"Geri sayım süresi" kutusunun açılışta görüneceği değer.

        Yüklü service.py dosyasından okunur; yoksa varsayılan 120."""
        return _current_countdown_seconds()

    # --- Onay kutularının sistemden dolan durumu -------------------------
    # Bu iki kutu "şu an açık mı" sorusunun cevabıdır, bir tercih değil.
    # Geri alma eta-shutdown yapılandırmasını sıfırlıyor ama kutular
    # işaretli kalıyordu; adıma tekrar girildiğinde de sistemde kurulu
    # olan kapanma kutuda görünmüyordu. Saat, dakika ve geri sayım
    # süresi kutuları bilinçli olarak dışarıda: onlar kullanıcının
    # girdiği tercih, sistem durumu değil.

    def auto_shutdown_active(self) -> bool:
        """Sabit saatte kapanma sistemde açık mı?"""
        return self.get_current_config().get("auto_enabled") == "True"

    def idle_shutdown_active(self) -> bool:
        """Boştayken kapanma sistemde açık mı?"""
        return self.get_current_config().get("idle_enabled") == "True"

    def apply(self, params=None, progress=None) -> ApplyResult:
        params = params or {}

        # Parametreleri al
        auto_enabled = str(params.get("auto_enabled", "False")).lower() == "true"
        auto_hour = int(params.get("auto_hour", 22))
        auto_minute = int(params.get("auto_minute", 0))

        idle_enabled = str(params.get("idle_enabled", "True")).lower() == "true"
        idle_minute = int(params.get("idle_minute", 15))

        # Geri sayım (uyarı diyaloğunun ekranda kalma) süresi — kullanıcı
        # yapılandırabilir. Alt sınır 30 sn (kullanıcının pencereyi
        # görmesi + tepki verebilmesi), üst sınır 600 sn (10 dk; daha
        # uzun anlamsız çünkü erteleme düğmesi zaten +10 dk veriyor).
        try:
            countdown_seconds = int(params.get("countdown_seconds", DEFAULT_COUNTDOWN_SECONDS))
        except (TypeError, ValueError):
            countdown_seconds = DEFAULT_COUNTDOWN_SECONDS
        if countdown_seconds < 30:
            countdown_seconds = 30
        elif countdown_seconds > 600:
            countdown_seconds = 600

        # Muaf tahtalar: iki mod da kapalıyken liste anlamsız (kutu gizli).
        exempt_macs: list[str] = []
        if auto_enabled or idle_enabled:
            exempt_macs, invalid_macs = parse_mac_list(params.get("exempt_macs", ""))
            if invalid_macs:
                return ApplyResult(
                    False,
                    t("m11.apply.error_macs"),
                    details=t("m11.apply.error_macs_details",
                              items=", ".join(invalid_macs)),
                )
        this_board_exempt = sorted(set(exempt_macs) & _local_macs())

        # Ekran-blank ile idle kapanma süresinin çakışma kontrolü (yumuşak uyarı).
        # Geri sayım diyalogu idle_threshold anında doğar; doğum anında ekran
        # açık olmalı, yoksa kullanıcı 2 dk'lık erteleme penceresini hiç görmez.
        blank_warning: str | None = None
        if idle_enabled:
            blank_sec = screen_blank_seconds()
            if blank_sec is not None:
                max_idle_min = max(0, (blank_sec - _BLANK_SAFETY_SEC) // 60)
                if idle_minute > max_idle_min:
                    blank_warning = t(
                        "m11.apply.blank_warning",
                        blank_minutes=blank_sec // 60,
                        idle_minutes=idle_minute,
                        max_idle_minutes=max_idle_min,
                    )
                    log.warning(blank_warning)
                    if progress:
                        progress(blank_warning)

        if progress:
            progress(t("m11.apply.progress_backup"))

        # Mevcut service dosyasını yedekle (ilk kez ise)
        if not ETA_SHUTDOWN_SERVICE_BACKUP.exists():
            try:
                shutil.copy2(ETA_SHUTDOWN_SERVICE, ETA_SHUTDOWN_SERVICE_BACKUP)
                log.info("Orijinal eta-shutdown service yedeklendi")
            except OSError as exc:
                return ApplyResult(False, t("m11.apply.error_backup", error=exc))

        if progress:
            progress(t("m11.apply.progress_service"))

        # Geliştirilmiş service dosyasını yaz
        try:
            ETA_SHUTDOWN_SERVICE.write_text(
                _render_enhanced_service(countdown_seconds, exempt_macs),
                encoding="utf-8",
            )
            ETA_SHUTDOWN_SERVICE.chmod(0o755)
        except OSError as exc:
            return ApplyResult(False, t("m11.apply.error_service", error=exc))

        if progress:
            progress(t("m11.apply.progress_countdown"))

        # Kullanıcı oturumunda gösterilecek GTK geri sayım penceresini kur
        try:
            COUNTDOWN_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
            COUNTDOWN_SCRIPT.write_text(_render_countdown_script(), encoding="utf-8")
            COUNTDOWN_SCRIPT.chmod(0o755)
        except OSError as exc:
            return ApplyResult(False, t("m11.apply.error_countdown", error=exc))

        if progress:
            progress(t("m11.apply.progress_config"))

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
            return ApplyResult(False, t("m11.apply.error_config", error=exc))

        # İmajın hazırlandığı tahta listedeyse burada ayarlar etkin kalsın:
        # servis bu adresler için "varsayılana döndürüldü" sayar. Klonlar
        # kendi adresleriyle değerlendirilir.
        try:
            if this_board_exempt:
                EXEMPT_MARKER.parent.mkdir(parents=True, exist_ok=True)
                EXEMPT_MARKER.write_text("\n".join(this_board_exempt) + "\n",
                                         encoding="utf-8")
            elif EXEMPT_MARKER.exists():
                EXEMPT_MARKER.unlink()
        except OSError as exc:
            log.warning("Muaf tahta işareti yazılamadı: %s", exc)

        if progress:
            progress(t("m11.apply.progress_restart"))

        # Servisi yeniden başlat
        restart_result = run_cmd(["systemctl", "restart", "eta-shutdown"])
        if not restart_result.ok:
            return ApplyResult(False, t("m11.apply.error_restart"),
                               details=restart_result.stderr)

        # Servisin aktif olduğunu doğrula
        enable_result = run_cmd(["systemctl", "enable", "eta-shutdown"])
        if not enable_result.ok:
            log.warning("eta-shutdown servisi etkinleştirilemedi: %s", enable_result.stderr)

        if progress:
            progress(t("m11.apply.progress_done"))

        # Özet bilgi
        if countdown_seconds % 60 == 0:
            countdown_label = t("m11.preview.duration_minutes", minutes=countdown_seconds // 60)
        else:
            countdown_label = t("m11.preview.duration_seconds", seconds=countdown_seconds)
        details_lines = [
            t("m11.apply.details_installed", backup=ETA_SHUTDOWN_SERVICE_BACKUP,
              config=ETA_SHUTDOWN_CONFIG),
            ""
        ]

        if auto_enabled:
            details_lines.extend([
                t("m11.apply.details_fixed", hour=auto_hour, minute=auto_minute),
                t("m11.apply.details_countdown_bullets", countdown=countdown_label),
            ])

        if idle_enabled:
            details_lines.extend([
                t("m11.apply.details_idle", minutes=idle_minute),
                t("m11.apply.details_countdown_bullets", countdown=countdown_label),
            ])
            if blank_warning:
                details_lines.extend(["", blank_warning])

        if not auto_enabled and not idle_enabled:
            details_lines.append(t("m11.apply.details_both_off"))

        if exempt_macs:
            details_lines.extend([
                "",
                t("m11.apply.details_exempt", count=len(exempt_macs)),
                *(f"   - {mac}" for mac in exempt_macs),
            ])
            if this_board_exempt:
                details_lines.append(t("m11.apply.details_exempt_this_board"))

        details_lines.extend([
            "",
            t("m11.apply.details_management"),
        ])

        if auto_enabled or idle_enabled:
            summary = t("m11.apply.summary_active")
        else:
            summary = t("m11.apply.summary_installed")

        return ApplyResult(
            True,
            summary,
            details="\n".join(details_lines),
            data={"exempt_macs": exempt_macs},
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
                removed_items.append(t("m11.undo.service_restored"))
            except OSError as exc:
                log.warning("Service geri yükleme başarısız: %s", exc)

        # Geri sayım penceresi scriptini kaldır
        if COUNTDOWN_SCRIPT.exists():
            try:
                COUNTDOWN_SCRIPT.unlink()
                removed_items.append(t("m11.undo.countdown_removed"))
            except OSError as exc:
                log.warning("Geri sayım scripti silinemedi: %s", exc)

        try:
            EXEMPT_MARKER.unlink()
        except OSError:
            pass

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
            removed_items.append(t("m11.undo.config_reset"))
        except OSError as exc:
            log.warning("Konfigürasyon sıfırlama başarısız: %s", exc)

        # Servisi yeniden başlat
        run_cmd(["systemctl", "restart", "eta-shutdown"])

        summary = t("m11.undo.summary")
        details = (
            "\n".join(f"• {item}" for item in removed_items) if removed_items
            else t("m11.undo.nothing_removed")
        )

        return ApplyResult(True, summary, details=details)

    def launch_eta_shutdown_gui_action(self, params: dict | None = None) -> ApplyResult:
        """ETA Zamanlı Kapatma GUI'sini kullanıcının X oturumunda açar."""
        binary = Path("/usr/bin/eta-shutdown")
        if not binary.exists():
            return ApplyResult(
                False,
                t("m11.launch.not_found"),
                details=t("m11.launch.not_found_details", path=binary),
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
                t("m11.launch.failed"),
                details=str(exc),
            )

        return ApplyResult(
            True,
            t("m11.launch.opened", user=user),
            details=t("m11.launch.opened_details"),
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
                "idle_minute": str(timed_minute),
                "countdown_seconds": str(_current_countdown_seconds()),
            }
        except Exception as exc:
            log.warning("Config okuma hatası: %s", exc)
            return {}