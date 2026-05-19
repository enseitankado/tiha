"""Modül 12 — Ahenk (LiderAhenk) ajan kimliğini sıfırla.

**Ne yapar?**
``ahenk`` servisini durdurur; ``/etc/ahenk/ahenk.conf`` içindeki
``uid``, ``parola`` (password) ve ``sunucu adresi`` (host) alanlarını;
``/etc/ahenk/config.d/messaging.conf`` içindeki Pulsar bağlantı
alanlarını (``pulsar_host``, ``pulsar_port``,
``tls_trust_certs_file_path``) boşaltır; yerel kayıt veritabanını
(``/etc/ahenk/ahenk.db``) siler; ahenk günlüğünü (``/var/log/ahenk.log``)
boşaltır. Paket kaldırılmaz.

**Kazara reboot koruması.** Bu adımdan sonra tahta yanlışlıkla
yeniden başlatılırsa ahenk'in tekrar kimlik üretip imajı kirletmesini
önlemek için:

  1. İmaj anındaki birincil arayüzün MAC adresi ``STATE_DIR/imaged-mac``
     altına yazılır.
  2. ``ahenk.service`` **disable** edilir.
  3. ``tiha-post-image-init.service`` (oneshot) kurulur ve enabled
     bırakılır. Bu servis her boot'ta MAC karşılaştırması yapar:
       • **Eşit MAC → orijinal tahta**, yanlışlıkla yeniden başlatılmış;
         hiçbir şey yapılmaz, ahenk başlatılmaz.
       • **Farklı MAC → klon tahta**; ahenk kimliği savunma katmanıyla
         tekrar boşaltılır, ``ahenk.service`` enable + start edilir,
         MAC dosyası yeni MAC ile güncellenir, klon-tespit servisi
         kendini disable eder.
  4. eta-register backend'e MAC sorgusu attığı için klon tahtada
     otomatik olarak yeni kayıt akışına girer; ahenk daemon Lider'e
     kendi MAC'iyle kayıt olur ve uid/parola yeniden atanır.

**Güvenlik kemeri.** Yıkımdan önce ilgili dosyaların ham hâli
``STATE_DIR/m12_ahenk_backup/`` altına kopyalanır. İmaj alınmadan
vazgeçilirse Özet sayfasındaki **Geri al** düğmesi yedeği geri
yükler, klon-tespit servisini kaldırır ve ``ahenk.service``'i tekrar
enable + start eder.

**Önkoşul.** ahenk bu tahtada yüklü değilse adım atlanır (uygula
çağrıldığında sessizce "yapılacak iş yok" mesajıyla başarı döner);
klon-tespit servisi de kurulmaz.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.paths import STATE_DIR
from ..core.utils import run_cmd

log = get_logger(__name__)

AHENK_BACKUP_DIR = STATE_DIR / "m12_ahenk_backup"
AHENK_CONF = Path("/etc/ahenk/ahenk.conf")
AHENK_MSG_CONF = Path("/etc/ahenk/config.d/messaging.conf")
AHENK_DB = Path("/etc/ahenk/ahenk.db")
AHENK_LOG = Path("/var/log/ahenk.log")

# Klon tespiti için: imaj alma anındaki birincil arayüzün MAC adresi.
# Boot anında bu dosya okunur ve mevcut MAC ile karşılaştırılır:
#   eşit  → orijinal tahta (kazara reboot) — ahenk başlatılmaz
#   farklı → yeni donanım (klon) — ahenk temizle + enable + start
IMAGED_MAC_FILE = STATE_DIR / "imaged-mac"
POST_IMAGE_SERVICE = Path("/etc/systemd/system/tiha-post-image-init.service")
POST_IMAGE_SCRIPT = Path("/usr/local/sbin/tiha-post-image-init.sh")
POST_IMAGE_SERVICE_NAME = POST_IMAGE_SERVICE.name


POST_IMAGE_SCRIPT_CONTENT = f"""#!/bin/bash
# TiHA — klon tespiti ve ahenk yeniden kayıt akışına sevk.
#
# Mantık:
#   1. m12 (Ahenk kimliği sıfırla) uygulandığında imaj anındaki birincil
#      MAC adresi {IMAGED_MAC_FILE} altına yazılır ve ahenk.service
#      disable edilir.
#   2. Bu betik her boot'ta çalışır. Mevcut MAC eşitse orijinal tahta
#      yanlışlıkla yeniden başlatılmış demektir; hiçbir şey yapılmaz
#      ve ahenk başlatılmaz — imaj kirletilmez.
#   3. Mevcut MAC farklıysa yeni donanım (klon) açılmış demektir.
#      ahenk kimliği bir savunma katmanı olarak tekrar boşaltılır;
#      ahenk.service enable + start edilir. ahenk daemonu boş kimliği
#      görüp Lider'e kendi MAC'iyle yeniden kayıt akışına girer.
#      Ardından bu MAC dosyası güncellenir ve servis kendini disable
#      eder; sonraki boot'larda artık bir şey yapmaz.
#
# eta-register yerel sentinel bırakmaz; backend'e GET /board/check?mac=...
# sorgusu atar. MAC değiştiği için backend "not registered" döner ve
# eta-register XDG autostart üzerinden zaten yeni kayıt akışına girer.
set -uo pipefail

