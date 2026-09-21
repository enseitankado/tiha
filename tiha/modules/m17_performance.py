"""Modül 17 — Başarım: oturum kalıntıları ve ETA Hafif Mod.

Ne yapar?
1. **Eski oturum kalıntılarını temizle.** Pardus ETAP'ta (Debian 12,
   systemd 252) ``systemd-logind`` varsayılan olarak
   ``KillUserProcesses=no`` ile çalışır: öğretmen oturumunu kapattığında
   o oturumdan kalan süreçler (kapatılmadan bırakılan Firefox/Chrome ve
   bütün alt süreçleri, kullanıcı servisleri) arka planda yaşamaya
   devam eder, oturum ``closing`` durumunda asılı kalır. Gün içinde
   birkaç öğretmen girip çıktığında bellek ve işlemci birikerek tükenir.
   Bu adım ``/etc/systemd/logind.conf.d/90-tiha-oturum-kalintilari.conf``
   ile ``KillUserProcesses=yes`` yazar; logind oturum kapanınca oturumun
   cgroup'undaki her süreci sonlandırır (çift fork ile "kaçan" süreçler
   de cgroup'ta kaldığı için yakalanır). systemd 252'de logind
   yapılandırmayı yeniden okuyamaz (``CanReload=no``) ve çalışan grafik
   oturumun altında yeniden başlatılması riskli olduğundan ayar **bir
   sonraki açılışta** etkin olur. Bu bir system-wide logind ayarıdır;
   klon tahtaya sonradan eklenen hesaplar dahil, root dışındaki bütün
   kullanıcılara otomatik uygulanır.

2. **ETA Hafif Mod.** Pardus'un ``eta-light-mode`` paketi (yoksa)
   kurulur ve paketin "Tüm kullanıcılara uygula" düzeni kurulur:
   seçilen ayarlar ``/etc/eta-light-mode/settings.json``'a, autostart
   girdisi ``/etc/xdg/autostart/``'a yazılır; her kullanıcı oturum
   açtığında ``eta-light-mode --apply-config`` ayarları kendi
   oturumuna uygular. Yazma işi paketin kendi yardımcısı
   (``Action.py enable``) ile yapılır. JSON'a yalnızca seçilen
   anahtarlar ``true`` olarak yazılır: ``false`` paketin gözünde
   "dokunma" değil "koda gömülü normal değere döndür" demektir.
   Çalışan oturuma canlı uygulanmaz (bkz. m14 postmortem: canlı
   Cinnamon/Nemo müdahaleleri gerçek donanımda güvenilmez); etki bir
   sonraki oturum açılışında görülür.

Doğrulama durumu.
İki mekanizma da gerçek tahta donanımında henüz doğrulanmadı.
``eta-light-mode``'un JSON anahtarları paketin resmî bir arayüzü değil;
sürüm ve anahtar adları her uygulamada denetlenir.

Geri al.
İlk uygulamadan önceki durum ``original.json``'a kaydedilir. Geri alma
yalnızca TiHA'nın dokunduğu parçaları o duruma döndürür: logind drop-in
silinir (ya da önceki içeriği geri yazılır), hafif mod dosyaları silinir
ya da yedekten geri yüklenir, paketi TiHA kurduysa ``apt-get purge``
edilir. logind değişikliği yine açılışta etkin olur.

Hafif modun asimetrisi ve TiHA'nın kapattığı boşluk.
``eta-light-mode``'un kendi "kapat" yolu (``Action.py disable``) yalnız
``/etc/eta-light-mode/settings.json`` ile autostart girdisini siler; ayarlar
ise her oturum açılışında kullanıcının **kendi dconf'una** yazıldığı için
daha önce giriş yapmış hesaplarda olduğu gibi kalır — "hafif mod kapalı ama
ekran hâlâ hafif". Paketi ``apt remove`` etmek daha da kötü: bu iki dosya
dpkg'nin dosya listesinde olmadığından yerinde kalır, üstelik geri almak
için gereken araç silinir ve imaj depodaki standart kurulumdan uzaklaşır.
Bu yüzden TiHA hafif modu uygularken her hesabın ilgili dconf değerlerini
ve ``~/.config/cinnamon-monitors.xml`` dosyasını yedekler; geri alırken
(ya da adımdaki kutu kaldırılıp uygulandığında) hem sistem dosyalarını hem
de hesapların ayarlarını o hâle döndürür. Paket kaldırılmaz. Bir anahtara
yalnız oradaki değer hafif modun yazdığı değerse dokunulur; kullanıcının
kendi seçtiği değer ezilmez. Hafif mod uygulandıktan sonra açılmış
hesaplarda TiHA öncesi değer bulunmadığından anahtar silinir, yani sistem
varsayılanına döner.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from ..core.i18n import t
from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.utils import run_cmd, run_cmd_stream

log = get_logger(__name__)

# --- Oturum kalıntıları (systemd-logind) ------------------------------------

LOGIND_DROPIN = Path("/etc/systemd/logind.conf.d/90-tiha-oturum-kalintilari.conf")
# systemd drop-in'leri bütün dizinlerden dosya adına göre sıralayıp okur;
# sonra gelen kazanır. Çakışma taraması bu dizinlerin hepsine bakar.
LOGIND_DROPIN_DIRS = (
    Path("/etc/systemd/logind.conf.d"),
    Path("/run/systemd/logind.conf.d"),
    Path("/usr/local/lib/systemd/logind.conf.d"),
    Path("/usr/lib/systemd/logind.conf.d"),
)
LOGIND_DROPIN_CONTENT = """# TiHA — Başarım (Deneysel) adımı tarafından yazılmıştır.
# Kullanıcı oturumu kapandığında o oturumdan kalan bütün süreçler
# (kapatılmadan bırakılan tarayıcılar ve alt süreçleri dahil) sonlandırılır.
# systemd-logind bu dosyayı yalnızca başlarken (açılışta) okur.
[Login]
KillUserProcesses=yes
KillExcludeUsers=root
"""

# --- ETA Hafif Mod (eta-light-mode) -----------------------------------------

LIGHT_PKG = "eta-light-mode"
LIGHT_SRC = Path("/usr/share/eta/eta-light-mode/src")
LIGHT_ACTION = LIGHT_SRC / "Action.py"
LIGHT_SETTINGS_PY = LIGHT_SRC / "Settings.py"
LIGHT_SETTINGS = Path("/etc/eta-light-mode/settings.json")
LIGHT_AUTOSTART = Path("/etc/xdg/autostart/tr.org.eta.light-mode-autostart.desktop")
# JSON anahtarlarının ve Action.py davranışının okunup doğrulandığı sürümler.
LIGHT_TESTED_VERSIONS = ("0.2.3",)

# Form alanı → eta-light-mode ayar anahtarları. Yazı ve ikon küçültme
# başarım ayarı değil, 1600x900'ün büyüttüğü arayüzün telafisi; bu yüzden
# çözünürlük düşürmeyle birlikte gelir, tek başına seçilmez.
LIGHT_FIELDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("lm_effects", "Animasyonlar kapalı", ("effects",)),
    ("lm_compositor", "Tam ekran pencereler doğrudan çizilir", ("compositor",)),
    ("lm_thumbnails", "Resim önizlemeleri kapalı", ("thumbnails",)),
    ("lm_directory_counts", "Klasör öğesi sayımı kapalı", ("directory-item-counts",)),
    ("lm_app_monitoring", "Uygulama izleme kapalı", ("app-monitoring",)),
    ("lm_low_resolution", "Çözünürlük 1600x900 (+ yazı/ikon telafisi)",
     ("low-resolution", "text-scaling", "file-icon-size")),
    ("lm_low_refresh_rate", "Yenileme hızı 50 Hz", ("low-refresh-rate",)),
)

# --- Hafif modun kullanıcı başına bıraktığı iz ------------------------------
# eta-light-mode sistem tarafında yalnız iki dosya tutar; asıl ayarlar her
# oturum açılışında autostart ile kullanıcının KENDİ dconf'una yazılır.
# Paketin "kapat" yolu (Action.py disable) yalnız o iki sistem dosyasını
# siler; daha önce giriş yapmış kullanıcıların dconf değerleri olduğu gibi
# kalır — yani paketin geri alması asimetrik. TiHA bu izi de temizlemek
# zorunda, yoksa tahtada "hafif mod kapalı ama ekran hâlâ hafif" kalır.
#
# (eta-light-mode ayar adı, dconf yolu, hafif değer). Değerler dconf
# sözdiziminde; Cinnamon.py'deki *_LOW sabitlerinden birebir alındı.
LIGHT_DCONF: tuple[tuple[str, str, str], ...] = (
    ("effects", "/org/cinnamon/desktop-effects-workspace", "false"),
    ("compositor", "/org/cinnamon/muffin/unredirect-fullscreen-windows", "true"),
    ("thumbnails", "/org/nemo/preferences/show-image-thumbnails", "'never'"),
    ("directory-item-counts", "/org/nemo/preferences/show-directory-item-counts", "'never'"),
    ("app-monitoring", "/org/cinnamon/enable-app-monitoring", "false"),
    ("text-scaling", "/org/cinnamon/desktop/interface/font-name", "'Ubuntu Regular 9.5'"),
    ("text-scaling", "/org/nemo/desktop/font", "'Ubuntu Regular 9.5'"),
    ("text-scaling", "/org/cinnamon/desktop/wm/preferences/titlebar-font", "'Ubuntu Bold 9.5'"),
    ("file-icon-size", "/org/nemo/icon-view/default-zoom-level", "'small'"),
)
# Yol → o yolda "hafif" sayılan değerler. text-scaling gibi bir ayar birden
# çok yola yazdığı için ters tabloyu bir kez kuruyoruz.
LIGHT_DCONF_VALUES: dict[str, set[str]] = {}
for _lm_key, _lm_path, _lm_value in LIGHT_DCONF:
    LIGHT_DCONF_VALUES.setdefault(_lm_path, set()).add(_lm_value)

# Çözünürlük ve yenileme hızı dconf'a değil bu dosyaya yazılır
# (Screen._write_monitors_xml); Muffin açılışta oradan geri yükler.
USER_MONITORS_XML = ".config/cinnamon-monitors.xml"
USER_DCONF_DB = ".config/dconf/user"
# Kullanıcı başına yedeklerin state dizini içindeki alt klasörü.
USER_BACKUP_DIR = "kullanici"


# --- Fare imleci (ekran modu değişimi) --------------------------------------
# Çözünürlük ya da tazeleme frekansı değiştirilip uygulandığında fare imleci
# görünmez oluyor: odak ve sol/sağ tıklama çalışmayı sürdürüyor, yalnız imleç
# çizilmiyor; fare çıkarılıp takılınca düzeliyor. Neden, mod değişiminde
# donanımsal imleç düzlemi yeniden kurulurken imleç görüntüsünün geri
# yüklenmemesi. Birbirinden bağımsız iki çare sunulur; hangisinin yettiği
# gerçek tahtada denenecek. Hafif modun çözünürlük/Hz ayarları da aynı
# tetikleyiciyi kullandığı için bu seçenekler onunla birlikte anlamlıdır.
CURSOR_XORG_CONF = Path("/etc/X11/xorg.conf.d/20-tiha-imlec.conf")
CURSOR_SCRIPT = Path("/usr/local/bin/tiha-imlec-tazele.py")
CURSOR_AUTOSTART = Path("/etc/xdg/autostart/tr.org.tiha.imlec-tazele.desktop")
XORG_LOG = Path("/var/log/Xorg.0.log")

# Form seçenekleri. ETAP imajında eski xserver-xorg-video-intel (2.99.917)
# kurulu geliyor ve Intel tahtalarda X onu seçiyor; o sürücünün SWcursor
# seçeneği de yok. Bu yüzden ilk çare modesetting'e geçmek, ikincisi ona ek
# olarak donanımsal imleci tamamen kapatmak.
CURSOR_XORG_OFF = t("m17.params.cursor_xorg_fix.opt_off")
CURSOR_XORG_MODESETTING = t("m17.params.cursor_xorg_fix.opt_modesetting")
CURSOR_XORG_SWCURSOR = t("m17.params.cursor_xorg_fix.opt_swcursor")
CURSOR_XORG_CHOICES = {
    CURSOR_XORG_OFF: None,
    CURSOR_XORG_MODESETTING: False,
    CURSOR_XORG_SWCURSOR: True,
}


def _cursor_xorg_content(swcursor: bool) -> str:
    """Ekran kartını modesetting'e alan, istenirse donanımsal imleci kapatan
    Xorg parçası. OutputClass çekirdek sürücüsüne göre eşleştiği için Intel
    ve AMD tahtalarda tek dosya yeter."""
    swline = '    Option "SWcursor" "on"\n' if swcursor else ""
    return (
        "# TiHA — Başarım (Deneysel) adımı tarafından yazılmıştır.\n"
        "# Ekran modu (çözünürlük/tazeleme) değiştiğinde fare imlecinin\n"
        "# görünmez olmasını engeller.\n"
        'Section "OutputClass"\n'
        '    Identifier "TiHA imlec duzeltmesi"\n'
        '    MatchDriver "i915|radeon|amdgpu"\n'
        '    Driver "modesetting"\n'
        f"{swline}"
        "EndSection\n"
    )


# Donanımsal imleci koruyan hafif çare: mod değişiminden sonra imleç
# boyutunu bir tık oynatıp geri alır, böylece imleç görüntüsü yeniden
# oluşturulur. Başarım bedeli yok; imleç yalnız bir an kaybolur.
CURSOR_SCRIPT_CONTENT = """#!/usr/bin/python3
# TiHA — ekran modu değişiminden sonra fare imlecini tazeler.
# Muffin'in MonitorsChanged sinyalini dinler; her mod değişiminden kısa
# süre sonra org.cinnamon.desktop.interface cursor-size değerini bir
# artırıp geri alır. Bu, masaüstünün imleç görüntüsünü yeniden
# oluşturmasına ve donanımsal imleç düzlemine yeniden yüklemesine yol
# açar. Her oturumda XDG autostart ile çalışır.
from gi.repository import Gio, GLib

