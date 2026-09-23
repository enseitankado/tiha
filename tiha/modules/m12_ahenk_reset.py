"""Modül 12 — Klon-yeniden-talep (Ahenk).

Bu adım klon-ı tespit edip Lider sunucusuna kayıt akışına sokan
mekanizmayı **imaja gömer**. Wizard zamanında ahenk credential'larına
**dokunmaz**; tüm akıllı iş klonun ilk açılışında çalışacak bir boot
servisi tarafından yapılır.

**Wizard zamanında yapılanlar (apply):**

1. *MAC imzası* — kaynak tahtanın birincil arayüz MAC adresi
   ``/var/lib/tiha/state/imaged-mac-ahenk`` altına yazılır. Bu dosya
   klon tespitinin temel sentinel'ıdır: imajla açılan bir tahtanın
   MAC'i bu dosyadakiyle aynıysa "kaynak tahta", farklıysa "klon"
   demektir. m14 (BIOS parolası) kendi imza dosyasını
   (``imaged-mac-bios``) yönetir; iki adım birbirini etkilemez.

2. *ahenk kurulumu* — paket yoksa ``apt-get update`` +
   ``apt-get install -y ahenk`` ile kurulur ve ``ahenk.service``
   enable edilir. Eta-register'ın ``installer.py`` + ``opr.py`` akışı
   örnek alınmıştır (aynı paket adı, aynı komut zinciri).

3. *Klon-reclaim servisi* —
   ``/usr/local/sbin/tiha-clone-reclaim.py`` (Python betiği) ve
   ``/etc/systemd/system/tiha-clone-reclaim.service`` (Type=oneshot,
   ``Before=ahenk.service``) yazılır ve enable edilir. Her boot'ta
   ahenk başlamadan önce çalışır.

Wizard adımı credential'ları **silmez**; kaynak tahta sanitize'a kadar
ahenk'iyle çalışmaya devam eder. Sanitize (m11) bu adımdan sonra
çalışıp imajı son hâline getirir.

**Boot servisi mantığı (klonda her açılışta):**

  a. ``imaged-mac-ahenk`` dosyası yoksa → çık (adım hiç uygulanmamış
     veya dosya elle silinmiş).
  b. Mevcut MAC == kayıtlı MAC → çık ("orijinal tahta — kazara
     reboot", hiçbir şey yapma; ahenk normal başlasın).
  c. Mevcut MAC ≠ kayıtlı MAC → klon tespit edildi.
     ``GET /api/board/check?mac=<mevcut>`` ile ETAP backend'ine sorgu
     yapılır (eta-register'ın kullandığı endpoint, hardcoded prod
     URL: ``http://api-etap.eba.gov.tr:1000/api``).
       - **Ağ/HTTP hatası** → işlem yapma, çık. Servis enabled kalır;
         sonraki boot'ta tekrar dener.
       - **Kayıtlı** (registered=True) → ``ahenk.service`` durdurulur,
         credential'lar (uid/password/host + Pulsar alanları +
         ``ahenk.db`` + ``ahenk.log``) sıfırlanır, ahenk yeniden
         enable + start edilir. ahenk daemonu boş kimliği görür,
         yeni UUID üretir, Lider'e kendi MAC'iyle kayıt akışına
         girer. ``imaged-mac-ahenk`` mevcut MAC ile imzalanır (sonraki
         boot'larda tekrar tetiklenmesin), servis kendini disable
         eder.
       - **Kayıtsız** (registered=False) → ``ahenk.service``
         durdurulur, credential'lar sıfırlanır, ahenk **disable**
         edilir ve servis kendini disable eder. Bu noktada
         kullanıcı etapadmin oturumu açıp eta-register'la tahtayı
         kaydedecek; eta-register kayıt sonrası ahenk'i otomatik
         enable + start eder.

Tasarım gerekçesi: credential temizliğini boot anına ertelemek, kaynak
tahtanın imajı alınana dek normal işlemesine imkân tanır. Klon kayıtsız
durumda olsa bile credential'ları silmek, ahenk'in eski uid/parola ile
Pulsar'a bağlanıp Lider'de Exclusive consumer çakışmasına / cross-board
impersonation'a yol açmasını **kesinlikle engeller**.

**Geri al.** Klon-reclaim servisi (.service + .sh) ve
``imaged-mac-ahenk`` dosyası kaldırılır, ``systemctl daemon-reload``
çalıştırılır. ahenk
paketi TiHA tarafından kurulduysa ``apt-get purge`` ile sökülür ve
``autoremove`` çalıştırılır; daha önce zaten kuruluysa korunur.
Wizard ahenk credential'larına dokunmadığı için geri yüklenecek bir
yedek yoktur.

Akışın görsel şeması ve ayrıntılı gerekçeler için bakınız:
``docs/m12-clone-reclaim.md``.
"""

