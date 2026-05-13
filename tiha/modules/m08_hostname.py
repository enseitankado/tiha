"""Modül 8 — Dinamik benzersiz bilgisayar adı (hostname) stratejisi.

**Ne yapar?**
Ağa dağıtılan imajlı tahtaların aynı hostname ile çakışmaması için dinamik
bir hostname stratejisi kurar:

1. Şimdiki hostname'i seçilen şablonla (ör. ``etap-image``) değiştirir
   ve ``/etc/hosts`` dosyasındaki ``127.0.1.1`` satırını bu yeni
   hostname ile eşitler. *Hosts dosyası güncellenmediği takdirde her*
   ``sudo`` *çağrısı hostname'i çözemeyip ~10 saniye timeout'a takılır;*
   *uygulama açılışı sürünür.*
2. Her açılışta çalışan bir ``systemd`` servisi kurar. Bu servis kablolu
   MAC adresinin son 6 karakterinden benzersiz bir hostname üretir
   (ör. ``etap-1a2b3c``) ve ``/etc/hosts``'u da günceller.
   Hostname zaten doğruysa değişiklik yapmaz.

**Neden gerekir?**
Aynı imajdan çıkan binlerce tahta ağa aynı isimle girerse DHCP/DNS,
yönetim araçları (Ahenk vs.) ve merkezi log hizmeti açısından karışıklık
doğar. Dinamik yaklaşım ağ kartı değişse bile hostname'in güncellenmesini
sağlar.

**Geri al.** Dinamik hostname servisi + script kaldırılır; hostname ve
``/etc/hosts`` uygula adımından önceki hâline geri yüklenir (yedekten).
"""

from __future__ import annotations

import re
from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.utils import backup_file, restore_file, run_cmd

log = get_logger(__name__)

HOSTS_FILE = Path("/etc/hosts")
FIRST_BOOT_SCRIPT = Path("/usr/local/sbin/tiha-hostname.sh")
FIRST_BOOT_SERVICE = Path("/etc/systemd/system/tiha-hostname.service")


def _current_hostname() -> str:
    result = run_cmd(["hostname"])
    return result.stdout.strip()


def _sync_hosts_file(hosts_path: Path, new_name: str) -> None:
    """``/etc/hosts``'taki 127.0.1.1 satırını ``new_name`` olacak şekilde
    yeniden yazar. Satır yoksa 127.0.0.1'in hemen altına ekler.

    Bu işlem atomiktir (geçici dosyaya yazıp yeniden adlandırma), böylece
    yarıda kalmış bir yazma hosts dosyasını bozmaz.
    """
    if not hosts_path.exists():
        hosts_path.write_text(
            f"127.0.0.1\tlocalhost\n127.0.1.1\t{new_name}\n",
            encoding="utf-8",
        )
        return

    lines = hosts_path.read_text(encoding="utf-8").splitlines()
    new_lines: list[str] = []
    found_127_0_1_1 = False
    for line in lines:
        if re.match(r"^\s*127\.0\.1\.1\s+", line):
            new_lines.append(f"127.0.1.1\t{new_name}")
            found_127_0_1_1 = True
        else:
            new_lines.append(line)

    if not found_127_0_1_1:
        # 127.0.0.1 satırının hemen ardına yerleştir, yoksa en başa
        inserted = False
        result = []
        for line in new_lines:
            result.append(line)
            if not inserted and re.match(r"^\s*127\.0\.0\.1\s+", line):
                result.append(f"127.0.1.1\t{new_name}")
                inserted = True
        if not inserted:
            result.insert(0, f"127.0.1.1\t{new_name}")
        new_lines = result

    tmp = hosts_path.with_suffix(".tiha-tmp")
    tmp.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    tmp.replace(hosts_path)