DISPLAY_CONFIG = "org.cinnamon.Muffin.DisplayConfig"
DISPLAY_PATH = "/org/cinnamon/Muffin/DisplayConfig"
SCHEMA = "org.cinnamon.desktop.interface"
KEY = "cursor-size"
# Mod değişimi oturana kadar bekle; art arda gelen sinyaller tek
# tazelemede birleşsin (çözünürlük ve tazeleme ayrı ayrı uygulanıyor).
SETTLE_MS = 800
RESTORE_MS = 300

state = {"timer": 0}


def restore(size):
    settings.set_int(KEY, size)
    return False


def nudge():
    state["timer"] = 0
    size = settings.get_int(KEY)
    settings.set_int(KEY, size + 1)
    GLib.timeout_add(RESTORE_MS, restore, size)
    return False


def on_monitors_changed(*_args):
    if state["timer"]:
        GLib.source_remove(state["timer"])
    state["timer"] = GLib.timeout_add(SETTLE_MS, nudge)


source = Gio.SettingsSchemaSource.get_default()
if source is None or source.lookup(SCHEMA, True) is None:
    raise SystemExit(0)  # Cinnamon yok — yapacak iş yok.

settings = Gio.Settings.new(SCHEMA)
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
bus.signal_subscribe(
    None, DISPLAY_CONFIG, "MonitorsChanged", DISPLAY_PATH, None,
    Gio.DBusSignalFlags.NONE, on_monitors_changed,
)
GLib.MainLoop().run()
"""

CURSOR_AUTOSTART_CONTENT = """[Desktop Entry]
Type=Application
Name=TiHA cursor refresh
Name[tr]=TiHA imleç tazeleyici
Comment[tr]=Ekran modu değiştiğinde fare imlecini yeniden çizdirir
Exec=/usr/local/bin/tiha-imlec-tazele.py
NoDisplay=true
Terminal=false
X-GNOME-Autostart-enabled=true
"""


def _read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# Xorg günlüğünde ekran sürücülerinin yanı sıra girdi sürücüleri de
# (libinput, evdev, wacom) "Loading ..._drv.so" satırıyla geçer ve genelde
# en sonda yüklenirler; bu yüzden sürücü adını süzmek gerekiyor.
X_VIDEO_DRIVERS = (
    "intel", "modesetting", "amdgpu", "radeon", "nouveau", "nvidia",
    "ati", "fbdev", "vesa", "vmware", "qxl", "virtio_gpu",
)


def _x_driver() -> str:
    """Xorg günlüğüne göre en son yüklenen EKRAN sürücüsü (ör. "intel",
    "modesetting"). Okunamazsa boş döner — önizlemede bilgi amaçlı."""
    names = [
        name
        for line in _read_file(XORG_LOG).splitlines()
        if "_drv.so" in line and "Loading" in line
        for name in [line.rsplit("/", 1)[-1].replace("_drv.so", "").strip()]
        if name in X_VIDEO_DRIVERS
    ]
    return names[-1] if names else ""


# İlk uygulamadan önceki durum ve dosya yedekleri (state_dir altında).
ORIGINAL_STATE = "original.json"
BACKUP_NAMES = {
    "logind_dropin": "logind-dropin.conf.orig",
    "light_settings": "light-settings.json.orig",
    "light_autostart": "light-autostart.desktop.orig",
    "cursor_xorg": "xorg-imlec.conf.orig",
    "cursor_script": "imlec-tazele.py.orig",
    "cursor_autostart": "imlec-tazele.desktop.orig",
}
BACKUP_TARGETS = {
    "logind_dropin": LOGIND_DROPIN,
    "light_settings": LIGHT_SETTINGS,
    "light_autostart": LIGHT_AUTOSTART,
    "cursor_xorg": CURSOR_XORG_CONF,
    "cursor_script": CURSOR_SCRIPT,
    "cursor_autostart": CURSOR_AUTOSTART,
}


def _as_bool(value: object) -> bool:
    """GUI ``"True"/"False"`` metni, CLI preset gerçek bool gönderir."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("true", "1", "yes", "on", "evet")


