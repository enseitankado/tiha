"""Modül 5 — Samba paylaşımı ile tüm diski uzaktan erişilebilir yap.

**Ne yapar?**
``samba`` paketini kurar, varsayılan olarak ``root`` kullanıcısıyla kök
``/`` dizinini tam yetkili paylaşan ``[root]`` adlı bir Samba paylaşımı
oluşturur ve ``smbd``'yi yeniden yükler. Kullanıcı dilerse paylaşım
sahibi kullanıcıyı değiştirebilir.

**Neden gerekir?**
Teknik destek ekibi tahtada herhangi bir dizine hızlı dosya yerleştirme
ya da alma işlemi yapabilsin. SSH'a ek olarak dosya gezgininden görsel
erişim sağlanır.

**Geri al (tam restore).**
- ``[root]`` paylaşım tanımı silinir ve smb.conf include satırı geri alınır.
- Eğer TiHA Samba'yı *kurduysa* (daha önce kurulu değildi), paket
  ``apt-get purge`` ile kaldırılır ve başlangıçtaki duruma dönülür.
- Samba Python smb kullanıcısı (varsa) parola kaydı silinir.
"""

from __future__ import annotations

from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.paths import SAMBA_DROPIN, SAMBA_SMB_CONF
from ..core.utils import run_cmd, run_cmd_stream

log = get_logger(__name__)

SHARE_NAME = "root"


