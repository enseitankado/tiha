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


class SSHServerModule(Module):
    id = "m04_ssh_server"
    title = "SSH sunucusu (root girişi)"
    apply_hint = (
        "openssh-server kurulur, uzak root girişi açılır."
    )
    streams_output = True
    rationale = (
        "Tahta sınıfa indikten sonra her birinin başına fiziksel olarak "
        "gidip bakım yapmak pratik değildir. Bu adım, uzak terminalle "
        "bağlanıp sorun giderme, ayar değişikliği ve günlük inceleme "
        "yapabilmeniz için SSH sunucusunu kurar ve root kullanıcısının "
        "uzak oturum açmasına izin verir.\n\n"
        "🌐 Uzaktan bağlanmak için tahtayla aynı ağda olmalısınız. "
        "Okulda tahtalar ve kablosuz erişim noktaları (AP) genellikle "
        "`10.x.x.x` aralığındadır — bu ağdaki bir bilgisayardan "
        "`ssh root@<tahta-ip>` komutunu kullanırsınız. Farklı bir ağdan "
        "(örn. öğrenci/misafir ağları) ulaşılamaz; bu bilinçli bir "
        "güvenlik kısıtıdır. Okul içinde güvenlik duvarı/VLAN ile erişimi "
        "yalnızca yönetim istasyonlarıyla sınırlamanız tavsiye edilir.\n\n"
        "Apt çıktısı aşağıda canlı olarak akar."
    )

    def preview(self) -> str:
        installed = _is_package_installed("openssh-server")
        return (
            "openssh-server zaten kurulu — yalnızca yapılandırma eklenecek."
            if installed
            else "openssh-server kurulacak ve yapılandırma ek bir yapılandırma dosyası yazılacak."
        )

    def apply(self, params=None, progress: ProgressCallback | None = None) -> ApplyResult:
        # Başlangıç durumu (undo için saklanacak)
        was_installed_before = _is_package_installed("openssh-server")
        conf_existed_before = SSH_CONF.exists()

        if progress:
            progress(f"Başlangıç: openssh-server {'kurulu' if was_installed_before else 'kurulu değil'}")
            progress(f"Başlangıç: ek yapılandırma dosyası {'var' if conf_existed_before else 'yok'}")

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

        # Ek yapılandırma dosyası yaz
        try:
            SSH_CONF.parent.mkdir(parents=True, exist_ok=True)
            SSH_CONF.write_text(SSH_CONF_CONTENT, encoding="utf-8")
            SSH_CONF.chmod(0o644)
        except OSError as exc:
            return ApplyResult(
                False, f"SSH ek yapılandırma dosyası yazılamadı: {exc}",
                data={"was_installed_before": was_installed_before,
                      "conf_existed_before": conf_existed_before},
            )
        if progress:
            progress(f"Ek yapılandırma dosyası yazıldı: {SSH_CONF}")

        # Servis
        en = run_cmd(["systemctl", "enable", "--now", "ssh"])
        rel = run_cmd(["systemctl", "reload", "ssh"])
        if progress:
            progress(f"ssh enable/reload: {'tamam' if en.ok else 'hata'} / {'tamam' if rel.ok else 'hata'}")

        return ApplyResult(
            True,
            "SSH sunucusu kuruldu, root girişine izin verildi.",
            details=f"Yapılandırma: {SSH_CONF}\nServis: ssh (etkin)",
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
            if not purge.ok:
                return ApplyResult(False, "openssh-server kaldırılamadı.",
                                   details=purge.stderr)
            return ApplyResult(True, "SSH yapılandırması ve openssh-server paketi kaldırıldı.")

        return ApplyResult(True, "SSH root izni kaldırıldı (paket zaten başlangıçta kuruluydu, korundu).")