def _mb(n_bytes: int) -> str:
    return f"{n_bytes / (1024 * 1024):.0f} MB"


# --- logind yardımcıları -----------------------------------------------------

def _unlink_ok(path: Path, errors: list[str]) -> bool:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        errors.append(f"{path}: {exc}")
        return False
    return True


def _runtime_kill_user_processes() -> bool | None:
    """logind'in **şu an çalışan** KillUserProcesses değeri (dosya değil)."""
    r = run_cmd(
        ["busctl", "get-property", "org.freedesktop.login1",
         "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
         "KillUserProcesses"],
        timeout=5,
    )
    if not r.ok:
        return None
    return r.stdout.strip() == "b true"


def _logind_conflicts() -> list[Path]:
    """Bizden sonra okunup KillUserProcesses'i ezen drop-in dosyaları."""
    conflicts = []
    for directory in LOGIND_DROPIN_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.conf")):
            if path.name <= LOGIND_DROPIN.name:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if re.search(r"^\s*KillUserProcesses\s*=", text, re.MULTILINE):
                conflicts.append(path)
    return conflicts


def _cgroup_anon_bytes(unit: str) -> int | None:
    """Birimin cgroup'unda tutulan anonim bellek (önbellek hariç)."""
    r = run_cmd(["systemctl", "show", unit, "-p", "ControlGroup", "--value"], timeout=5)
    cgroup = r.stdout.strip() if r.ok else ""
    if not cgroup:
        return None
    try:
        for line in Path(f"/sys/fs/cgroup{cgroup}/memory.stat").read_text().splitlines():
            key, _, value = line.partition(" ")
            if key == "anon":
                return int(value)
    except (OSError, ValueError):
        pass
    return None


def _sessions() -> list[dict[str, str]]:
    r = run_cmd(["loginctl", "list-sessions", "--no-legend"], timeout=5)
    if not r.ok:
        return []
    sessions = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        det = run_cmd(
            ["loginctl", "show-session", parts[0],
             "-p", "Id", "-p", "Name", "-p", "User", "-p", "State",
             "-p", "Service", "-p", "Timestamp", "-p", "Scope"],
            timeout=5,
        )
        if not det.ok:
            continue
        props = {}
        for prop_line in det.stdout.splitlines():
            key, sep, value = prop_line.partition("=")
            if sep:
                props[key] = value
        sessions.append(props)
    return sessions


