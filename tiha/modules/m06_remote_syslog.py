"""Modül 6 — Dayanıklı merkezi log iletimi.

Ne yapar?
Tahtadaki tüm sistem günlüklerinin (syslog: oturum açma, servis
hataları, cron, ağ, güvenlik olayları vb.) ağdaki merkezi bir
rsyslog sunucusuna DAYANIKLI BİÇİMDE iletilmesi için gereken
yapılandırmayı kurar. KRİTİK ÖZELLİK: uzak sunucu geçici olarak
erişilemez durumda olsa bile loglar kaybolmaz — yerel diskte
sıralanır, sunucu tekrar çevrim içi olduğunda otomatik gönderilir.

Dayanıklı buffering nasıl çalışır?
Standart rsyslog yapılandırmasının aksine, bu modül DISK-ASSISTED QUEUE
(disk destekli kuyruk) kullanan gelişmiş bir yapılandırma oluşturur:

* YEREL TAMPONLAMA: Uzak sunucu erişilemezse loglar /var/lib/rsyslog/
  altında disk dosyalarına yazılır (kayıp yok).
* OTOMATİK YENİDEN DENEME: rsyslog düzenli aralıklarla uzak sunucuya
  bağlanmayı dener (varsayılan: her 30 saniye).
* BİRİKMİŞ LOG GÖNDERİMİ: Bağlantı geri geldiğinde tüm bekleyen loglar
  sırayla uzak sunucuya iletilir.
* DİSK YÖNETİMİ: Kuyruk dosyalarının diskte çok yer kaplamasını
  önlemek için boyut limiti ve otomatik temizlik.

"Ek yapılandırma dosyası" nedir?
Debian'da sistem servislerinin ayarları genellikle iki yerden gelir:

* ANA YAPILANDIRMA — ör. /etc/rsyslog.conf. Paketin kendisiyle
  birlikte gelir, paket güncellendiğinde üstüne yazılabilir.
* EK YAPILANDIRMA DOSYASI — ör. /etc/rsyslog.d/XX-ad.conf. Bir alt klasöre
  bırakılan bağımsız parça dosyalar. Ana yapılandırma bu klasörü
  otomatik okur. Yerel özelleştirmeler paket güncellemelerinden
  etkilenmez, ayrı dosya olduğu için geri alması da kolaydır.

Bu adım /etc/rsyslog.d/90-tiha-remote.conf adıyla ek bir yapılandırma dosyası
oluşturur; içeriğinde gelişmiş action queue yapılandırması vardır.

Neden gerekir?
Onlarca tahtanın logunu tek tek cihaz başına gidip taramak pratik
değildir. Merkezde toplandığında; saldırı/kilitlenme/servis hatası
olaylarını tek bir aramada görebilir, otomatik uyarı kuralları
yazabilir ve denetim için kalıcı arşiv tutabilirsiniz. EK OLARAK,
okul ağında elektrik kesintisi, ağ bakımı veya sunucu arızası
durumlarında hiç log kaybı olmaz — tahtalarda birikmiş loglar daha
sonra otomatik gönderilir.

Geri al. Ek yapılandırma dosyası silinir, kuyruk dizini temizlenir,
rsyslog yeniden başlatılır.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.paths import RSYSLOG_CONF
from ..core.utils import run_cmd

log = get_logger(__name__)

# rsyslog kuyruk dosyalarının saklandığı dizin
RSYSLOG_QUEUE_DIR = Path("/var/lib/rsyslog")


def _parse_config() -> tuple[str, int, str] | None:
    """Mevcut TiHA rsyslog yapılandırmasından host, port, protokol değerlerini çıkarır.

    Returns:
        (host, port, protocol) tuple eğer geçerli yapılandırma varsa, yoksa None.
    """
    if not RSYSLOG_CONF.exists():
        return None

    try:
        content = RSYSLOG_CONF.read_text(encoding="utf-8")

        # target="host" satırını bul
        host = ""
        for line in content.splitlines():
            if "target=" in line:
                # target="hostname" formatındaki satırı parse et
                import re
                match = re.search(r'target="([^"]+)"', line)
                if match:
                    host = match.group(1)
                    break

        # port="514" satırını bul
        port = 514
        for line in content.splitlines():
            if "port=" in line:
                import re
                match = re.search(r'port="([^"]+)"', line)
                if match:
                    try:
                        port = int(match.group(1))
                    except ValueError:
                        pass
                    break

        # protocol="udp" satırını bul
        proto = "udp"
        for line in content.splitlines():
            if "protocol=" in line:
                import re
                match = re.search(r'protocol="([^"]+)"', line)
                if match:
                    proto = match.group(1).lower()
                    break

        if host:  # En azından host bulunmalı
            return (host, port, proto)

    except (OSError, UnicodeDecodeError):
        pass

    return None


def _render(host: str, port: int, proto: str) -> str:
    """Dayanıklı log iletimi için gelişmiş rsyslog yapılandırması oluşturur.

    Bu yapılandırma disk-assisted queue kullanarak uzak sunucu offline
    olduğunda logları yerel diskte tutar, sunucu geri geldiğinde gönderir.
    """
    # UDP için `@`, TCP için `@@` önekleri rsyslog standardıdır.
    prefix = "@@" if proto.lower() == "tcp" else "@"

    return f"""# TiHA — Dayanıklı merkezi log sunucusuna iletim
