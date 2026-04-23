"""Modül 4 — SSH sunucusu kur ve root uzak bağlantısına izin ver.

**Ne yapar?**
``openssh-server`` paketini kurar; ``/etc/ssh/sshd_config.d/`` altına
``PermitRootLogin yes`` ve ``PasswordAuthentication yes`` ayarlarını içeren
bir drop-in bırakır; ``ssh`` servisini etkinleştirir.

**Neden gerekir?**
Dağıtılmış tahtalarda uzaktan teknik destek/bakım için root erişimi
gereklidir. Ağdaki merkezî destek ekibi belirli bir yönetim istasyonundan
tüm tahtalara SSH ile bağlanabilmelidir.

**Geri al.** Drop-in dosyası silinir, ``sshd`` yeniden yüklenir. Paket
bilinçli olarak kaldırılmaz (başka hizmetler de kullanıyor olabilir).
"""

from __future__ import annotations

from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.utils import run_cmd

log = get_logger(__name__)

DROPIN = Path("/etc/ssh/sshd_config.d/99-tiha.conf")

DROPIN_CONTENT = """# TiHA tarafından yazılmıştır.
PermitRootLogin yes
PasswordAuthentication yes
"""


class SSHServerModule(Module):
    id = "m04_ssh_server"
    title = "SSH sunucusu (root girişi)"
    rationale = (
        "Teknik bakım ekibi tahtaya uzaktan erişebilsin diye SSH sunucusunu "
        "kurar ve root kullanıcısının uzaktan oturum açabilmesine izin verir."
    )

    def preview(self) -> str:
        return "openssh-server kurulacak ve drop-in ile root girişine izin verilecek."

    def apply(self, params: dict | None = None) -> ApplyResult:
        install = run_cmd(
            ["apt-get", "install", "-y", "openssh-server"],
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        if not install.ok:
            return ApplyResult(False, "openssh-server kurulamadı.", details=install.stderr)

        try:
            DROPIN.parent.mkdir(parents=True, exist_ok=True)
            DROPIN.write_text(DROPIN_CONTENT, encoding="utf-8")
            DROPIN.chmod(0o644)
        except OSError as exc:
            return ApplyResult(False, f"SSH drop-in yazılamadı: {exc}")

        run_cmd(["systemctl", "enable", "--now", "ssh"])
        reload_res = run_cmd(["systemctl", "reload", "ssh"])

        return ApplyResult(
            True,
            "SSH sunucusu kuruldu, root girişine izin verildi.",
            details=f"Yapılandırma: {DROPIN}\nServis: ssh (etkin)",
        )

    def undo(self, data: dict) -> ApplyResult:
        try:
            DROPIN.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Drop-in silinemedi: %s", exc)
        run_cmd(["systemctl", "reload", "ssh"])
        return ApplyResult(True, "SSH root izni kaldırıldı (paket korundu).")
