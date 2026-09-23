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
from ..core.i18n import t
from ..core.logger import get_logger
from ..core.private_files import owner_ids
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
# ogretmenler grubuna (ve dolayısıyla grup PIN'ine) hiç girmemesi gereken
# hesaplar: yönetici ve ortak öğretmen hesabı.
GROUP_EXCLUDED_USERS = ("etapadmin", "ogretmen")


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


# eta-otp-lock grup anahtarlarının ön eki: '@ogretmenler' gibi bir kayıt
# bir kullanıcıya değil, o gruba üye herkese aittir.
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
# yalnız etapadmin'e aittir (root:etapadmin). Dizin de listelenemez
# olmalı, aksi hâlde dosya adları (öğretmen adları) sızar.
SECRET_FILE_MODE = 0o640
SECRET_DIR_MODE = 0o750


def harden_secret_store(state_dir: Path) -> int:
    """Anahtar taşıyan durum dizinini ve içindeki dosyaları kilitler.

    Dizin 0750, dosyalar 0640 yapılır; sahip root, grup ise etapadmin
    olur. Böylece yönetici kâğıdı tarayıcıda açıp
    okuyabilir ama değiştiremez, diğer hesaplar hiç göremez.

    Geçmişte gevşek izinle (0644) yazılmış dosyalar da bu çağrıyla
    düzeltilir; düzeltilen dosya sayısı döner.
    """
    # Yalnız etapadmin: eskiden aktif grafik oturumun kullanıcısının grubu
    # seçiliyordu; o an bir öğretmen oturumu açıksa kâğıtlar ona açılırdı.
    admin = owner_ids()
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


# PIN kâğıdında en başta durması gereken, sızması en ağır sonuç doğuran
# anahtarlar: grup ortak PIN'i, sistem yöneticisi, ortak öğretmen hesabı.
PAPER_PRIORITY = ("@ogretmenler", "etapadmin", "ogretmen")


def paper_order(users) -> list[str]:
    """Kâğıt sırası: grup PIN'i ve etapadmin başta, sonra ortak hesap,
    kalanlar alfabetik."""
    present = set(users)
    head = [u for u in PAPER_PRIORITY if u in present]
    return head + sorted(present - set(PAPER_PRIORITY))


def _branch_usernames() -> set[str]:
    """MEB branş listesinden türeyen bütün branş hesabı adları."""
    try:
        from ..core.meb_data import all_branch_labels, branch_to_username
        return {branch_to_username(label) for label in all_branch_labels()}
    except Exception as exc:  # veri dosyası yok/bozuk: tür eki yazılmaz
        log.debug("Branş listesi okunamadı: %s", exc)
        return set()


def _paper_account_kind(user: str, branches: set[str]) -> str:
    """Kart başlığındaki hesap türü eki (yoksa boş)."""
    if user in branches:
        return t("m03.paper.kind_branch")
    if RESERVE_USER_RE.match(user):
        return t("m03.paper.kind_personal")
    return ""


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
        return t("m03.paper.group_display", group=user[1:])

    import pwd as _pwd
    try:
        gecos = _pwd.getpwnam(user).pw_gecos.split(",")[0].strip()
    except KeyError:
        # Anahtarı var ama sistem hesabı yok (hesap sonradan silinmiş).
        return t("m03.paper.no_account", user=user)
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

# Geri alma hiçbir koşulda silmez.
PROTECTED_ACCOUNTS = frozenset({"root", "etapadmin", "ogretmen", "ogrenci"})


def _account_names() -> set[str]:
    """Sistemdeki bütün hesap adları."""
    import pwd as _pwd
    try:
        return {e.pw_name for e in _pwd.getpwall()}
    except OSError:
        return set()


def list_reserve_accounts() -> list[str]:
    """Sistemde ogretmenN / ogretmen.N biçiminde tüm hesap adları.

    Indeks sırasına göre döner (ogretmen1, ogretmen2, ...). ``count_reserve_accounts``
    en büyük indeksi verirken bu fonksiyon fiili hesap adlarının listesini
    döner — silme işi bunlara doğrudan ``deluser --remove-home`` çekilir.
    """
    import pwd as _pwd

    found: list[tuple[int, str]] = []
    try:
        entries = _pwd.getpwall()
    except OSError as exc:
        log.warning("Kullanıcı listesi okunamadı: %s", exc)
        return []
    for entry in entries:
        if not 1000 <= entry.pw_uid < 60000:
            continue
        match = RESERVE_USER_RE.match(entry.pw_name)
        if match:
            found.append((int(match.group(1)), entry.pw_name))
    return [name for _, name in sorted(found)]


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



