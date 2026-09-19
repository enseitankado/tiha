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

from ..core.i18n import t
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
    title = t("m07.title")
    sidebar_title = t("m07.sidebar_title")
    rationale_inline = True
    apply_hint = t("m07.apply_hint")
    rationale = t("m07.rationale")

    def preview(self) -> str:
        if TIMESYNCD_CONF.exists():
            return t(
                "m07.preview.current",
                path=TIMESYNCD_CONF,
                content=TIMESYNCD_CONF.read_text(encoding='utf-8'),
            )
        # Dosya yoksa kullanıcıyı şaşırtacak "(yok)" yerine ne yapılacağı anlatılır.
        return t("m07.preview.none", path=TIMESYNCD_CONF)

    def apply(self, params=None, progress=None) -> ApplyResult:
        params = params or {}
        ntp = (params.get("ntp_servers") or "").strip()
        fallback = (params.get("ntp_fallback") or "").strip()
        tz = (params.get("timezone") or "Europe/Istanbul").strip()

        if not ntp and not fallback:
            return ApplyResult(False, t("m07.apply.no_server"))

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
            return ApplyResult(False, t("m07.apply.write_failed", error=exc))

        run_cmd(["timedatectl", "set-ntp", "true"])
        restart = run_cmd(["systemctl", "restart", "systemd-timesyncd"])
        if not restart.ok:
            return ApplyResult(False, t("m07.apply.restart_failed"),
                               details=restart.stderr)

        # Mevcut durumu göster
        status = run_cmd(["timedatectl"]).stdout
        return ApplyResult(
            True,
            t("m07.apply.done", tz=tz),
            details=t("m07.apply.done_details", path=TIMESYNCD_CONF, status=status),
        )

    def test_ntp_servers_action(self, progress=None) -> ApplyResult:
        """Form'daki NTP sunucularını test eder."""
        if progress:
            progress(t("m07.test.start"))

        # Mevcut yapılandırmadaki sunucuları test et
        if TIMESYNCD_CONF.exists():
            try:
                if progress:
                    progress(t("m07.test.reading"))
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
                        progress(t("m07.test.no_server_progress"))
                    return ApplyResult(False, t("m07.test.no_server"))

                if progress:
                    progress(t("m07.test.found", count=len(servers), servers=', '.join(servers)))

                return self._test_ntp_servers(servers, progress)

            except Exception as e:
                if progress:
                    progress(t("m07.test.read_failed_progress", error=e))
                return ApplyResult(False, t("m07.test.read_failed", error=e))
        else:
            # Mevcut config yoksa varsayılan sunucuları test et
            if progress:
                progress(t("m07.test.no_config"))
            default_servers = ["0.tr.pool.ntp.org", "1.tr.pool.ntp.org", "time.cloudflare.com"]
            if progress:
                progress(t("m07.test.default_list", servers=', '.join(default_servers)))
            return self._test_ntp_servers(default_servers, progress)

    def _test_ntp_servers(self, servers: list[str], progress=None) -> ApplyResult:
        """NTP sunucularını UDP 123 portunda test eder."""
        results = []
        successful_count = 0
        total_servers = len(servers)

        if progress:
            progress(t("m07.test.testing_count", count=total_servers))
            progress("")

        for i, server in enumerate(servers, 1):
            if progress:
                progress(t("m07.test.testing_server", index=i, total=total_servers, server=server))

            try:
                # NTP portu UDP 123
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(5.0)  # 5 saniye timeout

                if progress:
                    progress(t("m07.test.connecting"))

                # Basit NTP paket gönder (48 byte, ilk byte 0x1b)
                ntp_packet = b'\x1b' + b'\x00' * 47
                sock.sendto(ntp_packet, (server, 123))

                if progress:
                    progress(t("m07.test.sent"))

                # Cevap bekle
                response, addr = sock.recvfrom(1024)
                sock.close()

                if len(response) >= 48:
                    result_msg = t("m07.test.ok_result", server=server)
                    if progress:
                        progress(t("m07.test.ok_progress", size=len(response)))
                    successful_count += 1
                else:
                    result_msg = t("m07.test.bad_format_result", server=server)
                    if progress:
                        progress(t("m07.test.bad_format_progress"))

                results.append(result_msg)

            except socket.timeout:
                result_msg = t("m07.test.timeout_result", server=server)
                results.append(result_msg)
                if progress:
                    progress(t("m07.test.timeout_progress"))

            except socket.gaierror:
                result_msg = t("m07.test.dns_result", server=server)
                results.append(result_msg)
                if progress:
                    progress(t("m07.test.dns_progress"))

            except Exception as e:
                result_msg = t("m07.test.error_result", server=server, error=str(e)[:50])
                results.append(result_msg)
                if progress:
                    progress(t("m07.test.error_progress", error=str(e)[:50]))

            if progress:
                progress("")

        if progress:
            progress(t("m07.test.finished"))
            progress(t("m07.test.result_line", ok=successful_count, total=total_servers))
            progress("")

        summary = t("m07.test.summary", ok=successful_count, total=total_servers)

        if successful_count == 0:
            return ApplyResult(
                False,
                t("m07.test.none_reached"),
                details="\n".join(results)
            )
        elif successful_count < total_servers:
            return ApplyResult(
                True,
                t("m07.test.partial", summary=summary),
                details="\n".join(results) + t(
                    "m07.test.partial_details", count=total_servers - successful_count
                )
            )
        else:
            return ApplyResult(
                True,
                t("m07.test.all_ok", summary=summary),
                details="\n".join(results) + t("m07.test.all_ok_details")
            )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        try:
            TIMESYNCD_CONF.unlink(missing_ok=True)
        except OSError:
            pass
        run_cmd(["systemctl", "restart", "systemd-timesyncd"])
        return ApplyResult(True, t("m07.undo.done"))