def _render_script(prefix: str, template: str) -> str:
    """Her açılışta çalışan script: hostname'i MAC'ten üretip ``/etc/hosts``'u da
    eşitler. Aksi hâlde sudo her çağrıda timeout'a takılır."""
    return f"""#!/bin/bash
# TiHA — her açılışta benzersiz hostname üretir ve /etc/hosts'u eşitler.
# /etc/hosts senkronu kritik: aksi hâlde sudo ~10 sn beklemeye takılır.
set -euo pipefail

current=$(hostname)
template="{template}"

# Her açılışta MAC'ten hostname üret (template hostname'i kontrol etmeden)
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

# Hostname zaten doğruysa değiştirme
if [[ "$current" != "$new" ]]; then
    hostnamectl set-hostname "$new"

    # /etc/hosts içindeki 127.0.1.1 satırını yeni isme eşitle.
    if grep -qE '^[[:space:]]*127\\.0\\.1\\.1[[:space:]]+' /etc/hosts; then
        sed -i -E "s|^[[:space:]]*127\\.0\\.1\\.1[[:space:]]+.*|127.0.1.1\\t$new|" /etc/hosts
    else
        printf '127.0.1.1\\t%s\\n' "$new" >> /etc/hosts
    fi

    logger -t tiha-hostname "hostname '$current' -> '$new' (hosts güncellendi)"
else
    logger -t tiha-hostname "hostname '$current' zaten doğru (değişiklik yok)"
fi
"""


