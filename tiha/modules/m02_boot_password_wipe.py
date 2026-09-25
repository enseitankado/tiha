"""Modül 2 — Her açılışta parola temizliği.

Ne yapar?
Tahta her açıldığında, etapadmin dışındaki gerçek kullanıcıların (ortak
ogretmen/ogrenci hesapları ve varsa "Öğretmen PIN anahtarları" adımının
oluşturduğu kişisel öğretmen/yedek hesapları) parolalarını kriptografik
olarak güvenli rastgele bir değere çeviren bir systemd oneshot servisi
kurar. Servis her sistem açılışında bir kez çalışır.

Neden gerekir?
Bu, parolalı girişi tamamen kapatan isteğe bağlı bir sertleştirme
adımıdır. Tahtaya birinin sonradan elle parola atayıp paylaştığı
senaryoyu (ör. dokunmatik ekrana yazılması arka sıralardan görülebilen
bir parolanın kalıcı kalması) önler — atanan parola bir sonraki açılışta
otomatik olarak rastgele değişir.

- ogrenci (ortak hesap) parolası her açılışta rastgele bir değerle
  ezilir; bu hesaba yalnızca EBA-QR, PIN veya USB bellek ile girilebilir.
- ortak ogretmen hesabı bu işlemin DIŞINDADIR: parolası bilinen tek sınırlı
  hesaptır. Tahtanın pili bitip saati kayarsa PIN'ler çalışmaz, internet
  yoksa EBA QR da çalışmaz; o durumda etapadmin dışında parolayla
  girilebilecek tek hesap budur.
- "Öğretmen PIN anahtarları" adımında oluşturulan kişisel hesaplar (ör.
  ayse.yilmaz, ogretmen01 …) aynı temizliğe dahil edilir; girişleri
  yalnızca PIN kodu ile olur.
- etapadmin bu işlemin DIŞINDADIR; yönetici bakım erişimi korunur.

Bu adımı atlarsanız tahta varsayılan davranışını korur (yerel parola
ile giriş mümkündür); uygulamak ya da uygulamamak yöneticinin tercihidir.

Geri al (tam restore).
- Açılış servisi ve script dosyası silinir.
- Ek olarak, TiHA oturumları sırasında sistemde standart dışı
  (etapadmin/ogretmen/ogrenci dışında kalan) kullanıcı hesabı
  varsa bunlar listelenip kullanıcıdan onay alınarak userdel -r ile
  sistemden silinir — ev dizinleri ve posta kuyruklarıyla birlikte.
  Böylece açılış temizlik servisi kapatıldığında atıl hesap bırakılmaz.
"""

from __future__ import annotations

import json
import pwd

from ..core.i18n import t
from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.paths import BOOT_WIPE_SCRIPT, BOOT_WIPE_SERVICE, OTP_SECRETS_FILE
from ..core.utils import run_cmd

log = get_logger(__name__)

# Kalıcı ayrıcalıklı kullanıcılar — silmeye ASLA dahil edilmez.
PROTECTED_USERS = {"etapadmin", "root"}
# Açılışta parolası sıfırlanmayan hesaplar: yönetici ve parolası bilinen
# tek sınırlı (yedek giriş) hesabı olan ortak ogretmen.
WIPE_EXCLUDED_USERS = ("root", "etapadmin", "ogretmen")
# "Standart" dağıtım hesapları — bilerek dokunmayız.
STANDARD_USERS = {"ogretmen", "ogrenci"}


def _otp_registered_users() -> set[str]:
    """``/etc/otp-secrets.json`` içinde PIN anahtarı kayıtlı olan kullanıcılar."""
    if not OTP_SECRETS_FILE.exists():
        return set()
    try:
        data = json.loads(OTP_SECRETS_FILE.read_text(encoding="utf-8"))
        return set(data.keys()) if isinstance(data, dict) else set()
    except (OSError, json.JSONDecodeError):
        return set()


