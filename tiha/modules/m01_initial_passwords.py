"""Modül 1 — Kullanıcı parolaları.

Ne yapar?
- Kullanıcının belirlediği yeni `root` ve `etapadmin` parolalarını uygular.
- İsteğe bağlı olarak `ogretmen` hesabı için parola belirler (alan boş
  bırakılırsa hesaba dokunulmaz).
- İsteğe bağlı olarak `ogretmen`/`ogrenci` ortak hesaplarını sistemden
  tamamen siler.
- Parolalar SHA-512 hash olarak doğrudan `/etc/shadow` dosyasına yazılır.

Diğer hesaplara dokunulmaz; bu adım kimseyi kilitlemez.

Neden gerekir?
İmajdan onlarca tahtaya dağıtılacı bir kurulumda root ve etapadmin
parolalarının siz tarafından bilinçli olarak belirlenmiş olması gerekir;
varsayılan/önceden bilinen parolaların imajda kalmaması için.

Teknik not: Bu modül `chpasswd` yerine doğrudan `/etc/shadow` dosyasını
düzenler; böylece PAM politikaları ve AppArmor kısıtlamalarından etkilenmez.

Geri al. Apply öncesi alınan `/etc/shadow` yedeği yerine yazılır;
böylece root, etapadmin ve ogretmen başta olmak üzere tüm hesapların
parolası `apply` öncesi haline döner.
"""

from __future__ import annotations

import crypt
import json
import pwd
import subprocess
import time
from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.privilege import invoking_username
from ..core.utils import backup_file, restore_file, run_cmd, user_exists

log = get_logger(__name__)

SHADOW = Path("/etc/shadow")

# Sistem kullanıcıları (silinebilir)
REMOVABLE_USERS = {"ogrenci", "ogretmen"}


def _generate_password_hash(password: str) -> str:
    """SHA-512 ile parola hash'i üretir."""
    # SHA-512 salt ile hash üret
    salt = crypt.mksalt(crypt.METHOD_SHA512)
    return crypt.crypt(password, salt)


