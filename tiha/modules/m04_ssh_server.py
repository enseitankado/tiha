"""Modül 4 — SSH sunucusu kur ve root uzak bağlantısına izin ver.

**Ne yapar?**
``openssh-server`` paketini (yoksa) kurar; ``/etc/ssh/sshd_config.d/``
altına ``PermitRootLogin yes`` ve ``PasswordAuthentication yes`` ayarlarını
içeren bir drop-in bırakır; ``ssh`` servisini etkinleştirir.

**Neden gerekir?**
Dağıtılmış tahtalarda uzaktan teknik destek/bakım için root erişimi
gereklidir.

**Geri al (tam restore).**
- Drop-in dosyası silinir.
- Eğer TiHA `openssh-server`'ı *kurduysa* (daha önce kurulu değildi),
  paket ``apt-get purge`` ile kaldırılır — başlangıçtaki temiz duruma
  dönülür. Daha önce zaten kuruluysa paket korunur.
- Servis durumu uygun şekilde ayarlanır.

Apply sırasında apt çıktısı **canlı olarak** ekrana akar; kullanıcı
bekliyor gibi hissetmez.
"""

from __future__ import annotations

from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.utils import run_cmd, run_cmd_stream

log = get_logger(__name__)

DROPIN = Path("/etc/ssh/sshd_config.d/99-tiha.conf")

DROPIN_CONTENT = """# TiHA tarafından yazılmıştır.
PermitRootLogin yes
PasswordAuthentication yes
"""


def _is_package_installed(name: str) -> bool:
    """``dpkg-query`` ile paket kurulu mu denetler."""
    result = run_cmd(["dpkg-query", "-W", "-f=${Status}", name])
    return result.ok and "install ok installed" in result.stdout


class SSHServerModule(Module):
    id = "m04_ssh_server"
    title = "SSH sunucusu (root girişi)"
    streams_output = True
    rationale = (
        "Tahta sınıfa indikten sonra her bir cihazın başına gidip fiziksel "
        "erişimle bakım yapmak pratik değildir. Bu adım, teknik destek "
        "ekibinin ağdaki bir yönetim istasyonundan komut satırıyla tahtaya "
        "bağlanıp sorun giderme, yapılandırma güncelleme ve günlük "
        "inceleme yapabilmesi için SSH sunucusunu kurar. Root girişi, "
        "sistemin her yerine dokunabilen bir bakım hesabı gerektiği için "
        "açık bırakılır — erişim, ağ tarafından (güvenlik duvarı, VLAN) "
        "yönetim istasyonlarıyla sınırlanmalıdır. Apt çıktısı aşağıda "
        "canlı olarak akar."
    )

    def preview(self) -> str:
        installed = _is_package_installed("openssh-server")
        return (
            "openssh-server zaten kurulu — yalnızca yapılandırma eklenecek."
            if installed
            else "openssh-server kurulacak ve yapılandırma drop-in'i eklenecek."
        )

    def apply(self, params=None, progress: ProgressCallback | None = None) -> ApplyResult:
        # Başlangıç durumu (undo için saklanacak)
        was_installed_before = _is_package_installed("openssh-server")
        dropin_existed_before = DROPIN.exists()

        if progress:
            progress(f"Başlangıç: openssh-server {'kurulu' if was_installed_before else 'kurulu değil'}")
            progress(f"Başlangıç: drop-in {'var' if dropin_existed_before else 'yok'}")

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
                progress("\n==== apt-get install openssh-server ====")
            inst = run_cmd_stream(
                ["apt-get", "install", "-y", "openssh-server"],
                progress=progress,
                env={"DEBIAN_FRONTEND": "noninteractive"},
                timeout=600,
            )
            if not inst.ok:
                return ApplyResult(False, "openssh-server kurulamadı.",
                                   data={"was_installed_before": was_installed_before})

        # Drop-in yaz
        try:
            DROPIN.parent.mkdir(parents=True, exist_ok=True)
            DROPIN.write_text(DROPIN_CONTENT, encoding="utf-8")
            DROPIN.chmod(0o644)
        except OSError as exc:
            return ApplyResult(
                False, f"SSH drop-in yazılamadı: {exc}",
                data={"was_installed_before": was_installed_before,
                      "dropin_existed_before": dropin_existed_before},
            )
        if progress:
            progress(f"Drop-in yazıldı: {DROPIN}")

        # Servis
        en = run_cmd(["systemctl", "enable", "--now", "ssh"])
        rel = run_cmd(["systemctl", "reload", "ssh"])
        if progress:
            progress(f"ssh enable/reload: {'tamam' if en.ok else 'hata'} / {'tamam' if rel.ok else 'hata'}")

        return ApplyResult(
            True,
            "SSH sunucusu kuruldu, root girişine izin verildi.",
            details=f"Yapılandırma: {DROPIN}\nServis: ssh (etkin)",
            data={
                "was_installed_before": was_installed_before,
                "dropin_existed_before": dropin_existed_before,
            },
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        data = data or {}
        was_installed_before = bool(data.get("was_installed_before", True))
        dropin_existed_before = bool(data.get("dropin_existed_before", False))

        # 1) Drop-in'i temizle (yalnızca biz eklemişsek)
        if not dropin_existed_before:
            try:
                DROPIN.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("Drop-in silinemedi: %s", exc)
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
            if not purge.ok:
                return ApplyResult(False, "openssh-server kaldırılamadı.",
                                   details=purge.stderr)
            return ApplyResult(True, "SSH yapılandırması ve openssh-server paketi kaldırıldı.")

        return ApplyResult(True, "SSH root izni kaldırıldı (paket zaten başlangıçta kuruluydu, korundu).")
