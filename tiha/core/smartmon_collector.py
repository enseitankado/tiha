"""Disk SMART metrikleri için node_exporter textfile toplayıcısı.

node_exporter disk sıcaklığını kendisi okuyamaz: ETAP çekirdeğinde
``drivetemp`` (SATA) ve ``NVME_HWMON`` yok. Debian'ın
``prometheus-node-exporter-collectors`` paketi bunu smartmon.sh ile çözer
ama ipmitool/openipmi gibi sunucu paketlerini önerir; TiHA o paketi
kurmaz. Bunun yerine ``data/tiha-smartmon.py`` betiği ve 15 dakikada bir
çalışan bir systemd zamanlayıcısı kurulur. Betik ``smartmon.prom``
dosyasını node_exporter'ın textfile dizinine yazar.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .logger import get_logger
from .utils import run_cmd

log = get_logger(__name__)

SOURCE = Path(__file__).resolve().parents[2] / "data" / "tiha-smartmon.py"
SCRIPT = Path("/usr/local/sbin/tiha-smartmon")
SERVICE = Path("/etc/systemd/system/tiha-smartmon.service")
TIMER = Path("/etc/systemd/system/tiha-smartmon.timer")
PROM_FILE = Path("/var/lib/prometheus/node-exporter/smartmon.prom")
# Paketteki eşdeğer toplayıcı. Elle kurulmuşsa aynı dosyaya iki yazar
# olmasın diye TiHA'nınki kurulmaz.
PACKAGE_TIMER = Path("/lib/systemd/system/prometheus-node-exporter-smartmon.timer")

_SERVICE_CONTENT = f"""\
[Unit]
Description=TiHA: disk SMART metriklerini node_exporter için topla
ConditionPathExists=/usr/sbin/smartctl

[Service]
Type=oneshot
Nice=10
ExecStart={SCRIPT}
"""

_TIMER_CONTENT = """\
[Unit]
Description=TiHA: disk SMART metriklerini 15 dakikada bir topla

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
"""


def package_collector_present() -> bool:
    return PACKAGE_TIMER.exists()


def is_installed() -> bool:
    return SCRIPT.exists() and TIMER.exists()


def install() -> bool:
    """Betiği ve zamanlayıcıyı kurar, ilk ölçümü hemen alır."""
    try:
        SCRIPT.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE, SCRIPT)
        SCRIPT.chmod(0o755)
        SERVICE.write_text(_SERVICE_CONTENT, encoding="utf-8")
        TIMER.write_text(_TIMER_CONTENT, encoding="utf-8")
        SERVICE.chmod(0o644)
        TIMER.chmod(0o644)
    except OSError as exc:
        log.error("SMART toplayıcısı yazılamadı: %s", exc)
        return False
    run_cmd(["systemctl", "daemon-reload"], check=False)
    timer = run_cmd(["systemctl", "enable", "--now", TIMER.name], check=False)
    # İlk ölçüm şimdi alınsın; imajda bayat değer kalmasın diye klon
    # açılışta (OnBootSec) kendi diskini yeniden yazar.
    run_cmd(["systemctl", "start", SERVICE.name], check=False, timeout=180)
    return timer.ok


def remove() -> bool:
    """Zamanlayıcıyı durdurur; betiği, birimleri ve metrik dosyasını siler."""
    present = any(p.exists() for p in (SCRIPT, SERVICE, TIMER))
    if not present:
        return True
    run_cmd(["systemctl", "disable", "--now", TIMER.name], check=False)
    ok = True
    for path in (TIMER, SERVICE, SCRIPT):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("SMART toplayıcısı dosyası silinemedi %s: %s", path, exc)
            ok = False
    # Paketin toplayıcısı aynı dosyayı yazıyorsa ona dokunma.
    if not package_collector_present():
        try:
            PROM_FILE.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("%s silinemedi: %s", PROM_FILE, exc)
    run_cmd(["systemctl", "daemon-reload"], check=False)
    run_cmd(["systemctl", "reset-failed", SERVICE.name], check=False)
    return ok
