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

from ..core.async_state import AsyncValue
from ..core.i18n import t
from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.paths import SAMBA_SHARE_CONF, SAMBA_SMB_CONF
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


# preview() bu cache'ten okur. apply()/undo() doğrudan
# _is_package_installed çağırır (kesin durum gerekir) ve cache'i
# invalidate eder.
_samba_installed = AsyncValue(
    lambda: _is_package_installed("samba"),
    name="m05.samba",
)


class SambaShareModule(Module):
    id = "m05_samba_share"
    title = t("m05.title")
    sidebar_title = t("m05.sidebar_title")
    rationale_inline = True
    apply_hint = t("m05.apply_hint")
    streams_output = True
    rationale = t("m05.rationale", share=SHARE_NAME)

    def preview(self) -> str:
        # samba kurulum durumu async cache'ten - UI'yı bloke etmez.
        installed = _samba_installed.get_async()
        share_exists = SAMBA_SHARE_CONF.exists()

        # smb.conf'ta include satırının varlığını kontrol et
        include_line = f"include = {SAMBA_SHARE_CONF}"
        include_exists = False
        if SAMBA_SMB_CONF.exists():
            try:
                content = SAMBA_SMB_CONF.read_text(encoding="utf-8")
                include_exists = include_line in content
            except OSError:
                pass

        if installed is None:
            return t("m05.preview.checking")
        if installed and share_exists and include_exists:
            return t("m05.preview.update")
        elif installed and (share_exists or include_exists):
            return t("m05.preview.partial")
        elif installed:
            return t("m05.preview.add_share")
        elif share_exists:
            return t("m05.preview.install_keep_share")
        else:
            return t("m05.preview.install_add_share")

    def prefetch_preview_state(self, on_ready=None) -> None:
        _samba_installed.get_async(on_ready)

    def apply(self, params=None, progress: ProgressCallback | None = None) -> ApplyResult:
        params = params or {}
        username = (params.get("samba_user") or "root").strip()
        password = params.get("samba_password") or ""
        if not password:
            return ApplyResult(False, t("m05.apply.password_required"))

        was_installed_before = _is_package_installed("samba")
        conf_existed_before = SAMBA_SHARE_CONF.exists()

        if progress:
            progress(t("m05.apply.start_pkg", state=t("m05.apply.state_installed") if was_installed_before else t("m05.apply.state_not_installed")))

        # Kurulum
        if not was_installed_before:
            if progress:
                progress("\n==== apt-get update ====")
            upd = run_cmd_stream(["apt-get", "update"], progress=progress,
                                 env={"DEBIAN_FRONTEND": "noninteractive"}, timeout=300)
            if not upd.ok:
                return ApplyResult(False, t("m05.apply.apt_update_failed"),
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
                return ApplyResult(False, t("m05.apply.install_failed"),
                                   data={"was_installed_before": was_installed_before})

        # Paylaşım ek yapılandırma dosyası
        try:
            SAMBA_SHARE_CONF.parent.mkdir(parents=True, exist_ok=True)
            SAMBA_SHARE_CONF.write_text(_render_share(username), encoding="utf-8")
            SAMBA_SHARE_CONF.chmod(0o644)
        except OSError as exc:
            return ApplyResult(False, t("m05.apply.conf_write_failed", error=exc),
                               data={"was_installed_before": was_installed_before,
                                     "conf_existed_before": conf_existed_before})

        # smb.conf içine include satırı ekle (yalnızca yoksa)
        include_line = f"include = {SAMBA_SHARE_CONF}"
        try:
            content = SAMBA_SMB_CONF.read_text(encoding="utf-8") if SAMBA_SMB_CONF.exists() else ""
            include_was_absent = include_line not in content
            if include_was_absent:
                SAMBA_SMB_CONF.write_text(
                    content.rstrip() + f"\n# TiHA include\n{include_line}\n",
                    encoding="utf-8",
                )
        except OSError as exc:
            return ApplyResult(False, t("m05.apply.smbconf_failed", error=exc),
                               data={"was_installed_before": was_installed_before,
                                     "conf_existed_before": conf_existed_before})

        # smbpasswd
        smbpw = run_cmd(
            ["smbpasswd", "-a", "-s", username],
            input_data=f"{password}\n{password}\n",
        )
        if not smbpw.ok:
            return ApplyResult(False, t("m05.apply.smbpasswd_failed"), details=smbpw.stderr,
                               data={"was_installed_before": was_installed_before,
                                     "conf_existed_before": conf_existed_before})
        run_cmd(["smbpasswd", "-e", username])

        run_cmd(["systemctl", "enable", "--now", "smbd"])
        run_cmd(["systemctl", "reload", "smbd"])
        _samba_installed.invalidate()
        if progress:
            progress(t("m05.apply.share_active", share=SHARE_NAME, user=username))

        return ApplyResult(
            True,
            t("m05.apply.summary", share=SHARE_NAME, user=username),
            details=t("m05.apply.details", path=SAMBA_SHARE_CONF,
                      user=username, share=SHARE_NAME),
            data={
                "was_installed_before": was_installed_before,
                "conf_existed_before": conf_existed_before,
                "include_was_absent": include_was_absent,
                "samba_user": username,
            },
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        data = data or {}
        was_installed_before = bool(data.get("was_installed_before", True))
        conf_existed_before = bool(data.get("conf_existed_before", False))
        include_was_absent = bool(data.get("include_was_absent", False))
        username = data.get("samba_user", "root")

        # 1) Ek yapılandırma dosyasını temizle (yalnızca biz eklemişsek)
        if not conf_existed_before:
            try:
                SAMBA_SHARE_CONF.unlink(missing_ok=True)
            except OSError:
                pass

        # 2) smb.conf include satırını geri al (yalnızca biz eklemişsek)
        if include_was_absent and SAMBA_SMB_CONF.exists():
            try:
                content = SAMBA_SMB_CONF.read_text(encoding="utf-8")
                lines = [
                    ln for ln in content.splitlines()
                    if f"include = {SAMBA_SHARE_CONF}" not in ln and ln.strip() != "# TiHA include"
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
            _samba_installed.invalidate()
            if not purge.ok:
                return ApplyResult(False, t("m05.undo.purge_failed"), details=purge.stderr)
            return ApplyResult(True, t("m05.undo.removed"))

        run_cmd(["systemctl", "reload", "smbd"])
        _samba_installed.invalidate()
        return ApplyResult(True, t("m05.undo.kept_pkg", share=SHARE_NAME))