SCRIPT_CONTENT = """#!/bin/bash
# TiHA — her açılışta etapadmin dışındaki kullanıcıların parolalarını
# rastgele bir değerle ezer. Birisi sonradan elle parola atasa bile bir
# sonraki açılışta o parola işe yaramaz hâle gelir; tahtaya yalnızca
# EBA-QR / PIN / USB yollarıyla girilebilir.
# Kurallar:
#  * etapadmin (yerel yönetici) ASLA değişmez.
#  * ogretmen (ortak öğretmen) de değişmez: parolası bilinen tek sınırlı
#    hesaptır; pil bitip PIN'ler çalışmazsa ve QR için internet yoksa
#    tahtaya girilebilen yedek yoldur.
#  * UID 1000-59999 aralığındaki diğer tüm kullanıcılar rastgele parola
#    alır.
# Parola hazır hash olarak yazılır (chpasswd -e): düz parolayla chpasswd
# PAM'in common-password zincirinden geçer ve ETAP'ın zinciri
# (pam_script + pam_unix use_authtok) bunu her hesapta reddeder.
set -euo pipefail
log() { logger -t tiha-boot-wipe "$*"; }
while IFS=: read -r user _ uid _ _ _ _; do
    if [[ "$uid" -ge 1000 && "$uid" -lt 60000 && "$user" != "etapadmin" && "$user" != "ogretmen" ]]; then
        rand=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 40 || true)
        hash=$(printf '%s' "$rand" | openssl passwd -6 -stdin 2>/dev/null || true)
        # openssl yoksa hiçbir parolanın eşleşemeyeceği kilitli değer.
        [[ "$hash" == '$6$'* ]] || hash="!${rand}"
        if err=$(printf '%s:%s\\n' "$user" "$hash" | chpasswd -e 2>&1); then
            log "kullanıcı '$user' parolası rastgele atandı"
        else
            log "HATA: '$user' için chpasswd başarısız: ${err}"
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


def _account_report() -> str:
    """Sonuç penceresi için: açılış betiğinin kuralına göre (UID
    1000–59999, etapadmin hariç) hangi hesapların parolası sıfırlanacak,
    hangilerine dokunulmayacak."""
    otp_users = _otp_registered_users()
    wiped = sorted(u for u in _human_users() if u not in WIPE_EXCLUDED_USERS)
    lines = [t("m02.apply.wiped_header", count=len(wiped))]
    if not wiped:
        lines.append(t("m02.apply.wiped_none"))
    for user in wiped:
        # Ortak ogretmen/ogrenci hesaplarına EBA QR ile de girilir; PIN
        # uyarısı yalnız kişisel hesaplar için anlamlı.
        if user not in STANDARD_USERS and user not in otp_users:
            lines.append(t("m02.apply.wiped_user_no_pin", user=user))
        else:
            lines.append(f"  - {user}")
    lines += ["", t("m02.apply.kept_header")]
    lines += [f"  - {user}" for user in ("root", "etapadmin")]
    if _user_exists("ogretmen"):
        lines.append(t("m02.apply.kept_ogretmen"))
    lines += ["", t("m02.apply.footer")]
    return "\n".join(lines)


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
    title = t("m02.title")
    sidebar_title = t("m02.sidebar_title")
    apply_hint = t("m02.apply_hint")
    rationale = t("m02.rationale")

    def preview(self) -> str:
        existing = BOOT_WIPE_SERVICE.exists()
        otp_users = _otp_registered_users()
        extras = extra_users()

        # m08 stiliyle: hizalı key-value başlık, sonra girintili dash liste.
        # Tablo görünümü kullanılmıyor - yatay kaydırma oluşmasın diye satır
        # kırılabilen serbest metin biçimindedir.
        lines: list[str] = []
        status = (
            t("m02.preview.service_existing") if existing
            else t("m02.preview.service_new")
        )
        lines.append(t("m02.preview.service_status", status=status))
        lines.append("")
        lines.append(t("m02.preview.intro"))
        lines.append("")
        lines.append(t("m02.preview.protected_header"))
        lines.append("  - root")
        lines.append("  - etapadmin")
        if _user_exists("ogretmen"):
            lines.append(t("m02.apply.kept_ogretmen"))
        lines.append("")

        # Ortak hesaplar
        ortak = [u for u in ("ogrenci",) if _user_exists(u)]
        if ortak:
            lines.append(t("m02.preview.shared_header"))
            for u in ortak:
                lines.append(f"  - {u}")
            lines.append("")

        # Kişisel hesaplar
        missing: list[str] = []
        if extras:
            lines.append(t("m02.preview.personal_header"))
            for u in extras:
                if u in otp_users:
                    lines.append(t("m02.preview.personal_with_pin", user=u))
                else:
                    lines.append(t("m02.preview.personal_without_pin", user=u))
                    missing.append(u)
            lines.append("")

        if missing:
            lines.append(t(
                "m02.preview.missing_warning",
                count=len(missing), users=", ".join(missing),
            ))
        return "\n".join(lines)

    def apply(self, params=None, progress=None) -> ApplyResult:
        try:
            BOOT_WIPE_SCRIPT.write_text(SCRIPT_CONTENT, encoding="utf-8")
            BOOT_WIPE_SCRIPT.chmod(0o750)
            BOOT_WIPE_SERVICE.write_text(SERVICE_CONTENT, encoding="utf-8")
        except OSError as exc:
            return ApplyResult(False, t("m02.apply.write_failed", error=exc))

        run_cmd(["systemctl", "daemon-reload"])
        enable = run_cmd(["systemctl", "enable", BOOT_WIPE_SERVICE.name])
        if not enable.ok:
            return ApplyResult(False, t("m02.apply.enable_failed"), details=enable.stderr)

        return ApplyResult(True, t("m02.apply.done"), details=_account_report())

    def pre_undo_prompt(self, data: dict) -> dict | None:
        """Eğer sistemde standart dışı kullanıcı varsa UI'dan onay iste."""
        extras = extra_users()
        if not extras:
            return None
        lines = "\n".join(f"    • {u}" for u in extras)
        return {
            "title": t("m02.undo_prompt.title"),
            "message": t("m02.undo_prompt.message", users=lines),
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

        msg = t("m02.undo.done")
        if removed:
            msg += t("m02.undo.removed_extras", users=", ".join(removed))
        return ApplyResult(True, msg)
