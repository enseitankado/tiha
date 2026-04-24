"""Modül 10 (wizard 11. adım) — İmaj için sanitizasyon (son adım).

**İş akışındaki yeri**

Normal iş akışımız şöyledir:

    1. Boş tahtaya Pardus ETAP temiz kurulum yapılır.
    2. etapadmin'e geçilir, TiHA bu komutla başlatılır.
    3. 1–10 arası adımlar sırasıyla uygulanır.
    4. Tahta bir kez yeniden başlatılır (servisler test edilir).
    5. İmaj alma aracıyla (Clonezilla vb.) diskin imajı alınır.
    6. Bu imaj diğer tahtalara uygulanır.

**Neden bu adıma ihtiyaç var?**
1. ve 4. adımlar arasında sistemde tekil kalması gereken çok sayıda
**benzersiz kimlik** oluşur:

* ``/etc/machine-id`` — kurulum anında üretilir; systemd, journald,
  gnome-keyring, Cinnamon oturum yönetimi, Ahenk gibi bileşenler buna
  bağlıdır. İmajla aynen kopyalanırsa **tüm klonlar aynı machine-id'ye**
  sahip olur; journald logları karışır, bazı servisler yanlış davranır.
* ``/etc/ssh/ssh_host_*`` — ``sshd`` ilk başlayışında üretilmiş olan
  ed25519/rsa/ecdsa host anahtarları. İmaj klonlarında aynıysa ağdaki
  istemciler "hey, bu 50 farklı tahta aslında aynı sunucu mu?" diye
  şüphelenip bağlantıyı reddeder (known_hosts çakışması). Ayrıca bir
  tahtanın özel anahtarı ele geçirilirse 50 tahta birden ifşa olur.
* ``/etc/NetworkManager/system-connections/*`` — kurulum sırasında test
  için WiFi parolası girdiyseniz burada düz yazılı kalır. Her klona
  aynı parola gider. Ayrıca her bağlantının UUID'si de aynıdır.
* ``/var/log/**`` — kurulum günleri, TiHA'nın kendi çalıştırma logları,
  başarısız sudo denemeleri; **gizlilik** açısından silinmeli.
* ``~etapadmin/.bash_history`` — TiHA bootstrap komutunuz, elle yazdığınız
  komutlar; klonlarda kalmamalı.
* ``/tmp``, ``/var/tmp`` — geçici indirme/açma artıkları.

**Sonuç:** Bu adım olmadan alınan bir imaj **fonksiyonel** çalışır
gibi görünür ama gerçek operasyonda adlandırma, güvenlik, SSH ve
NetworkManager çakışmaları yaratır. İmaj öncesi son adım olarak zorunludur.

Bu adım ayrıca SSH host anahtarlarını bir sonraki açılışta **her klonun
kendi anahtar setini** üretmesi için bir oneshot servis bırakır.

**Geri al.** Sanitizasyonun geri alınması anlamlı değildir (silinen
benzersiz kimliği üretemeyiz). Modül ``undo_supported = False``
olarak işaretlenmiştir.
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
    apply_hint = (
        "Son adım: tekil kimlikler ve izler temizlenir — imaj alınabilir."
    )
    rationale = (
        "İmaj almadan önce çalıştırılan ZORUNLU son adım. Aksi hâlde "
        "imajdan çıkan bütün tahtalarda aynı machine-id, aynı SSH host "
        "anahtarı, aynı NetworkManager UUID'si ve aynı kabuk geçmişi "
        "olur — bu bir işe yaramaz mı? Yarar — ama funcationel görünse de "
        "journald'ın dağınık davranışı, ssh bağlantılarının 'host key "
        "değişti' uyarısı, WiFi parolasının 50 tahtaya aynen sızması "
        "gibi sonuçlar doğar. TiHA burada tüm bu tekil kimlikleri "
        "temizler, ilk boot'ta kendi benzersiz SSH anahtarını üreten bir "
        "servis bırakır; ve tahtanın artık imaj alınmaya hazır olmasını "
        "sağlar. Geri alma anlamlı değildir — silinen kimliği üretemeyiz."
    )
    undo_supported = False

    def preview(self) -> str:
        return (
            "Bu SON adımdır. Uygulandıktan sonra tahta imaj alınmaya hazırdır.\n"
            "Aşağıdakiler temizlenecek (geri alınamaz):\n\n"
            "  • /etc/machine-id ve /var/lib/dbus/machine-id\n"
            "      → ilk boot'ta systemd yeniden üretir\n"
            "  • /etc/ssh/ssh_host_*\n"
            "      → first-boot servisi her tahtaya özel anahtar üretir\n"
            "  • /etc/NetworkManager/system-connections/* (WiFi parolaları dahil)\n"
            "  • /var/log/** içerikleri (dosya adları korunur, sadece boşaltılır)\n"
            "  • ~etapadmin/.bash_history ve önbellek klasörleri\n"
            "  • eta-register / ahenk cache'leri\n"
            "  • /tmp ve /var/tmp içerikleri\n\n"
            "Uyguladıktan sonra: tahtayı bir kez yeniden başlatmanıza gerek yok; "
            "doğrudan imaj alma aracınızı (Clonezilla vb.) çalıştırabilirsiniz."
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

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        return ApplyResult(False, "Bu işlem geri alınamaz.")