SAVED_MAC_FILE="{IMAGED_MAC_FILE}"
LOG_TAG="tiha-post-image-init"

log() {{ logger -t "$LOG_TAG" -- "$@"; echo "[$LOG_TAG] $*" >&2; }}

[[ -f "$SAVED_MAC_FILE" ]] || {{ log "MAC dosyası yok, atlanıyor."; exit 0; }}

# Birincil kablolu arayüzü bul; default route varsa onu kullan, yoksa
# /sys/class/net altındaki ilk fiziksel arayüzü seç.
iface=$(ip -o -4 route show to default 2>/dev/null | awk '{{print $5}}' | head -n1)
if [[ -z "$iface" || ! -d "/sys/class/net/$iface" ]]; then
    for cand in /sys/class/net/*; do
        name=$(basename "$cand")
        [[ "$name" == "lo" ]] && continue
        [[ -L "$cand/device" ]] || continue
        iface="$name"
        break
    done
fi

if [[ -z "$iface" || ! -r "/sys/class/net/$iface/address" ]]; then
    log "Birincil arayüz tespit edilemedi, atlanıyor."
    exit 0
fi

current_mac=$(cat "/sys/class/net/$iface/address" 2>/dev/null | tr 'A-F' 'a-f')
saved_mac=$(cat "$SAVED_MAC_FILE" 2>/dev/null | tr -d '[:space:]' | tr 'A-F' 'a-f')

if [[ -z "$current_mac" || -z "$saved_mac" ]]; then
    log "MAC okuma başarısız (cur='$current_mac' saved='$saved_mac'), atlanıyor."
    exit 0
fi

if [[ "$current_mac" == "$saved_mac" ]]; then
    log "MAC eşleşti ($current_mac); orijinal tahta — ahenk başlatılmıyor."
    exit 0
fi

log "MAC değişti ($saved_mac → $current_mac); klon tespit edildi, ahenk yeniden kayıt akışına alınıyor."

# Savunma katmanı: m12 zaten boşaltmıştı, garantiye al.
[[ -f /etc/ahenk/ahenk.conf ]] && sed -i -E 's/^(uid|password|host)[[:space:]]*=.*/\\1 =/' /etc/ahenk/ahenk.conf || true
[[ -f /etc/ahenk/config.d/messaging.conf ]] && sed -i -E 's/^(pulsar_host|pulsar_port|tls_trust_certs_file_path)[[:space:]]*=.*/\\1 =/' /etc/ahenk/config.d/messaging.conf || true
rm -f /etc/ahenk/ahenk.db
: > /var/log/ahenk.log 2>/dev/null || true

