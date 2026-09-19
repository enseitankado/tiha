"""Modül 4 — SSH sunucusu kur ve root uzak bağlantısına izin ver.

Ne yapar?
openssh-server paketini (yoksa) kurar; /etc/ssh/sshd_config.d/
altına PermitRootLogin yes ve PasswordAuthentication yes ayarlarını
içeren ek bir yapılandırma dosyası bırakır; ssh servisini etkinleştirir.

Neden gerekir?
Dağıtılmış tahtalarda uzaktan teknik destek/bakım için root erişimi
gereklidir.

Geri al (tam restore).
- Ek yapılandırma dosyası silinir.
- Eğer TiHA openssh-server'ı KURDUYSA (daha önce kurulu değildi),
  paket apt-get purge ile kaldırılır — başlangıçtaki temiz duruma
  dönülür. Daha önce zaten kuruluysa paket korunur.
- Servis durumu uygun şekilde ayarlanır.

Apply sırasında apt çıktısı CANLI OLARAK ekrana akar; kullanıcı
bekliyor gibi hissetmez.
"""

from __future__ import annotations

from pathlib import Path

from ..core.async_state import AsyncValue
from ..core.i18n import t
from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.utils import run_cmd, run_cmd_stream

log = get_logger(__name__)

SSH_CONF = Path("/etc/ssh/sshd_config.d/99-tiha.conf")

SSH_CONF_CONTENT = """# TiHA tarafından yazılmıştır.
PermitRootLogin yes
PasswordAuthentication yes
"""


def _is_package_installed(name: str) -> bool:
    """``dpkg-query`` ile paket kurulu mu denetler."""
    result = run_cmd(["dpkg-query", "-W", "-f=${Status}", name])
    return result.ok and "install ok installed" in result.stdout


# Cache: openssh-server kurulu mu? preview() bu cache'i okur, UI bloke
# etmez. apply()/undo() doğrudan _is_package_installed çağırır (güncel
# durumu kesin bilmek gerekir) ve sonrasında cache'i invalidate eder.
_ssh_installed = AsyncValue(
    lambda: _is_package_installed("openssh-server"),
    name="m04.openssh-server",
)


class SSHServerModule(Module):
    id = "m04_ssh_server"
    title = t("m04.title")
    sidebar_title = t("m04.sidebar_title")
    rationale_inline = True
    apply_hint = t("m04.apply_hint")
    streams_output = True
    rationale = t("m04.rationale")

    def preview(self) -> str:
        # AsyncValue cache'inden okuruz; cache yoksa "kontrol ediliyor"
        # gösterip arka plan worker'ı tetikleriz. main_window
        # prefetch_preview_state üzerinden zaten tetiklemiş olur; bu
        # çağrı güvenlik kemeri (cache yine yoksa worker başlasın).
        installed = _ssh_installed.get_async()
        if installed is None:
            return t("m04.preview.checking")
        return (
            t("m04.preview.installed")
            if installed
            else t("m04.preview.not_installed")
        )

    def prefetch_preview_state(self, on_ready=None) -> None:
        _ssh_installed.get_async(on_ready)

    def apply(self, params=None, progress: ProgressCallback | None = None) -> ApplyResult:
        # Başlangıç durumu (undo için saklanacak)
        was_installed_before = _is_package_installed("openssh-server")
        conf_existed_before = SSH_CONF.exists()

        if progress:
            progress(t("m04.apply.start_pkg", state=t("m04.apply.state_installed") if was_installed_before else t("m04.apply.state_not_installed")))
            progress(t("m04.apply.start_conf", state=t("m04.apply.state_exists") if conf_existed_before else t("m04.apply.state_missing")))

        # Kurulum
        if not was_installed_before:
            if progress:
                progress("\n==== apt-get update ====")
            upd = run_cmd_stream(["apt-get", "update"], progress=progress,
                                 env={"DEBIAN_FRONTEND": "noninteractive"}, timeout=300)
            if not upd.ok:
                return ApplyResult(False, t("m04.apply.apt_update_failed"),
                                   data={"was_installed_before": was_installed_before})
            if progress:
                progress("\n==== apt-get install openssh-server ====")
            inst = run_cmd_stream(
                ["apt-get", "install", "-y", "openssh-server"],
                progress=progress,
                env={"DEBIAN_FRONTEND": "noninteractive"},
                timeout=600,
            )
            if not inst.ok:
                return ApplyResult(False, t("m04.apply.install_failed"),
                                   data={"was_installed_before": was_installed_before})

        # Ek yapılandırma dosyası yaz
        try:
            SSH_CONF.parent.mkdir(parents=True, exist_ok=True)
            SSH_CONF.write_text(SSH_CONF_CONTENT, encoding="utf-8")
            SSH_CONF.chmod(0o644)
        except OSError as exc:
            return ApplyResult(
                False, t("m04.apply.conf_write_failed", error=exc),
                data={"was_installed_before": was_installed_before,
                      "conf_existed_before": conf_existed_before},
            )
        if progress:
            progress(t("m04.apply.conf_written", path=SSH_CONF))

        # Servis
        en = run_cmd(["systemctl", "enable", "--now", "ssh"])
        rel = run_cmd(["systemctl", "reload", "ssh"])
        if progress:
            ok_w, err_w = t("m04.apply.ok_word"), t("m04.apply.err_word")
            progress(t("m04.apply.service_status",
                       enable=ok_w if en.ok else err_w,
                       reload=ok_w if rel.ok else err_w))

        _ssh_installed.invalidate()
        return ApplyResult(
            True,
            t("m04.apply.summary"),
            details=t("m04.apply.details", path=SSH_CONF),
            data={
                "was_installed_before": was_installed_before,
                "conf_existed_before": conf_existed_before,
            },
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        data = data or {}
        was_installed_before = bool(data.get("was_installed_before", True))
        conf_existed_before = bool(data.get("conf_existed_before", False))

        # 1) Ek yapılandırma dosyasını temizle (yalnızca biz eklemişsek)
        if not conf_existed_before:
            try:
                SSH_CONF.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("Ek yapılandırma dosyası silinemedi: %s", exc)
        run_cmd(["systemctl", "reload", "ssh"])

        # 2) Paket başlangıçta kurulu değilse kaldır — tam temiz duruma dön
        if not was_installed_before:
            run_cmd(["systemctl", "disable", "--now", "ssh"])
            purge = run_cmd(
                ["apt-get", "purge", "-y", "openssh-server"],
                env={"DEBIAN_FRONTEND": "noninteractive"},
                timeout=300,
            )
            run_cmd(["apt-get", "autoremove", "-y"],
                    env={"DEBIAN_FRONTEND": "noninteractive"})
            _ssh_installed.invalidate()
            if not purge.ok:
                return ApplyResult(False, t("m04.undo.purge_failed"),
                                   details=purge.stderr)
            return ApplyResult(True, t("m04.undo.removed"))

        _ssh_installed.invalidate()
        return ApplyResult(True, t("m04.undo.kept_pkg"))
