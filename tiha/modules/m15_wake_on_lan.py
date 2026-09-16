"""Modül 15 — Uzaktan uyandırma (Wake-on-LAN).

Amaç
====
Bakımcının merkez bilgisayarından tahtaları sabah otomatik açabilmesi.
İlk derse gelen öğretmenin karşısına zaten boot olmuş, greeter'da bekleyen
bir tahta çıkar; kayıp süre yok.

Bu adım klonlanmış tahtaların Linux katmanını (Katman B) her açılışta
Wake-on-LAN dinlemede tutar. TiHA'nın yapmadığı şeyler:

* Katman A (BIOS/UEFI): "Wake on LAN" seçeneğinin **açık** olması gerekir.
  Faz 2 Vestel modellerinde fabrika default açık; yine de bir kez elle
  doğrulanmalı. TiHA bunu değiştirmez (eta-112 sadece parola alanına
  kalibre edilmiştir).
* Katman C (ağ): Ethernet kablosu takılı olmalı; switch portu kapalıyken
  de aktif kalmalı (çoğu switch varsayılan böyle). Aynı VLAN'da uyandırma
  bilgisayarı gerekir; broadcast paketleri VLAN'ı geçmez.

Wizard zamanında yapılan
========================
1. */usr/local/sbin/tiha-wake-on-lan.sh* — her boot'ta çalışır; birincil
   ağ arayüzünü tespit eder ve ``ethtool -s <iface> wol g`` çağırır.
2. */etc/systemd/system/tiha-wake-on-lan.service* — Type=oneshot,
   After=network-online.target, RemainAfterExit=yes.
3. ``ethtool`` paketi kurulur (yoksa).
4. Servis enable edilir.

Uyandırma sinyali
=================
Bakımcı, merkez bilgisayarında bir "wakeonlan" veya "etherwake" komutuyla
tahtanın MAC adresine magic packet gönderir. TiHA merkez betiği yazmaz;
o bakımcının kendi işidir. Tahtanın MAC'i m12'nin yazdığı
``imaged-mac`` dosyasından okunabilir veya klon boot ederse Ahenk üzerinden
Lider'e kayıt olur; oradan da MAC gözükür.

Geri al
=======
Servis + script + ``ethtool`` paketi (yalnız bu adım kurduysa) kaldırılır.
Ağ kartının WoL bayrağı bir sonraki kapanışta zaten sıfırlanır (kalıcı
değildir); ek işlem gerekmez.
"""

from __future__ import annotations

from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.utils import run_cmd

log = get_logger(__name__)


# --- Yerel yerleşim ----------------------------------------------------------

WOL_SCRIPT = Path("/usr/local/sbin/tiha-wake-on-lan.sh")
WOL_SERVICE = Path("/etc/systemd/system/tiha-wake-on-lan.service")
WOL_SERVICE_NAME = WOL_SERVICE.name


# --- Boot script -------------------------------------------------------------
# Bilinçli olarak bash; ethtool + ip komutları tek satırlık iş, Python'a
# gerek yok. Birincil ağ arayüzünü default route'tan tespit eder; yoksa
# ilk fiziksel (device symlink'i olan) arayüzü seçer. Ağ kartı WoL
# desteklemiyorsa ethtool hata döner; sessizce log'a yazılır ve boot
# devam eder.
WOL_SCRIPT_CONTENT = """#!/bin/bash
# TiHA — Her boot'ta ağ kartını Wake-on-LAN dinlemede tut.
# WoL ayarı kalıcı değildir; her boot'ta yeniden yazılması gerekir.
set -eu

TAG="tiha-wake-on-lan"
log() { logger -t "$TAG" -- "$*"; }

# 1) Birincil arayüzü default route'tan tespit et
iface="$(ip -o -4 route show to default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"

# 2) Yoksa ilk fiziksel arayüzü seç (device symlink'i olan)
if [ -z "${iface:-}" ]; then
    for candidate in /sys/class/net/*; do
        name="$(basename "$candidate")"
        [ "$name" = "lo" ] && continue
        [ -L "$candidate/device" ] || continue
        iface="$name"
        break
    done
fi

if [ -z "${iface:-}" ]; then
    log "Birincil arayüz tespit edilemedi; WoL ayarı atlandı."
    exit 0
fi

# 3) WoL modunu yaz (magic packet dinle)
if /usr/sbin/ethtool -s "$iface" wol g 2>/dev/null; then
    log "Arayüz $iface için WoL (magic packet) etkin."
else
    log "Arayüz $iface için WoL yazılamadı — kart desteklemiyor olabilir."
fi
"""


