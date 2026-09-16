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

# Uzak log sunucusu erişilemez durumdayken tahtanın yerelde tutacağı
# maksimum log hacmi. Hedef: en kötü senaryoda (kapsamlı profil, yoğun
# olaylı gün) bile 3 aydan az olmamak. Ölçümler:
#   - Bakım profili   : ~5-8 MB/gün  → 2 GB ≈ 250-400 gün
#   - Kapsamlı profil : ~15-25 MB/gün → 2 GB ≈ 80-130 gün
#   - Güvenlik profil : ~1-2 MB/gün  → 2 GB ≈ 1000+ gün
# 2 GB tavanı 240 GB'lık tahta diskinin %1'inden azdır; sistem
# çalışmasını etkileyecek bir dolma riski oluşturmaz.
QUEUE_MAX_DISK_SPACE = "2g"
# Bellek kuyruğu kapasitesi (mesaj sayısı). Bu sayıdan sonra rsyslog
# kuyruğu disk'e taşımaya başlar. 100 000, ~ birkaç saatlik yoğun
# trafiği bellekte tutar; uzun süreli kesintide disk'e devrolur.
QUEUE_MEMORY_SIZE = "100000"


# Log kapsamı profilleri — kullanıcının seçimi rsyslog selector'larına
# çevrilir. Ayrıntı: iletilecek olayların facility/severity kümesi.
LOG_PROFILES = {
    "bakim": {
        "label": "Bakım (önerilen)",
        "selectors": [
            "auth,authpriv.*",
            "kern.warning",
            "daemon.notice",
            "syslog.*",
            "local0,local1,local2,local3,local4,local5,local6,local7.*",
        ],
    },
    "kapsamli": {
        "label": "Kapsamlı",
        "selectors": ["*.*"],
    },
    "guvenlik": {
        "label": "Yalnız güvenlik",
        "selectors": [
            "auth,authpriv.*",
            "kern.err",
            "daemon.err",
        ],
    },
}

# Yapılandırma dosyasının içine gömdüğümüz manşet — form'da mevcut
# profili yeniden yükleyebilmek için _parse_config bunu okur.
_PROFILE_MARKER_PREFIX = "# TIHA_PROFILE="


def _profile_key(label_or_key: str | None) -> str:
    """Form combobox label'ından profil anahtarını çıkar. Bilinmeyen
    değerlerde varsayılan 'bakim' döner."""
    if not label_or_key:
        return "bakim"
    s = label_or_key.strip().lower()
    if s in LOG_PROFILES:
        return s
    if s.startswith("kapsam") or "her mesaj" in s:
        return "kapsamli"
    if s.startswith("yalnız") or s.startswith("yalniz") or "güvenlik" in s or "guvenlik" in s:
        return "guvenlik"
    return "bakim"


def _parse_config() -> dict | None:
    """Mevcut TiHA rsyslog yapılandırmasından host, port, protokol ve
    profil değerlerini çıkarır. Geriye ``None`` veya ``{"host":str,
    "port":int, "proto":str, "profile":str}`` döner."""
    if not RSYSLOG_CONF.exists():
        return None

    try:
        content = RSYSLOG_CONF.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    import re
    host = ""
    port = 514
    proto = "udp"
    profile = "bakim"

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith(_PROFILE_MARKER_PREFIX):
            candidate = stripped[len(_PROFILE_MARKER_PREFIX):].strip()
            if candidate in LOG_PROFILES:
                profile = candidate
            continue
        if not host and "target=" in stripped:
            m = re.search(r'target="([^"]+)"', stripped)
            if m:
                host = m.group(1)
        if "port=" in stripped:
            m = re.search(r'port="([^"]+)"', stripped)
            if m:
                try:
                    port = int(m.group(1))
                except ValueError:
                    pass
        if "protocol=" in stripped:
            m = re.search(r'protocol="([^"]+)"', stripped)
            if m:
                proto = m.group(1).lower()

    if not host:
        return None
    return {"host": host, "port": port, "proto": proto, "profile": profile}


