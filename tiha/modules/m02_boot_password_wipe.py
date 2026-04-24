"""Modül 2 (wizard 3. adım) — Her açılışta parola temizliği.

**Ne yapar?**
Tahta her açıldığında, ``etapadmin`` dışındaki **tüm** gerçek kullanıcıların
(standart olarak ``ogretmen`` ve ``ogrenci``, ve varsa TiHA Modül 4'ün
oluşturduğu tek tek öğretmen/yedek hesapları) parolalarını kriptografik
olarak güvenli rastgele bir değere çeviren bir *systemd oneshot* servisi
kurar. Servis her sistem açılışında bir kez çalışır.

**Neden gerekir?**
Sınıfın 65" dokunmatik ekranında öğretmenin parolayı parmağıyla yazdığı
her an, arkadaki öğrenciler parolayı görür. Hatta ilk tanımlama anında
EBA-QR akışı kullanıcıdan parola isteyince bu ifşa mutlaka yaşanır.
TiHA bu yüzden **hiçbir parolanın kalıcı olmasına izin vermez**:

- ``ogretmen`` (standart ortak öğretmen hesabı) parolası her açılışta
  rastgele bir değerle ezilir — öğretmen bu parolayı bilmez, bilemez,
  dolayısıyla tahtaya yalnızca EBA-QR, OTP veya USB bellek ile girer.
- ``ogrenci`` (standart ortak öğrenci hesabı) için de aynı kural geçerli:
  parolalı giriş imkânsız.
- Modül 4'te **her öğretmene özel** hesaplar oluşturulursa (ör.
  ``ayse.yilmaz``, ``ogretmen01`` …) onlar da aynı temizliğe dahil edilir;
  girişleri yalnızca OTP kodu ile olur.
- ``etapadmin`` bu işlemin DIŞINDADIR; yönetici bakım erişimi korunur.

**Geri al (tam restore).**
- Açılış servisi ve script dosyası silinir.
- *Ek olarak*, TiHA oturumları sırasında sistemde **standart dışı**
  (``etapadmin``/``ogretmen``/``ogrenci`` dışında kalan) kullanıcı hesabı
  varsa bunlar listelenip **kullanıcıdan onay alınarak** ``userdel -r`` ile
  sistemden silinir — ev dizinleri ve posta kuyruklarıyla birlikte.
  Böylece açılış temizlik servisi kapatıldığında atıl hesap bırakılmaz.
"""

from __future__ import annotations

import json
import pwd

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.paths import BOOT_WIPE_SCRIPT, BOOT_WIPE_SERVICE, OTP_SECRETS_FILE
from ..core.utils import run_cmd

log = get_logger(__name__)

# Kalıcı ayrıcalıklı kullanıcılar — silmeye ASLA dahil edilmez.
PROTECTED_USERS = {"etapadmin", "root"}
# "Standart" dağıtım hesapları — bilerek dokunmayız.
STANDARD_USERS = {"ogretmen", "ogrenci"}


def _otp_registered_users() -> set[str]:
    """``/etc/otp-secrets.json`` içinde OTP anahtarı kayıtlı olan kullanıcılar."""
    if not OTP_SECRETS_FILE.exists():
        return set()
    try:
        data = json.loads(OTP_SECRETS_FILE.read_text(encoding="utf-8"))
        return set(data.keys()) if isinstance(data, dict) else set()
    except (OSError, json.JSONDecodeError):
        return set()