def _excluded_group_members() -> list[str]:
    """ogretmenler grubunda bulunmaması gerektiği hâlde üye olan hesaplar."""
    import grp as _grp
    try:
        members = set(_grp.getgrnam(OGRETMENLER_GROUP).gr_mem)
    except KeyError:
        return []
    return [u for u in GROUP_EXCLUDED_USERS if u in members]


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
            progress("\n" + t("m03.reset.killing", user=username))
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
                progress(t("m03.reset.deleted", user=username))
        else:
            err = (result.stderr or result.stdout or "").strip() or \
                t("m03.reset.exit_code", code=result.returncode)
            errors[username] = err
            log.error("Kullanıcı silinemedi %s: %s", username, err)
            if progress:
                progress(t("m03.reset.failed", user=username, error=err))

    # PIN kayıtlarına burada dokunulmaz. Eskiden yalnız varsayılan
    # hesapların kayıtları bırakılıyordu; bu, onay metninin "korunur"
    # dediği grup anahtarını (@ogretmenler) ve silinemeyen hesapların
    # anahtarlarını da siliyordu. Karşılığı kalmayan kayıtları çağıran
    # (remove_extra_users_action) hemen ardından orphan_secret_users
    # kuralıyla temizler: grup anahtarları ve varsayılan hesaplar korunur.

    # Greeter cache'i güncelle
    if GREETER_SCRIPT_PATH.exists():
        run_greeter_script_once()
        log.info("Greeter cache güncellendi")

    return len(removed_users) == len(extra_users), removed_users, errors




