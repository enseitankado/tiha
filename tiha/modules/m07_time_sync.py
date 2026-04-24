"""Modül 7 — Zaman senkronizasyonu (systemd-timesyncd).

**Ne yapar?**
``/etc/systemd/timesyncd.conf.d/tiha.conf`` adında ek bir yapılandırma dosyası
yazar; ``NTP=`` ve ``FallbackNTP=`` yönergelerini kullanıcı tercihine
göre doldurur. ``timedatectl set-ntp true`` ile NTP istemcisini
etkinleştirir ve ``systemd-timesyncd``'yi yeniden başlatır. Opsiyonel
olarak saat dilimini de (``Europe/Istanbul`` varsayılan) ayarlar.

**Neden gerekir?**
Yanlış saat, sertifika doğrulamasını, Kerberos/TLS oturumlarını, TOTP
(6 haneli OTP) doğrulamasını ve merkezi log zaman damgalarını bozar.
Ağa göre dış internete NTP (UDP 123) çıkışı kısıtlı olabileceğinden
okulun iç NTP sunucusu (ör. ``time.meb.gov.tr``) tercih edilebilir.

**Geri al.** Yalnızca TiHA'nın eklediği ek yapılandırma dosyası kaldırılır, servis
yeniden başlatılır; Debian varsayılan davranışına dönülür.
"""

from __future__ import annotations

from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.utils import run_cmd

log = get_logger(__name__)

TIMESYNCD_CONF = Path("/etc/systemd/timesyncd.conf.d/tiha.conf")


def _render(ntp: str, fallback: str) -> str:
    lines = ["# TiHA — özel NTP sunucu listesi", "[Time]"]
    if ntp:
        lines.append(f"NTP={ntp}")
    if fallback:
        lines.append(f"FallbackNTP={fallback}")
    return "\n".join(lines) + "\n"


class TimeSyncModule(Module):
    id = "m07_time_sync"
    title = "Zaman senkronizasyonu (NTP)"
    apply_hint = (
        "NTP sunucuları ve saat dilimi ayarlanır."
    )
    rationale = (
        "Tahta saatinin doğru olmasını sağlar. TOTP (öğretmen OTP kodları) "
        "zaman tabanlıdır: tahtanın saati sunucu saatinden 30 saniyeden fazla "
        "kayarsa ÜRETTİĞİNİZ HER OTP KODU GEÇERSİZ SAYILIR, öğretmen tahtaya "
        "giremez. Aynı neden sertifika/TLS ve merkezi log zaman damgaları "
        "için de geçerlidir. Bu adım atlanırsa sahadaki tahtalarda saat "
        "kayması yaşanabilir. Okul ağı dış NTP'ye (udp/123) izin vermiyorsa "
        "MEB iç NTP sunucusu kullanılmalıdır."
    )

    def preview(self) -> str:
        if TIMESYNCD_CONF.exists():
            return f"Mevcut TiHA NTP ayarı ({TIMESYNCD_CONF}):\n\n{TIMESYNCD_CONF.read_text(encoding='utf-8')}"
        # Dosya yoksa kullanıcıyı şaşırtacak "(yok)" yerine ne yapılacağı anlatılır.
        return (
            "TiHA özel NTP yapılandırması henüz yok. Bu adım şunları yapar:\n"
            f"  • {TIMESYNCD_CONF} içerisine NTP=... ve FallbackNTP=... yazar\n"
            "  • saat dilimini Europe/Istanbul (varsayılan) olarak ayarlar\n"
            "  • timedatectl set-ntp true + systemd-timesyncd restart"
        )

    def apply(self, params=None, progress=None) -> ApplyResult:
        params = params or {}
        ntp = (params.get("ntp_servers") or "").strip()
        fallback = (params.get("ntp_fallback") or "").strip()
        tz = (params.get("timezone") or "Europe/Istanbul").strip()

        if not ntp and not fallback:
            return ApplyResult(False, "En az bir NTP sunucusu girmelisiniz.")

        # Saat dilimi
        tz_res = run_cmd(["timedatectl", "set-timezone", tz])
        if not tz_res.ok:
            log.warning("timezone atanamadı: %s", tz_res.stderr.strip())

        # Ek yapılandırma dosyası yaz
        try:
            TIMESYNCD_CONF.parent.mkdir(parents=True, exist_ok=True)
            TIMESYNCD_CONF.write_text(_render(ntp, fallback), encoding="utf-8")
            TIMESYNCD_CONF.chmod(0o644)
        except OSError as exc:
            return ApplyResult(False, f"Ek yapılandırma dosyası yazılamadı: {exc}")

        run_cmd(["timedatectl", "set-ntp", "true"])
        restart = run_cmd(["systemctl", "restart", "systemd-timesyncd"])
        if not restart.ok:
            return ApplyResult(False, "systemd-timesyncd yeniden başlatılamadı.",
                               details=restart.stderr)

        # Mevcut durumu göster
        status = run_cmd(["timedatectl"]).stdout
        return ApplyResult(
            True,
            f"Zaman senkronu ayarlandı (saat dilimi: {tz}).",
            details=f"Dosya: {TIMESYNCD_CONF}\n\n{status}",
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        try:
            TIMESYNCD_CONF.unlink(missing_ok=True)
        except OSError:
            pass
        run_cmd(["systemctl", "restart", "systemd-timesyncd"])
        return ApplyResult(True, "TiHA özel NTP yapılandırması kaldırıldı.")