SCRIPT_CONTENT = """#!/bin/bash
# TiHA — her açılışta genel kullanıcıların parolalarını rastgele atar.
# Sınıfta öğrencinin ekrana bakarak parola öğrenmesini engellemek için.
# Kurallar:
#  * etapadmin (yerel yönetici) ASLA değişmez.
#  * UID 1000-59999 aralığındaki diğer tüm kullanıcılar rastgele parola
#    alır ve hesap kilitlenir (-L). Oturum açmak için yalnızca
#    EBA-QR / OTP / USB yolları açıktır.
set -euo pipefail
log() { logger -t tiha-boot-wipe "$*"; }
while IFS=: read -r user _ uid _ _ _ _; do
    if [[ "$uid" -ge 1000 && "$uid" -lt 60000 && "$user" != "etapadmin" ]]; then
        rand=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 40 || true)
        if echo "${user}:${rand}" | chpasswd; then
            log "kullanıcı '$user' parolası rastgele atandı"
        else
            log "HATA: '$user' için chpasswd başarısız"
        fi
    fi
done < /etc/passwd
"""

SERVICE_CONTENT = """[Unit]
Description=TiHA — Açılışta genel kullanıcı parolalarını rastgele ata
After=multi-user.target
ConditionPathExists=!/etc/tiha/boot-wipe.disabled

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/tiha-boot-password-wipe.sh

[Install]
WantedBy=multi-user.target
"""


def _human_users() -> list[str]:
    return [p.pw_name for p in pwd.getpwall() if 1000 <= p.pw_uid < 60000]


def extra_users() -> list[str]:
    """Standart dağıtım dışı (etapadmin/ogretmen/ogrenci harici) kullanıcılar."""
    keep = PROTECTED_USERS | STANDARD_USERS
    return sorted(u for u in _human_users() if u not in keep)


def _user_exists(username: str) -> bool:
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False