SERVICE_CONTENT = f"""[Unit]
Description=TiHA — Her açılışta benzersiz hostname ata
After=network.target

[Service]
Type=oneshot
ExecStart={FIRST_BOOT_SCRIPT}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""


class HostnameModule(Module):
    id = "m08_hostname"
    title = "Dinamik hostname stratejisi"
    sidebar_title = "Dinamik hostname"
    apply_hint = (
        "İmaj hostname'i uygulanır; her açılışta dinamik hostname servisi kurulur."
    )
    rationale = (
        "Her tahtaya ağda benzersiz bir isim (hostname) lâzım. Aynı imajdan "
        "çıkan onlarca tahta aynı isimle ağa katılırsa DHCP/DNS, merkezi "
        "log sunucusu ve Ahenk/yönetim araçları karışır; kim kimdir ayırt "
        "edilemez. Bu adım dinamik hostname stratejisi kurar:\n\n"
        "1) İMAJ AŞAMASINDA: hostname geçici olarak 'etap-image' (ya da "
        "sizin seçtiğiniz şablon) yapılır; /etc/hosts da eşzamanlı "
        "güncellenir (aksi hâlde 'sudo' her çağrıda ~10 sn DNS timeout'una "
        "takılır, sistem sürünür).\n\n"
        "2) HER AÇILIŞTA: systemd servisi çalışır ve kablolu NIC'in MAC "
        "adresinin son 6 hanesinden üreterek 'etap-ab12cd' gibi tahtaya "
        "özgü bir hostname atar, /etc/hosts'u da yeni isme eşitler. "
        "Hostname zaten doğruysa değişiklik yapmaz, böylece ağ adaptörü "
        "değişse bile hostname dinamik olarak güncellenir."
    )

    def preview(self) -> str:
        current = _current_hostname()
        service_exists = FIRST_BOOT_SERVICE.exists()
        service_enabled = False
        if service_exists:
            result = run_cmd(["systemctl", "is-enabled", FIRST_BOOT_SERVICE.name])
            service_enabled = result.ok and "enabled" in result.stdout

        lines = [
            f"Mevcut hostname          : {current}",
            f"Dinamik hostname servisi : {'kurulu' if service_exists else 'yok'}",
            f"Servis durumu            : {'aktif' if service_enabled else 'devre dışı'}",
            "",
            "Bu adım uygulandığında:",
            "  1) Hostname 'etap-image' (ya da sizin girdiğiniz şablon) olur.",
            "  2) /etc/hosts içindeki 127.0.1.1 satırı yeni isme eşitlenir.",
            "  3) tiha-hostname.service kurulur ve etkinleştirilir.",
            "",
            "Her açılışta tahta:",
            "  • MAC adresini okur (kablolu NIC'ten)",
            "  • 'etap-XXXXXX' hostname'i üretir (MAC'in son 6 hanesi)",
            "  • hostname ve /etc/hosts güncellenir",
            "  • Zaten doğruysa değişiklik yapmaz",
            "",
            "Avantajlar:",
            "  • Ağ kartı değişse bile hostname dinamik güncellenir",
            "  • Her açılışta MAC'e göre benzersizlik garantilenir",
            "  • Hostname çakışması riski minimize edilir",
        ]
        return "\n".join(lines)

    def apply(self, params=None, progress=None) -> ApplyResult:
        params = params or {}
        template = (params.get("template") or "etap-image").strip()
        prefix = (params.get("prefix") or "etap").strip()

        # Undo için önceki durumu yedekle
        previous_hostname = _current_hostname()
        state = self.ensure_state_dir()
        backup_file(HOSTS_FILE, state)

        # 1) Hostname
        hn = run_cmd(["hostnamectl", "set-hostname", template])
        if not hn.ok:
            return ApplyResult(False, "hostnamectl set-hostname başarısız.",
                               details=hn.stderr,
                               data={"previous_hostname": previous_hostname})

        # 2) /etc/hosts eşitleme — KRİTİK
        try:
            _sync_hosts_file(HOSTS_FILE, template)
        except OSError as exc:
            return ApplyResult(False, f"/etc/hosts güncellenemedi: {exc}",
                               data={"previous_hostname": previous_hostname})

        # 3) First-boot servisi
        FIRST_BOOT_SCRIPT.write_text(_render_script(prefix, template), encoding="utf-8")
        FIRST_BOOT_SCRIPT.chmod(0o755)
        FIRST_BOOT_SERVICE.write_text(SERVICE_CONTENT, encoding="utf-8")
        run_cmd(["systemctl", "daemon-reload"])
        enable = run_cmd(["systemctl", "enable", FIRST_BOOT_SERVICE.name])
        if not enable.ok:
            return ApplyResult(False, "First-boot servisi etkinleştirilemedi.",
                               details=enable.stderr,
                               data={"previous_hostname": previous_hostname})

        return ApplyResult(
            True,
            f"Hostname '{template}' olarak ayarlandı; her açılışta '{prefix}-XXXXXX' olacak.",
            details=(
                f"/etc/hosts içindeki 127.0.1.1 satırı güncellendi.\n"
                f"Script: {FIRST_BOOT_SCRIPT}\n"
                f"Servis: {FIRST_BOOT_SERVICE}\n"
                f"Her açılışta MAC adresinden dinamik hostname üretilecek."
            ),
            data={"previous_hostname": previous_hostname},
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        data = data or {}
        previous = data.get("previous_hostname", "")

        # 1) Dinamik hostname servisi + script kaldırma
        run_cmd(["systemctl", "disable", "--now", FIRST_BOOT_SERVICE.name])
        for path in (FIRST_BOOT_SERVICE, FIRST_BOOT_SCRIPT):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        run_cmd(["systemctl", "daemon-reload"])

        # 2) Hostname'i eski hâle döndür
        if previous:
            run_cmd(["hostnamectl", "set-hostname", previous])

        # 3) /etc/hosts'u yedekten geri yükle
        state = self.state_dir
        backup = state / HOSTS_FILE.name
        if backup.exists():
            try:
                restore_file(backup, HOSTS_FILE)
            except OSError as exc:
                log.warning("hosts yedeği geri yüklenemedi: %s", exc)
                # Yine de mevcut hostname'e göre düzelt ki sistem takılmasın
                if previous:
                    _sync_hosts_file(HOSTS_FILE, previous)
        elif previous:
            _sync_hosts_file(HOSTS_FILE, previous)

        msg = (
            f"Dinamik hostname servisi kaldırıldı, hostname '{previous}' olarak geri alındı "
            "ve /etc/hosts yedekten yüklendi."
            if previous
            else "Dinamik hostname servisi kaldırıldı (önceki hostname kaydı bulunamadı)."
        )
        return ApplyResult(True, msg)
