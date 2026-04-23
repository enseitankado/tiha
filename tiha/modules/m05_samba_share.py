"""Modül 5 — Samba paylaşımı ile tüm diski uzaktan erişilebilir yap.

**Ne yapar?**
``samba`` paketini kurar, varsayılan olarak ``root`` kullanıcısıyla kök
``/`` dizinini tam yetkili paylaşan bir drop-in konfig bırakır ve
``smbd``'yi yeniden yükler. Kullanıcı dilerse paylaşım sahibi kullanıcıyı
değiştirebilir.

**Neden gerekir?**
Teknik destek ekibi tahtada herhangi bir dizine hızlı dosya yerleştirme
ya da alma işlemi yapabilsin. SSH'a ek olarak dosya gezgini üzerinden
görsel erişim sağlanır.

**Geri al.** Drop-in kaldırılır ve Samba kullanıcısı silinir. Paket
kaldırılmaz.
"""

from __future__ import annotations

from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.paths import SAMBA_DROPIN
from ..core.utils import run_cmd

log = get_logger(__name__)


def _render_share(username: str) -> str:
    return f"""# TiHA tarafından yazılmıştır — tam sistem erişimli root paylaşımı.
[tiha-root]
    comment = TiHA — tüm sistem (/)
    path = /
    browseable = yes
    read only = no
    guest ok = no
    valid users = {username}
    force user = root
    create mask = 0644
    directory mask = 0755
"""


class SambaShareModule(Module):
    id = "m05_samba_share"
    title = "Samba dosya paylaşımı"
    rationale = (
        "Teknik bakım ekibinin SMB protokolüyle tahtadaki dosyalara "
        "ulaşabilmesi için Samba kurar ve tahtanın kök dizinini (/) tam "
        "yetkili bir paylaşım olarak sunar."
    )

    def preview(self) -> str:
        return "samba kurulacak; /etc/samba içerisine tiha-root paylaşımı eklenecek."

    def apply(self, params: dict | None = None) -> ApplyResult:
        params = params or {}
        username = params.get("samba_user", "root")
        password = params.get("samba_password", "")
        if not password:
            return ApplyResult(False, "Samba parolası verilmeli.")

        install = run_cmd(
            ["apt-get", "install", "-y", "samba"],
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        if not install.ok:
            return ApplyResult(False, "samba kurulamadı.", details=install.stderr)

        try:
            SAMBA_DROPIN.parent.mkdir(parents=True, exist_ok=True)
            SAMBA_DROPIN.write_text(_render_share(username), encoding="utf-8")
            SAMBA_DROPIN.chmod(0o644)
        except OSError as exc:
            return ApplyResult(False, f"Samba drop-in yazılamadı: {exc}")

        # smb.conf içindeki 'include' yönlendirmesini bir kere ekle
        smb_conf = Path("/etc/samba/smb.conf")
        include_line = f"include = {SAMBA_DROPIN}"
        try:
            content = smb_conf.read_text(encoding="utf-8")
            if include_line not in content:
                smb_conf.write_text(content + f"\n{include_line}\n", encoding="utf-8")
        except OSError as exc:
            return ApplyResult(False, f"smb.conf güncellenemedi: {exc}")

        # Samba kullanıcısı için parola ayarla
        smbpasswd = run_cmd(
            ["smbpasswd", "-a", "-s", username],
            input_data=f"{password}\n{password}\n",
        )
        if not smbpasswd.ok:
            return ApplyResult(False, "smbpasswd başarısız.", details=smbpasswd.stderr)
        run_cmd(["smbpasswd", "-e", username])

        run_cmd(["systemctl", "enable", "--now", "smbd"])
        run_cmd(["systemctl", "reload", "smbd"])

        return ApplyResult(
            True,
            f"Samba paylaşımı '//{{IP}}/tiha-root' olarak hazır ({username} kullanıcısıyla).",
            details=(
                f"Drop-in: {SAMBA_DROPIN}\n"
                f"Kullanıcı: {username}\n"
                "İstemciden örnek: smbclient //tahta-ip/tiha-root -U " + username
            ),
        )

    def undo(self, data: dict) -> ApplyResult:
        try:
            SAMBA_DROPIN.unlink(missing_ok=True)
        except OSError:
            pass
        # smb.conf include satırını geri al
        smb_conf = Path("/etc/samba/smb.conf")
        try:
            lines = smb_conf.read_text(encoding="utf-8").splitlines()
            new = [ln for ln in lines if f"include = {SAMBA_DROPIN}" not in ln]
            smb_conf.write_text("\n".join(new) + "\n", encoding="utf-8")
        except OSError:
            pass
        run_cmd(["systemctl", "reload", "smbd"])
        return ApplyResult(True, "TiHA Samba paylaşımı kaldırıldı.")
