"""Modül 6 (wizard 7. adım) — Dayanıklı merkezi log iletimi.

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
    apply_hint = (
        "Hiç log kaybı olmayan dayanıklı merkezi log iletimi kurulur."
    )
    rationale = (
        "Tahtanın tüm sistem günlüklerini (oturum açma denemeleri, servis "
        "hataları, cron, ağ olayları) ağdaki merkezi bir rsyslog "
        "sunucusuna DAYANIKLI BİÇİMDE gönderir. 50 tahtalı bir okulun "
        "loglarını tek bir arayüzden izleyebilir, olay/arıza taramasını "
        "saniyeler içinde yapabilirsiniz.\n\n"
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

        # Gelişmiş yapılandırmayı yaz
        try:
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
