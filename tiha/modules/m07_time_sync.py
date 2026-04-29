"""Modül 7 — Zaman senkronizasyonu (systemd-timesyncd).

**Ne yapar?**
``/etc/systemd/timesyncd.conf.d/tiha.conf`` adında ek bir yapılandırma dosyası
yazar; ``NTP=`` ve ``FallbackNTP=`` yönergelerini kullanıcı tercihine
göre doldurur. ``timedatectl set-ntp true`` ile NTP istemcisini
etkinleştirir ve ``systemd-timesyncd``'yi yeniden başlatır. Opsiyonel
olarak saat dilimini de (``Europe/Istanbul`` varsayılan) ayarlar.

**Neden gerekir?**
Yanlış saat, sertifika doğrulamasını, Kerberos/TLS oturumlarını, 6 haneli
PIN kodu (TOTP) doğrulamasını ve merkezi log zaman damgalarını bozar.
Ağa göre dış internete NTP (UDP 123) çıkışı kısıtlı olabileceğinden
okulun iç NTP sunucusu (ör. ``time.meb.gov.tr``) tercih edilebilir.

**Geri al.** Yalnızca TiHA'nın eklediği ek yapılandırma dosyası kaldırılır, servis
yeniden başlatılır; Debian varsayılan davranışına dönülür.
"""

from __future__ import annotations

import socket
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
        "Tahtanın saatini doğru tutmak için birincil ve yedek zaman "
        "(NTP) sunucularını tanımlar. Bu adım atlanırsa sahada "
        "tahtaların saati kayabilir.\n\n"
        "Neden bu kadar kritik? PIN kodları **zaman tabanlıdır** — "
        "her 30 saniyede bir değişir. Tahtanın saati, PIN doğrulayan "
        "sunucudan 30 saniyeden fazla saparsa üretilen HER PIN kodu "
        "geçersiz sayılır ve öğretmen tahtaya giremez. Bu yüzden "
        "NTP'yi bilinen bir sunucuya sabitliyoruz; dış NTP'ye (udp/123) "
        "kapalı bir okul ağı varsa MEB iç NTP sunucusu kullanılır."
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

    def test_ntp_servers_action(self, progress=None) -> ApplyResult:
        """Form'daki NTP sunucularını test eder."""
        if progress:
            progress("NTP sunucu testi başlatılıyor...")

        # Mevcut yapılandırmadaki sunucuları test et
        if TIMESYNCD_CONF.exists():
            try:
                if progress:
                    progress("Mevcut yapılandırma dosyası okunuyor...")
                content = TIMESYNCD_CONF.read_text(encoding="utf-8")
                ntp_line = ""
                fallback_line = ""

                for line in content.splitlines():
                    if line.startswith("NTP="):
                        ntp_line = line.split("=", 1)[1].strip()
                    elif line.startswith("FallbackNTP="):
                        fallback_line = line.split("=", 1)[1].strip()

                servers = []
                if ntp_line:
                    servers.extend(ntp_line.split())
                if fallback_line:
                    servers.extend(fallback_line.split())

                if not servers:
                    if progress:
                        progress("❌ Yapılandırmada NTP sunucusu bulunamadı.")
                    return ApplyResult(False, "Mevcut yapılandırmada NTP sunucusu bulunamadı.")

                if progress:
                    progress(f"📋 {len(servers)} NTP sunucusu bulundu: {', '.join(servers)}")

                return self._test_ntp_servers(servers, progress)

            except Exception as e:
                if progress:
                    progress(f"❌ Yapılandırma dosyası okunamadı: {e}")
                return ApplyResult(False, f"Yapılandırma dosyası okunamadı: {e}")
        else:
            # Mevcut config yoksa varsayılan sunucuları test et
            if progress:
                progress("Mevcut yapılandırma yok, varsayılan sunucular test ediliyor...")
            default_servers = ["0.tr.pool.ntp.org", "1.tr.pool.ntp.org", "time.cloudflare.com"]
            if progress:
                progress(f"📋 Test edilecek sunucular: {', '.join(default_servers)}")
            return self._test_ntp_servers(default_servers, progress)

    def _test_ntp_servers(self, servers: list[str], progress=None) -> ApplyResult:
        """NTP sunucularını UDP 123 portunda test eder."""
        results = []
        successful_count = 0
        total_servers = len(servers)

        if progress:
            progress(f"\n🔍 {total_servers} sunucu test ediliyor...")
            progress("")

        for i, server in enumerate(servers, 1):
            if progress:
                progress(f"[{i}/{total_servers}] {server} test ediliyor...")

            try:
                # NTP portu UDP 123
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(5.0)  # 5 saniye timeout

                if progress:
                    progress(f"  → UDP 123 portuna bağlanıyor...")

                # Basit NTP paket gönder (48 byte, ilk byte 0x1b)
                ntp_packet = b'\x1b' + b'\x00' * 47
                sock.sendto(ntp_packet, (server, 123))

                if progress:
                    progress(f"  → NTP paketi gönderildi, yanıt bekleniyor...")

                # Cevap bekle
                response, addr = sock.recvfrom(1024)
                sock.close()

                if len(response) >= 48:
                    result_msg = f"✓ {server} — Erişilebilir ve yanıt veriyor"
                    if progress:
                        progress(f"  ✅ Başarılı! NTP yanıtı alındı ({len(response)} byte)")
                    successful_count += 1
                else:
                    result_msg = f"⚠ {server} — Yanıt aldı ama NTP formatı doğru değil"
                    if progress:
                        progress(f"  ⚠️ Yanıt alındı ama NTP formatı hatalı")

                results.append(result_msg)

            except socket.timeout:
                result_msg = f"✗ {server} — Zaman aşımı (5 saniye)"
                results.append(result_msg)
                if progress:
                    progress(f"  ❌ Zaman aşımı! 5 saniye içinde yanıt alınamadı")

            except socket.gaierror:
                result_msg = f"✗ {server} — DNS çözümlenemedi"
                results.append(result_msg)
                if progress:
                    progress(f"  ❌ DNS hatası! Sunucu adı çözümlenemedi")

            except Exception as e:
                result_msg = f"✗ {server} — Bağlantı hatası: {str(e)[:50]}"
                results.append(result_msg)
                if progress:
                    progress(f"  ❌ Bağlantı hatası: {str(e)[:50]}")

            if progress:
                progress("")

        if progress:
            progress("🏁 Test tamamlandı!")
            progress(f"📊 Sonuç: {successful_count}/{total_servers} sunucu başarılı")
            progress("")

        summary = f"{successful_count}/{total_servers} NTP sunucusu başarılı"

        if successful_count == 0:
            return ApplyResult(
                False,
                "Hiçbir NTP sunucusuna ulaşılamadı!",
                details="\n".join(results)
            )
        elif successful_count < total_servers:
            return ApplyResult(
                True,
                f"Kısmi başarı: {summary}",
                details="\n".join(results) + f"\n\n⚠ {total_servers - successful_count} sunucu erişilemez durumda."
            )
        else:
            return ApplyResult(
                True,
                f"Mükemmel: {summary}",
                details="\n".join(results) + "\n\n✓ Tüm NTP sunucuları çalışıyor."
            )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        try:
            TIMESYNCD_CONF.unlink(missing_ok=True)
        except OSError:
            pass
        run_cmd(["systemctl", "restart", "systemd-timesyncd"])
        return ApplyResult(True, "TiHA özel NTP yapılandırması kaldırıldı.")