from __future__ import annotations

from pathlib import Path

from ..core.i18n import t
from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.paths import STATE_DIR
from ..core.utils import run_cmd, run_cmd_stream

log = get_logger(__name__)

# --- Yerel kurulum yolları ----------------------------------------------------

# Kaynak tahta imzası: imajın alındığı anda birincil arayüzün MAC'i.
# Boot servisi bu dosya yoksa hemen çıkar (klon değil veya adım uygulanmamış).
# Ahenk klon-reclaim adımına ÖZGÜ dosya; m14 (BIOS parolası) kendi
# ``imaged-mac-bios`` dosyasını yönetir. Ayrı tutmamızın sebebi: bu
# script başarılı bir yeniden kayıttan sonra imzayı klonun yeni
# MAC'iyle üzerine yazıyor. Paylaşımlı bir dosya olsaydı bu, m14'ün
# BIOS parolasını sonraki boot'larda "kaynak tahta" sanıp atlamasına
# yol açardı.
IMAGED_MAC_FILE = STATE_DIR / "imaged-mac-ahenk"

# Klon-reclaim çalıştırılabilir betiği ve systemd unit'i.
RECLAIM_SCRIPT = Path("/usr/local/sbin/tiha-clone-reclaim.py")
RECLAIM_SERVICE = Path("/etc/systemd/system/tiha-clone-reclaim.service")
RECLAIM_SERVICE_NAME = RECLAIM_SERVICE.name


# --- Boot servisi Python betiği -----------------------------------------------
# Aşağıdaki şablon @@PLACEHOLDER@@ ile değiştirilen yer-tutucular dışında
# olduğu gibi diske yazılır. f-string KULLANMIYORUZ — iç Python f-string
# ifadeleri ({...}) ile çakışmasın diye. Yer-tutucular:
#   @@SAVED_MAC_FILE@@    → IMAGED_MAC_FILE mutlak yolu
#   @@SERVICE_NAME@@      → systemd unit dosya adı
RECLAIM_SCRIPT_TEMPLATE = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tiha-clone-reclaim — klon tespiti ve Ahenk yeniden talep akışı.

Her boot'ta ``ahenk.service``'ten ÖNCE çalışır. Klon tespit ederse
ETAP backend'ine ``/api/board/check?mac=...`` sorgusu atar; tahta
kayıtlıysa ahenk credential'larını sıfırlayıp ahenk'i yeniden başlatır.
Kayıtsızsa ahenk durdurulur, credential'lar sıfırlanır ve ahenk
disable edilir (kullanıcı eta-register ile kayıt yapacak).

Tasarımı için: ``tiha/modules/m12_ahenk_reset.py`` ve
``docs/m12-clone-reclaim.md``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# --- Sabitler ---------------------------------------------------------------

