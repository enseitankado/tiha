"""Modül 4 — SSH & Samba (uzaktan bakım kanalları).

Eski m04 (SSH sunucusu) ve m05 (Samba dosya paylaşımı) adımları tek bir
adımda birleştirildi: her ikisi de aynı amaca hizmet ediyor (bakımcı
uzaktan tahtaya bağlansın) ve birlikte açılıp kapatılmaları daha
sezgisel. İki kutu (SSH etkin / Samba etkin) sistemin gerçek durumunu
yansıtır; kutu işaretlenip uygulanırsa servis kurulur, işareti
kaldırılıp uygulanırsa sökülür.

İç işlerin tümünü ``SSHServerModule`` ve ``SambaShareModule`` sınıfları
yürütür; bu modül yalnız iki delegate'i sırayla çağırır ve UI/undo
tarafını birleşik gösterir. Böylece her iki servisin yerleşim /
undo mantığı tek bir yerde kalmaya devam eder.
"""

from __future__ import annotations

from ..core.async_state import AsyncValue
from ..core.i18n import t
from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from .m04_ssh_server import (
    SSH_CONF,
    SSHServerModule,
    _is_package_installed as _pkg_installed,
    _root_password_set,
)
from .m05_samba_share import SambaShareModule

log = get_logger(__name__)


def _ssh_state() -> bool:
    """SSH kurulu ve TiHA ek yapılandırması yerinde mi?"""
    return _pkg_installed("openssh-server") and SSH_CONF.exists()


def _samba_state() -> bool:
    """Samba kurulu ve TiHA paylaşım tanımı yerinde mi?"""
    from ..core.paths import SAMBA_SHARE_CONF
    return _pkg_installed("samba") and SAMBA_SHARE_CONF.exists()


_state = AsyncValue(
    lambda: (_ssh_state(), _samba_state()),
    name="m04.ssh_and_samba",
)