def _render_share(username: str) -> str:
    return f"""# TiHA tarafından yazılmıştır — tam sistem erişimli root paylaşımı.
[{SHARE_NAME}]
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


def _is_package_installed(name: str) -> bool:
    result = run_cmd(["dpkg-query", "-W", "-f=${Status}", name])
    return result.ok and "install ok installed" in result.stdout


class SambaShareModule(Module):
    id = "m05_samba_share"
    title = "Samba dosya paylaşımı"
    streams_output = True
    rationale = (
        "Teknik bakım ekibinin SMB protokolüyle tahtadaki dosyalara "
        f"ulaşabilmesi için Samba kurar ve tahtanın kök dizinini (/) [{SHARE_NAME}] "
        "adıyla tam yetkili bir paylaşım olarak sunar."
    )

    def preview(self) -> str:
        installed = _is_package_installed("samba")
        return ("samba zaten kurulu — yalnızca paylaşım tanımı eklenecek."
                if installed
                else "samba kurulacak ve paylaşım tanımı eklenecek.")

    def apply(self, params=None, progress: ProgressCallback | None = None) -> ApplyResult:
        params = params or {}
        username = (params.get("samba_user") or "root").strip()
        password = params.get("samba_password") or ""
        if not password:
            return ApplyResult(False, "Samba parolası verilmeli.")

        was_installed_before = _is_package_installed("samba")
        dropin_existed_before = SAMBA_DROPIN.exists()

        if progress:
            progress(f"Başlangıç: samba {'kurulu' if was_installed_before else 'kurulu değil'}")

        # Kurulum
        if not was_installed_before:
            if progress:
                progress("\n==== apt-get update ====")
            upd = run_cmd_stream(["apt-get", "update"], progress=progress,
                                 env={"DEBIAN_FRONTEND": "noninteractive"}, timeout=300)
            if not upd.ok:
                return ApplyResult(False, "apt-get update başarısız.",
                                   data={"was_installed_before": was_installed_before})
            if progress:
                progress("\n==== apt-get install samba ====")
            inst = run_cmd_stream(
                ["apt-get", "install", "-y", "samba"],
                progress=progress,
                env={"DEBIAN_FRONTEND": "noninteractive"},
                timeout=600,
            )
            if not inst.ok:
                return ApplyResult(False, "samba kurulamadı.",
                                   data={"was_installed_before": was_installed_before})

        # Paylaşım drop-in
        try:
            SAMBA_DROPIN.parent.mkdir(parents=True, exist_ok=True)
            SAMBA_DROPIN.write_text(_render_share(username), encoding="utf-8")
            SAMBA_DROPIN.chmod(0o644)
        except OSError as exc:
            return ApplyResult(False, f"Samba drop-in yazılamadı: {exc}",
                               data={"was_installed_before": was_installed_before,
                                     "dropin_existed_before": dropin_existed_before})

        # smb.conf içine include satırı ekle (yalnızca yoksa)
        include_line = f"include = {SAMBA_DROPIN}"
        try:
            content = SAMBA_SMB_CONF.read_text(encoding="utf-8") if SAMBA_SMB_CONF.exists() else ""
            include_was_absent = include_line not in content
            if include_was_absent:
                SAMBA_SMB_CONF.write_text(
                    content.rstrip() + f"\n# TiHA include\n{include_line}\n",
                    encoding="utf-8",
                )
        except OSError as exc:
            return ApplyResult(False, f"smb.conf güncellenemedi: {exc}",
                               data={"was_installed_before": was_installed_before,
                                     "dropin_existed_before": dropin_existed_before})

        # smbpasswd
        smbpw = run_cmd(
            ["smbpasswd", "-a", "-s", username],
            input_data=f"{password}\n{password}\n",
        )
        if not smbpw.ok:
            return ApplyResult(False, "smbpasswd başarısız.", details=smbpw.stderr,
                               data={"was_installed_before": was_installed_before,
                                     "dropin_existed_before": dropin_existed_before})
        run_cmd(["smbpasswd", "-e", username])

        run_cmd(["systemctl", "enable", "--now", "smbd"])
        run_cmd(["systemctl", "reload", "smbd"])
        if progress:
            progress(f"Paylaşım aktif: //<tahta-ip>/{SHARE_NAME} (kullanıcı: {username})")

        return ApplyResult(
            True,
            f"Samba paylaşımı '//<tahta-ip>/{SHARE_NAME}' olarak hazır ({username} kullanıcısıyla).",
            details=(
                f"Drop-in: {SAMBA_DROPIN}\n"
                f"Kullanıcı: {username}\n"
                f"İstemciden örnek: smbclient //<tahta-ip>/{SHARE_NAME} -U {username}"
            ),
            data={
                "was_installed_before": was_installed_before,
                "dropin_existed_before": dropin_existed_before,
                "include_was_absent": include_was_absent,
                "samba_user": username,
            },
        )

    def undo(self, data: dict) -> ApplyResult:
        data = data or {}
        was_installed_before = bool(data.get("was_installed_before", True))
        dropin_existed_before = bool(data.get("dropin_existed_before", False))
        include_was_absent = bool(data.get("include_was_absent", False))
        username = data.get("samba_user", "root")

        # 1) Drop-in'i temizle (yalnızca biz eklemişsek)
        if not dropin_existed_before:
            try:
                SAMBA_DROPIN.unlink(missing_ok=True)
            except OSError:
                pass

        # 2) smb.conf include satırını geri al (yalnızca biz eklemişsek)
        if include_was_absent and SAMBA_SMB_CONF.exists():
            try:
                content = SAMBA_SMB_CONF.read_text(encoding="utf-8")
                lines = [
                    ln for ln in content.splitlines()
                    if f"include = {SAMBA_DROPIN}" not in ln and ln.strip() != "# TiHA include"
                ]
                SAMBA_SMB_CONF.write_text("\n".join(lines) + "\n", encoding="utf-8")
            except OSError:
                pass

        # 3) smbpasswd kullanıcısı kaydını sil
        run_cmd(["smbpasswd", "-x", username])

        # 4) Paket başlangıçta kurulu değilse kaldır
        if not was_installed_before:
            run_cmd(["systemctl", "disable", "--now", "smbd"])
            purge = run_cmd(
                ["apt-get", "purge", "-y", "samba", "samba-common", "samba-common-bin"],
                env={"DEBIAN_FRONTEND": "noninteractive"}, timeout=300,
            )
            run_cmd(["apt-get", "autoremove", "-y"],
                    env={"DEBIAN_FRONTEND": "noninteractive"})
            if not purge.ok:
                return ApplyResult(False, "Samba paketleri kaldırılamadı.", details=purge.stderr)
            return ApplyResult(True, "Samba paylaşımı ve paketleri tamamen kaldırıldı.")

        run_cmd(["systemctl", "reload", "smbd"])
        return ApplyResult(True, f"[{SHARE_NAME}] paylaşımı kaldırıldı (Samba paketleri başlangıçta kuruluydu, korundu).")
