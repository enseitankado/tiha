"""Modül 3 (wizard 4. adım) — Öğretmen OTP anahtarlarını toplu hazırla.

**Ne yapar?**
Girdiğiniz öğretmen ad-soyad listesinden her öğretmen için bir kullanıcı
hesabı oluşturur (zaten varsa geçer), her hesaba kriptografik olarak
güvenli bir TOTP (Time-based One-Time Password) ``BASE32`` anahtarı
atar ve bu anahtarları Pardus ETAP'ın PAM modülünün okuduğu
``/etc/otp-secrets.json`` dosyasına yazar. Ayrıca isteğe bağlı olarak,
sonradan okula atanacak öğretmenler için belirlediğiniz sayıda yedek
hesap (``ogretmen01``, ``ogretmen02`` …) oluşturur.

Üretim, yerleşim ve JSON formatı enseitankado/eta-otp-cli aracıyla
birebir uyumludur: aynı ``pyotp.random_base32()`` üreticisi, aynı
dosya konumu ve şema (``{"kullanici":"BASE32"}``). İsterseniz
``eta-otp-cli goster <kullanici>`` komutuyla sonradan QR kodu tekrar
görüntüleyebilirsiniz.

**Neden gerekir?**
Normalde öğretmen TOTP anahtarını tahtadaki ``eta-otp-lock`` uygulamasıyla
kendisi üretir; ancak uygulama açılmadan önce kullanıcının mevcut yerel
parolasını girmesini ister. TiHA'nın 2. ve 3. adımları (parola kilidi +
açılışta parola temizliği) bu yerel parolayı kasıtlı olarak geçersiz
kıldığı için öğretmen, tahtadaki OTP üreticisini açamaz. Bu yüzden
anahtarları imaj öncesinde merkezî olarak üretir, her öğretmene
özel olarak teslim ederiz. Öğretmen anahtarını Google Authenticator
(veya benzeri) uygulamaya eklediği andan itibaren dağıtılmış tüm
tahtalarda 6 haneli kodla oturum açabilir.

**Geri al.** Önceki ``/etc/otp-secrets.json`` yedeği geri yüklenir.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote

import pyotp

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.paths import OTP_SECRETS_FILE
from ..core.utils import backup_file, restore_file, run_cmd, user_exists

log = get_logger(__name__)

# Google Authenticator vb. uygulamalara gömülen bilgi:
# otpauth://totp/<issuer>:<user>?secret=...&issuer=<issuer>&digits=6&period=30
OTP_ISSUER = "Pardus ETAP"

_TR_MAP = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


def normalize_username(full_name: str) -> str:
    """'Ayşe Yılmaz' -> 'ayse.yilmaz' biçiminde kullanıcı adı."""
    ascii_name = full_name.translate(_TR_MAP)
    ascii_name = re.sub(r"[^A-Za-z0-9 ]", "", ascii_name).strip().lower()
    parts = ascii_name.split()
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]}.{parts[-1]}"


def create_user(username: str) -> bool:
    """Sisteme kullanıcı ekler. Varsa sessizce geçer."""
    if user_exists(username):
        return True
    result = run_cmd(["useradd", "--create-home", "--shell", "/bin/bash", username])
    if not result.ok:
        log.error("Kullanıcı eklenemedi '%s': %s", username, result.stderr.strip())
    # Parolayı rastgele yapıp hesap kilitlenir (OTP ile girilecek)
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
    import os
    os.chown(OTP_SECRETS_FILE, 0, 0)


def otpauth_url(username: str, secret: str) -> str:
    """Google Authenticator/Authy vb.'in kabul ettiği otpauth:// URL'si."""
    issuer_enc = quote(OTP_ISSUER)
    user_enc = quote(f"{OTP_ISSUER}:{username}")
    return (
        f"otpauth://totp/{user_enc}?secret={secret}"
        f"&issuer={issuer_enc}&digits=6&period=30"
    )


class OTPSecretsModule(Module):
    id = "m03_otp_secrets"
    title = "Öğretmen OTP anahtarları"
    apply_hint = (
        "Listedeki ve yedek hesaplar için OTP anahtarları üretilir."
    )
    rationale = (
        "Her öğretmen için 6 haneli tek kullanımlık kod (TOTP) üreten "
        "güvenlik anahtarı oluşturur. Öğretmen bu anahtarı kendi "
        "Google Authenticator / Microsoft Authenticator / Authy benzeri "
        "uygulamasına (QR okutarak ya da elle girerek) ekler ve bundan "
        "böyle dağıtılmış tüm tahtalarda 6 haneli kodla oturum açar. "
        "Dosya formatı ve secret üretimi enseitankado/eta-otp-cli ile "
        "bire bir uyumludur."
    )

    def preview(self) -> str:
        import pwd as _pwd

        existing = load_secrets()
        # Sistemdeki kişisel hesaplar: UID 1000+, etapadmin/ogretmen/ogrenci hariç.
        # ogretmen ve ogrenci standart ortak hesaplardır; onlara OTP üretmek
        # genellikle anlamsızdır (kişisel kullanım için değiller).
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
        if has_otp:
            lines.append("✓ OTP anahtarı KURULU kişisel hesaplar:")
            lines.extend(f"    • {u}" for u in has_otp)
        else:
            lines.append("Henüz kişisel OTP anahtarı kayıtlı değil.")

        if missing_otp:
            lines.append("")
            lines.append(
                "⚠ Kişisel hesabı olan ama OTP anahtarı OLMAYAN kullanıcılar:"
            )
            lines.extend(f"    • {u}" for u in missing_otp)
            lines.append("")
            lines.append(
                "Bu hesaplar Modül 3 aktifken tahtaya hiç giremeyecekler. "
                "Anahtar üretmek için aşağıdaki metin kutusuna kullanıcı "
                "adlarını (her satıra bir tane) veya AD SOYAD biçiminde "
                "tam isimleri yazın."
            )

        if orphan_secrets:
            lines.append("")
            lines.append(
                "ℹ Sistemde hesabı olmayan OTP kayıtları "
                f"(hesap silinmiş olabilir): {', '.join(orphan_secrets)}"
            )

        return "\n".join(lines) if lines else "Henüz hiç kişisel hesap yok."

    def apply(self, params=None, progress=None) -> ApplyResult:
        params = params or {}
        raw_list: str = params.get("teacher_names", "")
        reserve: int = int(params.get("reserve_count", 0) or 0)

        teacher_names = [line.strip() for line in raw_list.splitlines() if line.strip()]

        state = self.ensure_state_dir()
        backup_file(OTP_SECRETS_FILE, state)

        secrets = load_secrets()
        # Rapor için: (görsel_ad, username, secret)
        created: list[tuple[str, str, str]] = []

        for name in teacher_names:
            user = normalize_username(name)
            if not user:
                continue
            create_user(user)
            secret = pyotp.random_base32()
            secrets[user] = secret
            created.append((name, user, secret))

        for i in range(1, reserve + 1):
            user = f"ogretmen{i:02d}"
            display = f"(yedek hesap)"
            create_user(user)
            secret = pyotp.random_base32()
            secrets[user] = secret
            created.append((display, user, secret))

        if not created:
            return ApplyResult(
                False,
                "Liste boş — öğretmen eklemediniz ve yedek hesap sayısı 0.",
                details="Lütfen en az bir isim girin veya yedek hesap sayısını artırın.",
            )

        save_secrets(secrets)

        # Rapor — dokunmatik ekranda okunabilir ve panoya kopyalanabilir biçim
        report_lines = []
        report_lines.append("─" * 76)
        report_lines.append(f"  {len(created)} öğretmen/yedek hesap için OTP anahtarı üretildi.")
        report_lines.append(f"  Dosya: {OTP_SECRETS_FILE}")
        report_lines.append(f"  Üretici: Issuer = \"{OTP_ISSUER}\", 6 hane, 30 sn periyot.")
        report_lines.append("─" * 76)
        for idx, (display, user, secret) in enumerate(created, 1):
            url = otpauth_url(user, secret)
            report_lines.append("")
            report_lines.append(f"[{idx:02d}]  {display}")
            report_lines.append(f"     Kullanıcı adı : {user}")
            report_lines.append(f"     OTP anahtarı  : {secret}")
            report_lines.append(f"     otpauth URL   : {url}")
        report_lines.append("")
        report_lines.append("─" * 76)
        report_lines.append(
            "Kullanım: öğretmenler bu anahtarı Google Authenticator vb. uygulamaya\n"
            "manuel girebilir ya da otpauth URL'sini çevrimdışı bir QR üreticide\n"
            "(veya öğretmenin telefonunda yazı olarak) tarayabilir. Anahtarları\n"
            "yalnızca özelden (şifreli mesaj, gizli dağıtım listesi) teslim edin."
        )
        copyable = "\n".join(report_lines)

        details = (
            f"{len(created)} hesap için anahtar üretildi. Tüm liste aşağıda;\n"
            "\"Panoya kopyala\" ile alıp istediğiniz şekilde paylaşabilirsiniz."
        )
        return ApplyResult(
            success=True,
            summary=f"{len(created)} OTP anahtarı üretildi ve {OTP_SECRETS_FILE} dosyasına yazıldı.",
            details=details,
            copyable=copyable,
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        state = self.state_dir
        backup = state / OTP_SECRETS_FILE.name
        if backup.exists():
            restore_file(backup, OTP_SECRETS_FILE)
            return ApplyResult(True, "Önceki otp-secrets.json geri yüklendi.")
        OTP_SECRETS_FILE.unlink(missing_ok=True)
        return ApplyResult(True, "otp-secrets.json kaldırıldı (yedek yoktu).")
