"""Modül 3 — Öğretmen PIN anahtarlarını toplu hazırla.

Ne yapar?
Girdiğiniz öğretmen ad-soyad listesinden her öğretmen için bir kullanıcı
hesabı oluşturur (zaten varsa geçer), her hesaba kriptografik olarak
güvenli bir PIN kodu (zaman tabanlı TOTP) BASE32 anahtarı atar ve
bu anahtarları Pardus ETAP'ın PAM modülünün okuduğu
/etc/otp-secrets.json dosyasına yazar. Sistemde önceden oluşturulmuş
yedek hesaplar (ogretmen1 …; eski kurulumlarda ogretmen01) otomatik
olarak PIN listesine eklenir; yedek hesap ÜRETME işlemi artık
"Kullanıcı parolaları" adımının işi.

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

Mevcut anahtarlar korunur
Adım, /etc/otp-secrets.json içinde anahtarı zaten bulunan hiçbir hesaba
dokunmaz — ne dahili yol ne de eta-otp-cli çağrılır (aracın ``olustur``
komutu mevcut kaydı sorgusuz üzerine yazıyor). Böylece adım aynı listeyle
yeniden uygulandığında yalnızca eksikler tamamlanır ve öğretmenlerin
telefonundaki anahtarlar geçerli kalır. Aynı koruma yedek hesapların
ortak grup-PIN'i için de geçerlidir.

Yazdırılabilir kâğıt
Adım sonunda üretilen HTML kâğıt, yalnız o turda üretilenleri değil
sistemdeki BÜTÜN anahtarları içerir; bu turda üretilenler "YENİ"
etiketiyle işaretlenir. Her kartta anahtarın QR kodu satır içi SVG
olarak gömülüdür — öğretmen elle anahtar girmek zorunda kalmaz ve kâğıt
çevrimdışı açılır (bkz. :mod:`tiha.core.qr`). otpauth:// URL'si kâğıtta
yazılı değildir: uzun, okunmaz ve yanlış kopyalanmaya açıktır; taşıyıcısı
QR kodudur. Kâğıt etapadmin oturumunda tarayıcıda açılır ve "Dosyaya
kaydet…" düğmesi onu HTML olarak kaydeder.

Dosya izinleri
Kâğıtlar ve otp-secrets.json yedeği düz metin PIN anahtarı taşır. Adımın
durum dizini (0750) ve içindeki dosyalar (0640) root'a ait, okuma hakkı
yalnız yönetici grubunda olacak şekilde kilitlenir; tahtadaki öğretmen ve
öğrenci hesapları bunları göremez. Geçmişte gevşek izinle yazılmış
dosyalar da adım her çalıştığında düzeltilir.

Değişen anahtar uyarısı
Tasarım gereği hiçbir mevcut anahtar değişmez. Yine de her turda önceki
değerlerle karşılaştırılır; bir anahtar değişmişse yönetici hem canlı
çıktıda hem sonuç kutusunda hem de modal bir uyarı diyaloğuyla
bilgilendirilir — değişen bir anahtar o öğretmenin telefonundaki kaydı
sessizce geçersiz kılar.

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
from ..core.qr import qr_svg, qrcode_available
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


def _admin_ids() -> tuple[int, int] | None:
    """Tahtanın yöneticisi olan kullanıcının (uid, gid) çifti.

    Anahtar taşıyan dosyaları bu kullanıcının grubuna açıyoruz: kâğıdı
    tarayıcıda açan yönetici okuyabilsin, tahtadaki öğretmen/öğrenci
    hesapları okuyamasın. Önce aktif grafik oturumun kullanıcısı, o
    yoksa TiHA'yı sudo/pkexec ile başlatan kullanıcı denenir. İkisi de
    bulunamazsa ``None`` döner ve dosyalar root'a kapalı kalır —
    sızdırmamak, açılabilir olmaktan önemli.
    """
    import pwd as _pwd

    from ..core.privilege import invoking_username
    from ..core.utils import _find_active_graphical_session

    candidates: list[str] = []
    try:
        env = _find_active_graphical_session()
        if env and env.get("USER"):
            candidates.append(env["USER"])
    except OSError as exc:
        log.debug("Grafik oturum bulunamadı: %s", exc)
    candidates.append(invoking_username())

    for name in candidates:
        if not name or name == "root":
            continue
        try:
            entry = _pwd.getpwnam(name)
        except KeyError:
            continue
        return entry.pw_uid, entry.pw_gid
    return None


# eta-otp-lock'un grup mekanizması: '@' ile başlayan kayıtlar bir
# kullanıcıya değil bir gruba aittir (ör. '@ogretmenler') ve gruba üye
# tüm hesaplarda geçerlidir. Tasarımı gereği karşılığında bir sistem
# hesabı YOKTUR; bu yüzden "hesabı kalmayan kayıt" taramalarında
# yetim sayılmamaları gerekir.
GROUP_SECRET_PREFIX = "@"


def is_group_secret(key: str) -> bool:
    """Kayıt bir grup anahtarı mı (kullanıcı anahtarı değil)?"""
    return key.startswith(GROUP_SECRET_PREFIX)


def orphan_secret_users(secrets, existing_users) -> list[str]:
    """Sistemde karşılığı kalmayan kullanıcı anahtarlarını döner.

    Grup anahtarları (``@...``) ve varsayılan hesaplar (etapadmin,
    ogretmen, ogrenci) hariç tutulur: ilki hiç hesap istemez, ikincisi
    işletim sistemiyle gelir ve silinmesi beklenmez.
    """
    return sorted(
        key for key in secrets
        if not is_group_secret(key)
        and key not in existing_users
        and key not in DEFAULT_SYSTEM_USERS
    )


# Silme/listeleme çıktılarında en başta görünmesi gereken kayıtlar.
# Bunlar tek bir öğretmene değil tahtanın tamamına ait ortak/yönetici
# anahtarlarıdır; bir listede gözden kaçmamaları gerekir.
PRIORITY_SECRET_USERS = ("@ogretmenler", "ogretmen", "etapadmin")


def order_secret_users(users) -> list[str]:
    """Ortak ve yönetici anahtarlarını başa alan sıralama.

    Önce ``@ogretmenler`` (grup ortak PIN'i), ``ogretmen`` (ortak hesap)
    ve ``etapadmin`` (sistem yöneticisi) — hangileri varsa bu sırayla.
    Kalanlar alfabetik. Yüzlerce öğretmenlik bir listede bu üçünün
    arada kaybolmaması için.
    """
    present = set(users)
    head = [u for u in PRIORITY_SECRET_USERS if u in present]
    rest = sorted(present - set(PRIORITY_SECRET_USERS))
    return head + rest


# Anahtar taşıyan dosyaların izinleri. Kâğıtlar ve otp-secrets.json
# yedeği düz metin PIN anahtarı taşır; dosya root'a, okuma hakkı da
# yalnız yönetici grubuna aittir. Dizin de listelenemez olmalı, aksi
# hâlde dosya adları (öğretmen adları) sızar.
SECRET_FILE_MODE = 0o640
SECRET_DIR_MODE = 0o750


def harden_secret_store(state_dir: Path) -> int:
    """Anahtar taşıyan durum dizinini ve içindeki dosyaları kilitler.

    Dizin 0750, dosyalar 0640 yapılır; sahip root, grup ise yönetici
    kullanıcının grubu olur. Böylece yönetici kâğıdı tarayıcıda açıp
    okuyabilir ama değiştiremez, diğer hesaplar hiç göremez.

    Geçmişte gevşek izinle (0644) yazılmış dosyalar da bu çağrıyla
    düzeltilir; düzeltilen dosya sayısı döner.
    """
    admin = _admin_ids()
    gid = admin[1] if admin is not None else 0

    try:
        os.chown(state_dir, 0, gid)
        state_dir.chmod(SECRET_DIR_MODE)
    except OSError as exc:
        log.warning("Durum dizini kilitlenemedi %s: %s", state_dir, exc)

    try:
        entries = list(state_dir.iterdir())
    except OSError as exc:
        log.warning("Durum dizini listelenemedi %s: %s", state_dir, exc)
        return 0

    fixed = 0
    for path in entries:
        if path.is_symlink() or not path.is_file():
            continue
        try:
            # "Başkalarına açık mı?" — düzeltilen dosyayı saymak için.
            if path.stat().st_mode & 0o007:
                fixed += 1
            os.chown(path, 0, gid)
            path.chmod(SECRET_FILE_MODE)
        except OSError as exc:
            log.warning("Dosya izni düzeltilemedi %s: %s", path, exc)
    return fixed


def _paper_display_name(user: str, display_of: dict[str, str]) -> str:
    """PIN kâğıdının başlığında görünecek ad.

    Kâğıt artık sistemdeki bütün anahtarları bastığı için, bu turda
    girilmemiş (daha önce oluşturulmuş) hesapların adı ``display_of``
    haritasında bulunmaz. Onlar için passwd GECOS alanındaki ad/soyad
    kullanılır; o da yoksa kullanıcı adının kendisi yazılır.
    """
    if user in display_of:
        return display_of[user]
    if user.startswith("@"):
        return f"{user[1:]} grubu — ORTAK PIN"

    import pwd as _pwd
    try:
        gecos = _pwd.getpwnam(user).pw_gecos.split(",")[0].strip()
    except KeyError:
        # Anahtarı var ama sistem hesabı yok (hesap sonradan silinmiş).
        return f"{user} (sistemde hesap yok)"
    return gecos or user


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


# Yedek hesap adlarının deseni. İki normalizasyon yolu farklı ad
# üretiyor: eta-otp-cli "ogretmen01", dahili yol "ogretmen.01".
RESERVE_USER_RE = re.compile(r"^ogretmen\.?(\d+)$")


def count_reserve_accounts() -> int:
    """Sistemde hazır duran yedek hesapların "kaçıncıya kadar" gittiği.

    ogretmen01 … ogretmen10 varsa 10 döner. En büyük indeksi
    kullanıyoruz (adet değil): yedek hesaplar toplu açıldığı için
    indeks aralığı kesintisizdir ve "bu tahtada 10 yedek hesap
    hazırlanmış" bilgisini doğru yansıtan sayı budur. Böylece adım
    yeniden uygulandığında kutu dolu gelir ve yönetici farkında
    olmadan yeni hesap açmaz.
    """
    import pwd as _pwd

    highest = 0
    try:
        entries = _pwd.getpwall()
    except OSError as exc:
        log.warning("Kullanıcı listesi okunamadı: %s", exc)
        return 0
    for entry in entries:
        if not 1000 <= entry.pw_uid < 60000:
            continue
        match = RESERVE_USER_RE.match(entry.pw_name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest


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
        "Listedeki ve yedek hesaplar için PIN anahtarları üretilir. "
        "Anahtarı zaten olan hesaplara dokunulmaz; yalnızca eksikler "
        "tamamlanır. Çıktı, sistemdeki tüm anahtarları QR kodlarıyla "
        "birlikte içeren yazdırılabilir bir HTML kâğıdıdır."
    )
    # "Dosyaya kaydet…" yazdırılabilir kâğıdı kaydeder; biçim HTML.
    # apply() gerçek dosya adını (zaman damgalı) result üzerinden verir.
    save_filename = "ogretmen-pin-kagitlari.html"
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
        # Grup anahtarları kendi başlığı altında; yetim listesine
        # karışmaları "hesap silinmiş" diye yanlış alarm veriyordu.
        group_secrets = sorted(u for u in existing if is_group_secret(u))
        orphan_secrets = orphan_secret_users(existing, set(personal_users))
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
        lines.append(
            "QR kodları           : "
            + ("kâğıda gömülecek" if qrcode_available()
               else "ATLANACAK (python3-qrcode kurulu değil)")
        )
        if user_count >= MIN_USERS_FOR_CACHE:
            greeter_state = (
                "kurulu"
                if GREETER_SERVICE_PATH.exists()
                else "kurulacak"
            )
            lines.append(f"Greeter cache        : {greeter_state}")
        lines.append("")

        lines.append("Not: Bu adım yalnız OTP anahtarları oluşturur; "
                     "sistem kullanıcı hesaplarını oluşturmaz. Yedek "
                     "hesaplar (ogretmen1, ogretmen2 …) 'Kullanıcı "
                     "parolaları' adımında oluşturulur; bu adım sistemde "
                     "bulduğu yedek hesapları otomatik olarak PIN "
                     "listesine ekler.")
        lines.append("")
        lines.append("Not: Anahtarı zaten olan hesaplara dokunulmaz — "
                     "yalnızca eksikler tamamlanır. Öğretmenlerin "
                     "telefonundaki anahtarlar geçerli kalır, adımı "
                     "güvenle yeniden uygulayabilirsiniz.")
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
        if group_secrets:
            lines.append(
                "Grup PIN anahtarları (kullanıcı hesabı gerektirmez; "
                f"gruba üye tüm hesaplarda geçerli): {', '.join(group_secrets)}"
            )
            lines.append("")
        if orphan_secrets:
            lines.append(
                f"Sistemde hesabı kalmayan PIN kayıtları ({len(orphan_secrets)} "
                f"adet — hesap silinmiş olabilir): {', '.join(orphan_secrets)}"
            )
            lines.append(
                "  Bu kayıtlar kullanılamaz; imaja gitmemeleri için "
                "\"Fazladan Hesapları Sil\" düğmesi onları da temizler."
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
        include_etapadmin: bool = str(
            params.get("include_etapadmin", "False")
        ).lower() in ("true", "1", "yes", "on")
        include_ogretmen: bool = str(
            params.get("include_ogretmen", "False")
        ).lower() in ("true", "1", "yes", "on")
        make_group_pin: bool = str(
            params.get("make_group_pin", "False")
        ).lower() in ("true", "1", "yes", "on")
        # Öğretmen hesaplarını ogretmenler grubuna ekle. Varsayılan açık:
        # grup üyeliği olmadan '@ogretmenler' ortak PIN'i işe yaramaz ve
        # EBA QR ile açılan hesaplar gruba hiç girmez.
        # Eski presetlerde bu anahtar "auto_group_new_teachers" adıyla
        # geçiyordu; okunmaya devam ediyor ki sahadaki preset dosyaları
        # sessizce başka bir davranışa kaymasın.
        add_teachers_to_group: bool = str(
            params.get(
                "add_teachers_to_group",
                params.get("auto_group_new_teachers", "True"),
            )
        ).lower() in ("true", "1", "yes", "on")

        # Grup PIN'i pam_otp tarafından yalnızca kullanıcı '/etc/group'
        # üyesiyse doğrulanır. Kullanıcı make_group_pin işaretleyip
        # add_teachers_to_group'u işaretsiz bıraktıysa PIN üretilir ama
        # kimse gruba girmediği için giriş yapılamaz. Bu bir UX tuzağı;
        # burada sessizce iki bayrağı birlikte etkinleştiriyoruz —
        # kullanıcı grup PIN istediyse üyelik zaten kaçınılmaz ön koşul.
        if make_group_pin and not add_teachers_to_group:
            add_teachers_to_group = True
            if progress:
                progress(
                    "Not: Grup PIN'i seçili — öğretmen hesaplarını "
                    f"'{OGRETMENLER_GROUP}' grubuna ekleme otomatik "
                    "etkinleştirildi (PAM grup PIN'ini yalnız gruba "
                    "üye kullanıcıya kabul ediyor)."
                )

        teacher_names = [line.strip() for line in raw_list.splitlines() if line.strip()]

        # Yedek hesaplar (ogretmen1 … ogretmenN, eski kurulumlarda
        # ogretmen01 / ogretmen.N / ogretmen.01 biçimi de olabilir) m01
        # "Kullanıcı parolaları" adımında oluşturuluyor. Burada yalnızca
        # sistemde bulduklarımızı yakalıyoruz: her indeks için hangi
        # varyant varsa onu (normalize edilmiş kullanıcı adı olarak
        # birebir) PIN üretim listesine ekliyoruz. Aynı liste grup
        # üyeliği hedefleri için de sonra kullanılıyor.
        reserve_existing = count_reserve_accounts()
        reserve_usernames: list[str] = []
        for i in range(1, reserve_existing + 1):
            for candidate in (
                f"ogretmen{i}",
                f"ogretmen{i:02d}",
                f"ogretmen.{i}",
                f"ogretmen.{i:02d}",
            ):
                if user_exists(candidate):
                    reserve_usernames.append(candidate)
                    teacher_names.append(candidate)
                    break

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
                "Liste boş — öğretmen eklemediniz ve sistemde yedek hesap yok.",
                details=(
                    "Lütfen en az bir isim girin ya da 'Kullanıcı "
                    "parolaları' adımında 'Yedek hesap sayısı' kutusuna "
                    "sıfırdan büyük bir değer yazıp o adımı uygulayın."
                ),
            )

        state = self.ensure_state_dir()
        # Anahtar taşıyan dosyalar yalnız yöneticiye açık olmalı. Bu
        # çağrı hem dizini kilitler hem daha önce gevşek izinle yazılmış
        # eski kâğıtları düzeltir.
        harden_secret_store(state)
        backup_file(OTP_SECRETS_FILE, state)
        # Yalnız anahtar adları değil DEĞERLERİ de saklanır: sonda
        # hangi hesabın anahtarının değiştiğini karşılaştırabilmek için.
        before_map = load_secrets()
        before_secrets = set(before_map)

        # Hâlihazırda anahtarı olan hesaplara dokunulmaz: öğretmenin
        # telefonundaki anahtar geçerli kalsın. Adım aynı listeyle
        # yeniden uygulandığında yalnızca eksikler tamamlanır.
        keep_users = set(before_secrets)

        cli_script = _eta_otp_cli_bulk_script()
        if cli_script:
            success = self._apply_with_tool(
                cli_script, teacher_names, progress, keep_users
            )
        else:
            success = self._apply_with_internal(
                teacher_names, progress, keep_users
            )

        if not success:
            return ApplyResult(
                False,
                "PIN anahtarları üretilemedi.",
                details="Ayrıntı için /var/log/tiha/tiha.log dosyasına bakın.",
            )

        # reserve_usernames yukarıda apply girişinde hesaplandı; grup
        # üyeliği ve auto-group izleyici aşağıda o listeyi kullanır.

        # Her hesap için passwd GECOS (ad/soyad) alanını yaz.
        self._apply_gecos(teacher_names, cli_used=bool(cli_script), progress=progress)

        # Yeni eklenenleri ve anahtarlarını oku
        after_secrets = load_secrets()
        new_users = [u for u in after_secrets if u not in before_secrets]

        # Listede olup anahtarı zaten bulunan hesaplar — dokunulmadı.
        _normalize = _eta_otp_cli_normalize if cli_script else normalize_username
        requested_users = {u for u in (_normalize(n) for n in teacher_names) if u}
        preserved_users = sorted(requested_users & keep_users)

        # ===== ogretmenler grubu =====================================
        # Grup üyeliği artık yedek hesap sayısına bağlı değil: bu adımın
        # yönettiği bütün öğretmen hesapları (listeden gelenler + yedek
        # hesaplar) gruba alınır ve gelecekte EBA QR ile açılacaklar için
        # izleyici servis kurulur.
        group_key = f"@{OGRETMENLER_GROUP}"
        group_secret_is_new = False
        grouped_users: list[str] = []
        auto_group_service_installed = False

        if add_teachers_to_group:
            if not ensure_ogretmenler_group():
                if progress:
                    progress(f"\n'{OGRETMENLER_GROUP}' grubu olusturulamadi; "
                             "grup uyeligi atlandi.")
            else:
                targets = sorted(requested_users | set(reserve_usernames))
                if progress:
                    progress(f"\nOgretmen hesaplari '{OGRETMENLER_GROUP}' "
                             "grubuna ekleniyor...")
                for u in targets:
                    # etapadmin bir öğretmen hesabı değil; ortak PIN'in
                    # yönetici hesabına da geçmesi istenmez.
                    if u == "etapadmin" or not user_exists(u):
                        continue
                    run_cmd(["usermod", "-a", "-G", OGRETMENLER_GROUP, u], check=False)
                    grouped_users.append(u)
                    if progress:
                        progress(f"  + {u}")
                if not grouped_users and progress:
                    progress("  (gruba eklenecek mevcut hesap yok)")
                # Sonradan EBA QR ile açılacak hesaplar için izleyici servis
                if install_auto_group_service():
                    auto_group_service_installed = True
                    if progress:
                        progress("\nOtomatik grup ekleme servisi kuruldu — "
                                 "EBA QR ile sonradan acilan ogretmen "
                                 "hesaplari da gruba dahil edilecek.")

        if make_group_pin:
            if not ensure_ogretmenler_group():
                if progress:
                    progress(f"\n'{OGRETMENLER_GROUP}' grubu olusturulamadi; "
                             "grup-PIN akisi iptal edildi.")
            elif group_key in after_secrets:
                # Mevcut grup-PIN'i yenilemek telefonlardaki anahtarı
                # geçersiz kılardı; koruyoruz.
                if progress:
                    progress(f"\nMevcut ortak grup-PIN korundu: {group_key} "
                             "— telefonlardaki anahtar geçerli kalir.")
            else:
                after_secrets[group_key] = pyotp.random_base32()
                save_secrets(after_secrets)
                group_secret_is_new = True
                if progress:
                    progress(f"\nOrtak grup-PIN uretildi: {group_key}")


        # ===== Madde: değişen anahtarlar =============================
        # Tasarım gereği hiçbir mevcut anahtar değişmemeli. Yine de her
        # turda karşılaştırıp değişen olursa yöneticiyi MUTLAKA uyarırız:
        # değişen bir anahtar, o öğretmenin telefonundaki kaydı sessizce
        # geçersiz kılar ve bunu fark etmenin başka yolu yoktur.
        changed_users = sorted(
            user for user, secret in after_secrets.items()
            if user in before_map and before_map[user] != secret
        )
        if changed_users and progress:
            progress("\n" + "!" * 60)
            progress(f"UYARI: {len(changed_users)} hesabin PIN anahtari DEGISTI.")
            for user in changed_users:
                progress(f"  ! {user}")
            progress("Bu hesaplarin telefonundaki eski kayit artik "
                     "calismaz; yeni kagidi teslim edin.")
            progress("!" * 60)

        if not new_users and not group_secret_is_new and not preserved_users:
            return ApplyResult(
                False,
                "Hiç PIN anahtarı üretilemedi.",
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
        if preserved_users:
            report_lines.append(
                f"  {len(preserved_users)} hesabın mevcut anahtarı korundu "
                "(yeniden üretilmedi)."
            )
        if changed_users:
            report_lines.append(
                f"  DİKKAT: {len(changed_users)} hesabın anahtarı DEĞİŞTİ — "
                "eski kayıtları çalışmaz."
            )
        report_lines.append(f"  Toplam PIN kaydı: {len(after_secrets)}")
        report_lines.append(f"  Dosya: {OTP_SECRETS_FILE}")
        report_lines.append(f"  Üretici: Issuer = \"{OTP_ISSUER}\", 6 hane, 30 sn periyot.")
        if cli_script:
            report_lines.append("  Araç:   enseitankado/eta-otp-cli  →  toplu-kullanici-olustur.py")
        report_lines.append("─" * 76)
        for idx, user in enumerate(sorted(new_users), 1):
            secret = after_secrets[user]
            display = display_of.get(user, "(yedek hesap)")
            report_lines.append("")
            report_lines.append(f"[{idx:02d}]  {display}")
            report_lines.append(f"     Kullanıcı adı : {user}")
            report_lines.append(f"     PIN anahtarı  : {secret}")
        if preserved_users:
            report_lines.append("")
            report_lines.append("─" * 76)
            report_lines.append(
                "  Mevcut anahtarı korunan hesaplar (dokunulmadı — "
                "telefondaki anahtar geçerli):"
            )
            for user in preserved_users:
                report_lines.append(f"     · {user}")
        report_lines.append("")
        report_lines.append("─" * 76)
        report_lines.append(
            "Kullanım: öğretmenler bu anahtarı Google Authenticator vb.\n"
            "uygulamaya elle girebilir; daha kolayı, yazdırılabilir\n"
            "kâğıttaki QR kodunu uygulamayla taratmaktır. Anahtarları\n"
            "yalnızca özelden (şifreli mesaj, gizli dağıtım listesi)\n"
            "teslim edin."
        )
        copyable = "\n".join(report_lines)

        # Yazdırılabilir HTML kâğıt — sistemdeki BÜTÜN anahtarlar için
        # birer kart. Yalnız bu turda üretilenleri göstermek, adım
        # yeniden uygulandığında ya da liste parça parça girildiğinde
        # eksik bir çıktı veriyordu; öğretmene teslim edilecek kâğıt
        # setinin tamamı tek dosyada olmalı. Bu turda üretilenler "YENİ"
        # etiketiyle işaretlenir.
        all_users = sorted(after_secrets)
        html_path, html_text = self._write_printable_paper(
            all_users=all_users,
            new_users=set(new_users),
            secrets=after_secrets,
            display_of=display_of,
            state_dir=state,
        )
        if html_path and progress:
            progress(f"🖨️ Yazdırılabilir kâğıt ({len(all_users)} anahtar): {html_path}")

        # details, UI'da aşağıdaki metin raporuyla TEK alanda birleşerek
        # gösteriliyor. Bu yüzden burada sayıları yinelemiyoruz —
        # üretilen/korunan/değişen sayıları hem tek satırlık özette hem
        # raporun başlığında zaten var. Buraya yalnız raporda olmayan
        # bilgi yazılır: kâğıdın nerede olduğu ve ne yapılacağı.
        details_lines: list[str] = []
        if html_path:
            details_lines.append(
                f"🖨️ Yazdırılabilir öğretmen kâğıdı — "
                f"sistemdeki {len(all_users)} anahtarın tamamı:"
            )
            details_lines.append(f"  {html_path}")
            details_lines.append(
                "  Tarayıcıda açıp Ctrl+P ile yazdırın veya PDF kaydedin."
            )
            details_lines.append(
                "  (Etapadmin oturumunda otomatik açılmayı denedik.)"
            )
            details_lines.append(
                "  'Dosyaya kaydet…' bu kâğıdı HTML olarak kaydeder."
            )
        details = "\n".join(details_lines)

        # Greeter cache bilgisini ekle
        if new_users:
            summary_parts = [
                f"{len(new_users)} PIN anahtarı üretildi ve "
                f"{OTP_SECRETS_FILE} dosyasına yazıldı."
            ]
        else:
            summary_parts = [
                "Yeni anahtar gerekmedi; listedeki hesapların anahtarı "
                "zaten vardı ve korundu."
            ]
        if preserved_users and new_users:
            summary_parts.append(
                f"{len(preserved_users)} hesabın mevcut anahtarına dokunulmadı."
            )
        if greeter_cache_applied:
            summary_parts.append("Greeter cache güncellendi ve otomatik çalıştırma ayarlandı.")
        elif total_users >= MIN_USERS_FOR_CACHE:
            summary_parts.append("Greeter cache kurulumu tamamlanamadı.")
        if grouped_users:
            summary_parts.append(
                f"{len(grouped_users)} hesap {OGRETMENLER_GROUP} grubuna eklendi."
            )
        if auto_group_service_installed:
            summary_parts.append("Yeni öğretmen hesabı → ogretmenler grubu servis kuruldu.")

        return ApplyResult(
            success=True,
            summary=" ".join(summary_parts),
            details=details,
            copyable=copyable,
            # "Dosyaya kaydet…" ekrandaki metin raporunu değil,
            # yazdırılabilir HTML kâğıdı kaydeder: teslim edilecek çıktı
            # bu, ve tarayıcıda/yazıcıda doğru görünen biçim de bu.
            save_payload=html_text,
            save_filename=(html_path.name if html_path else None),
            warning=(
                "Bu adımda {n} hesabın PIN anahtarı DEĞİŞTİ:\n\n{liste}\n\n"
                "Bu hesapların telefonlarındaki eski kayıt artık "
                "çalışmaz. Yeni PIN kâğıdını bu öğretmenlere mutlaka "
                "yeniden teslim edin.".format(
                    n=len(changed_users),
                    liste=", ".join(changed_users),
                )
                if changed_users else None
            ),
            data={
                "passed_names": teacher_names,
                "created_users": sorted(new_users),
                "preserved_users": preserved_users,
                "changed_users": changed_users,
                "grouped_users": grouped_users,
                "used_tool": bool(cli_script),
                "greeter_cache_applied": greeter_cache_applied,
                "total_users": total_users,
                "auto_group_service_installed": auto_group_service_installed,
            },
        )

    def _write_printable_paper(
        self,
        *,
        all_users: list[str],
        new_users: set[str],
        secrets: dict[str, str],
        display_of: dict[str, str],
        state_dir: Path,
    ) -> tuple[Path | None, str | None]:
        """Sistemdeki her PIN anahtarı için yazdırılabilir bir kart içeren
        HTML dosyası oluşturur.

        ``all_users`` kâğıda basılacak anahtarların tamamıdır; bu turda
        üretilenler (``new_users``) "YENİ" etiketiyle işaretlenir, böylece
        yönetici hangi kâğıtları yeni teslim etmesi gerektiğini görür.

        Dosya yolunu ve HTML içeriğini döner — içerik "Dosyaya kaydet…"
        butonunda yeniden kullanılır. Best-effort xdg-open ile aktif
        kullanıcı oturumunda tarayıcıda açılır.
        """
        from datetime import datetime as _dt
        from html import escape as _esc
        import subprocess as _sp

        ts = _dt.now().strftime("%Y%m%d-%H%M%S")
        out = state_dir / f"ogretmen-pin-kagitlari-{ts}.html"

        cards: list[str] = []
        for user in all_users:
            secret = secrets.get(user, "")
            display = _paper_display_name(user, display_of)
            is_new = user in new_users
            grouped = " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
            # QR, otpauth:// URL'sini taşır — öğretmen elle anahtar
            # girmek zorunda kalmaz. URL'nin kendisi kâğıtta yazılı
            # DEĞİL: uzun, okunmaz ve yanlış kopyalanmaya açık.
            svg = qr_svg(otpauth_url(user, secret))
            if svg:
                qr_block = (
                    '  <aside class="qr">\n'
                    f'    {svg}\n'
                    '    <div class="qr-label">Uygulamayla taratın</div>\n'
                    '  </aside>'
                )
            else:
                qr_block = ""
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
      <li>Uygulamada <em>"+ Anahtar ekle"</em> &gt; <em>"QR kodu tara"</em>'yı
          seçip yandaki kodu taratın. QR okunamazsa <em>"Anahtarı manuel
          gir"</em> ile yukarıdaki anahtarı yazın.</li>
      <li>Hesap adı olarak <em>istediğiniz</em> bir etiket yazın
          (örn. <code>Sınıf-PIN</code>).</li>
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
      <li>Uygulamada <em>"+ Anahtar ekle"</em> &gt; <em>"QR kodu tara"</em>'yı
          seçip yandaki kodu taratın. QR okunamazsa <em>"Anahtarı manuel
          gir"</em> ile yukarıdaki anahtarı ve hesap adı olarak
          <code>{_esc(user)}</code> yazın.</li>
      <li>Tür: <em>Zaman tabanlı</em> (varsayılan).</li>
      <li>Kaydedin. Artık her 30 saniyede yeni bir 6 haneli PIN üretilir;
          tahta giriş ekranında bu PIN'i girersiniz.</li>
    </ol>'''
            badge = '<span class="badge">YENİ</span>' if is_new else ""
            cards.append(f'''
<article class="card{' group' if is_group else ''}{' fresh' if is_new else ''}">
  <div class="body">
    <header>
      <h2>{_esc(display)}{badge}</h2>
      {user_line}
    </header>
    <section class="secret">
      <div class="label">PIN anahtarı (QR okunmazsa elle girin):</div>
      <div class="key">{_esc(grouped)}</div>
    </section>
    <section class="instructions">
{instructions}
    </section>
  </div>
{qr_block}
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
  .card.fresh {{ border-color: #2e7d32; background: #f3f9f3; }}
  .card h2 {{ margin: 0 0 4px 0; font-size: 14pt; }}
  .badge {{
    display: inline-block;
    margin-left: 8px;
    padding: 1px 7px;
    border-radius: 9px;
    background: #2e7d32;
    color: #fff;
    font-size: 8pt;
    font-weight: 600;
    vertical-align: middle;
    letter-spacing: 0.5px;
  }}
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
  .card {{ display: flex; gap: 16px; align-items: flex-start; }}
  .card > .body {{ flex: 1; min-width: 0; }}
  .qr {{ flex: 0 0 auto; text-align: center; }}
  .qr svg {{ display: block; border: 1px solid #ddd; border-radius: 4px; }}
  .qr .qr-label {{ font-size: 8pt; color: #555; margin-top: 4px; }}
  @media print {{
    body {{ margin: 8mm; }}
    .card {{ break-inside: avoid; }}
  }}
</style>
</head><body>
<h1>Öğretmen PIN Kâğıtları</h1>
<div class="meta">
  Oluşturulma: {ts.replace("-", " ")} ·
  Toplam: {len(all_users)} kâğıt ({len(new_users)} tanesi bu turda üretildi,
  <span class="badge">YENİ</span> etiketli) ·
  TiHA tarafından üretildi · Issuer: <em>{_esc(OTP_ISSUER)}</em>
</div>
{"".join(cards)}
</body></html>
'''
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html, encoding="utf-8")
            # Kâğıt, sistemdeki BÜTÜN PIN anahtarlarını düz metin olarak
            # taşıyor — otp-secrets.json kadar gizli. Dizinin tamamını
            # tek yerden kilitliyoruz (bkz. harden_secret_store).
            harden_secret_store(out.parent)
        except OSError as exc:
            log.warning("Yazdırılabilir kâğıt oluşturulamadı: %s", exc)
            return None, html

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

        return out, html

    def _apply_with_tool(
        self,
        script: Path,
        names: list[str],
        progress: ProgressCallback | None,
        keep_users: set[str],
    ) -> bool:
        """otp-cli.py aracını kullanarak sadece PIN anahtarları üret (kullanıcı oluşturmadan).

        ``keep_users`` içindeki hesaplar için araç hiç çağrılmaz: aracın
        ``olustur`` komutu mevcut anahtarı sorgusuz üzerine yazar
        (``ayarlar[kullanici] = yeni_anahtar``), bu da öğretmenin
        telefonundaki anahtarı geçersiz kılardı.
        """
        if progress:
            progress("enseitankado/eta-otp-cli aracı çalıştırılıyor (sadece OTP anahtarları)…")

        # otp-cli.py dosyası aynı dizinde olmalı
        otp_cli_script = script.parent / "otp-cli.py"
        if not otp_cli_script.exists():
            log.error("otp-cli.py bulunamadı: %s", otp_cli_script)
            return False

        # Her kullanıcı için olustur komutunu çalıştır (sadece OTP anahtarı)
        success_count = 0
        attempted = 0
        kept_count = 0
        total_count = len(names)

        for idx, full_name in enumerate(names, 1):
            username = _eta_otp_cli_normalize(full_name)
            if not username:
                continue

            if username in keep_users:
                kept_count += 1
                if progress:
                    progress(f"  {idx}/{total_count}: {full_name} → {username} "
                             "— mevcut anahtar korundu, dokunulmadı")
                continue

            attempted += 1
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
            progress(f"Tamamlandı: {success_count}/{attempted} yeni OTP anahtarı "
                     f"üretildi, {kept_count} mevcut anahtar korundu")

        # Üretilecek yeni anahtar yoksa bu bir hata değil: adımın aynı
        # listeyle yeniden uygulanması olağan bir durumdur.
        if attempted == 0:
            return True
        return success_count > 0

    def _apply_with_internal(
        self,
        names: list[str],
        progress: ProgressCallback | None,
        keep_users: set[str],
    ) -> bool:
        """Aracın olmadığı durumda TiHA'nın kendi pyotp yolu.

        ``keep_users`` içindeki hesapların anahtarına dokunulmaz; sistem
        hesabı yine garantilenir (``create_user`` var olanı bozmaz, en
        çok ad/soyad alanını günceller).
        """
        if progress:
            progress("Dahili pyotp yolu kullanılıyor.")

        secrets = load_secrets()
        for name in names:
            user = normalize_username(name)
            if not user:
                continue
            create_user(user, full_name=name)
            if user in keep_users:
                if progress:
                    progress(f"  • {user} ({name}): mevcut PIN anahtarı korundu")
                continue
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

    def suggested_reserve_count(self) -> int:
        """Yedek hesap kutusunun açılışta görüneceği değer.

        Sistemde ogretmen01 … ogretmen10 duruyorsa kutu 10 gelir; adım
        yeniden uygulandığında yönetici farkında olmadan 11. hesabı
        açmaz, mevcut hesaplar da (anahtarlarıyla birlikte) korunur.
        """
        return count_reserve_accounts()

    def can_purge_secrets(self) -> bool:
        """'Tüm PIN anahtarlarını sil' düğmesi görünsün mü?"""
        return bool(load_secrets())

    def label_purge_all_secrets(self) -> str:
        """'Tüm PIN Anahtarlarını Sil' düğmesinin dinamik etiketi.
        Adımın her aksiyonundan sonra yeniden hesaplanır."""
        n = len(load_secrets())
        return f"Tüm PIN Anahtarlarını Sil ({n} anahtar)"

    def label_remove_extra_users(self) -> str:
        """'Fazladan Hesapları Sil' düğmesinin dinamik etiketi.
        Silinecek hesap sayısı ve karşılığında düşecek yetim PIN
        anahtar sayısı gösterilir."""
        extras = get_extra_users()
        secrets = load_secrets()
        # extras arasında PIN kaydı olan hesap sayısı — silme onunla
        # birlikte otp-secrets.json'dan da bu kayıtları düşürür.
        matching_keys = sum(1 for u in extras if u in secrets)
        return (
            f"Fazladan Hesapları Sil "
            f"({len(extras)} hesap, {matching_keys} anahtar)"
        )

    def purge_all_secrets_action(
        self, params: dict | None = None,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        """Sistemdeki bütün OTP anahtarlarını siler.

        Kaynak imaj hazırlanırken temiz bir sayfadan başlamak (ör. test
        amaçlı üretilmiş anahtarları imaja taşımamak) için gerekir.
        Kullanıcı onayı UI tarafında alınır — bu geri alınamaz bir
        işlemdir ve anahtarların dağıtılmış kopyaları geçersiz olur.

        Silme öncesi ``otp-secrets.json`` yedeklenir; kâğıtlar da eski
        anahtarları taşıdığı için birlikte silinir.
        """
        secrets = load_secrets()
        if not secrets:
            return ApplyResult(
                False,
                "Silinecek PIN anahtarı yok.",
                details=f"{OTP_SECRETS_FILE} boş ya da mevcut değil.",
            )

        users = order_secret_users(secrets)
        state = self.ensure_state_dir()
        harden_secret_store(state)
        backup = backup_file(OTP_SECRETS_FILE, state)

        if progress:
            progress(f"{len(users)} PIN anahtari siliniyor...")
            for user in users:
                progress(f"  - {user}")

        save_secrets({})

        # Kâğıtlar silinen anahtarları düz metin taşıyor; onları geride
        # bırakmak silme işlemini anlamsız kılar.
        removed_papers = 0
        for paper in state.glob("ogretmen-pin-kagitlari-*.html"):
            try:
                paper.unlink()
                removed_papers += 1
            except OSError as exc:
                log.warning("Kâğıt silinemedi %s: %s", paper, exc)

        harden_secret_store(state)

        details = [
            f"{len(users)} anahtar silindi: {', '.join(users)}",
            f"{OTP_SECRETS_FILE} artık boş.",
        ]
        if removed_papers:
            details.append(
                f"{removed_papers} yazdırılabilir kâğıt da silindi "
                "(silinen anahtarları içeriyordu)."
            )
        if backup is not None:
            details.append(f"Silme öncesi yedek: {backup}")
        details.append(
            "Dağıtılmış anahtarlar artık geçersiz. Öğretmenlerin PIN ile "
            "giriş yapabilmesi için adımı yeniden uygulayıp yeni kâğıtları "
            "teslim etmeniz gerekir."
        )

        return ApplyResult(
            True,
            f"{len(users)} PIN anahtarı silindi; dosya boşaltıldı.",
            details="\n".join(details),
            warning=(
                f"{len(users)} PIN anahtarı silindi. Bu anahtarların "
                "telefonlardaki kayıtları artık çalışmaz. Öğretmenlerin "
                "PIN ile giriş yapabilmesi için adımı yeniden uygulayıp "
                "yeni kâğıtları teslim etmelisiniz."
            ),
            data={"purged_users": users},
        )

    def _purge_orphan_secrets(
        self, progress: ProgressCallback | None = None,
    ) -> list[str]:
        """Sistemde karşılığı kalmayan PIN kayıtlarını dosyadan siler.

        Grup anahtarları (``@...``) ve varsayılan hesaplar korunur.
        Silme öncesi ``otp-secrets.json`` yedeklenir. Silinen kayıt
        adlarını (önemli olanlar başta) döner.
        """
        import pwd as _pwd

        secrets = load_secrets()
        if not secrets:
            return []

        try:
            existing_users = {
                entry.pw_name for entry in _pwd.getpwall()
            }
        except OSError as exc:
            log.warning("Kullanıcı listesi okunamadı, yetim taraması "
                        "atlandı: %s", exc)
            return []

        orphans = orphan_secret_users(secrets, existing_users)
        if not orphans:
            return []

        state = self.ensure_state_dir()
        harden_secret_store(state)
        backup_file(OTP_SECRETS_FILE, state)

        if progress:
            progress(f"\nKarsiligi kalmayan {len(orphans)} PIN kaydi "
                     "temizleniyor...")
        for user in orphans:
            del secrets[user]
            if progress:
                progress(f"  - {user}")

        save_secrets(secrets)
        harden_secret_store(state)
        log.info("%d yetim PIN kaydı silindi", len(orphans))
        return order_secret_users(orphans)

    def can_remove_extra_users(self) -> bool:
        """Fazladan hesapları sil düğmesi görünsün mü?

        Fazladan hesap YOKKEN de karşılığı kalmayan PIN kayıtları
        kalmış olabilir (hesaplar daha önce silinmiş, anahtarları
        dosyada durmuş). Düğme o durumda da görünmeli, yoksa yetim
        kayıtları temizlemenin yolu kalmıyor.
        """
        if get_extra_users():
            return True
        return bool(self._orphan_secret_names())

    def _orphan_secret_names(self) -> list[str]:
        """Sistemde karşılığı kalmayan PIN kayıtlarının adları."""
        import pwd as _pwd

        secrets = load_secrets()
        if not secrets:
            return []
        try:
            existing_users = {entry.pw_name for entry in _pwd.getpwall()}
        except OSError as exc:
            log.warning("Kullanıcı listesi okunamadı: %s", exc)
            return []
        return orphan_secret_users(secrets, existing_users)

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
        orphan_names = self._orphan_secret_names()

        if not extra_users and not orphan_names:
            return ApplyResult(
                False,
                "Silinecek fazladan kullanıcı ya da yetim PIN kaydı bulunamadı.",
                details="Sistemde sadece varsayılan kullanıcılar (etapadmin, "
                        "ogrenci, ogretmen) mevcut ve tüm PIN kayıtlarının "
                        "karşılığı var."
            )

        # Silinecek hesap yok ama yetim kayıt var: yalnız temizlik yap.
        if not extra_users:
            purged = self._purge_orphan_secrets(progress=progress)
            return ApplyResult(
                True,
                f"Fazladan hesap yoktu; karşılığı kalmayan {len(purged)} "
                "PIN kaydı temizlendi.",
                details="Silinen kayıtlar (imaja ölü sır gitmesin):\n"
                        + "\n".join(f"  · {u}" for u in purged),
                data={"removed_users": [], "purged_secrets": purged},
            )

        if progress:
            progress(
                f"{len(extra_users)} fazladan hesap silinecek: "
                + ", ".join(extra_users)
            )

        success, removed, errors = reset_to_default_users(progress=progress)

        # Hesabı gitmiş kayıtların anahtarı dosyada kalırsa imaja ölü
        # sır olarak gider: kullanılamaz ama okunabilir. Hesapları
        # sildikten sonra karşılığı kalmayan kayıtları da temizliyoruz.
        # Grup anahtarları ve varsayılan hesaplar korunur.
        purged_secrets = self._purge_orphan_secrets(progress=progress)

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

        if purged_secrets:
            detail_parts.append("")
            detail_parts.append(
                f"Karşılığı kalmayan {len(purged_secrets)} PIN kaydı da "
                "silindi (imaja ölü sır gitmesin):"
            )
            detail_parts.extend(f"  · {u}" for u in purged_secrets)

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
            summary = (f"{len(removed)} fazladan kullanıcı silindi, "
                       "sistem varsayılan durumuna getirildi.")
            if purged_secrets:
                summary += f" {len(purged_secrets)} yetim PIN kaydı temizlendi."
            return ApplyResult(
                True,
                summary,
                details="\n".join(detail_parts),
                data={"removed_users": removed,
                      "purged_secrets": purged_secrets},
            )
        else:
            failed_count = len(errors)
            return ApplyResult(
                False,
                f"{len(removed)} kullanıcı silindi, "
                f"{failed_count} kullanıcı silinemedi.",
                details="\n".join(detail_parts),
            )

