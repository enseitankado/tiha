"""Modül 3 — Öğretmen PIN anahtarlarını toplu hazırla.

Ne yapar?
Girdiğiniz öğretmen ad-soyad listesinden her öğretmen için bir kullanıcı
hesabı oluşturur (zaten varsa geçer), her hesaba kriptografik olarak
güvenli bir PIN kodu (zaman tabanlı TOTP) BASE32 anahtarı atar ve
bu anahtarları Pardus ETAP'ın PAM modülünün okuduğu
/etc/otp-secrets.json dosyasına yazar. Ayrıca isteğe bağlı olarak,
sonradan okula atanacak öğretmenler için belirlediğiniz sayıda yedek
hesap (ogretmen01, ogretmen02 …) oluşturur.

LightDM greeter cache desteği:
50+ kullanıcı oluşturulduğunda, LightDM'in tüm kullanıcıları gösterebilmesi
için AccountsService cache'ini güncelleyen betik (greeter-cache-olustur.sh)
GitHub'dan indirilerek güvenli konuma (/usr/local/bin/) kaydedilir ve
systemd service olarak otomatik çalıştırma ayarlanır. Bu sayede yeni
kullanıcılar login ekranında görünür ve her açılışta cache güncel kalır.

enseitankado/eta-otp-cli entegrasyonu
Üretim, yerleşim ve JSON formatı enseitankado/eta-otp-cli
(https://github.com/enseitankado/eta-otp-cli)
aracıyla bire bir uyumludur. bootstrap.sh aracın dosyalarını
(otp-cli.py, toplu-kullanici-olustur.py) indirir ve
TIHA_ETA_OTP_CLI_DIR ortam değişkeninde açar. Bu adım varsayılan
olarak aracın toplu-kullanici-olustur.py betiğini çağırır; böylece:

* Kullanıcılar doğru gruplarda (cdrom, audio, video, plugdev, bluetooth,
  scanner, netdev, dip, lpadmin) açılır,
* AccountsService cache'i güncellenir — yeni kullanıcılar LightDM
  login ekranında görünür,
* PIN anahtarları yazılır ve dosya sahipliği/izinleri (root:root, 0o600)
  otomatik ayarlanır.

Araç indirilemediyse TiHA dahili pyotp tabanlı yedek yolu kullanır.

Neden gerekir?
Pardus ETAP'ın kendi PIN üretici uygulaması (eta-otp-lock) kullanıcının
yerel parolasını ister. "Açılışta parola temizliği" adımı uygulanırsa o
parolalar her açılışta rastgele bir değere çevrildiği için öğretmen
tahtada kendi başına PIN üretemez. Bu adım, anahtarları imaj öncesinde
merkezî olarak üretip her öğretmene özel olarak teslim etmeyi sağlar.
Öğretmen anahtarını Google Authenticator (veya benzeri) uygulamaya
eklediği andan itibaren dağıtılmış tüm tahtalarda 6 haneli kodla oturum
açabilir.

Parola temizliği adımını uygulamamış olsanız da bu adımı kullanmak
pratiktir: öğretmenleri tek tek tahta başına götürmek yerine anahtarları
hazır olarak teslim edersiniz.

Geri al. Oluşturulan Linux kullanıcıları
toplu-kullanici-olustur.py --kullanicilari-sil ile kaldırılır (ya
da araç yoksa deluser --remove-home); ardından önceki
/etc/otp-secrets.json yedeği geri yüklenir. Greeter cache kurulumu
varsa, betik bir kez daha çalıştırılır (cache temizliği için),
systemd service devre dışı bırakılır ve script dosyası silinir.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote

import pyotp

from ..core.async_state import AsyncValue
from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.paths import OTP_SECRETS_FILE, VAR_ROOT
from ..core.utils import (
    backup_file,
    restore_file,
    run_cmd,
    run_cmd_stream,
    user_exists,
)

log = get_logger(__name__)

# Google Authenticator vb. uygulamalara gömülen bilgi:
# otpauth://totp/<issuer>:<user>?secret=...&issuer=<issuer>&digits=6&period=30
OTP_ISSUER = "Pardus ETAP"

_TR_MAP = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")

# Greeter cache betik yönetimi
GREETER_SCRIPT_URL = "https://raw.githubusercontent.com/enseitankado/eta-otp-cli/main/greeter-cache-olustur.sh"
GREETER_SCRIPT_PATH = Path("/usr/local/bin/greeter-cache-olustur.sh")
GREETER_SERVICE_PATH = Path("/etc/systemd/system/greeter-cache.service")
MIN_USERS_FOR_CACHE = 50

# Varsayılan sistem kullanıcıları (işletim sistemi kurulumunda gelir)
DEFAULT_SYSTEM_USERS = {"etapadmin", "ogrenci", "ogretmen"}


# enseitankado/eta-otp-cli entegrasyonu: bootstrap.sh aracı
# /tmp/tiha.XXXXXX/eta-otp-cli/ altına indirir ve TIHA_ETA_OTP_CLI_DIR
# ortam değişkenine yazar. TiHA bootstrap.sh dışında başlatıldığında
# (geliştirme, doğrudan ``python3 -m tiha``) bu değişken boş kalır;
# o durumda aracı kendimiz aşağıdaki sabit önbellek dizinine indiririz.
ETA_OTP_RAW_BASE = "https://raw.githubusercontent.com/enseitankado/eta-otp-cli/main"
ETA_OTP_FILES = ("toplu-kullanici-olustur.py", "otp-cli.py")
ETA_OTP_CACHE_DIR = VAR_ROOT / "eta-otp-cli"

# Modül seviyesi bellek: aracı bir kez indirmeyi deneriz, sonuç tüm
# çağrılar için saklanır (preview her sayfa girişinde yeniden indirme
# çalıştırmasın diye).
_eta_otp_cli_path: Path | None = None
_eta_otp_cli_download_attempted: bool = False


def _eta_otp_cli_download(dest_dir: Path) -> bool:
    """Aracı GitHub'dan ``dest_dir`` altına indirir.

    Başarıyla en az ``toplu-kullanici-olustur.py`` indirildiyse True
    döner. Hatalar günce dosyasına yazılır; arayüze duyurulmaz.
    """
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("eta-otp-cli için %s oluşturulamadı: %s", dest_dir, exc)
        return False
    for fname in ETA_OTP_FILES:
        target = dest_dir / fname
        url = f"{ETA_OTP_RAW_BASE}/{fname}"
        res = run_cmd(["curl", "-fsSL", "-o", str(target), url], timeout=60)
        if not res.ok:
            log.warning(
                "eta-otp-cli/%s indirilemedi: %s",
                fname, (res.stderr or "").strip(),
            )
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        try:
            target.chmod(0o755)
        except OSError:
            pass
    # En azından toplu-kullanici-olustur.py inebildiyse kullanılabilir sayılır
    return (dest_dir / "toplu-kullanici-olustur.py").is_file()


def _ensure_eta_otp_cli() -> Path | None:
    """``toplu-kullanici-olustur.py``'nin yolunu döner; gerekirse indirir.

    Bulunma sırası:
      1. Önceki çağrıda hazırlanmışsa (modül seviyesi önbellek)
      2. ``TIHA_ETA_OTP_CLI_DIR`` ortam değişkenindeki yol (bootstrap.sh)
      3. ``ETA_OTP_CACHE_DIR`` (TiHA'nın kendi indirme önbelleği)
      4. GitHub'dan ``ETA_OTP_CACHE_DIR``'a indir (oturum başına 1 deneme)

    Hiçbiri çalışmazsa ``None`` döner — çağıran taraf sessizce dahili
    pyotp yoluna düşer.
    """
    global _eta_otp_cli_path, _eta_otp_cli_download_attempted

    if _eta_otp_cli_path is not None and _eta_otp_cli_path.is_file():
        return _eta_otp_cli_path

    # 1) Ortam değişkeninden gelen yol (bootstrap.sh tarafından)
    dir_env = os.environ.get("TIHA_ETA_OTP_CLI_DIR")
    if dir_env:
        candidate = Path(dir_env) / "toplu-kullanici-olustur.py"
        if candidate.is_file():
            _eta_otp_cli_path = candidate
            return candidate

    # 2) Önceden indirilmiş önbellek
    cached = ETA_OTP_CACHE_DIR / "toplu-kullanici-olustur.py"
    if cached.is_file():
        _eta_otp_cli_path = cached
        os.environ["TIHA_ETA_OTP_CLI_DIR"] = str(ETA_OTP_CACHE_DIR)
        return cached

    # 3) Oturum başına bir kez indirme dene
    if _eta_otp_cli_download_attempted:
        return None
    _eta_otp_cli_download_attempted = True
    if _eta_otp_cli_download(ETA_OTP_CACHE_DIR) and cached.is_file():
        _eta_otp_cli_path = cached
        os.environ["TIHA_ETA_OTP_CLI_DIR"] = str(ETA_OTP_CACHE_DIR)
        return cached
    return None


def _eta_otp_cli_bulk_script() -> Path | None:
    return _ensure_eta_otp_cli()


# preview() ``_ensure_eta_otp_cli()``'yi ÇAĞIRMAZ — bu çağrı oturumun
# ilk seferinde GitHub'dan dosya indirebilir (≤60 sn timeout) ve UI
# thread'inde bekleyiş olur. Bunun yerine async wrapper okur: ilk
# çağrıda arka planda worker başlar, sonuç gelince preview yeniden
# çizilir. ``apply()`` doğrudan senkron çağrıya devam eder — orada
# kesin sonuç gerekir ve kullanıcı zaten apply progress'ini görüyor.
_eta_otp_cli_available = AsyncValue(
    lambda: _ensure_eta_otp_cli() is not None,
    name="m03.eta-otp-cli",
)


def _eta_otp_cli_normalize(full_name: str) -> str:
    """``toplu-kullanici-olustur.py`` aracının normalizasyon kuralı.

    Türkçe karakterleri sadeleştirir, boşlukları ve özel karakterleri
    kaldırır, küçük harfe çevirir. "Ayşe Yılmaz" → "ayseyilmaz".
    """
    s = full_name.translate(_TR_MAP)
    s = re.sub(r"[^A-Za-z0-9]", "", s).lower()
    return s


def normalize_username(full_name: str) -> str:
    """TiHA dahili yedek normalizasyonu (nokta ayırıcılı kullanıcı adı).

    Yalnızca ``eta-otp-cli`` aracının bulunmadığı durumda kullanılır.
    'Ayşe Yılmaz' -> 'ayse.yilmaz'.
    """
    ascii_name = full_name.translate(_TR_MAP)
    ascii_name = re.sub(r"[^A-Za-z0-9 ]", "", ascii_name).strip().lower()
    parts = ascii_name.split()
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]}.{parts[-1]}"


# EBA QR / eta-usb-login mekanizmasında yeni açılan öğretmen hesapları
# şu cihaz gruplarına eklenir (kullanıcının ses, USB, kamera, yazıcı
# gibi standart masaüstü kaynaklarına erişebilmesi için). Yedek
# hesapları da aynı setle açıyoruz ki ileride bir öğretmen o hesaba
# atandığında ek yapılandırma gerekmesin.
EBA_STANDARD_GROUPS = (
    "cdrom", "floppy", "audio", "video", "plugdev",
    "bluetooth", "scanner", "netdev", "dip", "lpadmin",
)

# eta-otp-lock/pam_otp.py `@ogretmenler` gibi grup-OTP secret'larını
# destekliyor. Bu grup Pardus ETAP kurulumunda default gelmiyor;
# TiHA gerekirse kendisi oluşturur (idempotent).
OGRETMENLER_GROUP = "ogretmenler"


def ensure_ogretmenler_group() -> bool:
    """`ogretmenler` grubunu garantiler; yoksa groupadd çağırır.
    Zaten varsa dokunmaz."""
    check = run_cmd(["getent", "group", OGRETMENLER_GROUP], check=False)
    if check.ok:
        return True
    add = run_cmd(["groupadd", OGRETMENLER_GROUP], check=False)
    if add.ok:
        log.info("'%s' grubu oluşturuldu.", OGRETMENLER_GROUP)
        return True
    log.warning("'%s' grubu oluşturulamadı: %s",
                OGRETMENLER_GROUP, add.stderr.strip())
    return False


# EBA QR ile yeni açılan öğretmen hesaplarını `ogretmenler` grubuna
# otomatik ekleyen sistem servisi. /etc/passwd izlenir, yeni bir kayıt
# oluştuğunda script çalışır ve öğretmen kriterlerine uyan yeni
# hesapları gruba dahil eder. `dpkg` paket kurulumu sırasında oluşan
# hesaplar (dahili) UID < 1000 olduğu için filtrelenir.
AUTO_GROUP_SCRIPT = Path("/usr/local/sbin/tiha-auto-teacher-group.sh")
AUTO_GROUP_SERVICE = Path("/etc/systemd/system/tiha-auto-teacher-group.service")
AUTO_GROUP_PATH_UNIT = Path("/etc/systemd/system/tiha-auto-teacher-group.path")

AUTO_GROUP_SCRIPT_CONTENT = """#!/bin/bash
# TiHA — EBA QR ile yeni açılan öğretmen hesabını ogretmenler grubuna ekler.
# /etc/passwd her değiştiğinde tetiklenir. Sadece UID >= 1000 ve
# ogretmenler grubunda olmayan hesapları hedefler; sistem hesaplarına
# (etapadmin, ogrenci, ogretmen, root, nobody vb.) dokunmaz.
set -eu
EXCLUDE="etapadmin ogrenci ogretmen root nobody guest"
while IFS=: read -r user _ uid _ _ _ _; do
    [ "$uid" -ge 1000 ] || continue
    [ "$uid" -lt 60000 ] || continue
    for skip in $EXCLUDE; do
        [ "$user" = "$skip" ] && continue 2
    done
    # Zaten gruba üye mi?
    if id -nG "$user" 2>/dev/null | tr ' ' '\\n' | grep -qx ogretmenler; then
        continue
    fi
    /usr/sbin/usermod -a -G ogretmenler "$user" 2>/dev/null || true
    logger -t tiha-auto-teacher-group "Kullanıcı '$user' ogretmenler grubuna eklendi."
done < /etc/passwd
"""

AUTO_GROUP_SERVICE_CONTENT = """[Unit]
Description=TiHA — Yeni öğretmen hesabını ogretmenler grubuna ekle
Documentation=https://github.com/enseitankado/tiha

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/tiha-auto-teacher-group.sh
"""

AUTO_GROUP_PATH_CONTENT = """[Unit]
Description=TiHA — /etc/passwd izleyip yeni öğretmen hesabını ogretmenler grubuna ekle

[Path]
PathModified=/etc/passwd
Unit=tiha-auto-teacher-group.service

[Install]
WantedBy=multi-user.target
"""


def install_auto_group_service() -> bool:
    """Auto-group servisini kurar. `ogretmenler` grubunun varlığını
    çağıran taraf garantiler. Başarıda True."""
    try:
        AUTO_GROUP_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
        AUTO_GROUP_SCRIPT.write_text(AUTO_GROUP_SCRIPT_CONTENT, encoding="utf-8")
        AUTO_GROUP_SCRIPT.chmod(0o755)
        AUTO_GROUP_SERVICE.write_text(AUTO_GROUP_SERVICE_CONTENT, encoding="utf-8")
        AUTO_GROUP_PATH_UNIT.write_text(AUTO_GROUP_PATH_CONTENT, encoding="utf-8")
    except OSError as exc:
        log.warning("auto-group servis dosyaları yazılamadı: %s", exc)
        return False
    run_cmd(["systemctl", "daemon-reload"], check=False)
    en = run_cmd(
        ["systemctl", "enable", "--now", "tiha-auto-teacher-group.path"],
        check=False,
    )
    if not en.ok:
        log.warning("auto-group path unit enable edilemedi: %s", en.stderr.strip())
        return False
    # Kurulum anında zaten mevcut olan öğretmen hesapları varsa bir kez tetikle
    run_cmd(["systemctl", "start", "tiha-auto-teacher-group.service"], check=False)
    return True


def uninstall_auto_group_service() -> None:
    """Auto-group servisini kaldırır (undo yolu)."""
    run_cmd(
        ["systemctl", "disable", "--now", "tiha-auto-teacher-group.path"],
        check=False,
    )
    for p in (AUTO_GROUP_PATH_UNIT, AUTO_GROUP_SERVICE, AUTO_GROUP_SCRIPT):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    run_cmd(["systemctl", "daemon-reload"], check=False)


def create_user(username: str, full_name: str = "") -> bool:
    """TiHA dahili yedek kullanıcı oluşturma (useradd + usermod -L).

    ``full_name`` verilirse passwd dosyasının GECOS alanına yazılır
    (öğretmenin görünen ad/soyadı). Yeni açılan hesap EBA QR /
    eta-usb-login mekanizmasıyla aynı standart cihaz gruplarına
    eklenir; parola atanmadığı için usermod -L ile kilitlenir
    (öğretmen yalnız OTP/QR ile giriş yapar).
    `ogretmenler` grubuna eklemek create_user'ın kapsamı dışı — o iş
    apply()'da opsiyonel "Yedek hesaplar için grup-PIN" akışına ait.
    """
    if user_exists(username):
        if full_name:
            set_user_full_name(username, full_name)
        return True
    cmd = ["useradd", "--create-home", "--shell", "/bin/bash"]
    if full_name:
        cmd += ["--comment", full_name]
    cmd.append(username)
    result = run_cmd(cmd)
    if not result.ok:
        log.error("Kullanıcı eklenemedi '%s': %s", username, result.stderr.strip())
        return False
    for grp in EBA_STANDARD_GROUPS:
        run_cmd(["usermod", "-a", "-G", grp, username], check=False)
    run_cmd(["usermod", "-L", username])
    return True


def set_user_full_name(username: str, full_name: str) -> bool:
    """Var olan bir kullanıcının GECOS (ad/soyad) alanını günceller."""
    if not user_exists(username):
        return False
    res = run_cmd(["usermod", "-c", full_name, username])
    if not res.ok:
        log.warning("GECOS güncellenemedi '%s': %s", username, res.stderr.strip())
    return res.ok


def kill_user_processes(username: str) -> tuple[int, int]:
    """Kullanıcıya ait tüm prosesleri sonlandırır.

    Önce SIGTERM, kısa bir gecikmeden sonra SIGKILL gönderir. Dönüş:
    (term_returncode, kill_returncode). pkill'in '0=öldürdü, 1=hiç süreç
    yok' yarı-anlamlı çıkış kodu nedeniyle bunları işin sonucuna direkt
    bağlamayız; çağıran tarafta yalnız bilgilendirme amacıyla kullanılır.
    """
    import time
    term = run_cmd(["pkill", "-TERM", "-u", username])
    time.sleep(1)
    # Ayrıca varsa systemd-logind oturumlarını sonlandır
    run_cmd(["loginctl", "terminate-user", username])
    time.sleep(0.5)
    kill = run_cmd(["pkill", "-KILL", "-u", username])
    return term.returncode, kill.returncode


def load_secrets() -> dict[str, str]:
    if not OTP_SECRETS_FILE.exists():
        return {}
    try:
        return json.loads(OTP_SECRETS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("otp-secrets.json okunamadı: %s", exc)
        return {}


def save_secrets(secrets: dict[str, str]) -> None:
    OTP_SECRETS_FILE.write_text(
        json.dumps(secrets, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    OTP_SECRETS_FILE.chmod(0o600)
    os.chown(OTP_SECRETS_FILE, 0, 0)


def otpauth_url(username: str, secret: str) -> str:
    """Google Authenticator/Authy vb.'in kabul ettiği otpauth:// URL'si."""
    issuer_enc = quote(OTP_ISSUER)
    user_enc = quote(f"{OTP_ISSUER}:{username}")
    return (
        f"otpauth://totp/{user_enc}?secret={secret}"
        f"&issuer={issuer_enc}&digits=6&period=30"
    )


def count_regular_users() -> int:
    """UID >= 1000 olan normal kullanıcı sayısını döndürür."""
    result = run_cmd(["getent", "passwd"])
    if not result.ok:
        return 0

    count = 0
    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 3:
            try:
                uid = int(parts[2])
                if uid >= 1000 and uid != 65534:  # nobody user hariç
                    count += 1
            except ValueError:
                continue
    return count


def download_greeter_script() -> bool:
    """Greeter cache betiğini GitHub'dan indirip güvenli konuma kaydeder."""
    try:
        # curl ile betiği indir
        result = run_cmd([
            "curl", "-fsSL", "-o", str(GREETER_SCRIPT_PATH), GREETER_SCRIPT_URL
        ], timeout=60)

        if not result.ok:
            log.error("Greeter script indirilemedi: %s", result.stderr)
            return False

        # Çalıştırılabilir yap
        GREETER_SCRIPT_PATH.chmod(0o755)
        log.info("Greeter script indirildi: %s", GREETER_SCRIPT_PATH)
        return True

    except Exception as exc:
        log.error("Greeter script indirme hatası: %s", exc)
        return False


def create_greeter_service() -> bool:
    """Sistemd service dosyası oluşturur (açılışta çalışır)."""
    service_content = f"""[Unit]
Description=AccountsService Greeter Cache Updater
After=accounts-daemon.service
Wants=accounts-daemon.service

[Service]
Type=oneshot
ExecStart={GREETER_SCRIPT_PATH}
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

    try:
        GREETER_SERVICE_PATH.write_text(service_content, encoding="utf-8")
        GREETER_SERVICE_PATH.chmod(0o644)

        # Servisi etkinleştir (açılışta çalışsın)
        result = run_cmd(["systemctl", "enable", "greeter-cache.service"])
        if not result.ok:
            log.error("Greeter service etkinleştirilemedi: %s", result.stderr)
            return False

        log.info("Greeter service oluşturuldu ve etkinleştirildi: %s", GREETER_SERVICE_PATH)
        return True

    except Exception as exc:
        log.error("Greeter service oluşturma hatası: %s", exc)
        return False


def run_greeter_script_once() -> bool:
    """Greeter cache betiğini bir kez çalıştırır."""
    if not GREETER_SCRIPT_PATH.exists():
        log.error("Greeter script bulunamadı: %s", GREETER_SCRIPT_PATH)
        return False

    result = run_cmd([str(GREETER_SCRIPT_PATH)], timeout=300)
    if not result.ok:
        log.error("Greeter script çalıştırılamadı: %s", result.stderr)
        return False

    log.info("Greeter script başarıyla çalıştırıldı")
    return True


def remove_greeter_setup() -> bool:
    """Greeter script ve service'ini kaldırır."""
    success = True

    # Service'i durdur ve devre dışı bırak
    run_cmd(["systemctl", "disable", "greeter-cache.service"])
    run_cmd(["systemctl", "stop", "greeter-cache.service"])

    # Service dosyasını sil
    try:
        GREETER_SERVICE_PATH.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Service dosyası silinemedi: %s", exc)
        success = False

    # Script dosyasını sil
    try:
        GREETER_SCRIPT_PATH.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Script dosyası silinemedi: %s", exc)
        success = False

    # systemd daemon'ı yenile
    run_cmd(["systemctl", "daemon-reload"])

    return success


def get_extra_users() -> list[str]:
    """Varsayılan kullanıcılar dışındaki UID >= 1000 kullanıcıları döndürür."""
    import pwd
    extra_users = []

    try:
        for user in pwd.getpwall():
            if (1000 <= user.pw_uid < 60000 and
                user.pw_name not in DEFAULT_SYSTEM_USERS):
                extra_users.append(user.pw_name)
    except Exception as exc:
        log.error("Kullanıcı listesi alınamadı: %s", exc)

    return sorted(extra_users)



def reset_to_default_users(
    progress: ProgressCallback | None = None,
) -> tuple[bool, list[str], dict[str, str]]:
    """Varsayılan kullanıcılar dışındaki tüm kullanıcıları siler.

    Her kullanıcı için önce prosesleri sonlandırılır (deluser açık
    oturum/proses varsa başarısız olabilir), sonra ``deluser --remove-home``
    çağrılır. Dönüş: ``(başarı, silinenler, hatalar)`` — ``hatalar``
    sözlüğü silinemeyen kullanıcı adından deluser stderr çıktısına eşler.
    """
    extra_users = get_extra_users()
    removed_users: list[str] = []
    errors: dict[str, str] = {}

    if not extra_users:
        return True, [], {}

    for username in extra_users:
        if progress:
            progress(f"\n→ {username}: prosesler sonlandırılıyor...")
        log.info("Kullanıcı için prosesler sonlandırılıyor: %s", username)
        kill_user_processes(username)

        if progress:
            progress(f"  deluser --remove-home {username}")
        log.info("Kullanıcı siliniyor: %s", username)
        result = run_cmd(["deluser", "--remove-home", username])
        if result.ok:
            removed_users.append(username)
            log.info("Kullanıcı başarıyla silindi: %s", username)
            if progress:
                progress(f"  ✓ {username} silindi")
        else:
            err = (result.stderr or result.stdout or "").strip() or \
                f"deluser çıkış kodu {result.returncode}"
            errors[username] = err
            log.error("Kullanıcı silinemedi %s: %s", username, err)
            if progress:
                progress(f"  ✗ {username} silinemedi: {err}")

    # OTP secrets dosyasını temizle (sadece varsayılan kullanıcılar kalacak)
    try:
        secrets = load_secrets()
        default_secrets = {k: v for k, v in secrets.items()
                         if k in DEFAULT_SYSTEM_USERS}
        save_secrets(default_secrets)
        log.info("OTP secrets dosyası temizlendi")
    except Exception as exc:
        log.error("OTP secrets temizlenemedi: %s", exc)

    # Greeter cache'i güncelle
    if GREETER_SCRIPT_PATH.exists():
        run_greeter_script_once()
        log.info("Greeter cache güncellendi")

    return len(removed_users) == len(extra_users), removed_users, errors




class OTPSecretsModule(Module):
    id = "m03_otp_secrets"
    title = "Öğretmen PIN anahtarları"
    sidebar_title = "Toplu pin anahtarı"
    apply_hint = (
        "Listedeki ve yedek hesaplar için PIN anahtarları üretilir."
    )
    save_filename = "ogretmen-pin-anahtarlari.txt"
    streams_output = True
    rationale = (
        "Her öğretmen için 6 haneli pin kodu üreten anahtarları toplu "
        "olarak üretir.\n\n"
        "Anahtar nedir, kod nedir?\n"
        "  • Anahtar: Telefona kurulan uygulamada (Google Authenticator vb.) "
        "saklanan, uzun ve gizli bir tanıtıcıdır — bir tür dijital kimlik "
        "kartı. Bir kez kurulur, sonra hep telefonda durur.\n"
        "  • Kod: Bu anahtar kullanılarak zamana bağlı olarak üretilen, "
        "30 saniyede bir değişen 6 haneli bir sayıdır. Tahtaya giriş "
        "yaparken bu sayıyı yazarsınız. Geçici bir parola gibi düşünün — "
        "her seferinde tahtanın kabul ettiği yeni bir kapı şifresi.\n\n"
        "Olağan kullanımda her öğretmen kendi anahtarını uygulamasından "
        "üretip her tahtaya tek tek (dışa aktar / içe aktar) yüklemek "
        "zorundadır; onlarca tahtada bu işlem pratik değil. Bu adım "
        "anahtarları imaj alınmadan ÖNCE her öğretmen için merkezî olarak "
        "üretir ve imaja gömer. İmajdan dağıtılan tüm tahtalarda anahtarlar "
        "hazır gelir; öğretmen yalnızca kendi anahtarını size verdiğimiz "
        "biçimde (yazılı veya QR kodu olarak) uygulamasına bir kez ekler ve "
        "sonra ister sınıf 1'deki tahta olsun ister sınıf 50'deki, hepsinde "
        "kendi PIN kodunu görüp girerek oturum açabilir.\n\n"
        "Dikkat: Otomatik parola temizliği adımı uygulandıysa öğretmen "
        "tahtada kendi PIN'ini üretemez (yerel parola bilinmediği için); "
        "bu durumda anahtarları merkezî olarak üretmek mecburidir. "
        "Uygulanmadıysa da tek tek kurulumdan çok daha hızlıdır.\n\n"
        "Geri alma ile varsayılan kullanıcılar (etapadmin, ogrenci, "
        "ogretmen) dışındakiler silinir."
    )

    def prefetch_preview_state(self, on_ready=None) -> None:
        """Sayfa açıldığında eta-otp-cli erişilebilirlik kontrolünü arka
        planda başlat — gerekirse GitHub indirme yapılabilir, UI
        bloke olmasın."""
        _eta_otp_cli_available.get_async(on_ready)

    def preview(self) -> str:
        """m08 stiliyle hizalı key-value + girintili dash liste."""
        import pwd as _pwd
        import datetime

        existing = load_secrets()
        standard_or_admin = {"etapadmin", "ogretmen", "ogrenci"}
        personal_users = sorted(
            p.pw_name for p in _pwd.getpwall()
            if 1000 <= p.pw_uid < 60000 and p.pw_name not in standard_or_admin
        )
        has_otp = [u for u in personal_users if u in existing]
        missing_otp = [u for u in personal_users if u not in existing]
        orphan_secrets = sorted(
            u for u in existing
            if u not in personal_users and u not in standard_or_admin
        )
        current_time = datetime.datetime.now().strftime("%H:%M")

        tool_available = _eta_otp_cli_available.get_async()
        if tool_available is None:
            tool_line = "kontrol ediliyor"
        elif tool_available:
            tool_line = "enseitankado/eta-otp-cli (yalnız OTP anahtarları)"
        else:
            tool_line = "TiHA dahili pyotp yolu (yalnız OTP anahtarları)"

        user_count = count_regular_users()
        extra_users = get_extra_users()

        lines: list[str] = []
        lines.append(f"Durum kontrolü       : {current_time}")
        lines.append(f"Araç                 : {tool_line}")
        lines.append(f"Sistem kullanıcıları : {user_count} (UID >= 1000)")
        lines.append(f"Kişisel hesaplar     : {len(personal_users)}")
        lines.append(
            f"PIN anahtarlı hesap  : {len(has_otp)}/{len(personal_users)}"
        )
        lines.append(f"Toplam PIN kaydı     : {len(existing)}")
        if user_count >= MIN_USERS_FOR_CACHE:
            greeter_state = (
                "kurulu"
                if GREETER_SERVICE_PATH.exists()
                else "kurulacak"
            )
            lines.append(f"Greeter cache        : {greeter_state}")
        lines.append("")

        lines.append("Not: Bu adım yalnız OTP anahtarları oluşturur; "
                     "sistem kullanıcı hesaplarını oluşturmaz (yedek "
                     "hesap sayısı belirtilirse onların hesabı bu adımda "
                     "açılır).")
        lines.append("")

        if has_otp:
            lines.append("PIN anahtarı kurulu kişisel hesaplar:")
            for u in has_otp:
                lines.append(f"  - {u}")
            lines.append("")
        if missing_otp:
            lines.append("PIN anahtarı OLMAYAN kişisel hesaplar:")
            for u in missing_otp:
                lines.append(f"  - {u}")
            lines.append(
                "  \"Açılışta parola temizliği\" adımı etkinken bu "
                "hesaplar tahtaya hiç giremez. Anahtar üretmek için "
                "aşağıdaki listeye adlarını yazın."
            )
            lines.append("")
        if orphan_secrets:
            lines.append(
                "Sistemde hesabı kalmayan PIN kayıtları "
                f"(hesap silinmiş olabilir): {', '.join(orphan_secrets)}"
            )
            lines.append("")

        lines.append("Kullanıcı yönetimi")
        if extra_users:
            head = extra_users[:10]
            more = len(extra_users) - len(head)
            lines.append(
                f"  Fazladan kullanıcı hesabı : {len(extra_users)} adet"
            )
            for u in head:
                lines.append(f"    - {u}")
            if more > 0:
                lines.append(f"    - ... ve {more} tane daha")
            lines.append(
                "  \"Fazladan Hesapları Sil\" düğmesi ile bunları "
                "kaldırabilirsiniz; sistemde yalnız etapadmin, "
                "ogrenci ve ogretmen kalır."
            )
        else:
            lines.append(
                "  Sistemde yalnız varsayılan kullanıcılar mevcut "
                "(etapadmin, ogrenci, ogretmen)."
            )
        return "\n".join(lines)

    # -----------------------------------------------------------------
    # Uygula
    # -----------------------------------------------------------------

    def apply(self, params=None, progress: ProgressCallback | None = None) -> ApplyResult:
        params = params or {}
        raw_list: str = params.get("teacher_names", "")
        reserve: int = int(params.get("reserve_count", 0) or 0)
        include_etapadmin: bool = str(
            params.get("include_etapadmin", "False")
        ).lower() in ("true", "1", "yes", "on")
        include_ogretmen: bool = str(
            params.get("include_ogretmen", "False")
        ).lower() in ("true", "1", "yes", "on")
        make_group_pin: bool = str(
            params.get("make_group_pin", "False")
        ).lower() in ("true", "1", "yes", "on")
        auto_group_new_teachers: bool = str(
            params.get("auto_group_new_teachers", "False")
        ).lower() in ("true", "1", "yes", "on")

        teacher_names = [line.strip() for line in raw_list.splitlines() if line.strip()]

        # Yedek hesaplar — toplu-kullanici-olustur.py'nin normalizasyonu
        # 'Ogretmen 01' → 'ogretmen01' verir.
        for i in range(1, reserve + 1):
            teacher_names.append(f"Ogretmen {i:02d}")

        # Opsiyonel: etapadmin için de PIN üret. Sistem yöneticisi
        # parolasını paylaşmadan birine sadece o anlık 6 haneli PIN'i
        # vererek geçici yetki devretsin diye.
        if include_etapadmin:
            teacher_names.append("etapadmin")
        # Opsiyonel: ortak ogretmen hesabı için de PIN üret.
        if include_ogretmen:
            teacher_names.append("ogretmen")

        if not teacher_names:
            return ApplyResult(
                False,
                "Liste boş — öğretmen eklemediniz ve yedek hesap sayısı 0.",
                details="Lütfen en az bir isim girin veya yedek hesap sayısını artırın.",
            )

        state = self.ensure_state_dir()
        backup_file(OTP_SECRETS_FILE, state)
        before_secrets = set(load_secrets().keys())

        cli_script = _eta_otp_cli_bulk_script()
        if cli_script:
            success = self._apply_with_tool(cli_script, teacher_names, progress)
        else:
            success = self._apply_with_internal(teacher_names, progress)

        if not success:
            return ApplyResult(
                False,
                "PIN anahtarları üretilemedi.",
                details="Ayrıntı için /var/log/tiha/tiha.log dosyasına bakın.",
            )

        # Yedek hesaplar için sistem hesabını GARANTİLE. Dahili yol
        # (_apply_with_internal) zaten create_user çağırıyor; dış araç
        # yolu (_apply_with_tool) yalnız OTP anahtarını üretiyor,
        # sistem hesabı oluşturmuyor.
        created_reserve_usernames: list[str] = []
        if reserve > 0 and cli_script:
            if progress:
                progress(f"\n{reserve} yedek hesap sistemde oluşturuluyor "
                         "(useradd + EBA cihaz grupları + parola kilitli)...")
            for i in range(1, reserve + 1):
                full_name = f"Ogretmen {i:02d}"
                username = _eta_otp_cli_normalize(full_name)
                if not username:
                    continue
                if create_user(username, full_name=full_name):
                    created_reserve_usernames.append(username)
                    if progress:
                        progress(f"  + {username}")
                else:
                    if progress:
                        progress(f"  - {username} olusturulamadi")
        elif reserve > 0:
            for i in range(1, reserve + 1):
                full_name = f"Ogretmen {i:02d}"
                username = normalize_username(full_name)
                if username and user_exists(username):
                    created_reserve_usernames.append(username)

        # Her hesap için passwd GECOS (ad/soyad) alanını yaz.
        self._apply_gecos(teacher_names, cli_used=bool(cli_script), progress=progress)

        # Yeni eklenenleri ve anahtarlarını oku
        after_secrets = load_secrets()
        new_users = [u for u in after_secrets if u not in before_secrets]

        # Yedek hesap grup-PIN akışı — checkbox işaretli + reserve > 0 +
        # en az bir ogretmenX hesabı oluşturuldu koşulu.
        group_key = f"@{OGRETMENLER_GROUP}"
        group_secret_is_new = False
        if make_group_pin and reserve > 0 and created_reserve_usernames:
            if not ensure_ogretmenler_group():
                if progress:
                    progress(f"\n'{OGRETMENLER_GROUP}' grubu olusturulamadi; "
                             "grup-PIN akisi iptal edildi.")
            else:
                if progress:
                    progress(f"\n{len(created_reserve_usernames)} yedek hesap "
                             f"'{OGRETMENLER_GROUP}' grubuna ekleniyor...")
                for u in created_reserve_usernames:
                    run_cmd(["usermod", "-a", "-G", OGRETMENLER_GROUP, u], check=False)
                    if progress:
                        progress(f"  + {u}")
                after_secrets[group_key] = pyotp.random_base32()
                save_secrets(after_secrets)
                group_secret_is_new = True
                if progress:
                    progress(f"\nOrtak grup-PIN uretildi: {group_key} — "
                             "onceki grup-PIN gecersizdir, telefonlara "
                             "yeniden ekletin.")
        elif make_group_pin and reserve == 0:
            if progress:
                progress("\n'Yedek hesaplar icin ortak PIN' isaretli ama "
                         "yedek hesap sayisi 0 — akis atlandi.")

        if not new_users and not group_secret_is_new:
            return ApplyResult(
                False,
                "Hiç yeni kullanıcı oluşmadı — hepsi zaten vardı olabilir.",
                details=f"Mevcut kayıt sayısı: {len(after_secrets)}",
            )
        # Grup secret'ı da PIN kartı listesine dahil et
        if group_secret_is_new and group_key not in new_users:
            new_users.append(group_key)

        # Greeter cache kontrolü ve kurulumu
        total_users = count_regular_users()
        greeter_cache_applied = False
        if total_users >= MIN_USERS_FOR_CACHE:
            if progress:
                progress(f"{total_users} kullanıcı tespit edildi — greeter cache kurulumu başlatılıyor...")

            # GitHub'dan script indir
            if not GREETER_SCRIPT_PATH.exists():
                if progress:
                    progress("Greeter cache script GitHub'dan indiriliyor...")
                if not download_greeter_script():
                    log.warning("Greeter cache script indirilemedi, devam ediliyor...")
                else:
                    if progress:
                        progress("✓ Greeter cache script indirildi")

            # Systemd service oluştur
            if not GREETER_SERVICE_PATH.exists() and GREETER_SCRIPT_PATH.exists():
                if progress:
                    progress("Otomatik greeter cache service oluşturuluyor...")
                if not create_greeter_service():
                    log.warning("Greeter cache service oluşturulamadı, devam ediliyor...")
                else:
                    if progress:
                        progress("✓ Greeter cache service oluşturuldu")

            # Script'i bir kez çalıştır (yeni kullanıcıları cache'e al)
            if GREETER_SCRIPT_PATH.exists():
                if progress:
                    progress("Greeter cache güncellemesi yapılıyor...")
                if run_greeter_script_once():
                    greeter_cache_applied = True
                    if progress:
                        progress("✓ Greeter cache güncellendi")
                else:
                    log.warning("Greeter cache güncellemesi başarısız, devam ediliyor...")
        elif progress:
            progress(f"{total_users} kullanıcı var — greeter cache gerekli değil (limit: {MIN_USERS_FOR_CACHE})")

        # Tam adları username -> display map'e koy (rapor için)
        display_of: dict[str, str] = {}
        for name in teacher_names:
            u = _eta_otp_cli_normalize(name) if cli_script else normalize_username(name)
            if u in new_users:
                if u == "etapadmin":
                    display_of[u] = "Sistem Yöneticisi (etapadmin)"
                    continue
                display_of[u] = name
        # Grup-PIN kartı için özel etiket
        if group_key in new_users:
            display_of[group_key] = "Ogretmenler grubu — ORTAK PIN"

        # Rapor
        report_lines: list[str] = []
        report_lines.append("─" * 76)
        report_lines.append(f"  {len(new_users)} hesap için PIN anahtarı üretildi.")
        report_lines.append(f"  Dosya: {OTP_SECRETS_FILE}")
        report_lines.append(f"  Üretici: Issuer = \"{OTP_ISSUER}\", 6 hane, 30 sn periyot.")
        if cli_script:
            report_lines.append("  Araç:   enseitankado/eta-otp-cli  →  toplu-kullanici-olustur.py")
        report_lines.append("─" * 76)
        for idx, user in enumerate(sorted(new_users), 1):
            secret = after_secrets[user]
            url = otpauth_url(user, secret)
            display = display_of.get(user, "(yedek hesap)")
            report_lines.append("")
            report_lines.append(f"[{idx:02d}]  {display}")
            report_lines.append(f"     Kullanıcı adı : {user}")
            report_lines.append(f"     PIN anahtarı  : {secret}")
            report_lines.append(f"     otpauth URL   : {url}")
        report_lines.append("")
        report_lines.append("─" * 76)
        report_lines.append(
            "Kullanım: öğretmenler bu anahtarı Google Authenticator vb.\n"
            "uygulamaya manuel girebilir ya da otpauth URL'sini çevrimdışı\n"
            "bir QR üreticide taratabilir. Anahtarları yalnızca özelden\n"
            "(şifreli mesaj, gizli dağıtım listesi) teslim edin."
        )
        copyable = "\n".join(report_lines)

        # Yazdırılabilir HTML kâğıt — her öğretmen için tek satırlık kart.
        # Tarayıcıda açılır, Ctrl+P ile yazdırılır ya da PDF olarak kaydedilir.
        html_path = self._write_printable_paper(
            new_users=sorted(new_users),
            secrets=after_secrets,
            display_of=display_of,
            state_dir=state,
        )
        if html_path and progress:
            progress(f"🖨️ Yazdırılabilir kâğıt: {html_path}")

        details = (
            f"{len(new_users)} hesap için anahtar üretildi. Tam liste aşağıda; "
            "'Panoya kopyala' veya 'Dosyaya kaydet…' ile alın."
        )
        if html_path:
            details += (
                f"\n\n🖨️ Yazdırılabilir öğretmen kâğıdı:\n"
                f"  {html_path}\n"
                "  Tarayıcıda açıp Ctrl+P ile yazdırın veya PDF kaydedin.\n"
                "  (Etapadmin oturumunda otomatik açılmayı denedik.)"
            )

        # Yeni öğretmen hesaplarını ogretmenler grubuna otomatik ekleyen
        # sistem servisini kur (checkbox işaretliyse).
        auto_group_service_installed = False
        if auto_group_new_teachers:
            ensure_ogretmenler_group()
            if install_auto_group_service():
                auto_group_service_installed = True
                if progress:
                    progress("\nOtomatik grup ekleme servisi kuruldu — "
                             "EBA QR ile yeni açılan öğretmen hesapları "
                             "ogretmenler grubuna otomatik dahil edilecek.")

        # Greeter cache bilgisini ekle
        summary_parts = [f"{len(new_users)} PIN anahtarı üretildi ve {OTP_SECRETS_FILE} dosyasına yazıldı."]
        if greeter_cache_applied:
            summary_parts.append("Greeter cache güncellendi ve otomatik çalıştırma ayarlandı.")
        elif total_users >= MIN_USERS_FOR_CACHE:
            summary_parts.append("Greeter cache kurulumu tamamlanamadı.")
        if auto_group_service_installed:
            summary_parts.append("Yeni öğretmen hesabı → ogretmenler grubu servis kuruldu.")

        return ApplyResult(
            success=True,
            summary=" ".join(summary_parts),
            details=details,
            copyable=copyable,
            data={
                "passed_names": teacher_names,
                "created_users": sorted(new_users),
                "used_tool": bool(cli_script),
                "greeter_cache_applied": greeter_cache_applied,
                "total_users": total_users,
                "auto_group_service_installed": auto_group_service_installed,
            },
        )

    def _write_printable_paper(
        self,
        *,
        new_users: list[str],
        secrets: dict[str, str],
        display_of: dict[str, str],
        state_dir: Path,
    ) -> Path | None:
        """Her öğretmen için yazdırılabilir bir kart içeren HTML dosyası
        oluşturur. Best-effort xdg-open ile aktif kullanıcı oturumunda
        tarayıcıda açar."""
        from datetime import datetime as _dt
        from html import escape as _esc
        import subprocess as _sp

        ts = _dt.now().strftime("%Y%m%d-%H%M%S")
        out = state_dir / f"ogretmen-pin-kagitlari-{ts}.html"

        cards: list[str] = []
        for user in new_users:
            secret = secrets.get(user, "")
            display = display_of.get(user, "(yedek hesap)")
            grouped = " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
            url = otpauth_url(user, secret)
            is_group = user.startswith("@")
            if is_group:
                user_line = (
                    f'<div class="user">Grup: <code>{_esc(user[1:])}</code> '
                    "— bu PIN, gruba üye tüm öğretmen hesaplarına giriş için "
                    "geçerlidir.</div>"
                )
                instructions = f'''    <ol>
      <li>Telefonunuza <strong>Google Authenticator</strong> veya benzeri
          bir uygulama kurun.</li>
      <li>Uygulamada <em>"+ Anahtar ekle"</em> > <em>"Anahtarı manuel
          gir"</em>'i seçin.</li>
      <li>Hesap adı olarak <em>istediğiniz</em> bir etiket yazın
          (örn. <code>Sınıf-PIN</code>).</li>
      <li>Anahtarı 4'lü gruplar hâlinde yukarıdaki kutudan kopyalayın.</li>
      <li>Tür: <em>Zaman tabanlı</em> (varsayılan).</li>
      <li>Kaydedin. Bu PIN, tahtada <strong>{_esc(user[1:])}</strong>
          grubuna üye herhangi bir hesabın giriş ekranında
          kullanılabilir.</li>
    </ol>'''
            else:
                user_line = (
                    f'<div class="user">Kullanıcı adı: '
                    f'<code>{_esc(user)}</code></div>'
                )
                instructions = f'''    <ol>
      <li>Telefonunuza <strong>Google Authenticator</strong> veya benzeri
          bir uygulama kurun.</li>
      <li>Uygulamada <em>"+ Anahtar ekle"</em> > <em>"Anahtarı manuel
          gir"</em>'i seçin.</li>
      <li>Hesap adı olarak yazın: <code>{_esc(user)}</code></li>
      <li>Anahtarı 4'lü gruplar hâlinde yukarıdaki kutudan kopyalayın.</li>
      <li>Tür: <em>Zaman tabanlı</em> (varsayılan).</li>
      <li>Kaydedin. Artık her 30 saniyede yeni bir 6 haneli PIN üretilir;
          tahta giriş ekranında bu PIN'i girersiniz.</li>
    </ol>'''
            cards.append(f'''
<article class="card{' group' if is_group else ''}">
  <header>
    <h2>{_esc(display)}</h2>
    {user_line}
  </header>
  <section class="secret">
    <div class="label">PIN anahtarı (telefonunuza manuel girin):</div>
    <div class="key">{_esc(grouped)}</div>
  </section>
  <section class="instructions">
{instructions}
  </section>
  <footer class="otpauth">
    <small>otpauth URL: <code>{_esc(url)}</code></small>
  </footer>
</article>
''')

        html = f'''<!DOCTYPE html>
<html lang="tr"><head>
<meta charset="utf-8">
<title>Öğretmen PIN kâğıtları — {ts}</title>
<style>
  body {{ font-family: Ubuntu, sans-serif; margin: 20px; color: #222; }}
  h1 {{ font-size: 16pt; border-bottom: 2px solid #2e7d32; padding-bottom: 6px; }}
  .meta {{ color: #666; font-size: 10pt; margin-bottom: 18px; }}
  .card {{
    page-break-inside: avoid;
    border: 1.5px dashed #888;
    padding: 14px 18px;
    margin-bottom: 16px;
    border-radius: 6px;
    background: #fafafa;
  }}
  .card h2 {{ margin: 0 0 4px 0; font-size: 14pt; }}
  .card .user {{ font-size: 10pt; color: #555; margin-bottom: 8px; }}
  .secret .label {{ font-size: 9pt; color: #555; }}
  .secret .key {{
    font-family: 'Ubuntu Mono', monospace;
    font-size: 18pt;
    letter-spacing: 1px;
    background: #fff;
    padding: 6px 12px;
    border: 1px solid #ddd;
    border-radius: 4px;
    margin: 4px 0 12px 0;
    display: inline-block;
    color: #1a73e8;
    font-weight: 600;
  }}
  .instructions ol {{ font-size: 10pt; margin: 0; padding-left: 20px; }}
  .instructions li {{ margin: 2px 0; }}
  .otpauth {{ margin-top: 8px; }}
  .otpauth code {{ font-size: 7pt; color: #888; word-break: break-all; }}
  @media print {{
    body {{ margin: 8mm; }}
    .card {{ break-inside: avoid; }}
  }}
</style>
</head><body>
<h1>Öğretmen PIN Kâğıtları</h1>
<div class="meta">
  Oluşturulma: {ts.replace("-", " ")} · Toplam: {len(new_users)} kâğıt ·
  TiHA tarafından üretildi · Issuer: <em>{_esc(OTP_ISSUER)}</em>
</div>
{"".join(cards)}
</body></html>
'''
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html, encoding="utf-8")
            # Etapadmin'in açabilmesi için izinleri açık tut
            out.chmod(0o644)
        except OSError as exc:
            log.warning("Yazdırılabilir kâğıt oluşturulamadı: %s", exc)
            return None

        # Best-effort: aktif grafik oturumda tarayıcıyı aç
        try:
            from ..core.utils import _find_active_graphical_session
            env = _find_active_graphical_session()
            if env:
                _sp.Popen(
                    ["sudo", "-u", env["USER"], "env"]
                    + [f"{k}={v}" for k, v in env.items()]
                    + ["xdg-open", str(out)],
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                    start_new_session=True,
                )
        except (OSError, ImportError) as exc:
            log.debug("xdg-open atlandı: %s", exc)

        return out

    def _apply_with_tool(
        self, script: Path, names: list[str], progress: ProgressCallback | None,
    ) -> bool:
        """otp-cli.py aracını kullanarak sadece PIN anahtarları üret (kullanıcı oluşturmadan)."""
        if progress:
            progress("enseitankado/eta-otp-cli aracı çalıştırılıyor (sadece OTP anahtarları)…")

        # otp-cli.py dosyası aynı dizinde olmalı
        otp_cli_script = script.parent / "otp-cli.py"
        if not otp_cli_script.exists():
            log.error("otp-cli.py bulunamadı: %s", otp_cli_script)
            return False

        # Her kullanıcı için olustur komutunu çalıştır (sadece OTP anahtarı)
        success_count = 0
        total_count = len(names)

        for idx, full_name in enumerate(names, 1):
            username = _eta_otp_cli_normalize(full_name)
            if not username:
                continue

            if progress:
                progress(f"  {idx}/{total_count}: {full_name} → {username}")

            # otp-cli.py olustur komutunu çalıştır (sadece OTP anahtarı)
            result = run_cmd([
                "python3", str(otp_cli_script), "olustur", username
            ], timeout=30)

            if result.ok:
                success_count += 1
                if progress:
                    progress(f"    ✓ OTP anahtarı oluşturuldu: {username}")
            else:
                log.error("OTP anahtarı oluşturulamadı %s: %s", username, result.stderr)
                if progress:
                    progress(f"    ✗ Hata: {username}")

        if progress:
            progress(f"Tamamlandı: {success_count}/{total_count} OTP anahtarı oluşturuldu")

        return success_count > 0

    def _apply_with_internal(
        self, names: list[str], progress: ProgressCallback | None,
    ) -> bool:
        """Aracın olmadığı durumda TiHA'nın kendi pyotp yolu."""
        if progress:
            progress("Dahili pyotp yolu kullanılıyor.")

        secrets = load_secrets()
        for name in names:
            user = normalize_username(name)
            if not user:
                continue
            create_user(user, full_name=name)
            secrets[user] = pyotp.random_base32()
            if progress:
                progress(f"  • {user} ({name}): PIN anahtarı üretildi")
        save_secrets(secrets)
        return True

    def _apply_gecos(
        self,
        teacher_names: list[str],
        cli_used: bool,
        progress: ProgressCallback | None = None,
    ) -> int:
        """Her öğretmen için passwd GECOS alanını ad-soyad ile günceller.

        Hem dahili hem harici (eta-otp-cli) yol için çağrılır; harici
        araç GECOS yazmadığı için bu adım imajda ad-soyadı garanti
        eder. Güncellenen kullanıcı sayısını döner.
        """
        normalize = _eta_otp_cli_normalize if cli_used else normalize_username
        updated = 0
        for name in teacher_names:
            user = normalize(name)
            if not user:
                continue
            # etapadmin sistem yöneticisi hesabı — GECOS'unu "etapadmin"
            # diye ezmeyelim (mevcut ad/soyad varsa korunsun).
            if user == "etapadmin":
                continue
            if set_user_full_name(user, name):
                updated += 1
        if progress and updated:
            progress(f"  • {updated} kullanıcının ad/soyad alanı güncellendi")
        return updated

    # -----------------------------------------------------------------
    # Geri al
    # -----------------------------------------------------------------

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        data = data or {}
        created = data.get("created_users", []) or []
        passed = data.get("passed_names", []) or []
        cli_script = _eta_otp_cli_bulk_script()

        # Auto-group izleme servisi kurulduysa kaldır (idempotent).
        uninstall_auto_group_service()

        removed: list[str] = []
        if created:
            if cli_script and passed:
                # Aynı isim dosyası ile --kullanicilari-sil
                with tempfile.NamedTemporaryFile(
                    "w", suffix="-tiha-isimler.txt", delete=False, encoding="utf-8",
                ) as f:
                    f.write("\n".join(passed) + "\n")
                    names_file = f.name
                try:
                    run_cmd_stream(
                        ["python3", str(cli_script), names_file, "--kullanicilari-sil"],
                        timeout=600,
                    )
                finally:
                    try:
                        os.unlink(names_file)
                    except OSError:
                        pass
                removed = [u for u in created if not user_exists(u)]
            else:
                # Elle deluser
                for user in created:
                    if user_exists(user):
                        if run_cmd(["deluser", "--remove-home", user]).ok:
                            removed.append(user)

        # /etc/otp-secrets.json yedekten geri yükle
        backup = self.state_dir / OTP_SECRETS_FILE.name
        if backup.exists():
            try:
                restore_file(backup, OTP_SECRETS_FILE)
            except OSError as exc:
                log.warning("otp-secrets.json geri yüklenemedi: %s", exc)
        else:
            OTP_SECRETS_FILE.unlink(missing_ok=True)

        # Greeter cache kurulumunu kaldır
        greeter_cache_removed = False
        if data.get("greeter_cache_applied", False):
            # Bir kez daha çalıştır (cache'i temizlemek için)
            if GREETER_SCRIPT_PATH.exists():
                run_greeter_script_once()

            # Kurulumu kaldır
            if remove_greeter_setup():
                greeter_cache_removed = True

        summary_parts = []
        if removed:
            summary_parts.append(f"{len(removed)} kullanıcı silindi")
        summary_parts.append("önceki PIN anahtar dosyası geri yüklendi")

        if greeter_cache_removed:
            summary_parts.append("greeter cache kurulumu kaldırıldı")

        summary = "; ".join(summary_parts) + "."
        return ApplyResult(True, summary)

    # -----------------------------------------------------------------
    # Ek Kullanıcı Yönetimi Fonksiyonları
    # -----------------------------------------------------------------

    def can_remove_extra_users(self) -> bool:
        """Fazladan hesapları sil düğmesinin aktif olup olmayacağını belirler."""
        return bool(get_extra_users())

    def remove_extra_users_action(
        self, params: dict | None = None,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        """Varsayılan hesaplar dışındaki tüm fazladan kullanıcıları siler.

        Her kullanıcı için önce prosesleri sonlandırılır, sonra
        ``deluser --remove-home`` ile silinir. Hatalar log dosyasına
        değil, doğrudan sonuç ayrıntılarına yazılır.
        """
        extra_users = get_extra_users()

        if not extra_users:
            return ApplyResult(
                False,
                "Silinecek fazladan kullanıcı bulunamadı.",
                details="Sistemde sadece varsayılan kullanıcılar (etapadmin, ogrenci, ogretmen) mevcut."
            )

        if progress:
            progress(
                f"{len(extra_users)} fazladan hesap silinecek: "
                + ", ".join(extra_users)
            )

        success, removed, errors = reset_to_default_users(progress=progress)

        # Detay metni — hem başarılı hem başarısız kayıtları topla
        detail_parts: list[str] = []
        if removed:
            detail_parts.append("Silinen hesaplar:")
            detail_parts.extend(f"  ✓ {u}" for u in removed)
        if errors:
            detail_parts.append("")
            detail_parts.append("Silinemeyen hesaplar:")
            for u, err in errors.items():
                # deluser çıktısı çok satırlı olabilir; girintili göster
                err_indented = "\n      ".join(err.splitlines()) or "(boş çıktı)"
                detail_parts.append(f"  ✗ {u}\n      {err_indented}")

        if success:
            detail_parts.append("")
            detail_parts.append(
                "Sistem artık sadece varsayılan hesapları içeriyor:"
            )
            detail_parts.extend([
                "  • etapadmin (yönetici)",
                "  • ogrenci (ortak hesap)",
                "  • ogretmen (ortak hesap)",
            ])
            return ApplyResult(
                True,
                f"{len(removed)} fazladan kullanıcı silindi, "
                "sistem varsayılan durumuna getirildi.",
                details="\n".join(detail_parts),
            )
        else:
            failed_count = len(errors)
            return ApplyResult(
                False,
                f"{len(removed)} kullanıcı silindi, "
                f"{failed_count} kullanıcı silinemedi.",
                details="\n".join(detail_parts),
            )