WOL_SERVICE_CONTENT = f"""[Unit]
Description=TiHA - Wake-on-LAN dinleme modunu her boot'ta yaz
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={WOL_SCRIPT}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""


# --- Yardımcılar -------------------------------------------------------------

def _is_ethtool_installed() -> bool:
    result = run_cmd(
        ["dpkg-query", "-W", "-f=${Status}", "ethtool"], check=False,
    )
    return result.ok and "install ok installed" in result.stdout


def _detect_primary_iface_mac() -> tuple[str | None, str | None]:
    """Birincil arayüzün adı + MAC'ini döner (preview için, apply
    çalıştırmaz)."""
    result = run_cmd(
        ["ip", "-o", "-4", "route", "show", "to", "default"], check=False,
    )
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
        return None, None
    addr_path = Path(f"/sys/class/net/{iface}/address")
    if not addr_path.is_file():
        return iface, None
    try:
        return iface, addr_path.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return iface, None


def _check_wol_support(iface: str) -> str:
    """ethtool kart hakkında ne diyor? 'destek yok', 'kapalı', 'aktif',
    'okunamadı' gibi kısa bir tanı satırı döner."""
    if not iface:
        return "arayüz yok"
    if not _is_ethtool_installed():
        return "ethtool paketi henüz kurulu değil"
    res = run_cmd(["ethtool", iface], check=False)
    if not res.ok:
        return "sorgulanamadı"
    wake_current = ""
    supported = ""
    for line in res.stdout.splitlines():
        s = line.strip()
        if s.lower().startswith("wake-on:"):
            wake_current = s.split(":", 1)[1].strip()
        elif s.lower().startswith("supports wake-on:"):
            supported = s.split(":", 1)[1].strip()
    if not supported:
        return "kart WoL desteği bildirmedi"
    if "g" not in supported:
        return f"kart magic packet desteklemiyor (destek: {supported})"
    if not wake_current:
        return f"kart destekliyor (Supports: {supported}), durum okunamadı"
    if wake_current == "d":
        return f"kart destekliyor ama şu an KAPALI (Wake-on: {wake_current})"
    return f"kart destekliyor ve AKTİF (Wake-on: {wake_current})"


# --- Modül -------------------------------------------------------------------

class WakeOnLanModule(Module):
    id = "m15_wake_on_lan"
    title = "Uzaktan uyandırma (Wake-on-LAN)"
    sidebar_title = "Uzaktan uyandırma"
    apply_hint = (
        "Ağ kartının magic packet dinleme ayarı her boot'ta yeniden yazılır."
    )
    popup_on_success = True
    rationale = (
        "Bu adım, klonlanmış tahtaların ağ kartını her açılışta Wake-on-LAN "
        "modunda tutar. Böylece bakımcı merkez bilgisayarından tahtaya "
        "'magic packet' göndererek kapalı tahtayı uzaktan açabilir; sabah "
        "07:50'de tüm sınıflar açılmış hâlde 09:00 dersine hazır bekler.\n\n"
        "Bu adımın çalışması için üç katman gerekir: (1) BIOS/UEFI'de "
        "\"Wake on LAN\" seçeneğinin AÇIK olması — böylece bilgisayar "
        "kapatıldığında ağ kartına standby güç verilir; (2) Linux tarafında "
        "ethtool ile magic packet dinleme modu yazılması — TiHA'nın bu "
        "adımda kurduğu servis her boot'ta bu ayarı tazeler çünkü bu ayar "
        "kalıcı değildir; (3) Ethernet kablosunun takılı olması ve "
        "switch portunun kapalı bilgisayara güç kesilmemesi.\n\n"
        "BIOS ayarı imaj klonlamayla taşınmaz (BIOS ayarları anakartın "
        "CMOS'unda tutulur, disk imajından bağımsızdır). Faz 2 Vestel "
        "modellerinde fabrika ayarı olarak WoL genelde AÇIK gelir; sahada "
        "her tahta için bir kez BIOS'a girip kontrol edilmesi önerilir. "
        "Linux katmanı ise bu adımın kurduğu systemd servisi ile her "
        "klonda otomatik hazır olur.\n\n"
        "Merkezden uyandırma için bakımcı kendi bilgisayarında "
        "'wakeonlan <mac>' veya 'etherwake <mac>' komutunu kullanır. "
        "TiHA merkez betiği yazmaz; bakımcının kendi tarafında bir cron "
        "işiyle sabah tüm tahtaları toplu uyandırabilir."
    )
    undo_supported = True

    def preview(self) -> str:
        iface, mac = _detect_primary_iface_mac()
        wol_installed = WOL_SERVICE.exists() and WOL_SCRIPT.exists()
        ethtool_ok = _is_ethtool_installed()
        wol_status = _check_wol_support(iface) if iface else "arayüz yok"

        lines: list[str] = []
        lines.append(
            f"Birincil ağ arayüzü  : {iface or '(tespit edilemedi)'}"
        )
        lines.append(
            f"MAC adresi           : {mac or '(okunamadı)'}"
        )
        lines.append(
            f"ethtool paketi       : {'kurulu' if ethtool_ok else 'kurulacak'}"
        )
        lines.append(
            f"TiHA WoL servisi     : {'kurulu' if wol_installed else 'kurulacak'}"
        )
        lines.append(f"Ağ kartı WoL durumu  : {wol_status}")
        lines.append("")
        lines.append("Bu adım uygulandığında:")
        lines.append(f"  - {WOL_SCRIPT} yazılır (birincil arayüzü tespit eder,")
        lines.append("    ethtool ile magic packet dinlemeyi açar)")
        lines.append(f"  - {WOL_SERVICE} yazılır (systemd oneshot)")
        lines.append(f"  - systemctl enable {WOL_SERVICE_NAME}")
        lines.append("")
        lines.append(
            "Klon makinede her açılışta servis çalışır; ağ kartına magic "
            "packet dinleme yazılır. Bilgisayar kapatıldığında kart "
            "dinlemede kalır; merkezden 'wakeonlan <MAC>' komutu ile "
            "uyandırılabilir."
        )
        lines.append("")
        lines.append("Hatırlatma: BIOS'ta \"Wake on LAN\" seçeneği AÇIK olmalı.")
        lines.append("Faz 2 Vestel'de fabrika ayarı genelde açık; klon başına")
        lines.append("bir kez doğrulanması önerilir. Bu ayar disk imajıyla")
        lines.append("taşınmaz (CMOS'ta tutulur, anakart başına).")
        return "\n".join(lines)

    def apply(
        self,
        params: dict | None = None,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        params = params or {}
        enable = str(
            params.get("enable_wol_listen", "False")
        ).lower() in ("true", "1", "yes", "on")
        if not enable:
            return ApplyResult(
                False,
                "Ağ kartını magic packet dinleme moduna alma seçeneği "
                "işaretlenmedi. Adım atlandı.",
            )

        was_ethtool_installed = _is_ethtool_installed()

        # 1) ethtool paketini garantile
        if not was_ethtool_installed:
            if progress:
                progress("ethtool paketi kuruluyor...")
            inst = run_cmd(
                ["apt-get", "install", "-y", "ethtool"],
                env={"DEBIAN_FRONTEND": "noninteractive"},
                timeout=180,
            )
            if not inst.ok:
                return ApplyResult(
                    False,
                    "ethtool paketi kurulamadı.",
                    details=inst.stderr,
                )
            if progress:
                progress("ethtool kuruldu.")
        elif progress:
            progress("ethtool zaten kurulu.")

        # 2) Boot script + systemd unit
        if progress:
            progress(f"Boot scripti yazılıyor: {WOL_SCRIPT}")
        try:
            WOL_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
            WOL_SCRIPT.write_text(WOL_SCRIPT_CONTENT, encoding="utf-8")
            WOL_SCRIPT.chmod(0o755)
            WOL_SERVICE.write_text(WOL_SERVICE_CONTENT, encoding="utf-8")
        except OSError as exc:
            return ApplyResult(
                False,
                f"WoL servis dosyaları yazılamadı: {exc}",
                data={"was_ethtool_installed": was_ethtool_installed},
            )
        if progress:
            progress(f"Systemd unit yazıldı: {WOL_SERVICE_NAME}")

        # 3) Servisi hazırla — daemon-reload + enable + hemen bir kez
        # çalıştır (canlı tahtada WoL ayarını da o an yazsın).
        run_cmd(["systemctl", "daemon-reload"], check=False)
        en = run_cmd(
            ["systemctl", "enable", WOL_SERVICE_NAME], check=False,
        )
        if not en.ok:
            return ApplyResult(
                False,
                f"{WOL_SERVICE_NAME} enable edilemedi.",
                details=en.stderr,
                data={"was_ethtool_installed": was_ethtool_installed},
            )
        if progress:
            progress(f"{WOL_SERVICE_NAME} enable edildi.")
        # Şimdi bir kez çalıştır — kaynak tahtada da WoL bayrağı yazılsın
        run_cmd(
            ["systemctl", "start", WOL_SERVICE_NAME], check=False,
        )
        if progress:
            progress("Servis bir kez çalıştırıldı; ağ kartı WoL modunda.")

        # 4) Doğrulama — ne yazıldı?
        iface, mac = _detect_primary_iface_mac()
        wol_status = _check_wol_support(iface) if iface else "arayüz yok"

        details = (
            f"Arayüz : {iface or '(?)'}\n"
            f"MAC    : {mac or '(?)'}\n"
            f"Durum  : {wol_status}\n\n"
            f"Servis : {WOL_SERVICE}\n"
            f"Script : {WOL_SCRIPT}\n\n"
            "Klon her boot'ta bu servisi çalıştırıp ağ kartını magic "
            "packet dinleme moduna alacak. Bakımcı merkez bilgisayarından "
            f"'wakeonlan {mac or '<MAC>'}' ile tahtayı uzaktan "
            "uyandırabilir."
        )
        return ApplyResult(
            True,
            "Uzaktan uyandırma (Wake-on-LAN) servisi kuruldu ve etkinleştirildi.",
            details=details,
            data={"was_ethtool_installed": was_ethtool_installed},
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        data = data or {}
        was_ethtool_installed = bool(data.get("was_ethtool_installed", True))
        notes: list[str] = []

        run_cmd(
            ["systemctl", "disable", "--now", WOL_SERVICE_NAME], check=False,
        )
        if WOL_SERVICE.exists():
            try:
                WOL_SERVICE.unlink()
                notes.append(f"{WOL_SERVICE} silindi")
            except OSError as exc:
                log.warning("%s silinemedi: %s", WOL_SERVICE, exc)
        if WOL_SCRIPT.exists():
            try:
                WOL_SCRIPT.unlink()
                notes.append(f"{WOL_SCRIPT} silindi")
            except OSError as exc:
                log.warning("%s silinemedi: %s", WOL_SCRIPT, exc)
        run_cmd(["systemctl", "daemon-reload"], check=False)

        # ethtool paketini TiHA kurduysa geri al
        if not was_ethtool_installed and _is_ethtool_installed():
            purge = run_cmd(
                ["apt-get", "purge", "-y", "ethtool"],
                env={"DEBIAN_FRONTEND": "noninteractive"},
                timeout=180,
            )
            if purge.ok:
                notes.append("ethtool paketi kaldırıldı (TiHA kurmuştu)")
            else:
                notes.append(
                    "ethtool paketi kaldırılamadı (apt hatası) - manuel kaldırma önerilir"
                )
        elif was_ethtool_installed:
            notes.append("ethtool paketi korundu (başlangıçta zaten kuruluydu)")

        return ApplyResult(
            True,
            "Uzaktan uyandırma servisi kaldırıldı.",
            details="\n".join(f"- {n}" for n in notes) if notes else None,
        )
