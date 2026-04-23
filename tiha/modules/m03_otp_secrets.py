"""Modül 3 — OTP anahtarlarını toplu hazırla.

**Ne yapar?**
Öğretmen ad-soyad listesinden her öğretmen için bir kullanıcı hesabı
oluşturur (yoksa) ve her hesap için bir TOTP (``pyotp`` ile üretilen
``BASE32`` secret) oluşturur. Bu anahtarlar ETAP'ın PAM modülünün okuduğu
``/etc/otp-secrets.json`` dosyasına yazılır. Ayrıca isteğe bağlı olarak,
okula sonradan atanacak öğretmenler için belirlenen sayıda yedek hesap
(``ogretmen01``, ``ogretmen02``, …) oluşturulur.

**Neden gerekir?**
Normalde öğretmen OTP anahtarını ``eta-otp-lock`` uygulamasıyla kendisi
üretir; ama bu uygulama çalışmadan önce kullanıcının mevcut parolasını
girmesini ister. TiHA Modül 1 ve 2, bu parolaları rastgele değerlerle
bozduğu için öğretmen anahtar üretemez. Bu yüzden imaj öncesinde anahtarları
toplu üretir, her öğretmene kendisininkini (QR veya düz metin olarak)
özelden teslim ederiz. Böylece öğretmen dağıtılan bütün tahtalarda Google
Authenticator benzeri bir uygulamadan üretilen 6 haneli kodla oturum açar.

**Geri al.** Eklenmiş kullanıcı hesapları ve ``otp-secrets.json`` girdileri
kaldırılır; önceki ``otp-secrets.json`` yedeği geri yüklenir.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pyotp

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.paths import OTP_SECRETS_FILE
from ..core.utils import backup_file, restore_file, run_cmd, user_exists

log = get_logger(__name__)

# Türkçe karakter → ASCII
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
    result = run_cmd(
        ["useradd", "--create-home", "--shell", "/bin/bash", username]
    )
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
    OTP_SECRETS_FILE.write_text(json.dumps(secrets, indent=2, ensure_ascii=False), encoding="utf-8")
    OTP_SECRETS_FILE.chmod(0o600)
    # sahiplik root:root — PAM modülü beklentisiyle uyum.
    import os
    os.chown(OTP_SECRETS_FILE, 0, 0)


class OTPSecretsModule(Module):
    id = "m03_otp_secrets"
    title = "Öğretmen OTP anahtarları"
    rationale = (
        "Her öğretmen için 6 haneli tek kullanımlık parola (TOTP) üreten "
        "güvenlik anahtarı oluşturur ve sisteme yazar. Bu anahtar her "
        "öğretmene özel olarak paylaşılır; öğretmen, anahtarını Google "
        "Authenticator vb. bir uygulamaya ekleyerek imajlanmış tüm tahtalarda "
        "oturum açabilir. Ayrıca sonradan okula atanacak öğretmenler için "
        "yedek hesaplar oluşturulabilir."
    )

    def preview(self) -> str:
        existing = load_secrets()
        if not existing:
            return "Henüz kayıtlı OTP anahtarı yok."
        return "Hâlihazırda kayıtlı kullanıcılar:\n  • " + "\n  • ".join(sorted(existing))

    def apply(self, params: dict | None = None) -> ApplyResult:
        params = params or {}
        raw_list: str = params.get("teacher_names", "")
        reserve: int = int(params.get("reserve_count", 0) or 0)

        teacher_names = [line.strip() for line in raw_list.splitlines() if line.strip()]

        state = self.ensure_state_dir()
        backup_file(OTP_SECRETS_FILE, state)

        secrets = load_secrets()
        created: list[tuple[str, str]] = []  # (kullanici, secret)

        # Listedeki öğretmenler
        for name in teacher_names:
            user = normalize_username(name)
            if not user:
                continue
            create_user(user)
            secret = pyotp.random_base32()
            secrets[user] = secret
            created.append((user, secret))

        # Yedek hesaplar
        for i in range(1, reserve + 1):
            user = f"ogretmen{i:02d}"
            create_user(user)
            secret = pyotp.random_base32()
            secrets[user] = secret
            created.append((user, secret))

        if not created:
            return ApplyResult(False, "Herhangi bir öğretmen/yedek girilmedi; işlem yapılmadı.")

        save_secrets(secrets)

        # Kopyalanabilir tam liste
        copyable = "\n".join(f"{u}\t{s}" for u, s in created)
        details = (
            f"{len(created)} hesap için OTP anahtarı oluşturuldu.\n"
            f"Dosya: {OTP_SECRETS_FILE}\n"
            "Aşağıdaki listeyi her öğretmene yalnızca özelden (ör. gizli not, e-posta) "
            "teslim edin. KULLANICI ADI ile ANAHTAR arasında sekme karakteri vardır."
        )
        return ApplyResult(
            success=True,
            summary=f"{len(created)} OTP anahtarı üretildi.",
            details=details,
            copyable=copyable,
        )

    def undo(self, data: dict) -> ApplyResult:
        state = self.state_dir
        backup = state / OTP_SECRETS_FILE.name
        if backup.exists():
            restore_file(backup, OTP_SECRETS_FILE)
            return ApplyResult(True, "Önceki otp-secrets.json geri yüklendi.")
        # Yedek yoksa dosyayı sil (önceden yoktu)
        OTP_SECRETS_FILE.unlink(missing_ok=True)
        return ApplyResult(True, "otp-secrets.json kaldırıldı (yedek yoktu).")
