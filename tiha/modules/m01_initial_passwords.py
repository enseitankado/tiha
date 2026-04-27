"""Modül 1 — Kullanıcı parolaları.

Ne yapar?
- Sistemdeki gerçek kullanıcıların (UID ≥ 1000) etapadmin dışındaki
  hepsine kriptografik olarak güvenli rastgele parola atar ve bu hesapları
  usermod -L ile kilitler.
- Kullanıcının belirlediği yeni root ve etapadmin parolalarını
  uygular.

Neden gerekir?
Sınıftaki 65" dokunmatik ekranda öğrencilerin izlediği ortamda kişisel
parola tanımlamak, parolanın görsel olarak ifşa olmasına yol açar. Bu adım
imaj alınmadan önce tüm genel hesapları kilitleyerek, tahta dağıtıldığında
öğrencilerin bu hesaplarla ekrana parola yazmasının yolunu kapatır.

Geri al. Önceki /etc/shadow yedeği geri yüklenir.
"""

from __future__ import annotations

import json
import pwd
from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.utils import backup_file, random_password, restore_file, run_cmd, user_exists

log = get_logger(__name__)

SHADOW = Path("/etc/shadow")

# Sistem kullanıcıları (silinebilir)
REMOVABLE_USERS = {"ogrenci", "ogretmen"}


def _human_users() -> list[str]:
    """UID 1000-59999 aralığındaki (gerçek kullanıcı) isimlerin listesi."""
    users = []
    for entry in pwd.getpwall():
        if 1000 <= entry.pw_uid < 60000:
            users.append(entry.pw_name)
    return users


def _set_password(user: str, password: str) -> bool:
    """``chpasswd`` ile parola atar. Başarıyı bool döner."""
    result = run_cmd(["chpasswd"], input_data=f"{user}:{password}\n")
    if not result.ok:
        log.error("'%s' için parola atanamadı: %s", user, result.stderr.strip())
    return result.ok


def _lock_user(user: str) -> bool:
    result = run_cmd(["usermod", "-L", user])
    return result.ok


def _unlock_user(user: str) -> bool:
    result = run_cmd(["usermod", "-U", user])
    return result.ok


def backup_user_info(username: str, state_dir: Path) -> bool:
    """Kullanıcı bilgilerini (UID, home dir, vb.) yedekler."""
    if not user_exists(username):
        return False

    try:
        user_info = pwd.getpwnam(username)
        backup_data = {
            "username": user_info.pw_name,
            "uid": user_info.pw_uid,
            "gid": user_info.pw_gid,
            "home_dir": user_info.pw_dir,
            "shell": user_info.pw_shell,
            "gecos": user_info.pw_gecos,
        }

        backup_file_path = state_dir / f"{username}_backup.json"
        backup_file_path.write_text(json.dumps(backup_data, indent=2), encoding="utf-8")
        return True
    except Exception as exc:
        log.error("Kullanıcı bilgileri yedeklenemedi %s: %s", username, exc)
        return False


def remove_user_with_backup(username: str, state_dir: Path) -> bool:
    """Kullanıcıyı bilgilerini yedekleyerek siler."""
    if not user_exists(username):
        return False

    # Önce yedekle
    if not backup_user_info(username, state_dir):
        log.error("Kullanıcı yedeklenemedi, silme işlemi iptal edildi: %s", username)
        return False

    # Sil
    result = run_cmd(["deluser", "--remove-home", username])
    if result.ok:
        log.info("Kullanıcı başarıyla silindi: %s", username)
        return True
    else:
        log.error("Kullanıcı silinemedi %s: %s", username, result.stderr)
        return False


def restore_user(username: str, state_dir: Path) -> bool:
    """Yedekten kullanıcıyı geri yükler."""
    backup_file_path = state_dir / f"{username}_backup.json"
    if not backup_file_path.exists():
        return False

    try:
        backup_data = json.loads(backup_file_path.read_text(encoding="utf-8"))

        # Kullanıcı oluştur
        result = run_cmd([
            "useradd",
            "--uid", str(backup_data["uid"]),
            "--gid", str(backup_data["gid"]),
            "--home-dir", backup_data["home_dir"],
            "--shell", backup_data["shell"],
            "--comment", backup_data["gecos"],
            "--create-home",
            username
        ])

        if result.ok:
            log.info("Kullanıcı geri yüklendi: %s", username)
            return True
        else:
            log.error("Kullanıcı geri yüklenemedi %s: %s", username, result.stderr)
            return False
    except Exception as exc:
        log.error("Kullanıcı geri yükleme hatası %s: %s", username, exc)
        return False


