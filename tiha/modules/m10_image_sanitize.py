"""Modül 10 — İmaj için sanitizasyon (son adım).

**Ne yapar?**
İmajlanan tahtanın her hedef makinede temiz çalışması için şunları siler:

- ``/etc/machine-id`` ve ``/var/lib/dbus/machine-id`` (ilk açılışta yeniden
  üretilmesi için boşaltılır)
- SSH sunucusunun host anahtarları (``/etc/ssh/ssh_host_*``)
- ``/etc/NetworkManager/system-connections/`` altındaki tüm ağ bağlantı
  profilleri. Bu klasörde WiFi SSID + parolası, Ethernet için statik IP/
  DNS/gateway ayarları, her bağlantının benzersiz UUID'si ve varsa MAC
  filtreleri tutulur. İmajla aynen kopyalanırsa tüm tahtalarda aynı UUID
  yüzünden NetworkManager karışır, bir tahtanın WiFi parolası diğerlerine
  sızar ve yanlış ağ ayarları devralınır.
- eta-register ve ahenk önbellek/log'ları
- Sistem günlük dosyalarının içeriği
- ``/home/etapadmin`` altında kabuk geçmişi
- ``/root`` ve ``/home/etapadmin`` altındaki önbellek dizinleri
- ``/var/tmp`` ve ``/tmp`` içerikleri

Ayrıca SSH host anahtarlarını ilk açılışta yeniden üretecek bir oneshot
servis kurar.

**Neden gerekir?**
Bu adım çalıştırılmadan alınan imajda tüm klonlarda aynı ``machine-id``,
aynı SSH host key'i, aynı NetworkManager UUID'si olur — hem gizlilik hem de
ağ kararlılığı açısından ciddi sorunlar çıkarır. ``eta-register`` özelinde
ise: bu modül, kayıt işlemi yapılmış bir imajın yeni tahtada *temiz* olarak
yeniden kayıt olmasını mümkün kılar.

**Geri al.** Bu işlemin pratik olarak geri alınması anlamlı değildir;
modül ``undo_supported = False`` olarak işaretlenmiştir.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.utils import run_cmd

log = get_logger(__name__)

REGEN_SSH_SERVICE = Path("/etc/systemd/system/tiha-first-boot-sshkeys.service")
REGEN_SSH_SCRIPT = Path("/usr/local/sbin/tiha-first-boot-sshkeys.sh")
REGEN_SSH_SENTINEL = Path("/var/lib/tiha/first-boot-sshkeys.done")

REGEN_SSH_SCRIPT_CONTENT = f"""#!/bin/bash
# TiHA — ilk açılışta SSH host anahtarlarını yeniden üretir.
set -euo pipefail
[[ -f {REGEN_SSH_SENTINEL} ]] && exit 0
rm -f /etc/ssh/ssh_host_*
ssh-keygen -A
mkdir -p "$(dirname {REGEN_SSH_SENTINEL})"
touch {REGEN_SSH_SENTINEL}
systemctl restart ssh || true
"""

REGEN_SSH_SERVICE_CONTENT = f"""[Unit]
Description=TiHA — İlk açılışta SSH host anahtarlarını üret
Before=ssh.service
ConditionPathExists=!{REGEN_SSH_SENTINEL}

[Service]
Type=oneshot
ExecStart={REGEN_SSH_SCRIPT}

[Install]
WantedBy=multi-user.target
"""


def _truncate(path: Path) -> bool:
    try:
        if path.exists():
            path.write_text("", encoding="utf-8")
            return True
    except OSError as exc:
        log.warning("Boşaltılamadı %s: %s", path, exc)
    return False


def _rm(path: Path) -> bool:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
        return True
    except OSError as exc:
        log.warning("Silinemedi %s: %s", path, exc)
        return False


class ImageSanitizeModule(Module):
    id = "m10_image_sanitize"
    title = "İmaj için sanitize"
    rationale = (
        "İmaj alınmadan önce çalıştırılır. Tüm tahtalarda aynı kalırsa sorun "
        "çıkaracak tekil bilgileri (machine-id, SSH host anahtarları, "
        "NetworkManager bağlantı dosyaları, log içerikleri, kabuk geçmişi, "
        "önbellekler) temizler ve ilk açılışta SSH anahtarlarının yeniden "
        "üretilmesini sağlayan bir servis kurar."
    )
    undo_supported = False

    def preview(self) -> str:
        return (
            "Aşağıdakiler temizlenecek:\n"
            "  • /etc/machine-id ve /var/lib/dbus/machine-id\n"
            "  • /etc/ssh/ssh_host_*\n"
            "  • /etc/NetworkManager/system-connections/*\n"
            "  • /var/log/** dosya içerikleri (dosya adları korunur)\n"
            "  • ~etapadmin/.bash_history, önbellekler\n"
            "  • eta-register / ahenk cache'leri\n"
            "  • /tmp ve /var/tmp içerikleri"
        )

    def apply(self, params: dict | None = None) -> ApplyResult:
        ops = []

        # 1) machine-id
        _truncate(Path("/etc/machine-id"))
        _rm(Path("/var/lib/dbus/machine-id"))
        ops.append("machine-id temizlendi")

        # 2) SSH host anahtarları + ilk açılışta üretme servisi
        for p in Path("/etc/ssh").glob("ssh_host_*"):
            _rm(p)
        REGEN_SSH_SCRIPT.write_text(REGEN_SSH_SCRIPT_CONTENT, encoding="utf-8")
        REGEN_SSH_SCRIPT.chmod(0o755)
        REGEN_SSH_SERVICE.write_text(REGEN_SSH_SERVICE_CONTENT, encoding="utf-8")
        run_cmd(["systemctl", "daemon-reload"])
        run_cmd(["systemctl", "enable", REGEN_SSH_SERVICE.name])
        ops.append("SSH host anahtarları silindi, ilk açılışta yenilenecek")

        # 3) NetworkManager bağlantıları
        nm_dir = Path("/etc/NetworkManager/system-connections")
        if nm_dir.exists():
            for conn in nm_dir.iterdir():
                _rm(conn)
            ops.append("NetworkManager bağlantı dosyaları silindi")

        # 4) Log içerikleri (dosyaları silmek journald vb. bozabilir, yalnızca boşalt)
        log_root = Path("/var/log")
        if log_root.exists():
            for item in log_root.rglob("*"):
                if item.is_file():
                    # tiha.log hariç — aktif oturumda yazılıyor
                    if "tiha" in item.name:
                        continue
                    _truncate(item)
            ops.append("/var/log içerikleri boşaltıldı (tiha.log hariç)")

        # 5) Kullanıcı önbellek ve geçmişler
        admin_home = Path("/home/etapadmin")
        for p in [
            admin_home / ".bash_history",
            admin_home / ".cache" / "eta-register",
            admin_home / ".cache" / "ahenk",
            admin_home / ".cache" / "tiha",
            Path("/root/.bash_history"),
        ]:
            _rm(p)
        ops.append("Kullanıcı geçmiş ve önbellekleri temizlendi")

        # 6) /tmp ve /var/tmp içerikleri
        for parent in (Path("/tmp"), Path("/var/tmp")):
            if parent.exists():
                for child in parent.iterdir():
                    _rm(child)
        ops.append("/tmp ve /var/tmp boşaltıldı")

        return ApplyResult(
            True,
            "İmaj sanitizasyonu tamamlandı. Artık imaj alabilirsiniz.",
            details="\n".join(f"• {o}" for o in ops),
        )

    def undo(self, data: dict) -> ApplyResult:
        return ApplyResult(False, "Bu işlem geri alınamaz.")