class SSHAndSambaModule(Module):
    id = "m04_ssh_and_samba"
    title = t("m04.title")
    sidebar_title = t("m04.sidebar_title")
    apply_hint = t("m04.apply_hint")
    rationale = t("m04.rationale")
    streams_output = True

    # ---- Delegate örnekleri (lazy) ---------------------------------------

    def _ssh(self) -> SSHServerModule:
        inst = getattr(self, "_ssh_inst", None)
        if inst is None:
            inst = SSHServerModule()
            self._ssh_inst = inst
        return inst

    def _samba(self) -> SambaShareModule:
        inst = getattr(self, "_samba_inst", None)
        if inst is None:
            inst = SambaShareModule()
            self._samba_inst = inst
        return inst

    # ---- Kutu durumları (default_from) ----------------------------------

    def ssh_installed(self) -> str:
        return "True" if _ssh_state() else "False"

    def samba_installed(self) -> str:
        return "True" if _samba_state() else "False"

    # ---- Önizleme -------------------------------------------------------

    def preview(self) -> str:
        state = _state.get_async()
        if state is None:
            return t("m04.preview.checking")
        ssh_on, samba_on = state
        lines: list[str] = []
        lines.append(t(
            "m04.preview.ssh_line",
            state=t("m04.preview.state_on") if ssh_on
            else t("m04.preview.state_off"),
        ))
        lines.append(t(
            "m04.preview.samba_line",
            state=t("m04.preview.state_on") if samba_on
            else t("m04.preview.state_off"),
        ))
        lines.append("")
        lines.append(t("m04.preview.body"))
        return "\n".join(lines)

    def prefetch_preview_state(self, on_ready=None) -> None:
        _state.get_async(on_ready)

    def notice(self) -> tuple[str, str] | None:
        # SSH kutusu bu turda işaretliyse root parolasız girişi engelleyecek.
        # Preview üzerinden değil kutu durumundan bağımsız olarak sistem
        # gerçekliğini yansıtıyoruz.
        if not _pkg_installed("openssh-server") and not SSH_CONF.exists():
            return None
        state = _root_password_set()
        if state is True:
            return ("info", t("m04.notice.root_set"))
        if state is False:
            return ("warning", t("m04.notice.root_missing"))
        return ("warning", t("m04.notice.root_unknown"))

    # ---- Apply / Undo ---------------------------------------------------

    def apply(
        self, params: dict | None = None,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        params = params or {}
        enable_ssh = str(params.get("enable_ssh", "False")).lower() in (
            "true", "1", "yes", "on",
        )
        enable_samba = str(params.get("enable_samba", "False")).lower() in (
            "true", "1", "yes", "on",
        )

        ssh_now = _ssh_state()
        samba_now = _samba_state()

        actions: list[str] = []
        details: list[str] = []
        warnings: list[str] = []
        data: dict = {"ssh_data": None, "samba_data": None}

        # ---- SSH ----------------------------------------------------------
        ssh_changed = False
        if enable_ssh and not ssh_now:
            if progress:
                progress(t("m04.apply.ssh_installing"))
            r = self._ssh().apply(progress=progress)
            if not r.success:
                return ApplyResult(False, r.summary, details=r.details or "")
            data["ssh_data"] = dict(r.data or {})
            actions.append(t("m04.apply.ssh_added"))
            if r.warning:
                warnings.append(r.warning)
            ssh_changed = True
        elif not enable_ssh and ssh_now:
            if progress:
                progress(t("m04.apply.ssh_removing"))
            r = self._ssh().undo(
                data=(getattr(self, "_last_ssh_data", None) or {}),
            )
            if not r.success:
                return ApplyResult(False, r.summary, details=r.details or "")
            actions.append(t("m04.apply.ssh_removed"))
            ssh_changed = True

        # ---- Samba --------------------------------------------------------
        samba_changed = False
        if enable_samba and not samba_now:
            username = (params.get("samba_user") or "root").strip()
            password = params.get("samba_password") or ""
            if not password:
                return ApplyResult(False, t("m05.apply.password_required"))
            if progress:
                progress(t("m04.apply.samba_installing"))
            r = self._samba().apply(
                params={
                    "samba_user": username, "samba_password": password,
                },
                progress=progress,
            )
            if not r.success:
                return ApplyResult(False, r.summary, details=r.details or "")
            data["samba_data"] = dict(r.data or {})
            actions.append(t("m04.apply.samba_added", user=username))
            samba_changed = True
        elif not enable_samba and samba_now:
            if progress:
                progress(t("m04.apply.samba_removing"))
            r = self._samba().undo(
                data=(getattr(self, "_last_samba_data", None) or {}),
            )
            if not r.success:
                return ApplyResult(False, r.summary, details=r.details or "")
            actions.append(t("m04.apply.samba_removed"))
            samba_changed = True

        _state.invalidate()

        if not actions:
            return ApplyResult(
                True, t("m04.apply.no_change"), not_applicable=False,
            )

        summary = "; ".join(actions) + "."
        warning = " ".join(warnings) if warnings else None
        return ApplyResult(
            True, summary,
            details="\n".join(details) if details else None,
            warning=warning,
            data=data,
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        """Bu apply'da açılan servisleri kapatır (her ikisi de bağımsızca).

        ``data`` içindeki alt-yapı verileri delegate'lere aktarılır ki
        önceden var olan paketler dokunulmasın ve smb.conf include satırı
        temiz alınsın.
        """
        data = data or {}
        ssh_data = data.get("ssh_data") or {}
        samba_data = data.get("samba_data") or {}
        results: list[str] = []

        if ssh_data:
            r = self._ssh().undo(data=ssh_data)
            results.append(r.summary)
        if samba_data:
            r = self._samba().undo(data=samba_data)
            results.append(r.summary)
        _state.invalidate()
        if not results:
            return ApplyResult(True, t("m04.undo.nothing"))
        return ApplyResult(True, "; ".join(results) + ".")