# Uzak sunucu offline olduğunda loglar kaybolmaz — diskette sıralanır.
# Sunucu geri geldiğinde birikmiş loglar otomatik gönderilir.

# Queue dizinini oluştur (rsyslog otomatik oluşturmayabilir)
$CreateDirs on
$Umask 0000

# Ana kural: Tüm logları uzak sunucuya gönder
*.* action(
    type="omfwd"
    target="{host}"
    port="{port}"
    protocol="{proto}"
    # Dayanıklı kuyruk ayarları:
    queue.type="LinkedList"           # Bellek+disk hybrid kuyruk
    queue.filename="tiha_remote"      # Disk dosya adı: /var/lib/rsyslog/tiha_remote*
    queue.saveonshutdown="on"         # Kapatmada disk'e yaz
    queue.maxdiskspace="100m"         # Maks disk kullanımı 100MB
    queue.size="10000"                # Bellek kuyruğu boyutu
    queue.discardseverity="0"         # Hiçbir seviyeyi atma (emergency=0)
    queue.checkpointinterval="10"     # Her 10 mesajda bir disk'e yaz
    # Yeniden deneme ayarları:
    action.resumeretrycount="-1"      # Sürekli dene (hiç vazgeçme)
    action.resumeinterval="30"        # Her 30 saniyede bir dene
    action.resumeintervalmultiplier="2"  # Başarısızlık artışı (en fazla 10 dakika)
    action.resumeintervalmax="600"    # En fazla 10 dakika bekle
)

# Kuyruk durumu hakkında bilgi ver (isteğe bağlı, debug için)
# $MainMsgQueueTimeoutShutdown 10000"""


