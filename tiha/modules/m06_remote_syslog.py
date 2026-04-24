"""Modül 6 (wizard 7. adım) — Merkezi log sunucusuna yönlendirme.

**Ne yapar?**
Tahtadaki tüm sistem günlüklerinin (``syslog``: oturum açma, servis
hataları, cron, ağ, güvenlik olayları vb.) ağdaki merkezi bir
``rsyslog`` sunucusuna iletilmesi için gereken yapılandırmayı kurar.

**"Ek yapılandırma dosyası" nedir?**
Debian'da sistem servislerinin ayarları genellikle iki yerden gelir:

* **Ana yapılandırma** — ör. ``/etc/rsyslog.conf``. Paketin kendisiyle
  birlikte gelir, paket güncellendiğinde üstüne yazılabilir.
* **Ek yapılandırma dosyası** — ör. ``/etc/rsyslog.d/XX-ad.conf``. Bir alt klasöre
  bırakılan bağımsız parça dosyalar. Ana yapılandırma bu klasörü
  otomatik okur. Yerel özelleştirmeler paket güncellemelerinden
  etkilenmez, ayrı dosya olduğu için geri alması da kolaydır.

Bu adım ``/etc/rsyslog.d/90-tiha-remote.conf`` adıyla ek bir yapılandırma dosyası
oluşturur; içeriğinde tek bir kural vardır:

    *.*  @host:port              (UDP için)  ya da
    *.*  @@host:port             (TCP için)

``*.*`` = "her kategori ve her seviyedeki tüm loglar", ``@`` = UDP
yönlendirme, ``@@`` = TCP yönlendirme. Sonunda ``rsyslog`` servisi
yeniden başlatılır.

**Neden gerekir?**
Onlarca tahtanın logunu tek tek cihaz başına gidip taramak pratik
değildir. Merkezde toplandığında; saldırı/kilitlenme/servis hatası
olaylarını tek bir aramada görebilir, otomatik uyarı kuralları
yazabilir ve denetim için kalıcı arşiv tutabilirsiniz. Okulda bir
rsyslog sunucusu yoksa bu adım atlanabilir; ancak bir kere rsyslog
sunucusu kurulduğunda tüm tahtalara ayrı ayrı gitmek zorunda kalmamak
için bu adım imaj öncesi tavsiye edilir.

**Geri al.** Ek yapılandırma dosyası silinir, ``rsyslog`` yeniden başlatılır.
"""

from __future__ import annotations

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.paths import RSYSLOG_CONF
from ..core.utils import run_cmd

log = get_logger(__name__)


def _render(host: str, port: int, proto: str) -> str:
    # UDP için `@`, TCP için `@@` önekleri rsyslog standardıdır.
    prefix = "@@" if proto.lower() == "tcp" else "@"
    return (
        "# TiHA — merkezi log sunucusuna iletim (ek yapılandırma dosyası).\n"
        "# *.* = her kategori+seviyedeki tüm loglar\n"
        "# @host  → UDP ile ilet\n"
        "# @@host → TCP ile ilet\n"
        f"*.*  {prefix}{host}:{port}\n"
    )


class RemoteSyslogModule(Module):
    id = "m06_remote_syslog"
    title = "Merkezi log iletimi"
    rationale = (
        "Tahtanın tüm sistem günlüklerini (oturum açma denemeleri, servis "
        "hataları, cron, ağ olayları) ağdaki merkezi bir rsyslog "
        "sunucusuna gönderir. Böylece 50 tahtalı bir okulun loglarını "
        "tek bir arayüzden izleyebilir, olay/arıza taramasını saniyeler "
        "içinde yapabilirsiniz. Bunun için /etc/rsyslog.d/ altına yalnızca "
        "TiHA'ya ait bir 'ek yapılandırma dosyası' yazılır — paket güncellemesi "
        "gelirse yapılandırmanız korunur, geri almak da o tek dosyayı "
        "silmek kadar kolaydır."
    )

    def preview(self) -> str:
        if RSYSLOG_CONF.exists():
            return (
                f"Mevcut TiHA yapılandırması: {RSYSLOG_CONF}\n\n"
                + RSYSLOG_CONF.read_text(encoding="utf-8")
            )
        return (
            "Henüz TiHA'ya ait bir yapılandırma yok. Bu adımda aşağıdaki içerikli dosya yazılacak:\n"
            f"  {RSYSLOG_CONF}\n\n"
            "  *.*  @<host>:<port>   (UDP varsayılan)\n"
            "  *.*  @@<host>:<port>  (TCP seçilirse)\n\n"
            "Ardından 'systemctl restart rsyslog' çalıştırılacak."
        )

    def apply(self, params=None, progress=None) -> ApplyResult:
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
        # rsyslog çoğunlukla zaten kuruludur; kurulumu kontrol etmek yeter.
        del install  # susturucu

        try:
            RSYSLOG_CONF.write_text(_render(host, port, proto), encoding="utf-8")
            RSYSLOG_CONF.chmod(0o644)
        except OSError as exc:
            return ApplyResult(False, f"rsyslog ek yapılandırma dosyası yazılamadı: {exc}")

        restart = run_cmd(["systemctl", "restart", "rsyslog"])
        if not restart.ok:
            return ApplyResult(False, "rsyslog yeniden başlatılamadı.",
                               details=restart.stderr)

        return ApplyResult(
            True,
            f"Loglar {host}:{port}/{proto.upper()} adresine iletilecek.",
            details=(
                f"Yazılan ek yapılandırma dosyası: {RSYSLOG_CONF}\n"
                f"Kural: *.* {'@@' if proto == 'tcp' else '@'}{host}:{port}\n"
                "Test: sunucu tarafında 'tcpdump -n -i any port {port}' ya da "
                "rsyslog sunucusunda gelen kayıtlara bakabilirsiniz."
            ),
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        try:
            RSYSLOG_CONF.unlink(missing_ok=True)
        except OSError:
            pass
        run_cmd(["systemctl", "restart", "rsyslog"])
        return ApplyResult(True, "Merkezi log iletimi için eklenen yapılandırma dosyası kaldırıldı.")