def _lingering_sessions() -> list[dict]:
    """Kapanmış ama süreçleri kaldığı için ``closing``'de asılı oturumlar.

    Kullanıcının başka canlı oturumu yoksa ``user@UID.service``'in
    (pipewire, gvfs, portal vb.) belleği de o kalıntıya sayılır.
    """
    sessions = _sessions()
    live_uids = {
        s.get("User") for s in sessions if s.get("State") in ("active", "online")
    }
    result = []
    for s in sessions:
        try:
            uid = int(s.get("User", "0"))
        except ValueError:
            continue
        if s.get("State") != "closing" or uid < 1000:
            continue
        anon = _cgroup_anon_bytes(s.get("Scope") or f"session-{s.get('Id')}.scope") or 0
        if s.get("User") not in live_uids:
            anon += _cgroup_anon_bytes(f"user@{uid}.service") or 0
        result.append({
            "id": s.get("Id", ""),
            "name": s.get("Name", ""),
            "service": s.get("Service", ""),
            "since": s.get("Timestamp", ""),
            "anon": anon,
        })
    return result


# --- eta-light-mode yardımcıları ---------------------------------------------

def _pkg_version(name: str) -> str | None:
    r = run_cmd(["dpkg-query", "-W", "-f=${Status}\t${Version}", name], timeout=10)
    if not r.ok:
        return None
    status, _, version = r.stdout.partition("\t")
    return version.strip() if "install ok installed" in status else None


def _light_mode_keys() -> set[str]:
    """Kurulu paketin tanıdığı ayar anahtarları (yorum satırları hariç)."""
    keys: set[str] = set()
    try:
        text = LIGHT_SETTINGS_PY.read_text(encoding="utf-8")
    except OSError:
        return keys
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = re.search(r'"name":\s*"([a-z0-9-]+)"', line)
        if m:
            keys.add(m.group(1))
    return keys


def _read_light_settings() -> dict | None:
    try:
        data = json.loads(LIGHT_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# --- Kullanıcı dconf'u (root'tan okuma/yazma) --------------------------------
# TiHA root olarak çalışır, ayarlar ise her kullanıcının kendi
# ``~/.config/dconf/user`` dosyasında. Okuma veri yolu istemez (dconf dosyayı
# doğrudan mmap'ler), yazma ister. Bu yüzden:
#   - komut ``runuser -u <kullanıcı>`` ile hesabın kendisi olarak çalışır,
#     böylece dosya sahipliği bozulmaz;
#   - ortam ``env -i`` ile sıfırlanır — root'un DBUS_SESSION_BUS_ADDRESS'i
#     miras kalsaydı yazma root'un dconf'una giderdi;
#   - kullanıcının açık oturumu varsa onun veri yolu kullanılır (çalışan
#     dconf-service yazıyı yutmasın), yoksa ``dbus-run-session`` ile geçici
#     bir veri yolu açılır.

_UserRef = tuple[str, int, Path]


def _runuser_bin() -> str:
    """``runuser`` /usr/sbin altındadır; sudo'nun secure_path'i o dizini her
    zaman içermediği için bilinen konumlara da bakılır."""
    found = shutil.which("runuser")
    if found:
        return found
    for candidate in ("/usr/sbin/runuser", "/sbin/runuser", "/usr/bin/runuser"):
        if Path(candidate).is_file():
            return candidate
    return "runuser"


def _path_exists(path: Path) -> bool:
    """Başka kullanıcının 700 izinli ev dizini ``exists()``'i patlatır."""
    try:
        return path.exists()
    except OSError:
        return False


def _human_users() -> list[_UserRef]:
    """Grafik oturum açabilecek gerçek hesaplar: (ad, uid, ev dizini)."""
    users: list[_UserRef] = []
    try:
        text = Path("/etc/passwd").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return users
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) < 7:
            continue
        name, _pw, uid_s, _gid, _gecos, home, shell = parts[:7]
        try:
            uid = int(uid_s)
        except ValueError:
            continue
        # Debian'da normal hesaplar 1000..59999; nobody (65534) ve sistem
        # hesapları dışarıda kalsın.
        if not 1000 <= uid < 60000:
            continue
        if Path(shell).name in ("nologin", "false", "sync"):
            continue
        path = Path(home)
        if not path.is_dir():
            continue
        users.append((name, uid, path))
    return sorted(users)


def _user_cmd(user: _UserRef, argv: list[str], *, write: bool) -> list[str]:
    name, uid, home = user
    env = [
        "env", "-i",
        f"HOME={home}", f"USER={name}", f"LOGNAME={name}",
        "PATH=/usr/bin:/bin", "LC_ALL=C",
    ]
    runuser = _runuser_bin()
    if not write:
        return [runuser, "-u", name, "--", *env, *argv]
    bus = Path(f"/run/user/{uid}/bus")
    if _path_exists(bus):
        env += [f"XDG_RUNTIME_DIR=/run/user/{uid}",
                f"DBUS_SESSION_BUS_ADDRESS=unix:path={bus}"]
        return [runuser, "-u", name, "--", *env, *argv]
    return [runuser, "-u", name, "--", *env, "dbus-run-session", "--", *argv]


def _dconf_dump(user: _UserRef) -> dict[str, str] | None:
    """Kullanıcının dconf'undaki bütün anahtarlar: yol → ham değer.

    Hiç oturum açmamış kullanıcıda dosya yoktur; boş sözlük döner. Komut
    çalıştırılamazsa **None** döner: "ayarı yok" ile "okuyamadım" birbirine
    karışırsa geri alma hiçbir şey yapmadan başarılı görünürdü.

    Tek süreçte okunur — anahtar başına ``dconf read`` çağırmak, kullanıcı
    sayısıyla çarpılınca önizlemeyi gözle görülür yavaşlatıyordu.
    """
    if not _path_exists(user[2] / USER_DCONF_DB):
        return {}
    r = run_cmd(_user_cmd(user, ["dconf", "dump", "/"], write=False), timeout=20)
    if not r.ok:
        log.warning("%s: dconf okunamadı — %s", user[0], (r.stderr or "").strip())
        return None
    values: dict[str, str] = {}
    section = ""
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip("/")
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        values[f"/{section}/{key}" if section else f"/{key}"] = value.strip()
    return values


def _dconf_apply(user: _UserRef, path: str, value: str | None) -> tuple[bool, str]:
    """``value`` None ise anahtar silinir (sistem varsayılanına döner)."""
    argv = ["dconf", "write", path, value] if value is not None else ["dconf", "reset", path]
    r = run_cmd(_user_cmd(user, argv, write=True), timeout=30)
    return r.ok, (r.stderr or "").strip()


def _monitors_xml_is_light(home: Path) -> bool:
    """Kullanıcının monitors.xml'i hafif modun yazdığı modu mu taşıyor?"""
    try:
        text = (home / USER_MONITORS_XML).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    low_res = "<width>1600</width>" in text and "<height>900</height>" in text
    low_hz = re.search(r"<rate>5[01](\.\d+)?</rate>", text) is not None
    return low_res or low_hz


def _light_user_traces(user: _UserRef, dump: dict[str, str] | None = None) -> list[str]:
    """Bu hesapta hafif modun bıraktığı izler (insan okunur etiketler)."""
    values = dump if dump is not None else (_dconf_dump(user) or {})
    traces = [
        path.rsplit("/", 1)[-1]
        for path, light in LIGHT_DCONF_VALUES.items()
        if values.get(path) in light
    ]
    if _monitors_xml_is_light(user[2]):
        traces.append("cinnamon-monitors.xml")
    return traces