def get_removable_user_status() -> dict[str, bool]:
    """Silinebilir kullanıcıların mevcut durumunu döndürür."""
    return {user: user_exists(user) for user in REMOVABLE_USERS}


class InitialPasswordsModule(Module):
    id = "m01_initial_passwords"
    title = "Kullanıcı parolaları"
    apply_hint = (
        "Belirlenen root/etapadmin parolaları uygulanır; diğer hesaplar kilitlenir."
    )
    rationale = (
        "Sistem kullanıcı hesaplarını güvenlik gereksinimlerinize göre yönetir. "
        "Gereksiz hesapları (ogrenci, ogretmen) tamamen silme seçeneği sunar. "
        "Root, etapadmin ve isteğe bağlı olarak ogretmen hesabı için güçlü "
        "parolalar belirleyebilirsiniz. Parolalar ekranda görülebilir formatta "
        "girilebilir ve onay kutuları ile doğrulanır."
    )

    def preview(self) -> str:
        user_status = get_removable_user_status()

        lines: list[str] = []
        lines.append("Kullanıcı Yönetimi ve Parola Ayarları:")
        lines.append("")

        # Silinebilir kullanıcı durumu
        removable_exists = any(user_status.values())
        if removable_exists:
            lines.append("🗑️ Sistem Kullanıcıları:")
            for user, exists in user_status.items():
                status = "✓ mevcut" if exists else "⨯ yok"
                lines.append(f"    • {user}: {status}")
            lines.append("")
            if removable_exists:
                lines.append("Bu kullanıcılar isteğe bağlı olarak silinebilir.")
        else:
            lines.append("✓ Sistem kullanıcıları (ogrenci, ogretmen) zaten yok")

        lines.append("")
        lines.append("🔒 Parola Belirleme:")
        lines.append("    • root parolası")
        lines.append("    • etapadmin parolası")
        if user_status.get("ogretmen", False):
            lines.append("    • ogretmen parolası (isteğe bağlı)")

        return "\n".join(lines)

    def apply(self, params: dict | None = None, progress=None) -> ApplyResult:
        params = params or {}
        root_pw = params.get("root_password", "")
        root_pw_conf = params.get("root_password_confirm", "")
        admin_pw = params.get("admin_password", "")
        admin_pw_conf = params.get("admin_password_confirm", "")
        teacher_pw = params.get("teacher_password", "")
        teacher_pw_conf = params.get("teacher_password_confirm", "")

        if not root_pw or not admin_pw:
            return ApplyResult(
                success=False,
                summary="Eksik giriş: root ve etapadmin parolaları girilmeli.",
            )

        # Çift onay doğrulaması
        if root_pw != root_pw_conf:
            return ApplyResult(False, "root parolası ile doğrulaması eşleşmiyor.")
        if admin_pw != admin_pw_conf:
            return ApplyResult(False, "etapadmin parolası ile doğrulaması eşleşmiyor.")
        if teacher_pw and teacher_pw != teacher_pw_conf:
            return ApplyResult(False, "ogretmen parolası ile doğrulaması eşleşmiyor.")
        if len(root_pw) < 8 or len(admin_pw) < 8:
            return ApplyResult(False, "root ve etapadmin parolaları en az 8 karakter olmalıdır.")
        if teacher_pw and len(teacher_pw) < 8:
            return ApplyResult(False, "ogretmen parolası en az 8 karakter olmalıdır.")

        state = self.ensure_state_dir()
        # Önce /etc/shadow yedeği al — undo için tek başına yeterli.
        backup_file(SHADOW, state)

        # Sistem kullanıcılarını silme seçeneği
        remove_system_users = params.get("remove_system_users", False)
        removed_users = []

        if remove_system_users:
            user_status = get_removable_user_status()
            for username, exists in user_status.items():
                if exists:
                    if remove_user_with_backup(username, state):
                        removed_users.append(username)
                        log.info("Sistem kullanıcısı silindi: %s", username)

        # 1) Gerçek kullanıcıları kilitle
        locked: list[str] = []
        failed: list[str] = []
        for user in _human_users():
            if user == "etapadmin":
                continue
            if _set_password(user, random_password(40)) and _lock_user(user):
                locked.append(user)
            else:
                failed.append(user)

        # 2) root, etapadmin ve ogretmen parolalarını ata
        ok_root = _set_password("root", root_pw)
        ok_admin = _set_password("etapadmin", admin_pw)
        ok_teacher = True
        if teacher_pw and user_exists("ogretmen"):
            ok_teacher = _set_password("ogretmen", teacher_pw)
            # Öğretmen hesabının kilidi açılmalı (parola ile giriş yapılabilsin)
            _unlock_user("ogretmen")

        details_lines = []
        if removed_users:
            details_lines.append("Silinen sistem kullanıcıları: " + ", ".join(removed_users))
        if locked:
            details_lines.append("Kilitlenen hesaplar: " + ", ".join(locked))
        if failed:
            details_lines.append("Kilitlenemeyen hesaplar: " + ", ".join(failed))
        details_lines.append(f"root parolası: {'atandı' if ok_root else 'ATANAMADI'}")
        details_lines.append(f"etapadmin parolası: {'atandı' if ok_admin else 'ATANAMADI'}")
        if teacher_pw:
            details_lines.append(f"ogretmen parolası: {'atandı' if ok_teacher else 'ATANAMADI'}")

        overall = ok_root and ok_admin and ok_teacher and not failed

        summary_parts = []
        if removed_users:
            summary_parts.append(f"{len(removed_users)} sistem kullanıcısı silindi")
        if locked:
            summary_parts.append(f"{len(locked)} hesap kilitlendi")

        password_parts = []
        if ok_root and ok_admin:
            password_parts.append("root/etapadmin")
        if teacher_pw and ok_teacher:
            password_parts.append("ogretmen")
        if password_parts:
            summary_parts.append(f"{'/'.join(password_parts)} parolaları atandı")

        return ApplyResult(
            success=overall,
            summary="; ".join(summary_parts) + "." if overall and summary_parts
                    else "Bazı işlemler başarısız oldu, ayrıntılara bakın.",
            details="\n".join(details_lines),
            data={"removed_users": removed_users}
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        state = self.state_dir
        backup = state / "shadow"
        if not backup.exists():
            return ApplyResult(False, "Yedek /etc/shadow bulunamadı; geri alınamıyor.")

        restored_users = []
        removed_users = data.get("removed_users", [])

        # Silinen kullanıcıları geri yükle
        for username in removed_users:
            if restore_user(username, state):
                restored_users.append(username)

        try:
            restore_file(backup, SHADOW)
            # Kilitli kullanıcıları da aç (izole olaylar için)
            for user in _human_users():
                if user != "etapadmin":
                    _unlock_user(user)

            summary_parts = ["Önceki /etc/shadow durumu geri yüklendi"]
            if restored_users:
                summary_parts.append(f"{len(restored_users)} kullanıcı geri yüklendi: {', '.join(restored_users)}")

            return ApplyResult(True, "; ".join(summary_parts) + ".")
        except OSError as exc:
            log.error("Shadow geri yükleme hatası: %s", exc)
            return ApplyResult(False, f"Geri yükleme başarısız: {exc}")

    # -----------------------------------------------------------------
    # Sistem Kullanıcı Yönetimi Fonksiyonları
    # -----------------------------------------------------------------

    def can_remove_system_users(self) -> bool:
        """Sistem kullanıcıları silme düğmesinin aktif olup olmayacağını belirler."""
        user_status = get_removable_user_status()
        return any(user_status.values())

    def remove_system_users_action(self, params: dict | None = None) -> ApplyResult:
        """Sistem kullanıcılarını (ogrenci, ogretmen) siler."""
        user_status = get_removable_user_status()
        existing_users = [user for user, exists in user_status.items() if exists]

        if not existing_users:
            return ApplyResult(
                False,
                "Silinecek sistem kullanıcısı bulunamadı.",
                details="ogrenci ve ogretmen kullanıcıları zaten yok."
            )

        state = self.ensure_state_dir()
        removed_users = []

        for username in existing_users:
            if remove_user_with_backup(username, state):
                removed_users.append(username)

        if removed_users:
            return ApplyResult(
                True,
                f"{len(removed_users)} sistem kullanıcısı silindi: {', '.join(removed_users)}",
                details="Kullanıcılar güvenli şekilde yedeklendi ve kaldırıldı.\n"
                       "Geri alma işlevi ile geri yüklenebilir.",
                data={"removed_users": removed_users}
            )
        else:
            return ApplyResult(
                False,
                "Hiçbir kullanıcı silinemedi.",
                details="Detaylar için /var/log/tiha/tiha.log dosyasına bakın."
            )
