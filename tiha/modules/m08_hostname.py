"""Modül 8 — Benzersiz bilgisayar adı (hostname) stratejisi.

**Ne yapar?**
Ağa dağıtılan imajlı tahtaların aynı hostname ile çakışmaması için iki
katmanlı bir strateji kurar:

1. Şimdiki hostname'i seçilen şablonla (ör. ``etap-image``) değiştirir.
2. Her yeni tahtada ilk açılışta bu şablon hostname'i yakalayıp MAC
   adresinin son 6 karakterinden benzersiz bir hostname üreten (ör.
   ``etap-1a2b3c``) bir ``systemd`` ``oneshot`` servisi kurar; servis
   kendini çalıştırdıktan sonra tekrar çalışmasın diye işaret dosyası
   bırakır.

**Neden gerekir?**
Aynı imajdan çıkan binlerce tahta ağa aynı isimle girerse DHCP/DNS,
yönetim araçları (Ahenk vs.) ve merkezi log hizmeti açısından karışıklık
doğar.

**Geri al.** Oneshot servis + script kaldırılır, hostname değiştirilmez
(geri alma sırasında yeni hostname mevcut).
"""

from __future__ import annotations

from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.utils import run_cmd

log = get_logger(__name__)

FIRST_BOOT_SCRIPT = Path("/usr/local/sbin/tiha-first-boot-hostname.sh")
FIRST_BOOT_SERVICE = Path("/etc/systemd/system/tiha-first-boot-hostname.service")
SENTINEL = Path("/var/lib/tiha/first-boot-hostname.done")


def _render_script(prefix: str, template: str) -> str:
    return f"""#!/bin/bash
# TiHA — ilk açılışta benzersiz hostname üretir.
set -euo pipefail
sentinel="{SENTINEL}"
[[ -f "$sentinel" ]] && exit 0

current=$(hostname)
template="{template}"
if [[ "$current" == "$template" ]]; then
    # Kablolu arayüzün MAC'inin son 6 hex karakterinden son-ek
    mac=""
    for nif in /sys/class/net/*; do
        name=$(basename "$nif")
        [[ "$name" == "lo" ]] && continue
        if [[ -f "$nif/address" && ! -e "$nif/wireless" ]]; then
            mac=$(cat "$nif/address" | tr -d ':')
            break
        fi
    done
    suffix="${{mac: -6}}"
    if [[ -z "$suffix" ]]; then
        suffix=$(tr -dc 'a-f0-9' </dev/urandom | head -c 6)
    fi
    new="{prefix}-$suffix"
    hostnamectl set-hostname "$new"
    sed -i "s/\\b$current\\b/$new/g" /etc/hosts || true
    logger -t tiha-first-boot "hostname '$current' -> '$new'"
fi
mkdir -p "$(dirname "$sentinel")"
touch "$sentinel"
"""


SERVICE_CONTENT = f"""[Unit]
Description=TiHA — İlk açılışta benzersiz hostname ata
After=network.target
ConditionPathExists=!{SENTINEL}

[Service]
Type=oneshot
ExecStart={FIRST_BOOT_SCRIPT}

[Install]
WantedBy=multi-user.target
"""


class HostnameModule(Module):
    id = "m08_hostname"
    title = "Benzersiz hostname stratejisi"
    rationale = (
        "Aynı imajdan çıkan tahtalar aynı hostname'e sahip olursa ağda isim "
        "çakışması yaşanır. Bu adım, imaj hostname'ini şablon bir değerle "
        "(varsayılan 'etap-image') sabitler ve her tahtanın ilk açılışında "
        "kablolu MAC adresinden otomatik benzersiz isim üreten bir servis kurar."
    )

    def preview(self) -> str:
        return "İmaj hostname'i şablona alınır; first-boot servisi kurulur."

    def apply(self, params: dict | None = None) -> ApplyResult:
        params = params or {}
        template = (params.get("template") or "etap-image").strip()
        prefix = (params.get("prefix") or "etap").strip()

        # İmaj hostname'i
        run_cmd(["hostnamectl", "set-hostname", template])

        FIRST_BOOT_SCRIPT.write_text(_render_script(prefix, template), encoding="utf-8")
        FIRST_BOOT_SCRIPT.chmod(0o755)
        FIRST_BOOT_SERVICE.write_text(SERVICE_CONTENT, encoding="utf-8")

        run_cmd(["systemctl", "daemon-reload"])
        enable = run_cmd(["systemctl", "enable", FIRST_BOOT_SERVICE.name])
        if not enable.ok:
            return ApplyResult(False, "First-boot servisi etkinleştirilemedi.",
                               details=enable.stderr)

        return ApplyResult(
            True,
            f"İmaj hostname'i '{template}'; ilk açılışta '{prefix}-XXXXXX' olarak düzelecek.",
            details=(
                f"Script: {FIRST_BOOT_SCRIPT}\nServis: {FIRST_BOOT_SERVICE}\n"
                "Sanitize adımı çalıştırılmazsa bile bu servis ilk açılışta bir defa çalışır."
            ),
        )

    def undo(self, data: dict) -> ApplyResult:
        run_cmd(["systemctl", "disable", "--now", FIRST_BOOT_SERVICE.name])
        for path in (FIRST_BOOT_SERVICE, FIRST_BOOT_SCRIPT, SENTINEL):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        run_cmd(["systemctl", "daemon-reload"])
        return ApplyResult(True, "First-boot hostname servisi kaldırıldı (mevcut hostname korunur).")