def _set_password_direct(user: str, password: str) -> tuple[bool, str]:
    """Parolayı doğrudan /etc/shadow dosyasına hash olarak yazar."""
    try:
        if not user_exists(user):
            return False, f"Kullanıcı bulunamadı: {user}"

        log.info("Kullanıcı '%s' için parola hash'i doğrudan shadow'a yazılıyor", user)

        # Hash üret
        password_hash = _generate_password_hash(password)
        log.debug("Hash üretildi, uzunluk: %d karakter", len(password_hash))

        # Shadow dosyasını oku
        try:
            with open(SHADOW, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except OSError as exc:
            return False, f"Shadow dosyası okunamadı: {exc}"

        # İlgili kullanıcının satırını bul ve güncelle
        user_found = False
        updated_lines = []

        for line in lines:
            if line.startswith(f"{user}:"):
                # Shadow format: username:password:lastchange:min:max:warn:inactive:expire:reserved
                fields = line.strip().split(':')
                if len(fields) >= 2:
                    # Parola hash'ini güncelle
                    fields[1] = password_hash
                    # Parola değişim tarihini güncelle (epoch günleri)
                    fields[2] = str(int(time.time() // 86400))  # bugünkü gün sayısı
                    updated_line = ':'.join(fields) + '\n'
                    updated_lines.append(updated_line)
                    user_found = True
                    log.debug("Kullanıcı '%s' shadow satırı güncellendi", user)
                else:
                    log.error("Shadow satırı bozuk format: %s", line.strip())
                    return False, f"Shadow dosyasında bozuk format: {user}"
            else:
                updated_lines.append(line)

        if not user_found:
            return False, f"Shadow dosyasında kullanıcı bulunamadı: {user}"

        # Güncellenmiş içeriği geri yaz
        try:
            with open(SHADOW, 'w', encoding='utf-8') as f:
                f.writelines(updated_lines)
            log.info("Kullanıcı '%s' parolası başarıyla shadow'a yazıldı", user)
            return True, "Başarılı"
        except OSError as exc:
            return False, f"Shadow dosyası yazılamadı: {exc}"

    except Exception as exc:
        log.error("Parola ayarlama sırasında beklenmeyen hata: %s", exc)
        return False, f"Beklenmeyen hata: {exc}"


def _set_password(user: str, password: str) -> bool:
    """Parolayı doğrudan shadow dosyasına hash olarak yazar."""
    success, message = _set_password_direct(user, password)
    if not success:
        log.error("'%s' için parola atanamadı: %s", user, message)
    else:
        log.info("'%s' için parola başarıyla atandı", user)
    return success


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
    sidebar_title = "Yerel hesaplar"
    apply_hint = (
        "Parolalar SHA-512 hash olarak doğrudan /etc/shadow'a yazılır. "
        "Doldurduğunuz alanlara göre ilgili hesapların parolaları ayarlanır."
    )
    rationale = (
        "root, etapadmin veya ogretmen hesaplarından istediğinizin parolasını "
        "belirleyin. Hangi alanları doldurursanız sadece o hesapların parolası "
        "değişir; diğerlerine dokunulmaz.\n\n"
        "⚡ Teknik: Bu adım PAM politikalarından etkilenmez çünkü parolalar "
        "doğrudan shadow dosyasına hash olarak yazılır. Fiziksel ve sanal "
        "makineler arasında tutarsızlık yaşanmaz.\n\n"
        "Ortak hesapları (ogretmen, ogrenci) tamamen silmek isterseniz "
        "aşağıdaki düğmeyi kullanabilirsiniz."
    )
    extra_links = [
        {"label": "Kullanıcılar ve Gruplar uygulamasını aç", "action": "launch_users_admin_gui_action"},
    ]

    def preview(self) -> str:
        user_status = get_removable_user_status()

        lines: list[str] = []
        lines.append("Sistemdeki ortak hesaplar:")
        for user, exists in user_status.items():
            status = "✓ mevcut" if exists else "⨯ yok"
            lines.append(f"    • {user}: {status}")
        if not any(user_status.values()):
            lines.append("    (ogrenci ve ogretmen zaten yok)")

        return "\n".join(lines)

    def apply(self, params: dict | None = None, progress=None) -> ApplyResult:
        params = params or {}
        root_pw = params.get("root_password", "").strip()
        admin_pw = params.get("admin_password", "").strip()
        teacher_pw = params.get("teacher_password", "").strip()

        # En az bir parola belirtilmiş olmalı
        if not root_pw and not admin_pw and not teacher_pw:
            return ApplyResult(
                success=False,
                summary="En az bir parola belirtmelisiniz (root, etapadmin veya ogretmen).",
            )

        # Parola uzunluk kontrolü (sadece dolu olanlar için)
        if root_pw and len(root_pw) < 8:
            return ApplyResult(False, "root parolası en az 8 karakter olmalıdır.")
        if admin_pw and len(admin_pw) < 8:
            return ApplyResult(False, "etapadmin parolası en az 8 karakter olmalıdır.")
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

        # Sadece doldurulmuş parolaları ata
        results = {}
        ok_root = True
        ok_admin = True
        ok_teacher = True

        if root_pw:
            ok_root = _set_password("root", root_pw)
            results["root"] = ok_root

        if admin_pw:
            ok_admin = _set_password("etapadmin", admin_pw)
            results["etapadmin"] = ok_admin

        if teacher_pw and user_exists("ogretmen"):
            ok_teacher = _set_password("ogretmen", teacher_pw)
            results["ogretmen"] = ok_teacher
            # ogretmen hesabı kilitliyse parola ile giriş yapılabilmesi için aç
            _unlock_user("ogretmen")

        details_lines = []
        if removed_users:
            details_lines.append("Silinen ortak hesaplar: " + ", ".join(removed_users))

        failed_users = []
        for user, success in results.items():
            details_lines.append(f"{user} parolası: {'atandı' if success else 'ATANAMADI'}")
            if not success:
                failed_users.append(user)

        # Başarısız olan kullanıcılar için bilgi
        if failed_users:
            details_lines.append("")
            details_lines.append("== BAŞARISIZLIK BİLGİLERİ ==")
            details_lines.append("Parola değişikliği doğrudan /etc/shadow dosyasına yapıldı.")
            details_lines.append("Olası nedenler:")
            details_lines.append("  • Kullanıcı shadow dosyasında bulunamadı")
            details_lines.append("  • Shadow dosyası yazma izni problemi")
            details_lines.append("  • Bozuk shadow dosyası formatı")
            details_lines.append("Detaylı hatalar /tmp/tiha.logs dosyasında.")

        overall = all(results.values()) if results else (len(removed_users) > 0)

        summary_parts = []
        if removed_users:
            summary_parts.append(f"{len(removed_users)} ortak hesap silindi")

        password_parts = []
        for user, success in results.items():
            if success:
                password_parts.append(user)
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

    def remove_student_user_action(self, params: dict | None = None) -> ApplyResult:
        """Öğrenci kullanıcısını (ogrenci) siler."""
        if not user_exists("ogrenci"):
            return ApplyResult(
                False,
                "Öğrenci kullanıcısı (ogrenci) bulunamadı.",
                details="Kullanıcı zaten silinmiş olabilir."
            )

        state = self.ensure_state_dir()

        if remove_user_with_backup("ogrenci", state):
            return ApplyResult(
                True,
                "Öğrenci kullanıcısı (ogrenci) başarıyla silindi.",
                details="Kullanıcı güvenli şekilde yedeklendi ve kaldırıldı.\n"
                       "Geri alma işlevi ile geri yüklenebilir.",
                data={"removed_users": ["ogrenci"]}
            )
        else:
            return ApplyResult(
                False,
                "Öğrenci kullanıcısı silinemedi.",
                details="Detaylar için /var/log/tiha/tiha.log dosyasına bakın."
            )

    def launch_users_admin_gui_action(self, params: dict | None = None) -> ApplyResult:
        """Cinnamon 'Kullanıcılar ve Gruplar' uygulamasını kullanıcının X oturumunda açar."""
        binary = Path("/usr/bin/cinnamon-settings-users")
        if not binary.exists():
            return ApplyResult(
                False,
                "Kullanıcılar ve Gruplar uygulaması bulunamadı.",
                details=f"{binary} mevcut değil; cinnamon-control-center paketi kurulu mu?",
            )

        user = invoking_username()
        try:
            subprocess.Popen(
                ["sudo", "-u", user, "env",
                 "DISPLAY=:0",
                 f"XAUTHORITY=/home/{user}/.Xauthority",
                 str(binary)],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return ApplyResult(
                False,
                "Kullanıcılar ve Gruplar başlatılamadı.",
                details=str(exc),
            )

        return ApplyResult(
            True,
            f"Kullanıcılar ve Gruplar '{user}' oturumunda açıldı.",
            details="Pencereyi kapattığınızda bu adımdaki hesap durumu önizlemesi yenilenir.",
        )
