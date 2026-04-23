"""Modül 7 — Zaman senkronizasyonu (systemd-timesyncd).

**Ne yapar?**
``/etc/systemd/timesyncd.conf.d/tiha.conf`` adında bir drop-in dosyası
yazar; ``NTP=`` ve ``FallbackNTP=`` yönergelerini kullanıcı tercihine
göre doldurur. ``timedatectl set-ntp true`` ile NTP istemcisini
etkinleştirir ve ``systemd-timesyncd``'yi yeniden başlatır. Opsiyonel
olarak saat dilimini de (``Europe/Istanbul`` varsayılan) ayarlar.

**Neden gerekir?**
Yanlış saat, sertifika doğrulamasını, Kerberos/TLS oturumlarını, TOTP
(6 haneli OTP) doğrulamasını ve merkezi log zaman damgalarını bozar.
Ağa göre dış internete NTP (UDP 123) çıkışı kısıtlı olabileceğinden
okulun iç NTP sunucusu (ör. ``time.meb.gov.tr``) tercih edilebilir.

**Geri al.** Yalnızca TiHA'nın eklediği drop-in kaldırılır, servis
yeniden başlatılır; Debian varsayılan davranışına dönülür.
"""

from __future__ import annotations

from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.utils import run_cmd

log = get_logger(__name__)

DROPIN = Path("/etc/systemd/timesyncd.conf.d/tiha.conf")


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
    rationale = (
        "Tahta saatinin doğru olmasını sağlar. Yanlış saat, sertifika/TLS "
        "doğrulamalarını, OTP kodlarını ve merkezi log zaman damgalarını "
        "bozar. Okul ağında dış NTP portu kısıtlıysa iç sunucuyu tanımlarız."
    )

    def preview(self) -> str:
        existing = DROPIN.read_text(encoding="utf-8") if DROPIN.exists() else "(yok)"
        return f"Mevcut TiHA NTP ayarı:\n{existing}"

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

        # Drop-in yaz
        try:
            DROPIN.parent.mkdir(parents=True, exist_ok=True)
            DROPIN.write_text(_render(ntp, fallback), encoding="utf-8")
            DROPIN.chmod(0o644)
        except OSError as exc:
            return ApplyResult(False, f"Drop-in yazılamadı: {exc}")

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
            details=f"Dosya: {DROPIN}\n\n{status}",
        )

    def undo(self, data: dict) -> ApplyResult:
        try:
            DROPIN.unlink(missing_ok=True)
        except OSError:
            pass
        run_cmd(["systemctl", "restart", "systemd-timesyncd"])
        return ApplyResult(True, "TiHA özel NTP yapılandırması kaldırıldı.")