class PerformanceModule(Module):
    id = "m17_performance"
    title = t("m17.title")
    sidebar_title = t("m17.sidebar_title")
    streams_output = True
    popup_on_success = True
    doc_url = (
        "https://github.com/enseitankado/tiha/blob/main/"
        "docs/m17-basarim-deneysel.md"
    )
    doc_label = t("m17.doc_label")
    apply_hint = t("m17.apply_hint")
    rationale = t("m17.rationale")

    # ------------------------------------------------------------------
    # Formun sistemden dolan alanları (params.py "default_from")
    # ------------------------------------------------------------------

    def session_cleanup_active(self) -> bool:
        """Oturum kalıntısı temizliği (TiHA'nın logind ayarı) kurulu mu?
        Ayar açılışta etkin olur; kutu kurulu olup olmadığını gösterir."""
        return self._dropin_is_ours()

    def light_mode_active(self) -> bool:
        """Hafif mod şu an tüm kullanıcılara uygulanıyor mu?"""
        return _read_light_settings() is not None and LIGHT_AUTOSTART.exists()

    def _light_key_active(self, key: str) -> bool:
        """Hafif mod etkinse o anahtarın gerçek durumu, değilse önerilen açık.

        Hafif mod kapalıyken bütün alt kutuları boş göstermek, kullanıcıyı
        ana kutuyu işaretledikten sonra yedi kutuyu tek tek işaretlemeye
        zorlardı.
        """
        if not self.light_mode_active():
            return True
        return bool((_read_light_settings() or {}).get(key))

    # Her alt kutu kendi anahtarının gerçek durumundan dolar. Adıma
    # girildiğinde form sistemin hâlini gösterdiği için bir kutuyu kaldırıp
    # uygulamak "o ayarı kaldır" anlamına gelebiliyor.
    def lm_effects_active(self) -> bool:
        return self._light_key_active("effects")

    def lm_compositor_active(self) -> bool:
        return self._light_key_active("compositor")

    def lm_thumbnails_active(self) -> bool:
        return self._light_key_active("thumbnails")

    def lm_directory_counts_active(self) -> bool:
        return self._light_key_active("directory-item-counts")

    def lm_app_monitoring_active(self) -> bool:
        return self._light_key_active("app-monitoring")

    def lm_low_resolution_active(self) -> bool:
        return self._light_key_active("low-resolution")

    def lm_low_refresh_rate_active(self) -> bool:
        return self._light_key_active("low-refresh-rate")

    # ------------------------------------------------------------------
    # Önizleme
    # ------------------------------------------------------------------

    def preview(self) -> str:
        lines = [t("m17.preview.sessions_header")]
        runtime = _runtime_kill_user_processes()
        dropin_ours = self._dropin_is_ours()
        if runtime is True:
            state = t("m17.preview.state_active")
        elif dropin_ours:
            state = t("m17.preview.state_configured")
        elif runtime is False:
            state = t("m17.preview.state_off")
        else:
            state = t("m17.preview.unreadable")
        lines.append(t("m17.preview.state", state=state))

        lingering = _lingering_sessions()
        if lingering:
            total = sum(s["anon"] for s in lingering)
            lines.append(t(
                "m17.preview.lingering", count=len(lingering), memory=_mb(total),
            ))
            for s in lingering:
                lines.append(t(
                    "m17.preview.lingering_item",
                    name=s["name"], id=s["id"], service=s["service"],
                    since=s["since"], memory=_mb(s["anon"]),
                ))
        else:
            lines.append(t("m17.preview.lingering_none"))

        lines += ["", t("m17.preview.light_header")]
        version = _pkg_version(LIGHT_PKG)
        if version:
            lines.append(t("m17.preview.package_installed", version=version))
        else:
            lines.append(t("m17.preview.package_missing"))
        settings = _read_light_settings()
        if settings is not None and LIGHT_AUTOSTART.exists():
            active = sorted(k for k, v in settings.items() if v is True)
            lines.append(t(
                "m17.preview.all_users_on",
                keys=", ".join(active) or t("m17.preview.no_keys_selected"),
            ))
        else:
            lines.append(t("m17.preview.all_users_off"))

        affected = [
            (user[0], traces)
            for user in _human_users()
            for traces in [_light_user_traces(user)]
            if traces
        ]
        if affected:
            lines.append(t(
                "m17.preview.traced_accounts",
                count=len(affected),
                accounts=", ".join(
                    t("m17.preview.traced_account_item", name=name, count=len(traces))
                    for name, traces in affected
                ),
            ))
            lines.append(t("m17.preview.traced_note"))
        else:
            lines.append(t("m17.preview.traced_none"))

        lines += ["", t("m17.preview.cursor_header")]
        driver = _x_driver()
        lines.append(t(
            "m17.preview.driver", driver=driver or t("m17.preview.unreadable"),
        ))
        if CURSOR_XORG_CONF.exists():
            conf = _read_file(CURSOR_XORG_CONF)
            mode = CURSOR_XORG_SWCURSOR if "SWcursor" in conf else CURSOR_XORG_MODESETTING
            lines.append(t("m17.preview.xorg_fix_on", mode=mode))
        else:
            lines.append(t("m17.preview.xorg_fix_off"))
        # Tazeleme servisi artık seçenek değil; yalnız eski bir uygulamadan
        # kalmışsa (bir sonraki uygulamada sökülecek) gösterilir.
        if CURSOR_AUTOSTART.exists():
            lines.append(t("m17.preview.cursor_service", state=t("m17.preview.installed")))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Uygula
    # ------------------------------------------------------------------

    def apply(
        self,
        params: dict | None = None,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        p = dict(params or {})
        kill_processes = _as_bool(p.get("kill_user_processes"))
        light_mode = _as_bool(p.get("light_mode_enabled"))
        # İmleç düzeltmesi seçenek değil: gerçek tahtada ekran modu değişiminde
        # imleç kaybolmasını yalnız modesetting + yazılımsal imleç giderdi.
        # Form alanı salt okunur; CLI/preset'ten başka bir değer gelse de
        # her uygulamada bu kurulur.
        cursor_xorg = CURSOR_XORG_SWCURSOR
        cursor_xorg_on = True
        # "Mod değişiminde imleci tazele" servisi kaldırıldı; önceki bir
        # uygulamanın kurduğu servis varsa bu uygulamada sökülür.
        cursor_service = False
        # Kutu sisteme bakarak dolduğu için (params.py "default_from"),
        # işaretinin kaldırılıp uygulanması bilinçli bir "kaldır" isteğidir.
        light_remove = not light_mode and self.light_mode_active()
        # Oturum kalıntıları kutusu da sisteme bakarak doluyor; aynı kural.
        session_remove = not kill_processes and self.session_cleanup_active()

        if not (kill_processes or session_remove or light_mode or light_remove
                or cursor_xorg_on or cursor_service):
            return ApplyResult(False, t("m17.apply.nothing_selected"))

        def say(line: str) -> None:
            if progress:
                progress(line)

        original = self._load_or_capture_original()
        summary: list[str] = []
        details: list[str] = []
        warnings: list[str] = []
        data: dict = {"params": p}

        # Parçalardan biri başarısız olsa da uygulanan parça "applied" olarak
        # günlüğe düşmeli; aksi hâlde UI geri al düğmesi göstermez ve
        # yazılmış ayar geri alınamaz kalır. Başarısızlık uyarı diyaloğuyla
        # bildirilir.
        failures: list[str] = []

        if kill_processes:
            ok, text = self._apply_logind(original, say, warnings)
            if ok:
                summary.append(text)
                details.append(f"logind: {LOGIND_DROPIN}")
                data["session_cleanup"] = True
            else:
                failures.append(text)

        if session_remove:
            removed = (
                self._restore_or_remove("logind_dropin", original, failures)
                if original["touched"].get("logind")
                else _unlink_ok(LOGIND_DROPIN, failures)
            )
            if removed:
                original["touched"]["logind"] = False
                summary.append(t("m17.apply.logind_removed"))
                data["session_cleanup_removed"] = True

        if light_mode:
            keys = [
                key
                for field, _label, field_keys in LIGHT_FIELDS
                if _as_bool(p.get(field))
                for key in field_keys
            ]
            if not keys:
                failures.append(t("m17.apply.light_no_subkeys"))
            else:
                ok, text = self._apply_light_mode(original, keys, say, warnings)
                if ok:
                    summary.append(text)
                    details.append(t(
                        "m17.apply.details_light", path=LIGHT_SETTINGS, keys=", ".join(keys),
                    ))
                    data["light_mode_keys"] = keys
                else:
                    failures.append(text)

        if light_remove:
            ok, text = self._remove_light_mode(original, say, warnings)
            if ok:
                summary.append(text)
                details.append(t("m17.apply.details_light_removed"))
                data["light_mode_removed"] = True
            else:
                failures.append(text)

        if cursor_xorg_on:
            ok, text = self._apply_cursor_xorg(original, cursor_xorg, say)
            if ok:
                summary.append(text)
                details.append(t(
                    "m17.apply.details_cursor_xorg", path=CURSOR_XORG_CONF, choice=cursor_xorg,
                ))
                data["cursor_xorg_fix"] = cursor_xorg
            else:
                failures.append(text)

        if original["touched"].get("cursor_service"):
            removed_ok = all([
                self._restore_or_remove("cursor_script", original, failures),
                self._restore_or_remove("cursor_autostart", original, failures),
            ])
            if removed_ok:
                original["touched"]["cursor_service"] = False
                summary.append(t("m17.apply.cursor_service_removed"))
                data["cursor_service_removed"] = True

        self._save_original(original)
        if not summary:
            return ApplyResult(
                False, " ".join(failures), details="\n".join(warnings), data=data,
            )
        return ApplyResult(
            True,
            " ".join(summary),
            details="\n".join(details + failures + warnings),
            data=data,
            warning="\n\n".join(failures + warnings) or None,
        )

    def _apply_logind(self, original: dict, say, warnings: list[str]) -> tuple[bool, str]:
        say(t("m17.logind.header"))
        try:
            current = LOGIND_DROPIN.read_text(encoding="utf-8") if LOGIND_DROPIN.exists() else None
            if current != LOGIND_DROPIN_CONTENT:
                LOGIND_DROPIN.parent.mkdir(parents=True, exist_ok=True)
                LOGIND_DROPIN.write_text(LOGIND_DROPIN_CONTENT, encoding="utf-8")
                LOGIND_DROPIN.chmod(0o644)
                say(t("m17.say.written", path=LOGIND_DROPIN))
            else:
                say(t("m17.say.already_written", path=LOGIND_DROPIN))
        except OSError as exc:
            return False, t("m17.logind.write_failed", error=exc)
        original["touched"]["logind"] = True
        self._save_original(original)

        for path in _logind_conflicts():
            warnings.append(t("m17.logind.conflict", path=path))

        if _runtime_kill_user_processes() is True:
            return True, t("m17.logind.active")
        say(t("m17.logind.reload_impossible"))
        return True, t("m17.logind.configured")

    def _apply_light_mode(
        self, original: dict, keys: list[str], say, warnings: list[str],
    ) -> tuple[bool, str]:
        say(t("m17.light.header"))
        version = _pkg_version(LIGHT_PKG)
        if version is None:
            env = {"DEBIAN_FRONTEND": "noninteractive"}
            say("\n==== apt-get update ====")
            if not run_cmd_stream(["apt-get", "update"], progress=say, env=env, timeout=300).ok:
                return False, t("m17.light.apt_update_failed")
            say(f"\n==== apt-get install {LIGHT_PKG} ====")
            if not run_cmd_stream(
                ["apt-get", "install", "-y", LIGHT_PKG], progress=say, env=env, timeout=600,
            ).ok:
                return False, t("m17.light.install_failed", package=LIGHT_PKG)
            version = _pkg_version(LIGHT_PKG)
            if version is None:
                return False, t("m17.light.install_unverified", package=LIGHT_PKG)
        say(t("m17.light.version", package=LIGHT_PKG, version=version))
        # Kullanıcı ayarlarının TiHA öncesi hâli bu noktada hâlâ el
        # değmemiş: autostart yazılmadan kimse yeni ayarla oturum açmadı.
        self._capture_user_state(original, say)
        original["touched"]["light"] = True
        self._save_original(original)

        if version not in LIGHT_TESTED_VERSIONS:
            warnings.append(t(
                "m17.light.untested_version",
                package=LIGHT_PKG, version=version,
                tested=", ".join(LIGHT_TESTED_VERSIONS),
            ))
        known = _light_mode_keys()
        unknown = [k for k in keys if k not in known]
        if unknown:
            warnings.append(t("m17.light.unknown_keys", keys=", ".join(unknown)))
            keys[:] = [k for k in keys if k in known]
        if not keys:
            return False, t("m17.light.no_known_keys")
        if not LIGHT_ACTION.is_file():
            return False, t("m17.light.action_missing", path=LIGHT_ACTION)

        # Daha önce açık olup bu kez seçilmeyen ayarlar. JSON'dan düşmeleri
        # yetmez: paket bir anahtarı yoksaydığında kullanıcıdaki değeri
        # olduğu gibi bırakır, ayar sessizce açık kalırdı.
        dropped = sorted(set((_read_light_settings() or {}).keys()) - set(keys))

        payload = {k: True for k in keys}
        r = run_cmd(
            ["python3", str(LIGHT_ACTION), "enable"],
            input_data=json.dumps(payload), timeout=30,
        )
        if not r.ok:
            return False, t(
                "m17.light.write_failed", error=r.stderr.strip() or r.returncode,
            )
        if _read_light_settings() != payload or not LIGHT_AUTOSTART.exists():
            return False, t("m17.light.write_unverified")
        say(t("m17.say.written_keys", path=LIGHT_SETTINGS, keys=", ".join(keys)))
        say(t("m17.say.autostart", path=LIGHT_AUTOSTART))

        if dropped:
            say(t("m17.light.reverting_dropped", keys=", ".join(dropped)))
            reverted, errors = self._revert_user_state(original, set(dropped), say)
            warnings.extend(errors)
            if reverted:
                say(t("m17.light.affected_accounts", accounts=", ".join(reverted)))
        return True, t("m17.light.applied")

    def _remove_light_mode(
        self, original: dict, say, warnings: list[str],
    ) -> tuple[bool, str]:
        """Hafif modu sistemden ve daha önce giriş yapmış hesaplardan kaldırır.

        Paket bilerek kaldırılmaz. ``/etc/xdg/autostart/...`` ve
        ``/etc/eta-light-mode/settings.json`` dpkg'nin dosya listesinde
        değil (Action.py çalışma anında yazıyor), bu yüzden ``apt remove``
        ikisini de yerinde bırakır; üstelik geri alma için gereken
        ``/usr/bin/eta-light-mode`` aracını siler ve ETAP imajını depodaki
        standart kurulumdan uzaklaştırır.
        """
        say(t("m17.remove.header"))
        # Kullanıcıların TiHA öncesi hâli kaydedilmemişse (hafif modu TiHA
        # açmadıysa) şimdi kaydetmenin anlamı yok: ayar zaten uygulanmış
        # durumda. Bu durumda geri alma sistem varsayılanına döner.
        if LIGHT_ACTION.is_file():
            r = run_cmd(["python3", str(LIGHT_ACTION), "disable"], timeout=30)
            if not r.ok:
                return False, t(
                    "m17.remove.disable_failed", error=r.stderr.strip() or r.returncode,
                )
        else:
            say(t("m17.remove.action_missing", path=LIGHT_ACTION))
            for path in (LIGHT_SETTINGS, LIGHT_AUTOSTART):
                try:
                    if path.exists():
                        path.unlink()
                except OSError as exc:
                    return False, t("m17.remove.delete_failed", path=path, error=exc)
        if self.light_mode_active():
            return False, t("m17.remove.unverified")
        say(t("m17.say.deleted", path=LIGHT_SETTINGS))
        say(t("m17.say.deleted", path=LIGHT_AUTOSTART))
        say(t("m17.remove.package_kept", package=LIGHT_PKG))

        reverted, errors = self._revert_user_state(original, None, say)
        warnings.extend(errors)
        if reverted:
            say(t("m17.remove.users_reverted", accounts=", ".join(reverted)))
            return True, t("m17.remove.done_with_users", count=len(reverted))
        return True, t("m17.remove.done_no_users")

    # ------------------------------------------------------------------
    # Geri al
    # ------------------------------------------------------------------


    def _apply_cursor_xorg(self, original: dict, choice: str, say) -> tuple[bool, str]:
        """Xorg parçasını yazar (modesetting, istenirse + SWcursor)."""
        swcursor = CURSOR_XORG_CHOICES.get(choice)
        if swcursor is None:
            return False, t("m17.cursor.unknown_choice", choice=choice)
        say(t("m17.cursor.xorg_header"))
        content = _cursor_xorg_content(swcursor)
        try:
            CURSOR_XORG_CONF.parent.mkdir(parents=True, exist_ok=True)
            if _read_file(CURSOR_XORG_CONF) != content:
                CURSOR_XORG_CONF.write_text(content, encoding="utf-8")
                CURSOR_XORG_CONF.chmod(0o644)
                say(t("m17.say.written", path=CURSOR_XORG_CONF))
            else:
                say(t("m17.say.already_written", path=CURSOR_XORG_CONF))
        except OSError as exc:
            return False, t("m17.cursor.xorg_write_failed", error=exc)
        original["touched"]["cursor_xorg"] = True
        self._save_original(original)
        driver = _x_driver()
        if driver:
            say(t("m17.cursor.current_driver", driver=driver))
        return True, t("m17.cursor.xorg_written", choice=choice.lower())

    def _apply_cursor_service(self, original: dict, say) -> tuple[bool, str]:
        """Mod değişiminde imleci tazeleyen kullanıcı servisini kurar."""
        say(t("m17.cursor.service_header"))
        try:
            CURSOR_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
            CURSOR_SCRIPT.write_text(CURSOR_SCRIPT_CONTENT, encoding="utf-8")
            CURSOR_SCRIPT.chmod(0o755)
            CURSOR_AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
            CURSOR_AUTOSTART.write_text(CURSOR_AUTOSTART_CONTENT, encoding="utf-8")
            CURSOR_AUTOSTART.chmod(0o644)
        except OSError as exc:
            return False, t("m17.cursor.service_failed", error=exc)
        original["touched"]["cursor_service"] = True
        self._save_original(original)
        say(t("m17.say.written", path=CURSOR_SCRIPT))
        say(t("m17.say.autostart", path=CURSOR_AUTOSTART))
        return True, t("m17.cursor.service_installed")

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        original = self._load_original()
        if original is None:
            return ApplyResult(False, t("m17.undo.no_original"))
        done: list[str] = []
        errors: list[str] = []

        if original["touched"].get("logind"):
            if self._restore_or_remove("logind_dropin", original, errors):
                done.append(t("m17.undo.logind_restored"))

        if original["touched"].get("light"):
            light_ok = all([
                self._restore_or_remove("light_settings", original, errors),
                self._restore_or_remove("light_autostart", original, errors),
            ])
            if light_ok and not original.get("light_pkg_installed") and _pkg_version(LIGHT_PKG):
                r = run_cmd(
                    ["apt-get", "purge", "-y", LIGHT_PKG],
                    env={"DEBIAN_FRONTEND": "noninteractive"}, timeout=600,
                )
                if r.ok:
                    done.append(t("m17.undo.package_removed", package=LIGHT_PKG))
                else:
                    errors.append(t(
                        "m17.undo.package_remove_failed",
                        package=LIGHT_PKG, error=r.stderr.strip(),
                    ))
            if light_ok:
                done.append(t("m17.undo.light_disabled"))
                # Sistem dosyalarını geri almak yetmez: ayarlar her oturum
                # açılışında kullanıcıların kendi dconf'una yazıldı ve orada
                # kalır. Bunları da TiHA öncesi hâline döndür.
                reverted, revert_errors = self._revert_user_state(
                    original, None, lambda _line: None,
                )
                errors.extend(revert_errors)
                if reverted:
                    done.append(t(
                        "m17.undo.users_reverted",
                        count=len(reverted), accounts=", ".join(reverted),
                    ))

        if original["touched"].get("cursor_xorg"):
            if self._restore_or_remove("cursor_xorg", original, errors):
                done.append(t("m17.undo.cursor_xorg_restored"))

        if original["touched"].get("cursor_service"):
            if all([
                self._restore_or_remove("cursor_script", original, errors),
                self._restore_or_remove("cursor_autostart", original, errors),
            ]):
                done.append(t("m17.undo.cursor_service_removed"))

        if errors:
            return ApplyResult(False, t("m17.undo.partial"), details="\n".join(done + errors))
        shutil.rmtree(self.state_dir, ignore_errors=True)
        return ApplyResult(
            True,
            "; ".join(done) + "." if done else t("m17.undo.nothing"),
            details=t("m17.undo.details"),
        )

    # ------------------------------------------------------------------
    # Özgün durum kaydı
    # ------------------------------------------------------------------

    def _dropin_is_ours(self) -> bool:
        try:
            return LOGIND_DROPIN.read_text(encoding="utf-8") == LOGIND_DROPIN_CONTENT
        except OSError:
            return False

    # --- Kullanıcı hesaplarının hafif mod öncesi hâli ----------------------

    def _capture_user_state(self, original: dict, say) -> None:
        """Her hesabın ilgili dconf değerlerini ve monitors.xml'ini yedekler.

        Yalnız bir kez çalışır: ikinci uygulama kaydı bozmasın, geri alma
        her zaman TiHA hiç dokunmadan önceki duruma dönsün.
        """
        if "user_dconf" in original:
            return
        snapshot: dict[str, dict[str, str]] = {}
        backup_dir = self.ensure_state_dir() / USER_BACKUP_DIR
        backup_dir.mkdir(parents=True, exist_ok=True)
        for user in _human_users():
            name, _uid, home = user
            dump = _dconf_dump(user)
            if dump is None:
                # Okunamayan hesabı kayda hiç almıyoruz; "boş kaydedildi"
                # sanılsa geri alma o hesabın ayarlarını silerdi.
                log.warning("%s: hafif mod öncesi ayarlar kaydedilemedi", name)
                continue
            # Yalnız ayarlanmış anahtarlar yazılır; kayıtta olmayan anahtar
            # "kullanıcıda yoktu" demektir ve geri alırken silinir.
            snapshot[name] = {p: dump[p] for p in LIGHT_DCONF_VALUES if p in dump}
            xml = home / USER_MONITORS_XML
            if _path_exists(xml):
                try:
                    shutil.copy2(xml, backup_dir / f"{name}.monitors.xml")
                except OSError as exc:
                    log.warning("%s monitors.xml yedeklenemedi: %s", name, exc)
        original["user_dconf"] = snapshot
        self._save_original(original)
        say(t("m17.light.users_captured", count=len(snapshot)))

    def _revert_user_state(
        self, original: dict, keys: set[str] | None, say,
    ) -> tuple[list[str], list[str]]:
        """Hafif modun hesaplara yazdığı ayarları geri alır.

        ``keys`` verilmezse bütün hafif mod ayarları, verilirse yalnız o
        eta-light-mode anahtarlarına karşılık gelen dconf yolları ele
        alınır. Bir anahtara ancak o hesaptaki değeri hafif modun yazdığı
        değerse dokunulur; kullanıcının kendi seçtiği bir değer ezilmez.

        Döner: (geri alınan hesaplar, hatalar).
        """
        if keys is None:
            paths = set(LIGHT_DCONF_VALUES)
            monitors = True
        else:
            paths = {p for k, p, _v in LIGHT_DCONF if k in keys}
            monitors = bool(keys & {"low-resolution", "low-refresh-rate"})
        snapshot = original.get("user_dconf") or {}
        backup_dir = self.state_dir / USER_BACKUP_DIR
        reverted: list[str] = []
        errors: list[str] = []

        for user in _human_users():
            name, _uid, home = user
            dump = _dconf_dump(user)
            if dump is None:
                errors.append(t("m17.revert.unreadable", name=name))
                continue
            # Kayıtta olmayan hesap (hafif mod uygulandıktan SONRA açılmış):
            # TiHA öncesi değeri yok, anahtarı silmek doğru davranış —
            # değer sistem varsayılanına döner.
            saved = snapshot.get(name)
            changed: list[str] = []
            for path in sorted(paths):
                current = dump.get(path)
                if current is None or current not in LIGHT_DCONF_VALUES[path]:
                    continue
                target = saved.get(path) if saved else None
                if target == current:
                    continue
                ok, err = _dconf_apply(user, path, target)
                if ok:
                    changed.append(path.rsplit("/", 1)[-1])
                else:
                    errors.append(t(
                        "m17.revert.key_failed", name=name, path=path,
                        error=err or t("m17.revert.unknown_error"),
                    ))
            if monitors and _monitors_xml_is_light(home):
                ok, err = self._restore_user_monitors(user, backup_dir)
                if ok:
                    changed.append("cinnamon-monitors.xml")
                else:
                    errors.append(t("m17.revert.monitors_failed", name=name, error=err))
            if changed:
                reverted.append(name)
                say(f"  {name}: {', '.join(changed)}")
        return reverted, errors

    def _restore_user_monitors(self, user: _UserRef, backup_dir: Path) -> tuple[bool, str]:
        """Hesabın ekran modu dosyasını yedekten geri yazar ya da siler."""
        name, uid, home = user
        target = home / USER_MONITORS_XML
        backup = backup_dir / f"{name}.monitors.xml"
        try:
            if backup.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, target)
                # Root olarak kopyalandı; sahipliği geri vermezsek Muffin
                # bir dahaki ekran ayarında dosyayı yazamaz.
                os.chown(target, uid, home.stat().st_gid)
            elif _path_exists(target):
                target.unlink()
        except OSError as exc:
            return False, str(exc)
        return True, ""

    def _load_original(self) -> dict | None:
        try:
            data = json.loads((self.state_dir / ORIGINAL_STATE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        data.setdefault("touched", {})
        return data

    def _load_or_capture_original(self) -> dict:
        """İlk uygulamada dokunulacak her şeyin önceki hâlini yedekler.

        Sonraki uygulamalar kaydı değiştirmez; geri alma her zaman TiHA
        hiç dokunmadan önceki duruma döner.
        """
        existing = self._load_original()
        if existing is not None:
            return existing
        state_dir = self.ensure_state_dir()
        original: dict = {
            "light_pkg_installed": _pkg_version(LIGHT_PKG) is not None,
            "touched": {},
        }
        for name, target in BACKUP_TARGETS.items():
            original[f"{name}_existed"] = target.exists()
            if target.exists():
                shutil.copy2(target, state_dir / BACKUP_NAMES[name])
        self._save_original(original)
        return original

    def _save_original(self, original: dict) -> None:
        path = self.ensure_state_dir() / ORIGINAL_STATE
        path.write_text(json.dumps(original, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _restore_or_remove(self, name: str, original: dict, errors: list[str]) -> bool:
        target = BACKUP_TARGETS[name]
        try:
            if original.get(f"{name}_existed"):
                backup = self.state_dir / BACKUP_NAMES[name]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, target)
            elif target.exists():
                target.unlink()
        except OSError as exc:
            errors.append(f"{target}: {exc}")
            return False
        return True