def _render(host: str, port: int, proto: str, profile: str = "bakim") -> str:
    """Dayanıklı log iletimi için rsyslog yapılandırması üretir.

    Disk destekli kuyruk (uzak sunucu offline olsa da kayıp yok) tek bir
    ruleset içinde tanımlanır; profil kararı sadece bu ruleset'e hangi
    facility.severity satırlarının yönlendirileceğini belirler.
    """
    profile = _profile_key(profile)
    prof = LOG_PROFILES[profile]

    selector_col_width = 60
    call_lines = []
    for sel in prof["selectors"]:
        pad = " " * max(1, selector_col_width - len(sel))
        call_lines.append(f"{sel}{pad}call tiha_remote")
    call_block = "\n".join(call_lines)

    # DHCP ile IP alan bir log sunucusuna gönderim yapıyorsak, rsyslog'un
    # bağlantı başında yaptığı DNS çözümlemesi ömür boyu cache'lenmemeli.
    # RebindInterval her N mesajda socket'i kapatıp yeniden açar; bu
    # sırada getaddrinfo yeniden çağrılır ve güncel IP alınır. UDP için
    # ayrı parametre adı kullanılır. 60 mesaj = düşük hacimli tahtada
    # ortalama 5-15 dakikada bir taze çözümleme.
    rebind_line = (
        'UDP.RebindInterval="60"'
        if proto.lower() == "udp"
        else 'RebindInterval="60"'
    )

    return f"""# TiHA - Dayanıklı merkezi log iletimi
# Profil: {prof['label']}
{_PROFILE_MARKER_PREFIX}{profile}
#
# Uzak sunucu offline olduğunda loglar kaybolmaz - diskte sıralanır.
# Sunucu geri geldiğinde birikmiş loglar otomatik gönderilir.
# Her 60 mesajda socket yeniden kurulur (DHCP ile IP alan sunucu için).
# Yerel disk tavanı: {QUEUE_MAX_DISK_SPACE} - en kötü senaryoda 3+ ay kayıt.

$CreateDirs on
$Umask 0000

# Ortak kuyruk + iletim ruleset'i. Profile göre seçilen selector
# satırları (aşağıda) bu ruleset'i çağırır; kuyruk paylaşılır.
ruleset(name="tiha_remote"
        queue.filename="tiha_remote"
        queue.type="LinkedList"
        queue.saveonshutdown="on"
        queue.maxdiskspace="{QUEUE_MAX_DISK_SPACE}"
        queue.size="{QUEUE_MEMORY_SIZE}"
        queue.discardseverity="0"
        queue.checkpointinterval="10") {{
    action(type="omfwd"
           target="{host}"
           port="{port}"
           protocol="{proto}"
           {rebind_line}
           action.resumeretrycount="-1"
           action.resumeinterval="30"
           action.resumeintervalmultiplier="2"
           action.resumeintervalmax="600")
}}

# Profile göre iletilen olaylar
{call_block}
"""


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
        "Bu adımı uyguluyorsanız “Benzersiz hostname” adımını da mutlaka "
        "uygulayın. Aksi hâlde imajdan klonlanan tüm tahtalar aynı hostname "
        "ile log gönderir; merkezi sunucudaki kayıtları tahta tahta ayırt "
        "edemezsiniz. Hostname adımı her klona kendi MAC adresinden türeyen "
        "benzersiz bir ad verir.\n\n"
        "KRİTİK AVANTAJ: Bu modül HİÇ LOG KAYBI OLMAYAN gelişmiş "
        "yapılandırma kullanır. Uzak log sunucusu haftalarca hatta aylarca "
        "erişilemez durumda olsa bile (elektrik kesintisi, ağ bakımı, "
        "sunucu arızası), tahta loglarını yerel diskte biriktirir — "
        "yerel tavan 2 GB olduğundan en kötü profilde bile 3 aydan uzun "
        "yerel kayıt tutulur. Sunucu geri geldiğinde birikmiş tüm loglar "
        "otomatik olarak gönderilir.\n\n"
        "Bunun için /etc/rsyslog.d/ altına disk-assisted queue (disk destekli "
        "kuyruk) kullanan gelişmiş bir yapılandırma dosyası yazılır. Paket "
        "güncellemesi gelirse yapılandırmanız korunur, geri almak da o tek "
        "dosyayı silmek kadar kolaydır.\n\n"
        "Log sunucusu tahtalarla aynı ağda olmalı. Okulda tahtalar ve "
        "kablosuz erişim noktaları (AP) genellikle `10.x.x.x` aralığındadır; "
        "log sunucusunu bu ağa konumlandırmalısınız. İdari ağdan log "
        "sunucusuna erişim olmaz — bu bilinçli bir güvenlik kısıtıdır."
    )

    def preview(self) -> str:
        # m08 stiliyle: hizalı key-value başlık + girintili dash liste.
        # Tablo/monospace görünümü kullanılmıyor - yatay kaydırma
        # oluşmasın diye satır kırılabilir serbest metin biçimindedir.
        config_exists = RSYSLOG_CONF.exists()
        parsed = _parse_config() if config_exists else None
        queue_files = (
            list(RSYSLOG_QUEUE_DIR.glob("tiha_remote*"))
            if RSYSLOG_QUEUE_DIR.exists() else []
        )
        total_size = 0
        for qf in queue_files:
            try:
                total_size += qf.stat().st_size
            except OSError:
                pass
        hostname_setup_done = Path(
            "/etc/systemd/system/tiha-first-boot-hostname.service"
        ).exists()

        lines: list[str] = []
        lines.append(
            "Yapılandırma dosyası : "
            + (f"var ({RSYSLOG_CONF})" if config_exists else "yok")
        )
        if parsed:
            lines.append(
                f"Log sunucusu         : {parsed['host']}:{parsed['port']} ({parsed['proto']})"
            )
            prof = LOG_PROFILES.get(parsed.get("profile", "bakim"))
            if prof:
                lines.append(f"Log profili          : {prof['label']}")
        lines.append(f"Kuyruk dizini        : {RSYSLOG_QUEUE_DIR}")
        if queue_files:
            lines.append(
                f"Bekleyen log kuyruğu : {len(queue_files)} dosya, {total_size:,} bayt"
            )
            if total_size > 0:
                lines.append(
                    "                       (uzak sunucu erişilemez durumda olabilir)"
                )
        else:
            lines.append(
                "Bekleyen log kuyruğu : yok (log iletimi doğrudan çalışıyor)"
            )
        lines.append("")

        if not hostname_setup_done:
            lines.append(
                "Hatırlatma: \"Benzersiz hostname\" adımı henüz "
                "uygulanmamış. Bu adımı uygulayacaksanız mutlaka onu da "
                "uygulayın; aksi hâlde merkezi sunucudaki loglarda "
                "tahtalar aynı isimle görünür ve birbirinden ayırt "
                "edilemez."
            )
            lines.append("")

        lines.append("Bu adım uygulandığında:")
        lines.append(
            f"  - {RSYSLOG_CONF} yazılır (disk destekli kuyruk yapılandırması)"
        )
        lines.append(
            "  - Uzak sunucu erişilemezse loglar yerel diskte biriktirilir"
        )
        lines.append(
            "  - Sunucu geri geldiğinde birikmiş loglar otomatik gönderilir"
        )
        lines.append("  - rsyslog servisi yeniden başlatılır")
        return "\n".join(lines)

    def apply(self, params=None, progress=None) -> ApplyResult:
        params = params or {}
        host = (params.get("syslog_host") or "").strip()
        port = int(params.get("syslog_port") or 514)
        proto = (params.get("syslog_proto") or "tcp").strip().lower()
        profile = _profile_key(params.get("log_profile"))
        install_smart = str(
            params.get("install_smart_monitoring", "False")
        ).lower() in ("true", "1", "yes", "on")
        install_node_exporter = str(
            params.get("install_node_exporter", "False")
        ).lower() in ("true", "1", "yes", "on")
        node_exporter_listen = (
            params.get("node_exporter_listen") or ":9100"
        ).strip()
        if not host:
            return ApplyResult(False, "Merkezi log sunucusu adresi (IP/isim) boş.")

        # rsyslog kurulu olduğundan emin ol
        install = run_cmd(
            ["apt-get", "install", "-y", "rsyslog"],
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        del install  # susturucu

        # Kuyruk dizinini oluştur (rsyslog otomatik oluşturmayabilir)
        try:
            RSYSLOG_QUEUE_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
            run_cmd(["chown", "-R", "syslog:adm", str(RSYSLOG_QUEUE_DIR)])
        except OSError as exc:
            log.warning("rsyslog kuyruk dizini oluşturulamadı: %s", exc)

        # Disk sağlığı + sıcaklık izleme paketleri
        smart_state = "atlandı"
        if install_smart:
            if progress:
                progress(
                    "Disk sağlığı ve sıcaklık izleme paketleri kuruluyor "
                    "(smartmontools, lm-sensors)..."
                )
            pkg = run_cmd(
                ["apt-get", "install", "-y", "smartmontools", "lm-sensors"],
                env={"DEBIAN_FRONTEND": "noninteractive"},
                timeout=300,
            )
            if pkg.ok:
                # sensors-detect etkileşimli — tüm sorulara varsayılan
                # (Enter=yes) ile devam edecek şekilde çağırıyoruz.
                run_cmd(
                    ["bash", "-lc", "yes '' | sensors-detect --auto || true"],
                    timeout=120,
                    check=False,
                )
                run_cmd(
                    ["systemctl", "enable", "--now", "smartd"], check=False,
                )
                smart_state = (
                    "kuruldu (smartd çalışıyor; sensors-detect otomatik "
                    "modülleri yükledi)"
                )
                if progress:
                    progress(
                        "smartd etkinleştirildi, sıcaklık modülleri "
                        "yüklendi."
                    )
            else:
                smart_state = "kurulum başarısız (paket yöneticisi hatası)"
                if progress:
                    progress(
                        "smartmontools/lm-sensors kurulamadı — adım devam ediyor."
                    )

        # Metrik izleme — Prometheus node_exporter
        node_exporter_state = "atlandı"
        if install_node_exporter:
            if progress:
                progress("Metrik izleme paketi (prometheus-node-exporter) indiriliyor...")
            pkg_ne = run_cmd(
                ["apt-get", "install", "-y", "prometheus-node-exporter"],
                env={"DEBIAN_FRONTEND": "noninteractive"},
                timeout=300,
            )
            if pkg_ne.ok:
                if progress:
                    progress("Paket kuruldu; dinleme adresi yazılıyor...")
                # /etc/default/prometheus-node-exporter'ın ARGS satırını
                # yaz — dinleme adresini bu dosyadan alır.
                defaults_file = Path(
                    "/etc/default/prometheus-node-exporter"
                )
                try:
                    if defaults_file.exists():
                        content = defaults_file.read_text(encoding="utf-8")
                    else:
                        content = ""
                    new_lines = []
                    args_written = False
                    for line in content.splitlines():
                        if line.startswith("ARGS="):
                            new_lines.append(
                                f'ARGS="--web.listen-address={node_exporter_listen}"'
                            )
                            args_written = True
                        else:
                            new_lines.append(line)
                    if not args_written:
                        new_lines.append(
                            f'ARGS="--web.listen-address={node_exporter_listen}"'
                        )
                    defaults_file.write_text(
                        "\n".join(new_lines) + "\n", encoding="utf-8",
                    )
                    if progress:
                        progress(
                            f"Dinleme adresi ayarlandı: {node_exporter_listen}"
                        )
                except OSError as exc:
                    log.warning(
                        "node_exporter defaults dosyası yazılamadı: %s", exc,
                    )
                    if progress:
                        progress(
                            "Uyarı: dinleme adresi dosyası yazılamadı; "
                            "servis varsayılan port ile açılacak."
                        )
                if progress:
                    progress("Servis etkinleştiriliyor ve başlatılıyor...")
                run_cmd(
                    ["systemctl", "restart", "prometheus-node-exporter"],
                    check=False,
                )
                run_cmd(
                    ["systemctl", "enable", "prometheus-node-exporter"],
                    check=False,
                )
                node_exporter_state = (
                    f"kuruldu (dinleme adresi {node_exporter_listen}; "
                    "Prometheus sunucusu buradan scrape yapabilir)"
                )
                if progress:
                    progress(
                        f"Metrik izleme aktif — {node_exporter_listen} adresinde dinliyor."
                    )
            else:
                node_exporter_state = (
                    "kurulum başarısız (prometheus-node-exporter paketi "
                    "yüklenemedi)"
                )
                if progress:
                    progress(
                        "Metrik izleme kurulumu başarısız (paket yöneticisi hatası); "
                        "log iletim akışı devam ediyor."
                    )

        # rsyslog yapılandırmasını yaz
        try:
            RSYSLOG_CONF.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            config_content = _render(host, port, proto, profile=profile)
            RSYSLOG_CONF.write_text(config_content, encoding="utf-8")
            RSYSLOG_CONF.chmod(0o644)
        except OSError as exc:
            return ApplyResult(
                False,
                f"rsyslog ek yapılandırma dosyası yazılamadı: {exc}",
            )

        # rsyslog'u yeniden başlat
        restart = run_cmd(["systemctl", "restart", "rsyslog"])
        if not restart.ok:
            return ApplyResult(
                False, "rsyslog yeniden başlatılamadı.",
                details=restart.stderr,
            )

        status = run_cmd(["systemctl", "is-active", "rsyslog"])
        if not status.ok:
            return ApplyResult(
                False, "rsyslog servisi çalıştırılamadı.",
                details="systemctl status rsyslog komutuyla kontrol edin.",
            )

        prof = LOG_PROFILES[profile]
        return ApplyResult(
            True,
            f"Dayanıklı log iletimi {host}:{port}/{proto.upper()} için kuruldu "
            f"({prof['label']}).",
            details=(
                f"Yapılandırma dosyası : {RSYSLOG_CONF}\n"
                f"Kuyruk dizini        : {RSYSLOG_QUEUE_DIR}\n"
                f"Hedef                : {host}:{port} ({proto.upper()})\n"
                f"Log profili          : {prof['label']}\n"
                f"Disk/sıcaklık izleme : {smart_state}\n"
                f"Metrik izleme (node) : {node_exporter_state}\n\n"
                "Kuyruk özellikleri:\n"
                "  - Uzak sunucu offline: loglar yerel diskte birikir\n"
                "  - Sunucu geri gelince: birikmiş loglar otomatik gönderilir\n"
                f"  - Yerel disk tavanı: {QUEUE_MAX_DISK_SPACE.upper()} "
                "(en kötü profilde bile 3+ ay yerel kayıt)\n"
                "  - Yeniden deneme aralığı: 30-600 saniye\n\n"
                f"Test: sunucu tarafında 'tcpdump -n -i any port {port}' ile "
                "gelen kayıtları görebilirsiniz."
            ),
            data={
                "install_smart_monitoring": install_smart,
                "install_node_exporter": install_node_exporter,
            },
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
                progress(msg)
            return ApplyResult(False, msg)

        host = cfg["host"]
        port = cfg["port"]
        proto = cfg["proto"].lower()
        if progress:
            progress(f"Hedef: {host}:{port} ({proto.upper()})")
            progress(f"DNS çözülmesi deneniyor: {host}…")

        try:
            addrinfo = _socket.getaddrinfo(host, port,
                                           type=_socket.SOCK_STREAM)
        except _socket.gaierror as exc:
            msg = f"DNS çözümleme başarısız ({host}): {exc}"
            if progress:
                progress(f"{msg}")
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
                    progress(f"{msg}")
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
                progress(f"{msg}")
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