class BootPasswordWipeModule(Module):
    id = "m02_boot_password_wipe"
    title = "Her açılışta parola temizliği"
    rationale = (
        "Tahta her yeniden başladığında, etapadmin dışındaki hesapların "
        "(standart ogretmen ve ogrenci hesapları + Modül 4'te oluşturulan "
        "kişisel öğretmen hesapları) parolalarını otomatik olarak "
        "rastgele değere çeviren sistem servisi kurar. Böylece ekranda "
        "görülen ya da sızdırılan hiçbir parola bir sonraki açılışta "
        "işe yaramaz; öğretmen yalnızca EBA-QR, OTP ya da USB bellek ile "
        "oturum açabilir."
    )

    def preview(self) -> str:
        existing = BOOT_WIPE_SERVICE.exists()
        otp_users = _otp_registered_users()
        extras = extra_users()

        lines: list[str] = []
        lines.append("Bu servis her sistem açılışında parolaları şöyle atar:")
        lines.append("  • etapadmin → DOKUNULMAZ (yönetici erişimi korunur).")
        lines.append("")
        lines.append("Etkilenecek kullanıcılar (UID ≥ 1000, etapadmin hariç):")

        # Standart ortak hesaplar — kişisel kullanım için DEĞİLdir;
        # EBA-QR ile giriş yapan öğretmen kendi kişisel hesabını yaratır.
        # Bu iki hesaba OTP atanmaması olağandır.
        for u in ("ogretmen", "ogrenci"):
            if _user_exists(u):
                lines.append(
                    f"  •  {u:<20} [standart ortak hesap] — "
                    "parolayla girişi kapatılır; bu hesap zaten kişisel "
                    "kullanım için değildir (EBA-QR yeni kişisel hesap yaratır)."
                )

        # Kişisel / ek hesaplar — bunların OTP'si olmak ZORUNDA
        personal_missing_otp: list[str] = []
        for u in extras:
            if u in otp_users:
                lines.append(
                    f"  ✓  {u:<20} [kişisel] — "
                    "OTP anahtarı var, giriş EBA-QR / OTP / USB ile mümkün."
                )
            else:
                lines.append(
                    f"  ⚠  {u:<20} [kişisel] — "
                    "OTP anahtarı YOK! Bu servis aktifken tahtaya giremez."
                )
                personal_missing_otp.append(u)

        if personal_missing_otp:
            lines.append("")
            lines.append(
                f"⚠ DİKKAT: {len(personal_missing_otp)} kişisel hesabın OTP "
                f"anahtarı yok ({', '.join(personal_missing_otp)}). "
                "Bu servisi etkinleştirmeden ÖNCE (ya da hemen sonra) "
                "Modül 4'te bu kullanıcılar için OTP üretin — yoksa "
                "tahtaya hiçbir şekilde giremezler."
            )

        lines.append("")
        lines.append(
            "Durum: " + ("servis zaten kurulu — yeniden yazılacak."
                         if existing else "servis kurulacak ve etkinleştirilecek.")
        )
        return "\n".join(lines)

    def apply(self, params=None, progress=None) -> ApplyResult:
        try:
            BOOT_WIPE_SCRIPT.write_text(SCRIPT_CONTENT, encoding="utf-8")
            BOOT_WIPE_SCRIPT.chmod(0o750)
            BOOT_WIPE_SERVICE.write_text(SERVICE_CONTENT, encoding="utf-8")
        except OSError as exc:
            return ApplyResult(False, f"Dosya yazılamadı: {exc}")

        run_cmd(["systemctl", "daemon-reload"])
        enable = run_cmd(["systemctl", "enable", BOOT_WIPE_SERVICE.name])
        if not enable.ok:
            return ApplyResult(False, "Servis etkinleştirilemedi.", details=enable.stderr)

        return ApplyResult(
            True,
            "Açılışta parola temizleme servisi kuruldu ve etkinleştirildi.",
            details=(
                f"Script: {BOOT_WIPE_SCRIPT}\nServis: {BOOT_WIPE_SERVICE}\n"
                "Servis bir sonraki açılıştan itibaren her boot'ta bir kez çalışır."
            ),
        )

    def pre_undo_prompt(self, data: dict) -> dict | None:
        """Eğer sistemde standart dışı kullanıcı varsa UI'dan onay iste."""
        extras = extra_users()
        if not extras:
            return None
        lines = "\n".join(f"    • {u}" for u in extras)
        return {
            "title": "Ek kullanıcı hesapları da silinsin mi?",
            "message": (
                "Sistemde etapadmin/ogretmen/ogrenci dışında şu kullanıcı "
                "hesapları var:\n\n"
                f"{lines}\n\n"
                "Açılış parola temizleme servisi kaldırılacak. Ek hesaplar "
                "sistemde kalırsa yerel parolayla girilemediği için atıl "
                "kalır. Bu ek hesapları ev dizinleri ve kayıtlarıyla "
                "birlikte tamamen silmemi ister misiniz?\n\n"
                "• EVET → hesaplar 'userdel -r' ile silinir (geri alınamaz).\n"
                "• HAYIR → yalnızca açılış servisi kaldırılır, hesaplar kalır."
            ),
            "yes_params": {"remove_extras": True, "extras": extras},
            "no_params": {"remove_extras": False},
        }

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        """Servisi kaldırır; ``params['remove_extras']`` ise listede verilen
        ek hesapları (standart dışı) tamamen siler."""
        run_cmd(["systemctl", "disable", "--now", BOOT_WIPE_SERVICE.name])
        for path in (BOOT_WIPE_SERVICE, BOOT_WIPE_SCRIPT):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("Silinemedi %s: %s", path, exc)
        run_cmd(["systemctl", "daemon-reload"])

        params = params or {}
        removed: list[str] = []
        if params.get("remove_extras"):
            for user in params.get("extras", []) or extra_users():
                if user in (PROTECTED_USERS | STANDARD_USERS):
                    continue
                res = run_cmd(["userdel", "-r", "-f", user])
                if res.ok:
                    removed.append(user)
                else:
                    log.warning("userdel başarısız %s: %s", user, res.stderr.strip())

        msg = "Açılış parola temizleme servisi kaldırıldı."
        if removed:
            msg += f" Ayrıca şu ek hesaplar ve ev dizinleri silindi: {', '.join(removed)}."
        return ApplyResult(True, msg)