class OTPSecretsModule(Module):
    id = "m03_otp_secrets"
    title = t("m03.title")
    sidebar_title = t("m03.sidebar_title")
    apply_hint = t("m03.apply_hint")
    # "Dosyaya kaydet…" yazdırılabilir kâğıdı kaydeder; biçim HTML.
    # apply() gerçek dosya adını (zaman damgalı) result üzerinden verir.
    save_filename = "ogretmen-pin-kagitlari.html"
    streams_output = True
    rationale = t("m03.rationale")

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
            tool_line = t("m03.preview.tool_checking")
        elif tool_available:
            tool_line = t("m03.preview.tool_cli")
        else:
            tool_line = t("m03.preview.tool_internal")

        user_count = count_regular_users()
        extra_users = get_extra_users()

        lines: list[str] = []
        lines.append(t("m03.preview.status_time", time=current_time))
        lines.append(t("m03.preview.tool", tool=tool_line))
        lines.append(t("m03.preview.system_users", count=user_count))
        lines.append(t("m03.preview.personal_count", count=len(personal_users)))
        lines.append(t(
            "m03.preview.pin_accounts",
            done=len(has_otp), total=len(personal_users),
        ))
        lines.append(t("m03.preview.total_pins", count=len(existing)))
        lines.append(t(
            "m03.preview.qr",
            state=(t("m03.preview.qr_embedded") if qrcode_available()
                   else t("m03.preview.qr_skipped")),
        ))
        if user_count >= MIN_USERS_FOR_CACHE:
            greeter_state = (
                t("m03.preview.greeter_installed")
                if GREETER_SERVICE_PATH.exists()
                else t("m03.preview.greeter_pending")
            )
            lines.append(t("m03.preview.greeter", state=greeter_state))
        lines.append("")

        lines.append(t("m03.preview.note_no_accounts"))
        lines.append("")
        lines.append(t("m03.preview.note_keep_existing"))
        lines.append("")

        if has_otp:
            lines.append(t("m03.preview.has_otp_header"))
            for u in has_otp:
                lines.append(f"  - {u}")
            lines.append("")
        if missing_otp:
            lines.append(t("m03.preview.missing_otp_header"))
            for u in missing_otp:
                lines.append(f"  - {u}")
            lines.append(t("m03.preview.missing_otp_hint"))
            lines.append("")
        if group_secrets:
            lines.append(t("m03.preview.group_secrets", keys=", ".join(group_secrets)))
            lines.append("")
        if orphan_secrets:
            lines.append(t(
                "m03.preview.orphans",
                count=len(orphan_secrets), users=", ".join(orphan_secrets),
            ))
            lines.append(t("m03.preview.orphans_hint"))
            lines.append("")

        lines.append(t("m03.preview.user_mgmt_header"))
        if extra_users:
            head = extra_users[:10]
            more = len(extra_users) - len(head)
            lines.append(t("m03.preview.extra_count", count=len(extra_users)))
            for u in head:
                lines.append(f"    - {u}")
            if more > 0:
                lines.append(t("m03.preview.extra_more", count=more))
            lines.append(t("m03.preview.extra_hint"))
        else:
            lines.append(t("m03.preview.only_defaults"))
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
        make_group_pin: bool = str(
            params.get("make_group_pin", "False")
        ).lower() in ("true", "1", "yes", "on")
        # Sistemdeki öğretmen hesapları (ortak ogretmen hesabı, EBA QR ile
        # açılanlar, branş hesapları…) için de PIN. Varsayılan açık; eski
        # presetlerde anahtar yok, onlar eski davranışta (kapalı) kalır.
        include_other_teachers: bool = str(
            params.get("include_other_teachers", "False")
        ).lower() in ("true", "1", "yes", "on")
        # Eski presetlerdeki "ortak ogretmen hesabı için de PIN" kutusu:
        # yalnız o hesabı kapsıyordu, anlamı korunur.
        include_ogretmen_legacy: bool = str(
            params.get("include_ogretmen", "False")
        ).lower() in ("true", "1", "yes", "on")
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

        # Diğer öğretmen hesapları: varsayılan hesaplar ve yedek hesaplar
        # dışında, listeye yazılmamış bütün kişisel hesaplar. Bunlar ad
        # listesinden geçirilmez — ad → kullanıcı adı dönüşümü var olan
        # hesabın adını (GECOS) ezer, farklı bir ad üretip yeni hesap bile
        # açabilirdi. Anahtarları aşağıda kullanıcı adıyla doğrudan üretilir.
        other_teachers: list[str] = []
        if include_other_teachers:
            listed = {normalize_username(n) for n in teacher_names}
            listed |= {_eta_otp_cli_normalize(n) for n in teacher_names}
            other_teachers = [
                u for u in get_extra_users()
                if u not in listed and u not in reserve_usernames
            ]
        # Ortak ogretmen hesabı da bir öğretmen hesabı; ayrı kutusu kalktı.
        if ((include_other_teachers or include_ogretmen_legacy)
                and user_exists("ogretmen") and "ogretmen" not in other_teachers):
            other_teachers.insert(0, "ogretmen")

        if not teacher_names and not other_teachers:
            return ApplyResult(
                False,
                t("m03.apply.empty_list"),
                details=t("m03.apply.empty_list_details"),
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
        # Uygulamadan önce var olan hesaplar: geri alma yalnız bu adımın
        # gerçekten açtığı hesapları silmeli. "Yeni anahtar alanlar"
        # (created_users) ortak ogretmen, etapadmin, branş ve EBA QR
        # hesaplarını da içerir; onları silmek veri kaybıdır.
        before_accounts = _account_names()

        # Hâlihazırda anahtarı olan hesaplara dokunulmaz: öğretmenin
        # telefonundaki anahtar geçerli kalsın. Adım aynı listeyle
        # yeniden uygulandığında yalnızca eksikler tamamlanır.
        keep_users = set(before_secrets)

        cli_script = _eta_otp_cli_bulk_script()
        if not teacher_names:
            success = True
        elif cli_script:
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
                t("m03.apply.keys_failed"),
                details=t("m03.apply.keys_failed_details"),
            )

        # reserve_usernames yukarıda apply girişinde hesaplandı; grup
        # üyeliği ve auto-group izleyici aşağıda o listeyi kullanır.

        # Her hesap için passwd GECOS (ad/soyad) alanını yaz.
        self._apply_gecos(teacher_names, cli_used=bool(cli_script), progress=progress)

        if other_teachers:
            self._apply_other_teachers(other_teachers, progress, keep_users)

        # Yeni eklenenleri ve anahtarlarını oku
        after_secrets = load_secrets()
        new_users = [u for u in after_secrets if u not in before_secrets]
        created_accounts = sorted(_account_names() - before_accounts)

        # Listede olup anahtarı zaten bulunan hesaplar — dokunulmadı.
        _normalize = _eta_otp_cli_normalize if cli_script else normalize_username
        requested_users = {u for u in (_normalize(n) for n in teacher_names) if u}
        requested_users |= set(other_teachers)
        preserved_users = sorted(requested_users & keep_users)

        # ===== ogretmenler grubu =====================================
        # Grup üyeliği artık yedek hesap sayısına bağlı değil: bu adımın
        # yönettiği bütün öğretmen hesapları (listeden gelenler + yedek
        # hesaplar) gruba alınır ve gelecekte EBA QR ile açılacaklar için
        # izleyici servis kurulur.
        group_key = f"@{OGRETMENLER_GROUP}"
        group_secret_is_new = False
        grouped_users: list[str] = []
        ungrouped_users: list[str] = []
        auto_group_service_installed = False

        # Öğretmen hesaplarının gruba üyeliği seçenek değil: '@ogretmenler'
        # ortak PIN'i yalnız gruba üye hesaplarda çalışır ve EBA QR ile
        # açılan hesaplar gruba kendiliğinden girmez.
        if not ensure_ogretmenler_group():
            if progress:
                progress("\n" + t(
                    "m03.apply.group_create_failed_membership",
                    group=OGRETMENLER_GROUP,
                ))
        else:
            # PIN kutusundan bağımsız: tahtadaki bütün kişisel hesaplar
            # (EBA QR, branş, yedek) öğretmen hesabıdır, gruba girer.
            targets = sorted(
                requested_users | set(reserve_usernames) | set(get_extra_users())
            )
            if progress:
                progress("\n" + t("m03.apply.adding_to_group", group=OGRETMENLER_GROUP))
            for u in targets:
                # etapadmin bir öğretmen hesabı değil; ortak PIN'in
                # yönetici hesabına da geçmesi istenmez. Ortak ogretmen
                # hesabı da gruba girmez: grup PIN'i ortak hesapta
                # geçmemeli (otomatik grup servisi de onu hariç tutar).
                if u in GROUP_EXCLUDED_USERS or not user_exists(u):
                    continue
                run_cmd(["usermod", "-a", "-G", OGRETMENLER_GROUP, u], check=False)
                grouped_users.append(u)
                if progress:
                    progress(f"  + {u}")
            if not grouped_users and progress:
                progress(t("m03.apply.no_group_targets"))
            # Eski sürüm ortak hesabı da gruba ekliyordu; üyeliği kaldır.
            for u in _excluded_group_members():
                if run_cmd(["gpasswd", "-d", u, OGRETMENLER_GROUP], check=False).ok:
                    ungrouped_users.append(u)
                    if progress:
                        progress(t("m03.apply.removed_from_group", user=u))
            # Sonradan EBA QR ile açılacak hesaplar için izleyici servis
            if install_auto_group_service():
                auto_group_service_installed = True
                if progress:
                    progress("\n" + t("m03.apply.auto_group_installed"))

        if make_group_pin:
            if not ensure_ogretmenler_group():
                if progress:
                    progress("\n" + t(
                        "m03.apply.group_create_failed_pin",
                        group=OGRETMENLER_GROUP,
                    ))
            elif group_key in after_secrets:
                # Mevcut grup-PIN'i yenilemek telefonlardaki anahtarı
                # geçersiz kılardı; koruyoruz.
                if progress:
                    progress("\n" + t("m03.apply.group_pin_kept", key=group_key))
            else:
                after_secrets[group_key] = pyotp.random_base32()
                save_secrets(after_secrets)
                group_secret_is_new = True
                if progress:
                    progress("\n" + t("m03.apply.group_pin_created", key=group_key))


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
            progress(t("m03.apply.changed_warning", count=len(changed_users)))
            for user in changed_users:
                progress(f"  ! {user}")
            progress(t("m03.apply.changed_hint"))
            progress("!" * 60)

        if not new_users and not group_secret_is_new and not preserved_users:
            return ApplyResult(
                False,
                t("m03.apply.none_generated"),
                details=t("m03.apply.none_generated_details", count=len(after_secrets)),
            )
        # Grup secret'ı da PIN kartı listesine dahil et
        if group_secret_is_new and group_key not in new_users:
            new_users.append(group_key)

        # Greeter cache kontrolü ve kurulumu
        total_users = count_regular_users()
        greeter_cache_applied = False
        if total_users >= MIN_USERS_FOR_CACHE:
            if progress:
                progress(t("m03.apply.greeter_start", count=total_users))

            # GitHub'dan script indir
            if not GREETER_SCRIPT_PATH.exists():
                if progress:
                    progress(t("m03.apply.greeter_downloading"))
                if not download_greeter_script():
                    log.warning("Greeter cache script indirilemedi, devam ediliyor...")
                else:
                    if progress:
                        progress(t("m03.apply.greeter_downloaded"))

            # Systemd service oluştur
            if not GREETER_SERVICE_PATH.exists() and GREETER_SCRIPT_PATH.exists():
                if progress:
                    progress(t("m03.apply.greeter_service_creating"))
                if not create_greeter_service():
                    log.warning("Greeter cache service oluşturulamadı, devam ediliyor...")
                else:
                    if progress:
                        progress(t("m03.apply.greeter_service_created"))

            # Script'i bir kez çalıştır (yeni kullanıcıları cache'e al)
            if GREETER_SCRIPT_PATH.exists():
                if progress:
                    progress(t("m03.apply.greeter_updating"))
                if run_greeter_script_once():
                    greeter_cache_applied = True
                    if progress:
                        progress(t("m03.apply.greeter_updated"))
                else:
                    log.warning("Greeter cache güncellemesi başarısız, devam ediliyor...")
        elif progress:
            progress(t(
                "m03.apply.greeter_not_needed",
                count=total_users, limit=MIN_USERS_FOR_CACHE,
            ))

        # Tam adları username -> display map'e koy (rapor için)
        display_of: dict[str, str] = {}
        for name in teacher_names:
            u = _eta_otp_cli_normalize(name) if cli_script else normalize_username(name)
            if u in new_users:
                if u == "etapadmin":
                    display_of[u] = t("m03.apply.display_admin")
                    continue
                display_of[u] = name
        # Grup-PIN kartı için özel etiket
        if group_key in new_users:
            display_of[group_key] = t("m03.apply.display_group")

        # Rapor
        report_lines: list[str] = []
        report_lines.append("─" * 76)
        report_lines.append(t("m03.pin_report.generated", count=len(new_users)))
        if preserved_users:
            report_lines.append(t("m03.pin_report.preserved", count=len(preserved_users)))
        if changed_users:
            report_lines.append(t("m03.pin_report.changed", count=len(changed_users)))
        report_lines.append(t("m03.pin_report.total", count=len(after_secrets)))
        report_lines.append(t("m03.pin_report.file", path=OTP_SECRETS_FILE))
        report_lines.append(t("m03.pin_report.issuer", issuer=OTP_ISSUER))
        if cli_script:
            report_lines.append(t("m03.pin_report.tool"))
        report_lines.append("─" * 76)
        for idx, user in enumerate(sorted(new_users), 1):
            secret = after_secrets[user]
            display = display_of.get(user, t("m03.pin_report.reserve_display"))
            report_lines.append("")
            report_lines.append(f"[{idx:02d}]  {display}")
            report_lines.append(t("m03.pin_report.username", user=user))
            report_lines.append(t("m03.pin_report.secret", secret=secret))
        if preserved_users:
            report_lines.append("")
            report_lines.append("─" * 76)
            report_lines.append(t("m03.pin_report.preserved_header"))
            for user in preserved_users:
                report_lines.append(f"     · {user}")
        report_lines.append("")
        report_lines.append("─" * 76)
        report_lines.append(t("m03.pin_report.usage"))
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
            progress(t("m03.apply.paper_written", count=len(all_users), path=html_path))

        # details, UI'da aşağıdaki metin raporuyla TEK alanda birleşerek
        # gösteriliyor. Bu yüzden burada sayıları yinelemiyoruz —
        # üretilen/korunan/değişen sayıları hem tek satırlık özette hem
        # raporun başlığında zaten var. Buraya yalnız raporda olmayan
        # bilgi yazılır: kâğıdın nerede olduğu ve ne yapılacağı.
        details_lines: list[str] = []
        if html_path:
            details_lines.append(t("m03.apply.paper_header", count=len(all_users)))
            details_lines.append(f"  {html_path}")
            details_lines.append(t("m03.apply.paper_print_hint"))
            details_lines.append(t("m03.apply.paper_autoopen"))
            details_lines.append(t("m03.apply.paper_save_hint"))
        details = "\n".join(details_lines)

        # Greeter cache bilgisini ekle
        if new_users:
            summary_parts = [
                t("m03.apply.summary_new", count=len(new_users), path=OTP_SECRETS_FILE)
            ]
        else:
            summary_parts = [t("m03.apply.summary_none_new")]
        if preserved_users and new_users:
            summary_parts.append(
                t("m03.apply.summary_preserved", count=len(preserved_users))
            )
        if greeter_cache_applied:
            summary_parts.append(t("m03.apply.summary_greeter_ok"))
        elif total_users >= MIN_USERS_FOR_CACHE:
            summary_parts.append(t("m03.apply.summary_greeter_failed"))
        if grouped_users:
            summary_parts.append(t(
                "m03.apply.summary_grouped",
                count=len(grouped_users), group=OGRETMENLER_GROUP,
            ))
        if auto_group_service_installed:
            summary_parts.append(t("m03.apply.summary_auto_group"))

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
                t(
                    "m03.apply.changed_dialog",
                    count=len(changed_users),
                    users=", ".join(changed_users),
                )
                if changed_users else None
            ),
            data={
                "passed_names": teacher_names,
                "created_users": sorted(new_users),
                # Bu uygulamada açılan Linux hesapları (geri alma bunları siler).
                "created_accounts": created_accounts,
                # "Öğretmen hesapları için de PIN üret" kapsamındaki hesaplar.
                "other_teachers": sorted(other_teachers),
                "preserved_users": preserved_users,
                "changed_users": changed_users,
                "grouped_users": grouped_users,
                "ungrouped_users": ungrouped_users,
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

        badge_html = f'<span class="badge">{t("m03.paper.badge_new")}</span>'
        meta = t(
            "m03.paper.meta",
            created=ts.replace("-", " "),
            total=len(all_users),
            new=len(new_users),
            badge=badge_html,
            issuer=_esc(OTP_ISSUER),
        )

        cards: list[str] = []
        branches = _branch_usernames()
        for user in paper_order(all_users):
            secret = secrets.get(user, "")
            display = _paper_display_name(user, display_of)
            kind = _paper_account_kind(user, branches)
            kind_html = f' <span class="kind">({_esc(kind)})</span>' if kind else ""
            # Sızması bütün tahtaları etkileyen anahtarlar: kısa uyarı.
            warn_key = {"@ogretmenler": "warn_group", "etapadmin": "warn_admin"}.get(user)
            warn_html = (
                f'    <div class="warn">⚠ {t("m03.paper." + warn_key)}</div>\n'
                if warn_key else ""
            )
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
                    f'    <div class="qr-label">{t("m03.paper.qr_label")}</div>\n'
                    '    <button type="button" class="save-img no-export">'
                    f'{t("m03.paper.save_image")}</button>\n'
                    '  </aside>'
                )
            else:
                qr_block = ""
            is_group = user.startswith("@")
            if is_group:
                group_esc = _esc(user[1:])
                user_line = (
                    '<div class="user">'
                    f'{t("m03.paper.group_line", group=group_esc)}</div>'
                )
                steps = [
                    t("m03.paper.step_install"),
                    t("m03.paper.group_step_scan"),
                    t("m03.paper.group_step_label"),
                    t("m03.paper.step_type"),
                    t("m03.paper.group_step_save", group=group_esc),
                ]
            else:
                user_line = (
                    '<div class="user">'
                    f'{t("m03.paper.user_line", user=_esc(user))}</div>'
                )
                steps = [
                    t("m03.paper.step_install"),
                    t("m03.paper.user_step_scan", user=_esc(user)),
                    t("m03.paper.step_type"),
                    t("m03.paper.user_step_save"),
                ]
            instructions = (
                "    <ol>\n"
                + "".join(f"      <li>{step}</li>\n" for step in steps)
                + "    </ol>"
            )
            badge = badge_html if is_new else ""
            cards.append(f'''
<article class="card{' group' if is_group else ''}{' fresh' if is_new else ''}" data-user="{_esc(user.lstrip('@') + ('-grubu' if is_group else ''))}">
  <div class="body">
    <header>
      <h2>{_esc(display)}{kind_html}{badge}</h2>
      {user_line}
    </header>
{warn_html}
    <section class="secret">
      <div class="label">{t("m03.paper.secret_label")}</div>
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
<html lang="{t("m03.paper.html_lang")}"><head>
<meta charset="utf-8">
<title>{t("m03.paper.title", ts=ts)}</title>
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
  .card h2 .kind {{ font-size: 10pt; font-weight: normal; color: #555; }}
  .warn {{
    font-size: 9.5pt; color: #8a1c1c; background: #fdecea;
    border: 1px solid #e0a3a3; border-radius: 4px;
    padding: 6px 10px; margin: 0 0 10px 0;
  }}
  .qr svg {{ cursor: pointer; transition: filter 0.15s; }}
  body.qr-focus .qr svg {{ filter: blur(6px); }}
  body.qr-focus .qr svg.qr-active {{ filter: none; }}
  .save-img, .save-all {{
    font: inherit; font-size: 9pt; cursor: pointer;
    border: 1px solid #bbb; border-radius: 4px; background: #fff;
    padding: 3px 8px; margin-top: 6px;
  }}
  .save-img:hover, .save-all:hover {{ background: #eef4ff; }}
  .toolbar {{ margin: 0 0 16px 0; font-size: 9pt; color: #666; }}
  .toolbar .save-all {{ font-size: 10pt; margin: 0 8px 0 0; }}
  @media print {{
    body {{ margin: 8mm; }}
    .card {{ break-inside: avoid; }}
    body.qr-focus .qr svg {{ filter: none; }}
    .no-export {{ display: none !important; }}
  }}
</style>
</head><body>
<h1>{t("m03.paper.heading")}</h1>
<div class="meta">
  {meta}
</div>
<div class="toolbar no-export">
  <button type="button" class="save-all">{t("m03.paper.save_all_images")}</button>
  {t("m03.paper.save_all_note")}
</div>
{"".join(cards)}
<script>
// Bir QR koda tıklanınca diğerleri bulanıklaşır (yanlış kodu taratmayı
// ve yandakilerin görülmesini önler); aynı koda yeniden tıklayınca düzelir.
(function () {{
  var codes = document.querySelectorAll(".qr svg");
  codes.forEach(function (svg) {{
    svg.addEventListener("click", function () {{
      var active = svg.classList.contains("qr-active");
      codes.forEach(function (o) {{ o.classList.remove("qr-active"); }});
      if (active) {{
        document.body.classList.remove("qr-focus");
      }} else {{
        svg.classList.add("qr-active");
        document.body.classList.add("qr-focus");
      }}
    }});
  }});
}})();

// Kartı resim (PNG) olarak kaydet. Dış kitaplık yok: kart, sayfanın
// stilleriyle birlikte bir SVG <foreignObject> içine konup tuvale çizilir.
// Düğmeler ve QR bulanıklığı resme girmez.
(function () {{
  var css = document.querySelector("style").textContent;
  function cardToPng(card) {{
    return new Promise(function (resolve, reject) {{
      var w = card.offsetWidth, h = card.offsetHeight, scale = 2, pad = 12;
      var clone = card.cloneNode(true);
      clone.querySelectorAll(".no-export").forEach(function (n) {{ n.remove(); }});
      clone.querySelectorAll(".qr-active").forEach(function (n) {{ n.classList.remove("qr-active"); }});
      clone.style.margin = "0";
      // offsetWidth kenarlık ve iç boşluğu içerir; klon aynı kutuya sığsın.
      clone.style.boxSizing = "border-box";
      clone.style.width = w + "px";
      var xhtml = new XMLSerializer().serializeToString(clone);
      var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="' + (w + 2 * pad) +
        '" height="' + (h + 2 * pad) + '"><foreignObject x="0" y="0" width="100%" height="100%">' +
        '<div xmlns="http://www.w3.org/1999/xhtml" style="background:#fff;padding:' + pad +
        'px;font-family:Ubuntu,sans-serif;color:#222"><style>' + css + '</style>' + xhtml +
        '</div></foreignObject></svg>';
      var img = new Image();
      img.onload = function () {{
        try {{
          var c = document.createElement("canvas");
          c.width = (w + 2 * pad) * scale; c.height = (h + 2 * pad) * scale;
          var ctx = c.getContext("2d");
          ctx.scale(scale, scale);
          ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, c.width, c.height);
          ctx.drawImage(img, 0, 0);
          resolve(c.toDataURL("image/png"));
        }} catch (e) {{ reject(e); }}
      }};
      img.onerror = function () {{ reject(new Error("SVG")); }};
      img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
    }});
  }}
  function download(url, name) {{
    var a = document.createElement("a");
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
  }}
  function fileName(card) {{
    return "pin-" + (card.getAttribute("data-user") || "kart") + ".png";
  }}
  function saveCard(card) {{
    return cardToPng(card).then(function (url) {{ download(url, fileName(card)); }})
      .catch(function (e) {{ alert({json.dumps(t("m03.paper.save_failed", error="__E__"))}.replace("__E__", e.message || e)); }});
  }}
  window.tihaCardToPng = cardToPng;
  document.querySelectorAll(".save-img").forEach(function (b) {{
    b.addEventListener("click", function () {{ saveCard(b.closest(".card")); }});
  }});
  var all = document.querySelector(".save-all");
  if (all) all.addEventListener("click", function () {{
    var cards = Array.prototype.slice.call(document.querySelectorAll(".card"));
    // Sırayla, aralıklı: tarayıcılar art arda anlık indirmeleri yutabiliyor.
    cards.reduce(function (p, card) {{
      return p.then(function () {{
        return saveCard(card).then(function () {{
          return new Promise(function (r) {{ setTimeout(r, 350); }});
        }});
      }});
    }}, Promise.resolve());
  }});
}})();
</script>
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
            progress(t("m03.apply.tool_running"))

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
                    progress(t(
                        "m03.apply.tool_kept",
                        idx=idx, total=total_count, name=full_name, user=username,
                    ))
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
                    progress(t("m03.apply.tool_ok", user=username))
            else:
                log.error("OTP anahtarı oluşturulamadı %s: %s", username, result.stderr)
                if progress:
                    progress(t("m03.apply.tool_error", user=username))

        if progress:
            progress(t(
                "m03.apply.tool_done",
                ok=success_count, attempted=attempted, kept=kept_count,
            ))

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
            progress(t("m03.apply.internal_start"))

        secrets = load_secrets()
        for name in names:
            user = normalize_username(name)
            if not user:
                continue
            create_user(user, full_name=name)
            if user in keep_users:
                if progress:
                    progress(t("m03.apply.internal_kept", user=user, name=name))
                continue
            secrets[user] = pyotp.random_base32()
            if progress:
                progress(t("m03.apply.internal_created", user=user, name=name))
        save_secrets(secrets)
        return True

    def _apply_other_teachers(
        self,
        users: list[str],
        progress: ProgressCallback | None,
        keep_users: set[str],
    ) -> None:
        """Sistemde zaten var olan öğretmen hesaplarına anahtar üretir.

        Hesap açmaz, ad/soyad (GECOS) alanına dokunmaz; anahtarı olan
        hesabın anahtarı korunur (telefondaki kayıt geçerli kalsın).
        """
        if progress:
            progress("\n" + t("m03.apply.other_teachers_start", count=len(users)))
        secrets = load_secrets()
        for user in users:
            if user in keep_users or user in secrets:
                if progress:
                    progress(t("m03.apply.other_teacher_kept", user=user))
                continue
            secrets[user] = pyotp.random_base32()
            if progress:
                progress(t("m03.apply.other_teacher_created", user=user))
        save_secrets(secrets)

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
            progress(t("m03.apply.gecos_updated", count=updated))
        return updated

    # -----------------------------------------------------------------
    # Geri al
    # -----------------------------------------------------------------

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        data = data or {}
        # Eski kayıtlarda yalnız "yeni anahtar alan" hesaplar (created_users)
        # tutuluyordu; içinde yedek, branş ve EBA QR hesapları da olabildiği
        # ve hangisinin bu adımda açıldığı bilinemediği için hesap silinmez.
        legacy = "created_accounts" not in data
        created = [] if legacy else list(data.get("created_accounts") or [])

        # Auto-group izleme servisi kurulduysa kaldır (idempotent).
        uninstall_auto_group_service()

        # Yalnız bu uygulamada açılan hesaplar silinir. Eskiden isim listesi
        # (passed_names) eta-otp-cli --kullanicilari-sil'e veriliyordu; o
        # listede yedek hesaplar ve etapadmin de bulunduğu için geri alma
        # onları da silebiliyordu. Anahtar dosyası aşağıda yedekten döner.
        removed: list[str] = []
        for user in created:
            if user in PROTECTED_ACCOUNTS or not user_exists(user):
                continue
            kill_user_processes(user)
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
            summary_parts.append(t("m03.undo.removed", count=len(removed)))
        summary_parts.append(t("m03.undo.restored"))

        if greeter_cache_removed:
            summary_parts.append(t("m03.undo.greeter_removed"))

        summary = "; ".join(summary_parts) + "."
        details = t("m03.undo.legacy_no_delete") if legacy else ""
        return ApplyResult(True, summary, details=details)

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
        return t("m03.buttons.purge_all", count=n)

    def label_remove_extra_users(self) -> str:
        """'Fazladan Hesapları Sil' düğmesinin dinamik etiketi.
        Silinecek hesap sayısı ve karşılığında düşecek yetim PIN
        anahtar sayısı gösterilir."""
        extras = get_extra_users()
        secrets = load_secrets()
        # extras arasında PIN kaydı olan hesap sayısı — silme onunla
        # birlikte otp-secrets.json'dan da bu kayıtları düşürür.
        matching_keys = sum(1 for u in extras if u in secrets)
        return t(
            "m03.buttons.remove_extra",
            accounts=len(extras), keys=matching_keys,
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
                t("m03.purge.none"),
                details=t("m03.purge.none_details", path=OTP_SECRETS_FILE),
            )

        users = order_secret_users(secrets)
        state = self.ensure_state_dir()
        harden_secret_store(state)
        backup = backup_file(OTP_SECRETS_FILE, state)

        if progress:
            progress(t("m03.purge.deleting", count=len(users)))
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
            t("m03.purge.deleted_list", count=len(users), users=", ".join(users)),
            t("m03.purge.file_empty", path=OTP_SECRETS_FILE),
        ]
        if removed_papers:
            details.append(t("m03.purge.papers_removed", count=removed_papers))
        if backup is not None:
            details.append(t("m03.purge.backup", path=backup))
        details.append(t("m03.purge.redistribute"))

        return ApplyResult(
            True,
            t("m03.purge.summary", count=len(users)),
            details="\n".join(details),
            warning=t("m03.purge.warning", count=len(users)),
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
            progress("\n" + t("m03.purge.orphans_cleaning", count=len(orphans)))
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
                t("m03.remove_extra.nothing"),
                details=t("m03.remove_extra.nothing_details"),
            )

        # Silinecek hesap yok ama yetim kayıt var: yalnız temizlik yap.
        if not extra_users:
            purged = self._purge_orphan_secrets(progress=progress)
            return ApplyResult(
                True,
                t("m03.remove_extra.only_orphans", count=len(purged)),
                details=t("m03.remove_extra.only_orphans_details") + "\n"
                        + "\n".join(f"  · {u}" for u in purged),
                data={"removed_users": [], "purged_secrets": purged},
            )

        if progress:
            progress(t(
                "m03.remove_extra.will_delete",
                count=len(extra_users), users=", ".join(extra_users),
            ))

        success, removed, errors = reset_to_default_users(progress=progress)

        # Hesabı gitmiş kayıtların anahtarı dosyada kalırsa imaja ölü
        # sır olarak gider: kullanılamaz ama okunabilir. Hesapları
        # sildikten sonra karşılığı kalmayan kayıtları da temizliyoruz.
        # Grup anahtarları ve varsayılan hesaplar korunur.
        purged_secrets = self._purge_orphan_secrets(progress=progress)

        # Detay metni — hem başarılı hem başarısız kayıtları topla
        detail_parts: list[str] = []
        if removed:
            detail_parts.append(t("m03.remove_extra.removed_header"))
            detail_parts.extend(f"  ✓ {u}" for u in removed)
        if errors:
            detail_parts.append("")
            detail_parts.append(t("m03.remove_extra.failed_header"))
            for u, err in errors.items():
                # deluser çıktısı çok satırlı olabilir; girintili göster
                err_indented = (
                    "\n      ".join(err.splitlines())
                    or t("m03.remove_extra.empty_output")
                )
                detail_parts.append(f"  ✗ {u}\n      {err_indented}")

        if purged_secrets:
            detail_parts.append("")
            detail_parts.append(
                t("m03.remove_extra.orphans_removed", count=len(purged_secrets))
            )
            detail_parts.extend(f"  · {u}" for u in purged_secrets)

        if success:
            detail_parts.append("")
            detail_parts.append(t("m03.remove_extra.defaults_only"))
            detail_parts.extend([
                t("m03.remove_extra.default_admin"),
                t("m03.remove_extra.default_student"),
                t("m03.remove_extra.default_teacher"),
            ])
            summary = t("m03.remove_extra.summary", count=len(removed))
            if purged_secrets:
                summary += t(
                    "m03.remove_extra.summary_orphans", count=len(purged_secrets),
                )
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
                t(
                    "m03.remove_extra.summary_partial",
                    removed=len(removed), failed=failed_count,
                ),
                details="\n".join(detail_parts),
            )