# ahenk'i etkinleştir ve başlat — kimlik üretip Lider'e MAC ile kayıt olur.
systemctl enable --now ahenk.service || log "ahenk.service başlatılamadı"

# Bu tahta artık 'imaj kaynağı' değil — kendi MAC'iyle imzala ki sonraki
# boot'larda tekrar tetiklenmesin.
echo "$current_mac" > "$SAVED_MAC_FILE"

# Servisin kendisini disable et (artık iş bitti).
systemctl disable {POST_IMAGE_SERVICE_NAME} || true

log "Tamamlandı; bu servis disable edildi."
"""

POST_IMAGE_SERVICE_CONTENT = f"""[Unit]
Description=TiHA — İmaj sonrası klon tespiti ve ahenk yeniden kayıt akışı
After=local-fs.target network.target
Before=ahenk.service

[Service]
Type=oneshot
ExecStart={POST_IMAGE_SCRIPT}
RemainAfterExit=no

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
        path.unlink(missing_ok=True)
        return True
    except OSError as exc:
        log.warning("Silinemedi %s: %s", path, exc)
        return False


def _primary_mac() -> str | None:
    """Sistemin birincil kablolu arayüzünün MAC adresini döner.

    Önce default route'un arayüzü; yoksa /sys/class/net altındaki ilk
    fiziksel (device symlink'i olan) arayüz seçilir. Lower-case döner.
    """
    import os
    result = run_cmd(["ip", "-o", "-4", "route", "show", "to", "default"], check=False)
    iface = ""
    if result.ok and result.stdout:
        parts = result.stdout.split()
        for i, tok in enumerate(parts):
            if tok == "dev" and i + 1 < len(parts):
                iface = parts[i + 1]
                break
    if not iface or not Path(f"/sys/class/net/{iface}").is_dir():
        net_root = Path("/sys/class/net")
        if net_root.is_dir():
            for entry in sorted(net_root.iterdir()):
                if entry.name == "lo":
                    continue
                if (entry / "device").is_symlink():
                    iface = entry.name
                    break
    if not iface:
        return None
    addr_path = Path(f"/sys/class/net/{iface}/address")
    if not addr_path.is_file():
        return None
    try:
        return addr_path.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return None


