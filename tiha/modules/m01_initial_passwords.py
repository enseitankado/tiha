"""Modül 1 — Başlangıç parolaları.

**Ne yapar?**
- Sistemdeki gerçek kullanıcıların (UID ≥ 1000) ``etapadmin`` dışındaki
  hepsine kriptografik olarak güvenli rastgele parola atar ve bu hesapları
  ``usermod -L`` ile kilitler.
- Kullanıcının belirlediği yeni ``root`` ve ``etapadmin`` parolalarını
  uygular.

**Neden gerekir?**
Sınıftaki 65" dokunmatik ekranda öğrencilerin izlediği ortamda kişisel
parola tanımlamak, parolanın görsel olarak ifşa olmasına yol açar. Bu adım
imaj alınmadan önce tüm genel hesapları kilitleyerek, tahta dağıtıldığında
öğrencilerin bu hesaplarla ekrana parola yazmasının yolunu kapatır.

**Geri al.** Önceki /etc/shadow yedeği geri yüklenir.
"""

from __future__ import annotations

import pwd
from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.utils import backup_file, random_password, restore_file, run_cmd

log = get_logger(__name__)

SHADOW = Path("/etc/shadow")


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


class InitialPasswordsModule(Module):
    id = "m01_initial_passwords"
    title = "Başlangıç parolaları"
    apply_hint = (
        "Belirlenen root/etapadmin parolaları uygulanır; diğer hesaplar kilitlenir."
    )
    rationale = (
        "Sınıf ortamında dokunmatik ekrana parola yazmanın ifşa riski nedeniyle "
        "genel kullanıcıların (ogrenci, ogretmen vb.) parolalarını güvenli "
        "rastgele değerle değiştirip hesaplarını kilitler. Ardından sizin "
        "belirleyeceğiniz güçlü parolaları root ve etapadmin hesaplarına uygular."
    )

    def preview(self) -> str:
        users = [u for u in _human_users() if u != "etapadmin"]
        if not users:
            return "Kilitlenecek ek kullanıcı bulunamadı."
        return "Rastgele parolanacak ve kilitlenecek kullanıcılar:\n  • " + "\n  • ".join(users)

    def apply(self, params: dict | None = None, progress=None) -> ApplyResult:
        params = params or {}
        root_pw = params.get("root_password", "")
        root_pw_conf = params.get("root_password_confirm", "")
        admin_pw = params.get("admin_password", "")
        admin_pw_conf = params.get("admin_password_confirm", "")

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
        if len(root_pw) < 8 or len(admin_pw) < 8:
            return ApplyResult(False, "Her iki parola da en az 8 karakter olmalıdır.")

        state = self.ensure_state_dir()
        # Önce /etc/shadow yedeği al — undo için tek başına yeterli.
        backup_file(SHADOW, state)

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

        # 2) root ve etapadmin parolalarını ata
        ok_root = _set_password("root", root_pw)
        ok_admin = _set_password("etapadmin", admin_pw)

        details_lines = []
        if locked:
            details_lines.append("Kilitlenen hesaplar: " + ", ".join(locked))
        if failed:
            details_lines.append("Kilitlenemeyen hesaplar: " + ", ".join(failed))
        details_lines.append(f"root parolası: {'atandı' if ok_root else 'ATANAMADI'}")
        details_lines.append(f"etapadmin parolası: {'atandı' if ok_admin else 'ATANAMADI'}")

        overall = ok_root and ok_admin and not failed
        return ApplyResult(
            success=overall,
            summary=(
                f"{len(locked)} hesap kilitlendi; root/etapadmin parolaları güncellendi."
                if overall
                else "Bazı işlemler başarısız oldu, ayrıntılara bakın."
            ),
            details="\n".join(details_lines),
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        state = self.state_dir
        backup = state / "shadow"
        if not backup.exists():
            return ApplyResult(False, "Yedek /etc/shadow bulunamadı; geri alınamıyor.")
        try:
            restore_file(backup, SHADOW)
            # Kilitli kullanıcıları da aç (izole olaylar için)
            for user in _human_users():
                if user != "etapadmin":
                    _unlock_user(user)
            return ApplyResult(True, "Önceki /etc/shadow durumu geri yüklendi.")
        except OSError as exc:
            log.error("Shadow geri yükleme hatası: %s", exc)
            return ApplyResult(False, f"Geri yükleme başarısız: {exc}")