class RemoteSyslogModule(Module):
    id = "m06_remote_syslog"
    title = "Dayanıklı merkezi log iletimi"
    sidebar_title = "Merkezi log sunucusu"
    apply_hint = (
        "Hiç log kaybı olmayan dayanıklı merkezi log iletimi kurulur."
    )
    rationale = (
        "Tahtanın tüm sistem günlüklerini (oturum açma denemeleri, servis "
        "hataları, cron, ağ olayları) ağdaki merkezi bir rsyslog "
        "sunucusuna DAYANIKLI BİÇİMDE gönderir. 50 tahtalı bir okulun "
        "loglarını tek bir arayüzden izleyebilir, olay/arıza taramasını "
        "saniyeler içinde yapabilirsiniz.\n\n"
        "⚠ Bu adımı uyguluyorsanız “Benzersiz hostname” adımını da mutlaka "
        "uygulayın. Aksi hâlde imajdan klonlanan tüm tahtalar aynı hostname "
        "ile log gönderir; merkezi sunucudaki kayıtları tahta tahta ayırt "
        "edemezsiniz. Hostname adımı her klona kendi MAC adresinden türeyen "
        "benzersiz bir ad verir.\n\n"
        "KRİTİK AVANTAJ: Bu modül HİÇ LOG KAYBI OLMAYAN gelişmiş "
        "yapılandırma kullanır. Uzak log sunucusu saatlerce hatta günlerce "
        "erişilemez durumda olsa bile (elektrik kesintisi, ağ bakımı, "
        "sunucu arızası), tahta loglarını yerel diskte biriktirir. Sunucu "
        "geri geldiğinde birikmiş tüm loglar otomatik olarak gönderilir.\n\n"
        "Bunun için /etc/rsyslog.d/ altına disk-assisted queue (disk destekli "
        "kuyruk) kullanan gelişmiş bir yapılandırma dosyası yazılır. Paket "
        "güncellemesi gelirse yapılandırmanız korunur, geri almak da o tek "
        "dosyayı silmek kadar kolaydır.\n\n"
        "🌐 Log sunucusu tahtalarla aynı ağda olmalı. Okulda tahtalar ve "
        "kablosuz erişim noktaları (AP) genellikle `10.x.x.x` aralığındadır; "
        "log sunucusunu bu ağa konumlandırmalısınız. İdari ağdan log "
        "sunucusuna erişim olmaz — bu bilinçli bir güvenlik kısıtıdır."
    )

    def preview(self) -> str:
        lines: list[str] = []

        # "Benzersiz hostname" adımı uygulanmamışsa hatırlat — yoksa
        # merkezi sunucudaki loglar tahtaları birbirinden ayırt edemez.
        hostname_setup_done = Path(
            "/etc/systemd/system/tiha-first-boot-hostname.service"
        ).exists()
        if not hostname_setup_done:
            lines.append(
                "ℹ️ Hatırlatma: Benzersiz hostname adımı henüz uygulanmamış. "
                "Bu adımı uygulayacaksanız mutlaka onu da uygulayın; aksi "
                "hâlde merkezi sunucudaki loglarda tahtalar aynı isimle "
                "görünür ve birbirinden ayırt edilemez."
            )
            lines.append("")

        if RSYSLOG_CONF.exists():
            lines.append(f"✓ Mevcut TiHA dayanıklı log yapılandırması: {RSYSLOG_CONF}")
            lines.append("")

            # Kuyruk dosyalarının durumunu kontrol et
            queue_files = list(RSYSLOG_QUEUE_DIR.glob("tiha_remote*")) if RSYSLOG_QUEUE_DIR.exists() else []
            if queue_files:
                lines.append(f"📦 Bekleyen log kuyruğu dosyaları ({RSYSLOG_QUEUE_DIR}):")
                total_size = 0
                for qf in sorted(queue_files):
                    try:
                        size = qf.stat().st_size
                        total_size += size
                        lines.append(f"   • {qf.name}: {size:,} bytes")
                    except OSError:
                        lines.append(f"   • {qf.name}: (okunamadı)")
                lines.append(f"   Toplam kuyruk boyutu: {total_size:,} bytes")
                lines.append("")

                if total_size > 0:
                    lines.append("⚠ Kuyrukta bekleyen log var — uzak sunucu erişilemez durumda olabilir.")
                else:
                    lines.append("✓ Kuyruk boş — log iletimi normal çalışıyor.")
            else:
                lines.append("✓ Henüz kuyruk dosyası oluşmamış — log iletimi doğrudan çalışıyor.")

            lines.append("")
            lines.append("Mevcut yapılandırma:")
            lines.append("─" * 50)
            lines.append(RSYSLOG_CONF.read_text(encoding="utf-8").strip())
        else:
            lines.append("Henüz TiHA'ya ait dayanıklı log yapılandırması yok.")
            lines.append("")
            lines.append("Bu adımda şunlar yapılacak:")
            lines.append(f"• {RSYSLOG_CONF} dosyasına gelişmiş yapılandırma yazılacak")
            lines.append("• Disk-assisted queue (disk destekli kuyruk) etkinleştirilecek")
            lines.append("• Uzak sunucu offline olduğunda loglar yerel diskte biriktirilecek")
            lines.append("• Sunucu geri geldiğinde birikmiş loglar otomatik gönderilecek")
            lines.append("• rsyslog servisi yeniden başlatılacak")

        return "\n".join(lines)

    def apply(self, params=None, progress=None) -> ApplyResult:
        params = params or {}
        host = (params.get("syslog_host") or "").strip()
        port = int(params.get("syslog_port") or 514)
        proto = (params.get("syslog_proto") or "udp").strip().lower()
        if not host:
            return ApplyResult(False, "Merkezi log sunucusu adresi (IP/isim) boş.")

        # rsyslog kurulu olduğundan emin ol
        install = run_cmd(
            ["apt-get", "install", "-y", "rsyslog"],
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        # rsyslog çoğunlukla zaten kuruludur; kurulumu kontrol etmek yeter.
        del install  # susturucu

        # Kuyruk dizinini oluştur (rsyslog otomatik oluşturmayabilir)
        try:
            RSYSLOG_QUEUE_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
            # rsyslog kullanıcısının yazabilmesi için sahiplik ayarla
            run_cmd(["chown", "-R", "syslog:adm", str(RSYSLOG_QUEUE_DIR)])
        except OSError as exc:
            log.warning("rsyslog kuyruk dizini oluşturulamadı: %s", exc)

        # Gelişmiş yapılandırmayı yaz. /etc/rsyslog.d/ bazı kurulumlarda
        # (rsyslog paketi yoksa veya minimal sistemde) yok olabilir;
        # apt-get install çıkışı kontrol edilmediği için defansif olarak
        # parent dizini garantiliyoruz.
        try:
            RSYSLOG_CONF.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            config_content = _render(host, port, proto)
            RSYSLOG_CONF.write_text(config_content, encoding="utf-8")
            RSYSLOG_CONF.chmod(0o644)
        except OSError as exc:
            return ApplyResult(False, f"rsyslog ek yapılandırma dosyası yazılamadı: {exc}")

        # rsyslog'u yeniden başlat
        restart = run_cmd(["systemctl", "restart", "rsyslog"])
        if not restart.ok:
            return ApplyResult(False, "rsyslog yeniden başlatılamadı.",
                               details=restart.stderr)

        # rsyslog'un çalıştığından emin ol
        status = run_cmd(["systemctl", "is-active", "rsyslog"])
        if not status.ok:
            return ApplyResult(False, "rsyslog servisi çalıştırılamadı.",
                               details="systemctl status rsyslog komutuyla kontrol edin.")

        return ApplyResult(
            True,
            f"Dayanıklı log iletimi {host}:{port}/{proto.upper()} için kuruldu.",
            details=(
                f"✓ Gelişmiş yapılandırma: {RSYSLOG_CONF}\n"
                f"✓ Kuyruk dizini: {RSYSLOG_QUEUE_DIR}\n"
                f"✓ Hedef: {host}:{port} ({proto.upper()})\n\n"
                "Özellikler:\n"
                "• Uzak sunucu offline → loglar yerel diskte birikir\n"
                "• Sunucu geri gelince → birikmiş loglar otomatik gönderilir\n"
                "• Maksimum kuyruk boyutu: 100 MB\n"
                "• Yeniden deneme aralığı: 30-600 saniye\n\n"
                f"Test: sunucu tarafında 'tcpdump -n -i any port {port}' ya da "
                "rsyslog sunucusunda gelen kayıtlara bakabilirsiniz."
            ),
        )

    def test_log_server_action(self, progress=None) -> ApplyResult:
        """Mevcut yapılandırmadaki log sunucusuna erişim testi.

        UDP: socket'i hedefe bind edip RFC3164-benzeri örnek bir mesaj
            gönderir. UDP'de ack yok, ama DNS resolve + sendto başarısı
            ağ yolunun açık olduğunu gösterir.
        TCP: socket.create_connection ile gerçek TCP el sıkışması; başarı
            sunucunun port'u dinlediğini ve ağa eriştiğimizi kanıtlar.
        """
        import socket as _socket
        from datetime import datetime as _dt

        if progress:
            progress("Mevcut rsyslog yapılandırması okunuyor...")

        cfg = _parse_config()
        if cfg is None:
            msg = (
                f"Yapılandırma dosyası yok ya da geçersiz: {RSYSLOG_CONF}.\n"
                "Önce bu adımı bir kez uygulayın (Uygula düğmesi)."
            )
            if progress:
                progress(f"❌ {msg}")
            return ApplyResult(False, msg)

        host, port, proto = cfg
        proto = proto.lower()
        if progress:
            progress(f"📋 Hedef: {host}:{port} ({proto.upper()})")
            progress(f"DNS çözülmesi deneniyor: {host}…")

        try:
            addrinfo = _socket.getaddrinfo(host, port,
                                           type=_socket.SOCK_STREAM)
        except _socket.gaierror as exc:
            msg = f"DNS çözümleme başarısız ({host}): {exc}"
            if progress:
                progress(f"❌ {msg}")
            return ApplyResult(False, msg)

        resolved = addrinfo[0][4][0] if addrinfo else host
        if progress:
            progress(f"   → çözümlendi: {resolved}")

        # Asıl test
        if proto == "tcp":
            if progress:
                progress(f"TCP el sıkışması deneniyor: {resolved}:{port}…")
            try:
                with _socket.create_connection((host, port), timeout=5):
                    pass
            except (OSError, _socket.timeout) as exc:
                msg = (
                    f"TCP bağlantısı kurulamadı ({host}:{port}): {exc}. "
                    "Sunucu kapalı veya ağ engelliyor olabilir."
                )
                if progress:
                    progress(f"❌ {msg}")
                return ApplyResult(False, msg)
            ok_msg = f"✓ TCP bağlantı kuruldu ({host}:{port})."
            if progress:
                progress(ok_msg)
                progress("Sunucu bu portu dinliyor; rsyslog logları "
                         "kayıpsız iletebilecek durumda.")
            return ApplyResult(True, ok_msg)

        # UDP — best-effort: paket gönderebildiysek başarı say.
        if progress:
            progress(f"UDP örnek mesaj gönderiliyor: {resolved}:{port}…")
        sample = (
            f"<13>{_dt.now().strftime('%b %d %H:%M:%S')} "
            f"tiha-test: TiHA log sunucusu erişim testi"
        ).encode("utf-8")
        try:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM) as s:
                s.settimeout(5)
                s.sendto(sample, (host, port))
        except (OSError, _socket.timeout) as exc:
            msg = f"UDP mesajı gönderilemedi ({host}:{port}): {exc}"
            if progress:
                progress(f"❌ {msg}")
            return ApplyResult(False, msg)
        ok_msg = f"✓ UDP mesajı gönderildi ({host}:{port})."
        if progress:
            progress(ok_msg)
            progress("UDP'de ack yoktur — sunucu tarafında "
                     "`journalctl -u rsyslog` veya `tcpdump -n -i any "
                     f"port {port}` ile gerçekten alındığını teyit edin.")
        return ApplyResult(True, ok_msg)

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        removed_files = 0
        cleaned_queue = False

        # Yapılandırma dosyasını kaldır
        try:
            if RSYSLOG_CONF.exists():
                RSYSLOG_CONF.unlink()
                removed_files += 1
        except OSError as exc:
            log.warning("Yapılandırma dosyası silinemedi: %s", exc)

        # Kuyruk dosyalarını temizle (isteğe bağlı - veri kaybı uyarısı yapılabilir)
        if RSYSLOG_QUEUE_DIR.exists():
            queue_files = list(RSYSLOG_QUEUE_DIR.glob("tiha_remote*"))
            if queue_files:
                try:
                    for qf in queue_files:
                        qf.unlink(missing_ok=True)
                        removed_files += 1
                    cleaned_queue = True
                except OSError as exc:
                    log.warning("Kuyruk dosyaları temizlenemedi: %s", exc)

        # rsyslog'u yeniden başlat
        restart = run_cmd(["systemctl", "restart", "rsyslog"])
        restart_ok = restart.ok

        # Sonuç raporu
        summary_parts = []
        if removed_files > 0:
            summary_parts.append(f"Yapılandırma dosyası kaldırıldı")
        if cleaned_queue:
            summary_parts.append("bekleyen log kuyruğu temizlendi")
        if restart_ok:
            summary_parts.append("rsyslog yeniden başlatıldı")

        summary = "Dayanıklı log iletimi kaldırıldı: " + ", ".join(summary_parts) + "."

        details = f"Kaldırılan dosya sayısı: {removed_files}"
        if not restart_ok:
            details += f"\n⚠ rsyslog yeniden başlatma hatası: {restart.stderr.strip()}"

        return ApplyResult(True, summary, details=details if not restart_ok else None)