class AhenkResetModule(Module):
    id = "m12_ahenk_reset"
    title = "Ahenk kimliği sıfırla"
    sidebar_title = "Ahenk kimliği"
    popup_on_success = True
    apply_hint = (
        "ahenk Pulsar bağlantısı kesilir; UID/parola/DB/günlük temizlenir, "
        "MAC-bazlı kazara reboot koruması kurulur."
    )
    rationale = (
        "Bir tahtanın imajı klonlanmadan önce LiderAhenk ajanının yerel "
        "kimliği sıfırlanmalıdır. Aksi hâlde tüm klonlar aynı UID/parola "
        "ile MEB Pulsar broker'ına bağlanır; aynı kimliğe sahip iki "
        "tahta Pulsar'da Exclusive consumer çakışması yaşar ve "
        "cross-board impersonation (bir tahta üzerinden gönderilen "
        "komutun başka bir tahtaya geçmesi) mümkün hâle gelir.\n\n"
        "Bu adım önce o tahtaya özel verileri temizler: ahenk.conf'taki "
        "uid / parola / sunucu adresi alanları ile messaging.conf'taki "
        "Pulsar bağlantı alanları boşaltılır, yerel kayıt veritabanı "
        "(ahenk.db) silinir, ahenk günlüğü boşaltılır. Paket kaldırılmaz; "
        "kurulum bütünlüğü korunur.\n\n"
        "Kazara yeniden başlatmaya karşı koruma: m12 ahenk servisini "
        "disable eder ve imaj anındaki MAC adresini yedek dosyaya "
        "yazar; her boot'ta çalışan küçük bir oneshot servis MAC'i "
        "karşılaştırır. Aynı MAC ise (orijinal tahta yanlışlıkla "
        "yeniden başlatılmış) ahenk başlatılmaz — imaj kirletilmez. "
        "Farklı MAC ise (klon tahta açıldı) ahenk savunma katmanıyla "
        "tekrar temizlenir, etkinleştirilir ve başlatılır; ahenk daemon "
        "Lider'e kendi MAC'iyle kayıt olur. eta-register backend'e MAC "
        "sorgusu attığı için yeni kayıt akışı otomatik tetiklenir.\n\n"
        "Güvenlik kemeri: yıkımdan önce dosyalar TiHA'nın özel "
        "klasörüne yedeklenir. İmajı almadan fikrinizi değiştirirseniz "
        "Özet sayfasındaki 'Geri al' düğmesi yedeği geri yükler, klon "
        "tespit servisini kaldırır ve ahenk'i tekrar enable + start "
        "eder; tahta Lider'e eski kimliğiyle bağlanmaya devam eder."
    )
    undo_supported = True

    def preview(self) -> str:
        if not AHENK_CONF.exists():
            return (
                "ahenk bu tahtada yüklü değil — yapılacak bir iş yok.\n"
                "Uygula çalıştırıldığında adım atlanır."
            )

        msg_conf_present = AHENK_MSG_CONF.exists()
        db_present = AHENK_DB.exists()
        log_present = AHENK_LOG.exists()

        current_mac = _primary_mac() or "(tespit edilemedi)"
        lines = [
            "Aşağıdaki ahenk dosyaları sıfırlanacak:",
            f"  • {AHENK_CONF} → uid / parola / sunucu adresi alanları boşaltılacak",
            (
                f"  • {AHENK_MSG_CONF} → Pulsar bağlantı alanları boşaltılacak"
                if msg_conf_present
                else f"  • {AHENK_MSG_CONF} → mevcut değil, atlanacak"
            ),
            (
                f"  • {AHENK_DB} → yerel kayıt veritabanı silinecek"
                if db_present
                else f"  • {AHENK_DB} → zaten yok"
            ),
            (
                f"  • {AHENK_LOG} → günlük dosyası boşaltılacak (eski uid/IP izleri)"
                if log_present
                else f"  • {AHENK_LOG} → mevcut değil"
            ),
            "",
            "Kazara yeniden başlatmaya karşı koruma:",
            f"  • Mevcut MAC ({current_mac}) {IMAGED_MAC_FILE} altına yazılır.",
            "  • ahenk.service DISABLE edilir (reboot'ta otomatik başlamasın).",
            f"  • {POST_IMAGE_SERVICE_NAME} kurulur ve enabled bırakılır:",
            "    her boot'ta MAC karşılaştırması yapar.",
            "      – Aynı MAC → orijinal tahta yanlışlıkla yeniden başlatıldı;",
            "        ahenk başlatılmaz, imaj kirletilmez.",
            "      – Farklı MAC → klon tahta; ahenk savunma katmanıyla tekrar",
            "        temizlenir, enable + start edilir, MAC dosyası güncellenir,",
            "        servis kendini disable eder. ahenk Lider'e MAC ile kayıt olur.",
            "        eta-register zaten backend'e MAC sorgusu attığı için yeni",
            "        kayıt akışını otomatik tetikler.",
            "",
            "Kısmi geri alma:",
            f"  • Yıkımdan önce dosyalar {AHENK_BACKUP_DIR} altına yedeklenir.",
            "  • Özet sayfasındaki 'Geri al' yedeği geri yükler, klon-tespit",
            "    servisini kaldırır ve ahenk'i tekrar enable + start eder.",
        ]
        return "\n".join(lines)

    def apply(self, params: dict | None = None, progress: ProgressCallback | None = None) -> ApplyResult:
        if not AHENK_CONF.exists():
            return ApplyResult(
                True,
                "ahenk bu tahtada yüklü değil — adım atlandı.",
                data={"ahenk_backed_up": False, "skipped": True},
            )

        mac = _primary_mac()
        if not mac:
            return ApplyResult(
                False,
                "Birincil ağ arayüzünün MAC adresi tespit edilemedi; "
                "klon koruma servisi kurulamadı, adım iptal edildi.",
            )

        if progress:
            progress("ahenk servisi durduruluyor…")
        run_cmd(["systemctl", "stop", "ahenk.service"], check=False)

        # 1) Yedek al
        ahenk_backed_up = False
        if progress:
            progress(f"Yedek alınıyor: {AHENK_BACKUP_DIR}")
        try:
            AHENK_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(AHENK_CONF, AHENK_BACKUP_DIR / "ahenk.conf")
            if AHENK_MSG_CONF.exists():
                shutil.copy2(AHENK_MSG_CONF, AHENK_BACKUP_DIR / "messaging.conf")
            if AHENK_DB.exists():
                shutil.copy2(AHENK_DB, AHENK_BACKUP_DIR / "ahenk.db")
            ahenk_backed_up = True
        except OSError as exc:
            log.warning("ahenk kimlik yedeği alınamadı: %s", exc)

        # 2) ahenk.conf alanlarını boşalt
        if progress:
            progress("ahenk.conf: uid / parola / sunucu adresi boşaltılıyor")
        run_cmd(
            ["sed", "-i", "-E",
             r"s/^(uid|password|host)\s*=.*/\1 =/",
             str(AHENK_CONF)],
            check=False,
        )

        # 3) messaging.conf Pulsar alanları
        if AHENK_MSG_CONF.exists():
            if progress:
                progress("messaging.conf: Pulsar bağlantı alanları boşaltılıyor")
            run_cmd(
                ["sed", "-i", "-E",
                 r"s/^(pulsar_host|pulsar_port|tls_trust_certs_file_path)\s*=.*/\1 =/",
                 str(AHENK_MSG_CONF)],
                check=False,
            )

        # 4) Yerel kayıt veritabanı
        if AHENK_DB.exists():
            if progress:
                progress("ahenk.db siliniyor")
            _rm(AHENK_DB)

        # 5) Günlük dosyası
        if AHENK_LOG.exists():
            if progress:
                progress("ahenk.log boşaltılıyor")
            _truncate(AHENK_LOG)

        # 6) Klon tespit servisini kur ve ahenk.service'i disable et.
        # Kazara reboot olursa ahenk otomatik başlamaz; klon ilk boot'unda
        # tiha-post-image-init.service MAC değişikliğini tespit edip
        # ahenk'i yeniden devreye alır.
        if progress:
            progress(f"Klon tespit servisi kuruluyor: {POST_IMAGE_SERVICE_NAME}")

        IMAGED_MAC_FILE.parent.mkdir(parents=True, exist_ok=True)
        IMAGED_MAC_FILE.write_text(mac + "\n", encoding="utf-8")

        POST_IMAGE_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
        POST_IMAGE_SCRIPT.write_text(POST_IMAGE_SCRIPT_CONTENT, encoding="utf-8")
        POST_IMAGE_SCRIPT.chmod(0o755)

        POST_IMAGE_SERVICE.write_text(POST_IMAGE_SERVICE_CONTENT, encoding="utf-8")
        run_cmd(["systemctl", "daemon-reload"], check=False)
        run_cmd(["systemctl", "enable", POST_IMAGE_SERVICE_NAME], check=False)

        # ahenk'in kendisini disable et — sadece tiha-post-image-init
        # tarafından (MAC değişikliği sonrası) tekrar etkinleştirilecek.
        if progress:
            progress("ahenk.service disable ediliyor (kazara reboot koruması)")
        run_cmd(["systemctl", "disable", "ahenk.service"], check=False)

        details = (
            f"Yedek: {AHENK_BACKUP_DIR}\n"
            "Sıfırlanan alanlar: uid, parola, sunucu adresi, "
            "pulsar_host, pulsar_port, tls_trust_certs_file_path\n"
            "Silinen: ahenk.db (yerel kayıt veritabanı)\n"
            "Boşaltılan: ahenk.log\n"
            f"İmaj anındaki MAC: {mac} → {IMAGED_MAC_FILE}\n"
            f"Klon tespit servisi: {POST_IMAGE_SERVICE_NAME} (enabled)\n"
            "Servis ahenk.service: DISABLED — "
            "kazara reboot'ta otomatik başlamaz, klon tahtada "
            "tiha-post-image-init enable edecek."
        )
        return ApplyResult(
            True,
            "ahenk kimliği sıfırlandı; klon tahtada MAC değişikliği "
            "tespit edildiğinde ahenk otomatik kayıt akışına girer.",
            details=details,
            data={
                "ahenk_backed_up": ahenk_backed_up,
                "skipped": False,
                "imaged_mac": mac,
            },
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        data = data or {}
        if data.get("skipped"):
            # Adım atlanmıştı (ahenk yoktu); klon servisi/MAC dosyası da
            # kurulmamış olur, geri alınacak bir şey yok.
            return ApplyResult(
                False,
                "Bu uygulamada ahenk yüklü olmadığı için sıfırlama "
                "atlandı — geri yüklenecek bir şey yok.",
            )
        if not data.get("ahenk_backed_up") or not AHENK_BACKUP_DIR.is_dir():
            return ApplyResult(
                False,
                "Geri alınabilecek bir kayıt yok — yedek bulunamadı.",
            )

        conf_bak = AHENK_BACKUP_DIR / "ahenk.conf"
        msg_bak = AHENK_BACKUP_DIR / "messaging.conf"
        db_bak = AHENK_BACKUP_DIR / "ahenk.db"
        if not conf_bak.exists():
            return ApplyResult(False, "ahenk.conf yedeği bulunamadı.")

        restored: list[str] = []
        run_cmd(["systemctl", "stop", "ahenk.service"], check=False)

        # 1) Klon tespit servisini ve MAC dosyasını kaldır.
        run_cmd(["systemctl", "disable", POST_IMAGE_SERVICE_NAME], check=False)
        _rm(POST_IMAGE_SERVICE)
        _rm(POST_IMAGE_SCRIPT)
        _rm(IMAGED_MAC_FILE)
        run_cmd(["systemctl", "daemon-reload"], check=False)

        # 2) ahenk dosyalarını yedekten geri yükle.
        try:
            AHENK_CONF.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(conf_bak, AHENK_CONF)
            restored.append("ahenk.conf")
            if msg_bak.exists():
                AHENK_MSG_CONF.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(msg_bak, AHENK_MSG_CONF)
                restored.append("messaging.conf")
            if db_bak.exists():
                shutil.copy2(db_bak, AHENK_DB)
                restored.append("ahenk.db")
        except OSError as exc:
            return ApplyResult(False, f"ahenk yedeği geri yüklenemedi: {exc}")

        # 3) ahenk.service'i tekrar enable et + başlat.
        run_cmd(["systemctl", "enable", "ahenk.service"], check=False)
        run_cmd(["systemctl", "start", "ahenk.service"], check=False)
        shutil.rmtree(AHENK_BACKUP_DIR, ignore_errors=True)

        return ApplyResult(
            True,
            "ahenk kimliği geri yüklendi (" + ", ".join(restored) + "); "
            "klon tespit servisi kaldırıldı.",
            details=(
                "ahenk.service tekrar enabled + start edildi. Pulsar "
                f"bağlantısı eski uid/parola ile yeniden kurulur. "
                f"{POST_IMAGE_SERVICE_NAME} ve {IMAGED_MAC_FILE} silindi."
            ),
        )
