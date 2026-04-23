"""Modül 6 — Merkezi log sunucusuna yönlendirme.

**Ne yapar?**
Sistem günlüklerini ağdaki merkezi bir ``syslog``/``rsyslog`` sunucusuna
ileten bir ``rsyslog`` drop-in yapılandırması kurar. Varsayılan protokol
UDP 514; TCP ve başka port da seçilebilir.

**Neden gerekir?**
Onlarca tahtanın logunu tek tek taramak yerine merkezde toplamak, sorun
tespit ve izleme açısından kritik önem taşır.

**Geri al.** Drop-in kaldırılır, ``rsyslog`` yeniden başlatılır.
"""

from __future__ import annotations

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.paths import RSYSLOG_DROPIN
from ..core.utils import run_cmd

log = get_logger(__name__)


def _render(host: str, port: int, proto: str) -> str:
    # UDP için `@`, TCP için `@@` önekleri rsyslog standardıdır.
    prefix = "@@" if proto.lower() == "tcp" else "@"
    return (
        "# TiHA — merkezi log sunucusuna iletim\n"
        f"*.*  {prefix}{host}:{port}\n"
    )


class RemoteSyslogModule(Module):
    id = "m06_remote_syslog"
    title = "Merkezi log iletimi"
    rationale = (
        "Tahtanın sistem kayıtlarını (ör. oturum açma, servis hataları) "
        "ağdaki merkezi bir log sunucusuna gönderecek rsyslog kuralını yazar. "
        "Böylece sahadaki tüm tahtaların logları tek bir noktadan izlenebilir."
    )

    def preview(self) -> str:
        return "rsyslog drop-in yazılacak; varsayılan 514/UDP."

    def apply(self, params: dict | None = None) -> ApplyResult:
        params = params or {}
        host = (params.get("syslog_host") or "").strip()
        port = int(params.get("syslog_port") or 514)
        proto = (params.get("syslog_proto") or "udp").strip().lower()
        if not host:
            return ApplyResult(False, "Merkezi log sunucusu adresi (IP/isim) boş.")

        install = run_cmd(
            ["apt-get", "install", "-y", "rsyslog"],
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        # rsyslog zaten kurulu olabilir; hatayı yalnızca günlüğe yaz.

        try:
            RSYSLOG_DROPIN.write_text(_render(host, port, proto), encoding="utf-8")
            RSYSLOG_DROPIN.chmod(0o644)
        except OSError as exc:
            return ApplyResult(False, f"rsyslog drop-in yazılamadı: {exc}")

        restart = run_cmd(["systemctl", "restart", "rsyslog"])
        if not restart.ok:
            return ApplyResult(False, "rsyslog yeniden başlatılamadı.", details=restart.stderr)

        return ApplyResult(
            True,
            f"Loglar {host}:{port}/{proto.upper()} adresine iletilecek.",
            details=f"Dosya: {RSYSLOG_DROPIN}",
        )

    def undo(self, data: dict) -> ApplyResult:
        try:
            RSYSLOG_DROPIN.unlink(missing_ok=True)
        except OSError:
            pass
        run_cmd(["systemctl", "restart", "rsyslog"])
        return ApplyResult(True, "Merkezi log iletimi kaldırıldı.")
