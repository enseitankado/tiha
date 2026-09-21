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

from ..core.i18n import t
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
        return t("m15.wol.no_iface")
    if not _is_ethtool_installed():
        return t("m15.wol.ethtool_missing")
    res = run_cmd(["ethtool", iface], check=False)
    if not res.ok:
        return t("m15.wol.query_failed")
    wake_current = ""
    supported = ""
    for line in res.stdout.splitlines():
        s = line.strip()
        if s.lower().startswith("wake-on:"):
            wake_current = s.split(":", 1)[1].strip()
        elif s.lower().startswith("supports wake-on:"):
            supported = s.split(":", 1)[1].strip()
    if not supported:
        return t("m15.wol.no_support_reported")
    if "g" not in supported:
        return t("m15.wol.no_magic", supported=supported)
    if not wake_current:
        return t("m15.wol.state_unknown", supported=supported)
    if wake_current == "d":
        return t("m15.wol.off", current=wake_current)
    return t("m15.wol.on", current=wake_current)


# --- Modül -------------------------------------------------------------------

class WakeOnLanModule(Module):
    id = "m15_wake_on_lan"
    title = t("m15.title")
    sidebar_title = t("m15.sidebar_title")
    apply_hint = t("m15.apply_hint")
    popup_on_success = True
    rationale = t("m15.rationale")
    undo_supported = True

    def preview(self) -> str:
        iface, mac = _detect_primary_iface_mac()
        wol_installed = WOL_SERVICE.exists() and WOL_SCRIPT.exists()
        ethtool_ok = _is_ethtool_installed()
        wol_status = _check_wol_support(iface) if iface else t("m15.wol.no_iface")
        installed, to_install = t("m15.preview.installed"), t("m15.preview.to_install")

        lines: list[str] = []
        lines.append(t("m15.preview.iface", iface=iface or t("m15.preview.not_detected")))
        lines.append(t("m15.preview.mac", mac=mac or t("m15.preview.unreadable")))
        lines.append(t("m15.preview.ethtool", state=installed if ethtool_ok else to_install))
        lines.append(t("m15.preview.service", state=installed if wol_installed else to_install))
        lines.append(t("m15.preview.wol_state", status=wol_status))
        lines.append("")
        lines.append(t("m15.preview.on_apply"))
        lines.append(t("m15.preview.script_written", script=WOL_SCRIPT))
        lines.append(t("m15.preview.service_written", service=WOL_SERVICE))
        lines.append(f"  - systemctl enable {WOL_SERVICE_NAME}")
        lines.append("")
        lines.append(t("m15.preview.clone_note"))
        lines.append("")
        lines.append(t("m15.preview.bios"))
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
            # Kutu sisteme bakarak doluyor (params.py "default_from"); servis
            # kuruluyken işaretin kaldırılıp uygulanması "kaldır" isteğidir.
            if self.wol_active():
                notes: list[str] = []
                self._remove_service(notes)
                return ApplyResult(
                    True,
                    t("m15.apply.removed"),
                    details="\n".join(f"- {n}" for n in notes) if notes else "",
                    data={"wol_removed": True},
                )
            return ApplyResult(False, t("m15.apply.not_selected"))

        was_ethtool_installed = _is_ethtool_installed()

        # 1) ethtool paketini garantile
        if not was_ethtool_installed:
            if progress:
                progress(t("m15.apply.ethtool_installing"))
            inst = run_cmd(
                ["apt-get", "install", "-y", "ethtool"],
                env={"DEBIAN_FRONTEND": "noninteractive"},
                timeout=180,
            )
            if not inst.ok:
                return ApplyResult(
                    False,
                    t("m15.apply.ethtool_failed"),
                    details=inst.stderr,
                )
            if progress:
                progress(t("m15.apply.ethtool_installed"))
        elif progress:
            progress(t("m15.apply.ethtool_present"))

        # 2) Boot script + systemd unit
        if progress:
            progress(t("m15.apply.writing_script", path=WOL_SCRIPT))
        try:
            WOL_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
            WOL_SCRIPT.write_text(WOL_SCRIPT_CONTENT, encoding="utf-8")
            WOL_SCRIPT.chmod(0o755)
            WOL_SERVICE.write_text(WOL_SERVICE_CONTENT, encoding="utf-8")
        except OSError as exc:
            return ApplyResult(
                False,
                t("m15.apply.write_failed", error=exc),
                data={"was_ethtool_installed": was_ethtool_installed},
            )
        if progress:
            progress(t("m15.apply.unit_written", name=WOL_SERVICE_NAME))

        # 3) Servisi hazırla — daemon-reload + enable + hemen bir kez
        # çalıştır (canlı tahtada WoL ayarını da o an yazsın).
        run_cmd(["systemctl", "daemon-reload"], check=False)
        en = run_cmd(
            ["systemctl", "enable", WOL_SERVICE_NAME], check=False,
        )
        if not en.ok:
            return ApplyResult(
                False,
                t("m15.apply.enable_failed", name=WOL_SERVICE_NAME),
                details=en.stderr,
                data={"was_ethtool_installed": was_ethtool_installed},
            )
        if progress:
            progress(t("m15.apply.enabled", name=WOL_SERVICE_NAME))
        # Şimdi bir kez çalıştır — kaynak tahtada da WoL bayrağı yazılsın
        run_cmd(
            ["systemctl", "start", WOL_SERVICE_NAME], check=False,
        )
        if progress:
            progress(t("m15.apply.ran_once"))

        # 4) Doğrulama — ne yazıldı?
        iface, mac = _detect_primary_iface_mac()
        wol_status = _check_wol_support(iface) if iface else t("m15.wol.no_iface")

        details = t(
            "m15.apply.details",
            iface=iface or "(?)",
            mac=mac or "(?)",
            status=wol_status,
            service=WOL_SERVICE,
            script=WOL_SCRIPT,
            wake_mac=mac or "<MAC>",
        )
        return ApplyResult(
            True,
            t("m15.apply.success"),
            details=details,
            data={"was_ethtool_installed": was_ethtool_installed},
        )

    def wol_active(self) -> bool:
        """Uzaktan uyandırma servisi kurulu mu? (Formdaki kutu bunu gösterir.)"""
        return WOL_SERVICE.exists() and WOL_SCRIPT.exists()

    @staticmethod
    def _remove_service(notes: list[str]) -> None:
        run_cmd(
            ["systemctl", "disable", "--now", WOL_SERVICE_NAME], check=False,
        )
        for path in (WOL_SERVICE, WOL_SCRIPT):
            if path.exists():
                try:
                    path.unlink()
                    notes.append(t("m15.undo.deleted", path=path))
                except OSError as exc:
                    log.warning("%s silinemedi: %s", path, exc)
        run_cmd(["systemctl", "daemon-reload"], check=False)

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        data = data or {}
        was_ethtool_installed = bool(data.get("was_ethtool_installed", True))
        notes: list[str] = []

        self._remove_service(notes)

        # ethtool paketini TiHA kurduysa geri al
        if not was_ethtool_installed and _is_ethtool_installed():
            purge = run_cmd(
                ["apt-get", "purge", "-y", "ethtool"],
                env={"DEBIAN_FRONTEND": "noninteractive"},
                timeout=180,
            )
            if purge.ok:
                notes.append(t("m15.undo.ethtool_removed"))
            else:
                notes.append(t("m15.undo.ethtool_remove_failed"))
        elif was_ethtool_installed:
            notes.append(t("m15.undo.ethtool_kept"))

        return ApplyResult(
            True,
            t("m15.undo.done"),
            details="\n".join(f"- {n}" for n in notes) if notes else None,
        )
