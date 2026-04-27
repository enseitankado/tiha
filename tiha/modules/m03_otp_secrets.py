"""Modül 3 (wizard 4. adım) — Öğretmen PIN anahtarlarını toplu hazırla.

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
Normalde öğretmen PIN anahtarını tahtadaki eta-otp-lock uygulamasıyla
kendisi üretir; ancak uygulama açılmadan önce kullanıcının mevcut yerel
parolasını girmesini ister. TiHA'nın 2. ve 3. adımları (parola kilidi +
açılışta parola temizliği) bu yerel parolayı kasıtlı olarak geçersiz
kıldığı için öğretmen, tahtadaki PIN üreticisini açamaz. Bu yüzden
anahtarları imaj öncesinde merkezî olarak üretir, her öğretmene
özel olarak teslim ederiz. Öğretmen anahtarını Google Authenticator
(veya benzeri) uygulamaya eklediği andan itibaren dağıtılmış tüm
tahtalarda 6 haneli kodla oturum açabilir.

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

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.paths import OTP_SECRETS_FILE
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


def _eta_otp_cli_bulk_script() -> Path | None:
    """``toplu-kullanici-olustur.py`` bulunabiliyorsa yolunu döndürür."""
    dir_env = os.environ.get("TIHA_ETA_OTP_CLI_DIR")
    if not dir_env:
        return None
    script = Path(dir_env) / "toplu-kullanici-olustur.py"
    return script if script.is_file() else None


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


def create_user(username: str) -> bool:
    """TiHA dahili yedek kullanıcı oluşturma (useradd + usermod -L)."""
    if user_exists(username):
        return True
    result = run_cmd(["useradd", "--create-home", "--shell", "/bin/bash", username])
    if not result.ok:
        log.error("Kullanıcı eklenemedi '%s': %s", username, result.stderr.strip())
    run_cmd(["usermod", "-L", username])
    return result.ok


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



def reset_to_default_users() -> tuple[bool, list[str]]:
    """Varsayılan kullanıcılar dışındaki tüm kullanıcıları siler."""
    extra_users = get_extra_users()
    removed_users = []

    if not extra_users:
        return True, []

    for username in extra_users:
        log.info("Kullanıcı siliniyor: %s", username)
        result = run_cmd(["deluser", "--remove-home", username])
        if result.ok:
            removed_users.append(username)
            log.info("Kullanıcı başarıyla silindi: %s", username)
        else:
            log.error("Kullanıcı silinemedi %s: %s", username, result.stderr)

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

    return len(removed_users) == len(extra_users), removed_users




class OTPSecretsModule(Module):
    id = "m03_otp_secrets"
    title = "Öğretmen PIN anahtarları"
    apply_hint = (
        "Listedeki ve yedek hesaplar için PIN anahtarları üretilir."
    )
    save_filename = "ogretmen-pin-anahtarlari.txt"
    streams_output = True
    rationale = (
        "Her öğretmen için 6 haneli PIN kodu üreten güvenlik anahtarını "
        "imaj öncesinde toplu oluşturur. Öğretmenler anahtarı Google "
        "Authenticator benzeri uygulamaya ekleyip tüm tahtalarda 30 saniyede "
        "bir değişen kodla giriş yapar.\n\n"
        "Neden imaj öncesi? Pardus ETAP PIN üretici uygulaması yerel parola "
        "ister, ancak TiHA güvenlik için tüm yerel parolaları rastgele hale "
        "getirir. Bu sebeple anahtarlar önceden üretilip güvenli yolla "
        "iletilir.\n\n"
        "50+ kullanıcıda greeter cache otomatik güncellenir. Geri alma ile "
        "varsayılan kullanıcılar (etapadmin, ogrenci, ogretmen) dışındakiler "
        "silinir. Misafir kullanıcı (ogrenci) güvensiz erişim sağladığından "
        "isteğe bağlı olarak kaldırılabilir."
    )

    def preview(self) -> str:
        import pwd as _pwd

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

        lines: list[str] = []
        cli_script = _eta_otp_cli_bulk_script()
        if cli_script:
            lines.append("Araç: enseitankado/eta-otp-cli  →  toplu-kullanici-olustur.py")
        else:
            lines.append("Araç indirilemedi; dahili pyotp yedek yolu kullanılacak.")
        lines.append("")

        # Kullanıcı sayısı ve greeter cache durumu
        user_count = count_regular_users()
        lines.append(f"Sistemdeki kullanıcı sayısı: {user_count}")
        if user_count >= MIN_USERS_FOR_CACHE:
            lines.append(f"⚠ {MIN_USERS_FOR_CACHE}+ kullanıcı tespit edildi — greeter cache güncellemesi gerekli")
            if GREETER_SCRIPT_PATH.exists():
                lines.append("✓ Greeter cache script mevcut")
            else:
                lines.append("  → GitHub'dan greeter cache script indirilecek")

            if GREETER_SERVICE_PATH.exists():
                lines.append("✓ Greeter cache service mevcut (otomatik çalıştırma aktif)")
            else:
                lines.append("  → Otomatik greeter cache service oluşturulacak")
        else:
            lines.append(f"ℹ {MIN_USERS_FOR_CACHE} kullanıcının altında — greeter cache gerekli değil")
        lines.append("")

        if has_otp:
            lines.append("✓ PIN anahtarı KURULU kişisel hesaplar:")
            lines.extend(f"    • {u}" for u in has_otp)
        else:
            lines.append("Henüz kişisel PIN anahtarı kayıtlı değil.")

        if missing_otp:
            lines.append("")
            lines.append("⚠ Kişisel hesabı olan ama PIN anahtarı OLMAYAN kullanıcılar:")
            lines.extend(f"    • {u}" for u in missing_otp)
            lines.append("")
            lines.append(
                "Bu hesaplar Modül 3 aktifken tahtaya hiç giremeyecekler. "
                "Anahtar üretmek için aşağıdaki metin kutusuna AD SOYAD "
                "biçiminde tam isimleri (ya da var olan kullanıcı adlarını) "
                "yazın."
            )

        if orphan_secrets:
            lines.append("")
            lines.append(
                "ℹ Sistemde hesabı olmayan PIN kayıtları "
                f"(hesap silinmiş olabilir): {', '.join(orphan_secrets)}"
            )

        # Geri alma durumu
        extra_users = get_extra_users()

        lines.append("")
        lines.append("─" * 50)
        lines.append("Kullanıcı Yönetimi")
        lines.append("─" * 50)

        if extra_users:
            lines.append(f"🗑️ Fazladan Kullanıcı Hesapları ({len(extra_users)} adet):")
            lines.extend(f"    • {user}" for user in extra_users[:10])
            if len(extra_users) > 10:
                lines.append(f"    • ... ve {len(extra_users) - 10} tane daha")
            lines.append("")
            lines.append("⚠️ Bu hesaplar varsayılan sistem kullanıcıları değil!")
            lines.append("→ 'Fazladan Hesapları Sil' (onaylı) butonu ile kaldırabilirsiniz")
            lines.append("   Sistem yalnızca etapadmin, ogrenci, ogretmen hesaplarıyla kalacak")
        else:
            lines.append("✓ Sadece varsayılan kullanıcılar mevcut (etapadmin, ogrenci, ogretmen)")

        return "\n".join(lines) if lines else "Henüz hiç kişisel hesap yok."

    # -----------------------------------------------------------------
    # Uygula
    # -----------------------------------------------------------------

    def apply(self, params=None, progress: ProgressCallback | None = None) -> ApplyResult:
        params = params or {}
        raw_list: str = params.get("teacher_names", "")
        reserve: int = int(params.get("reserve_count", 0) or 0)

        teacher_names = [line.strip() for line in raw_list.splitlines() if line.strip()]
        # Yedek hesaplar — toplu-kullanici-olustur.py'nin normalizasyonu
        # 'Ogretmen 01' → 'ogretmen01' verir.
        for i in range(1, reserve + 1):
            teacher_names.append(f"Ogretmen {i:02d}")

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

        # Yeni eklenenleri ve anahtarlarını oku
        after_secrets = load_secrets()
        new_users = [u for u in after_secrets if u not in before_secrets]

        if not new_users:
            return ApplyResult(
                False,
                "Hiç yeni kullanıcı oluşmadı — hepsi zaten vardı olabilir.",
                details=f"Mevcut kayıt sayısı: {len(after_secrets)}",
            )

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
                display_of[u] = name

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

        details = (
            f"{len(new_users)} hesap için anahtar üretildi. Tam liste aşağıda; "
            "'Panoya kopyala' veya 'Dosyaya kaydet…' ile alın."
        )

        # Greeter cache bilgisini ekle
        summary_parts = [f"{len(new_users)} PIN anahtarı üretildi ve {OTP_SECRETS_FILE} dosyasına yazıldı."]
        if greeter_cache_applied:
            summary_parts.append("Greeter cache güncellendi ve otomatik çalıştırma ayarlandı.")
        elif total_users >= MIN_USERS_FOR_CACHE:
            summary_parts.append("Greeter cache kurulumu tamamlanamadı.")

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
            },
        )

    def _apply_with_tool(
        self, script: Path, names: list[str], progress: ProgressCallback | None,
    ) -> bool:
        """``toplu-kullanici-olustur.py`` aracını çağırarak kullanıcı + PIN üret."""
        if progress:
            progress("enseitankado/eta-otp-cli aracı çalıştırılıyor…")

        with tempfile.NamedTemporaryFile(
            "w", suffix="-tiha-isimler.txt", delete=False, encoding="utf-8",
        ) as f:
            f.write("\n".join(names) + "\n")
            names_file = f.name
        try:
            result = run_cmd_stream(
                ["python3", str(script), names_file, "--kullanicilari-olustur"],
                progress=progress,
                timeout=600,
            )
            return result.ok
        finally:
            try:
                os.unlink(names_file)
            except OSError:
                pass

    def _apply_with_internal(
        self, names: list[str], progress: ProgressCallback | None,
    ) -> bool:
        """Aracın olmadığı durumda TiHA'nın kendi pyotp yolu."""
        if progress:
            progress("Dahili pyotp yolu kullanılıyor (araç indirilemedi).")

        secrets = load_secrets()
        for name in names:
            user = normalize_username(name)
            if not user:
                continue
            create_user(user)
            secrets[user] = pyotp.random_base32()
            if progress:
                progress(f"  • {user}: PIN anahtarı üretildi")
        save_secrets(secrets)
        return True

    # -----------------------------------------------------------------
    # Geri al
    # -----------------------------------------------------------------

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        data = data or {}
        created = data.get("created_users", []) or []
        passed = data.get("passed_names", []) or []
        cli_script = _eta_otp_cli_bulk_script()

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

    def remove_extra_users_action(self, params: dict | None = None) -> ApplyResult:
        """Varsayılan hesaplar dışındaki tüm fazladan kullanıcıları siler (onay gerektirir)."""
        extra_users = get_extra_users()

        if not extra_users:
            return ApplyResult(
                False,
                "Silinecek fazladan kullanıcı bulunamadı.",
                details="Sistemde sadece varsayılan kullanıcılar (etapadmin, ogrenci, ogretmen) mevcut."
            )

        # ONAYLIDIR: Bu işlem geri alınamaz, tüm fazladan kullanıcıları siler
        success, removed = reset_to_default_users()

        if success:
            return ApplyResult(
                True,
                f"{len(removed)} fazladan kullanıcı silindi, sistem varsayılan durumuna getirildi.",
                details=f"Silinen kullanıcılar: {', '.join(removed)}\n"
                       "Sistem artık sadece varsayılan hesapları içeriyor:\n"
                       "  • etapadmin (yönetici)\n"
                       "  • ogrenci (ortak hesap)\n"
                       "  • ogretmen (ortak hesap)\n\n"
                       "Greeter cache güncellendi."
            )
        else:
            failed_count = len(extra_users) - len(removed)
            return ApplyResult(
                False,
                f"{len(removed)} kullanıcı silindi, {failed_count} kullanıcı silinemedi.",
                details=f"Başarılı: {', '.join(removed) if removed else 'Hiçbiri'}\n"
                       "Detaylar için /var/log/tiha/tiha.log dosyasına bakın."
            )