SAVED_MAC_FILE = Path("@@SAVED_MAC_FILE@@")
AHENK_CONF     = Path("/etc/ahenk/ahenk.conf")
AHENK_MSG_CONF = Path("/etc/ahenk/config.d/messaging.conf")
AHENK_DB       = Path("/etc/ahenk/ahenk.db")
AHENK_LOG      = Path("/var/log/ahenk.log")

# Eta-register ile aynı production endpoint. Test/dev için elle düzenle.
BACKEND_URL  = "http://api-etap.eba.gov.tr:1000/api"
APP_CODE     = "eta_register!"
HTTP_TIMEOUT = 15

SERVICE_NAME = "@@SERVICE_NAME@@"
LOG_TAG      = "tiha-clone-reclaim"


def note(msg: str) -> None:
    """journald + stderr'e tek satırlık not."""
    try:
        subprocess.run(["logger", "-t", LOG_TAG, "--", msg], check=False)
    except Exception:
        pass
    print(f"[{LOG_TAG}] {msg}", file=sys.stderr)


def primary_mac() -> str | None:
    """Default route arayüzünün MAC'i; yoksa ilk fiziksel arayüz.
    Lowercase ``aa:bb:cc:dd:ee:ff`` formatında döner."""
    iface = ""
    try:
        out = subprocess.run(
            ["ip", "-o", "-4", "route", "show", "to", "default"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        parts = out.split()
        for i, tok in enumerate(parts):
            if tok == "dev" and i + 1 < len(parts):
                iface = parts[i + 1]
                break
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

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


def query_registered(mac: str) -> bool | None:
    """GET /api/board/check?mac=<MAC>.
       True  → kayıtlı
       False → kayıtsız
       None  → ağ/HTTP/parse hatası (bu boot atlanır)."""
    url = f"{BACKEND_URL}/board/check?mac={mac}"
    req = urllib.request.Request(url, headers={
        "etap-app-code": APP_CODE,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return bool(body.get("registered"))
    except urllib.error.HTTPError as e:
        # Backend bazı durumlarda 4xx/5xx ile de geçerli JSON döner
        # (eta-register'ın interpret_device_status mantığı).
        try:
            body = json.loads(e.read().decode("utf-8"))
            if isinstance(body, dict) and "registered" in body:
                return bool(body["registered"])
        except Exception:
            pass
        note(f"HTTPError: {e.code} {e.reason}")
        return None
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        note(f"API sorgu hatası: {e}")
        return None


def wipe_ahenk_credentials() -> None:
    """ahenk.conf uid/password/host alanlarını boşaltır; messaging.conf
    Pulsar bağlantı alanlarını boşaltır; ahenk.db'yi siler;
    ahenk.log'u boşaltır. configparser ExtendedInterpolation
    quirk'lerinden kaçınmak için satır-bazlı regex kullanır."""
    if AHENK_CONF.is_file():
        try:
            txt = AHENK_CONF.read_text(encoding="utf-8")
            new = re.sub(
                r"^(\\s*)(uid|password|host)(\\s*)=.*$",
                lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}=",
                txt, flags=re.MULTILINE,
            )
            AHENK_CONF.write_text(new, encoding="utf-8")
        except OSError as e:
            note(f"ahenk.conf yazılamadı: {e}")

    if AHENK_MSG_CONF.is_file():
        try:
            txt = AHENK_MSG_CONF.read_text(encoding="utf-8")
            new = re.sub(
                r"^(\\s*)(pulsar_host|pulsar_port|tls_trust_certs_file_path)(\\s*)=.*$",
                lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}=",
                txt, flags=re.MULTILINE,
            )
            AHENK_MSG_CONF.write_text(new, encoding="utf-8")
        except OSError as e:
            note(f"messaging.conf yazılamadı: {e}")

    try:
        AHENK_DB.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        if AHENK_LOG.is_file():
            AHENK_LOG.write_text("", encoding="utf-8")
    except OSError:
        pass


def systemctl(*args: str) -> None:
    """systemctl çağrısı; non-zero return code journald'e loglanır."""
    try:
        r = subprocess.run(
            ["systemctl", *args],
            capture_output=True, text=True, check=False,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip().replace("\\n", " ")
            note(f"systemctl {' '.join(args)} → rc={r.returncode} {err}")
    except FileNotFoundError:
        note(f"systemctl bulunamadı: {args}")


def disable_self() -> None:
    systemctl("disable", SERVICE_NAME)


def main() -> int:
    # 1) İmza dosyası yok → adım uygulanmamış veya elle silinmiş; çık.
    if not SAVED_MAC_FILE.is_file():
        note("İmza dosyası yok; klon olmadığı varsayılıyor.")
        return 0

    # 2) Mevcut MAC
    cur = primary_mac()
    if not cur:
        note("Birincil arayüzün MAC'i tespit edilemedi; atlanıyor.")
        return 0

    # 3) Kayıtlı MAC
    try:
        saved = SAVED_MAC_FILE.read_text(encoding="utf-8").strip().lower()
    except OSError as e:
        note(f"İmza dosyası okunamadı: {e}")
        return 0

    # 4) Eşit → kaynak tahta, sessizce devam.
    if cur == saved:
        note(f"MAC eşleşti ({cur}); kaynak tahta — yapılacak iş yok.")
        return 0

    # 5) Klon tespit edildi → API sorgu.
    note(f"MAC değişti ({saved} → {cur}); klon — API sorgusu yapılıyor.")
    registered = query_registered(cur)
    if registered is None:
        note("API'ye ulaşılamadı; bu boot atlanıyor (servis enabled kalır).")
        return 0

    # 6) ahenk'i durdur, credential'ları sıfırla (her iki yolda da).
    note("ahenk.service durduruluyor; credential'lar sıfırlanıyor.")
    systemctl("stop", "ahenk.service")
    wipe_ahenk_credentials()

    if registered:
        # Kayıtlı klon → ahenk yeniden başlatılır; yeni MAC ile Lider'e
        # kayıt akışına girer.
        note("Tahta API'de KAYITLI; ahenk yeniden enable + start ediliyor.")
        systemctl("enable", "ahenk.service")
        # --no-block zorunlu: Before=ahenk.service olduğumuz için blocking
        # start, ahenk'in bizi beklemesi + bizim ahenk'i beklememiz şeklinde
        # job ordering cycle'a girer. --no-block start'ı kuyruğa ekleyip
        # döner; biz çıkınca systemd ahenk'i başlatır.
        systemctl("start", "--no-block", "ahenk.service")
        # Yeni MAC ile imzala — bir daha tetiklenmesin.
        try:
            SAVED_MAC_FILE.write_text(cur + "\\n", encoding="utf-8")
        except OSError as e:
            note(f"İmza dosyası güncellenemedi: {e}")
        disable_self()
        note("Tamamlandı; klon-reclaim servisi disable edildi.")
        return 0

    # 7) Kayıtsız klon → credential'lar zaten silindi; ahenk'i disable et.
    #    Kullanıcı eta-register ile kayıt yaptığında installer akışı
    #    ahenk'i tekrar enable + start edecek.
    note("Tahta API'de KAYITSIZ; ahenk disable edildi. "
         "Kullanıcı etapadmin oturumu açıp eta-register ile "
         "tahtayı kaydetmelidir.")
    systemctl("disable", "ahenk.service")
    disable_self()
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def _build_reclaim_script() -> str:
    return (RECLAIM_SCRIPT_TEMPLATE
            .replace("@@SAVED_MAC_FILE@@", str(IMAGED_MAC_FILE))
            .replace("@@SERVICE_NAME@@", RECLAIM_SERVICE_NAME))


# --- systemd unit ------------------------------------------------------------
RECLAIM_SERVICE_TEMPLATE = '''[Unit]
Description=TiHA — Klon tespiti ve Ahenk yeniden talep akışı
Documentation=file:///usr/share/doc/tiha/m12-clone-reclaim.md
After=local-fs.target network-online.target
Wants=network-online.target
Before=ahenk.service

[Service]
Type=oneshot
ExecStart=@@RECLAIM_SCRIPT@@
RemainAfterExit=no
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
'''


def _build_reclaim_service() -> str:
    return RECLAIM_SERVICE_TEMPLATE.replace("@@RECLAIM_SCRIPT@@", str(RECLAIM_SCRIPT))


# --- Yardımcılar -------------------------------------------------------------

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


def _is_ahenk_installed() -> bool:
    """dpkg-query ile ahenk paketi kurulu mu denetler."""
    result = run_cmd(["dpkg-query", "-W", "-f=${Status}", "ahenk"], check=False)
    return result.ok and "install ok installed" in result.stdout


# --- Modül -------------------------------------------------------------------

class AhenkResetModule(Module):
    id = "m12_ahenk_reset"
    title = t("m12.title")
    sidebar_title = t("m12.sidebar_title")
    rationale_inline = True
    streams_output = True
    popup_on_success = True
    apply_hint = t("m12.apply_hint")
    # Algoritmanın görsel akış şeması ve ayrıntılı gerekçeleri.
    doc_url = (
        "https://github.com/enseitankado/tiha/blob/main/"
        "docs/m12-clone-reclaim.md"
    )
    doc_label = t("m12.doc_label")
    rationale = t("m12.rationale")
    undo_supported = True

    def preview(self) -> str:
        mac = _primary_mac() or t("m12.preview.mac_unknown")
        ahenk_kurulu = _is_ahenk_installed()
        lines = [
            t("m12.preview.header"),
            t("m12.preview.mac_line", mac=mac, path=IMAGED_MAC_FILE),
            (
                t("m12.preview.ahenk_installed")
                if ahenk_kurulu
                else t("m12.preview.ahenk_missing")
            ),
            t(
                "m12.preview.body",
                script=RECLAIM_SCRIPT,
                service=RECLAIM_SERVICE,
                service_name=RECLAIM_SERVICE_NAME,
            ),
        ]
        return "\n".join(lines)

    def apply(
        self,
        params: dict | None = None,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        # Başlangıç durumu — undo için
        was_installed_before = _is_ahenk_installed()

        # 1) MAC'i tespit et ve imza dosyasına yaz
        mac = _primary_mac()
        if not mac:
            return ApplyResult(
                False,
                t("m12.apply.no_mac"),
            )
        if progress:
            progress(t("m12.apply.source_mac", mac=mac))
        try:
            IMAGED_MAC_FILE.parent.mkdir(parents=True, exist_ok=True)
            IMAGED_MAC_FILE.write_text(mac + "\n", encoding="utf-8")
        except OSError as exc:
            return ApplyResult(False, t("m12.apply.mac_write_failed", error=exc))
        if progress:
            progress(t("m12.apply.mac_written", path=IMAGED_MAC_FILE))

        # 2) ahenk yüklü değilse kur (eta-register installer akışını taklit et)
        if not was_installed_before:
            if progress:
                progress(t("m12.apply.apt_update_header"))
            upd = run_cmd_stream(
                ["apt-get", "update"],
                progress=progress,
                env={"DEBIAN_FRONTEND": "noninteractive"},
                timeout=300,
            )
            if not upd.ok:
                return ApplyResult(
                    False,
                    t("m12.apply.apt_update_failed"),
                    data={"was_installed_before": False},
                )
            if progress:
                progress(t("m12.apply.apt_install_header"))
            inst = run_cmd_stream(
                ["apt-get", "install", "-y", "ahenk"],
                progress=progress,
                env={"DEBIAN_FRONTEND": "noninteractive"},
                timeout=900,
            )
            if not inst.ok:
                return ApplyResult(
                    False,
                    t("m12.apply.install_failed"),
                    data={"was_installed_before": False},
                )
            if progress:
                progress(t("m12.apply.enabling_ahenk"))
            run_cmd(["systemctl", "enable", "ahenk.service"], check=False)
        else:
            if progress:
                progress(t("m12.apply.already_installed"))

        # 3) Boot servisi: betik + unit yaz, enable et
        if progress:
            progress(t("m12.apply.writing_service", service=RECLAIM_SERVICE_NAME))
        try:
            RECLAIM_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
            RECLAIM_SCRIPT.write_text(_build_reclaim_script(), encoding="utf-8")
            RECLAIM_SCRIPT.chmod(0o755)
            RECLAIM_SERVICE.write_text(_build_reclaim_service(), encoding="utf-8")
        except OSError as exc:
            return ApplyResult(
                False,
                t("m12.apply.service_write_failed", error=exc),
                data={"was_installed_before": was_installed_before},
            )
        run_cmd(["systemctl", "daemon-reload"], check=False)
        en = run_cmd(["systemctl", "enable", RECLAIM_SERVICE_NAME], check=False)
        if not en.ok:
            return ApplyResult(
                False,
                t("m12.apply.enable_failed", service=RECLAIM_SERVICE_NAME),
                details=en.stderr,
                data={"was_installed_before": was_installed_before},
            )
        if progress:
            progress(t("m12.apply.enabled", service=RECLAIM_SERVICE_NAME))

        details = t(
            "m12.apply.details",
            mac_file=IMAGED_MAC_FILE,
            mac=mac,
            script=RECLAIM_SCRIPT,
            service=RECLAIM_SERVICE,
            ahenk=(t("m12.apply.ahenk_was_installed") if was_installed_before
                   else t("m12.apply.ahenk_installed_by_tiha")),
        )
        return ApplyResult(
            True,
            t("m12.apply.done"),
            details=details,
            data={
                "was_installed_before": was_installed_before,
                "imaged_mac": mac,
            },
        )

    def undo(
        self,
        data: dict,
        params: dict | None = None,
    ) -> ApplyResult:
        data = data or {}
        was_installed_before = bool(data.get("was_installed_before", True))

        notes: list[str] = []

        # 1) Boot servisini disable et + dosyaları sil
        run_cmd(["systemctl", "disable", RECLAIM_SERVICE_NAME], check=False)
        if _rm(RECLAIM_SERVICE):
            notes.append(t("m12.undo.removed_file", path=RECLAIM_SERVICE))
        if _rm(RECLAIM_SCRIPT):
            notes.append(t("m12.undo.removed_file", path=RECLAIM_SCRIPT))
        run_cmd(["systemctl", "daemon-reload"], check=False)

        # 2) İmza dosyasını sil
        if _rm(IMAGED_MAC_FILE):
            notes.append(t("m12.undo.removed_file", path=IMAGED_MAC_FILE))

        # 3) ahenk TiHA tarafından kurulduysa kaldır
        if not was_installed_before and _is_ahenk_installed():
            run_cmd(["systemctl", "disable", "--now", "ahenk.service"], check=False)
            purge = run_cmd(
                ["apt-get", "purge", "-y", "ahenk"],
                env={"DEBIAN_FRONTEND": "noninteractive"},
                timeout=600,
            )
            run_cmd(
                ["apt-get", "autoremove", "-y"],
                env={"DEBIAN_FRONTEND": "noninteractive"},
                timeout=300,
            )
            if purge.ok:
                notes.append(t("m12.undo.ahenk_removed"))
            else:
                return ApplyResult(
                    False,
                    t("m12.undo.ahenk_remove_failed"),
                    details=purge.stderr,
                )
        elif was_installed_before:
            notes.append(t("m12.undo.ahenk_kept"))

        return ApplyResult(
            True,
            t("m12.undo.done"),
            details="\n".join(t("m12.undo.detail_item", item=n) for n in notes) if notes else None,
        )
