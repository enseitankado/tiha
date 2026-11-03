#!/usr/bin/python3
"""TiHA — disk SMART metrikleri (node_exporter textfile toplayıcısı).

TiHA'nın "Merkezi log sunucusu" adımı metrik izlemeyi açtığında kurulur;
tiha-smartmon.timer bunu 15 dakikada bir çalıştırır. Tahtadaki diskleri
``smartctl --json`` ile okur ve sonucu node_exporter'ın textfile
dizinine yazar; sunucu disk sıcaklığını, sağlık durumunu ve aşınmayı
:9100/metrics üzerinden görür.

Metrik adları Prometheus topluluğunun smartmon.sh betiğiyle uyumludur
(``smartmon_*``), hazır Grafana panoları değiştirilmeden çalışır.
NVMe disklerin sıcaklığı da okunur. Yalnız Python standart kitaplığı ve
smartctl kullanılır.
"""

import json
import os
import subprocess
import sys
import tempfile
import time

OUT_DIR = "/var/lib/prometheus/node-exporter"
OUT_FILE = os.path.join(OUT_DIR, "smartmon.prom")
SMARTCTL = "/usr/sbin/smartctl"

# ATA öznitelikleri: SMART kimliği -> metrik adı.
ATA_ATTRS = {
    5: "reallocated_sector_ct",
    9: "power_on_hours",
    12: "power_cycle_count",
    177: "wear_leveling_count",
    187: "reported_uncorrect",
    194: "temperature_celsius",
    196: "reallocated_event_count",
    197: "current_pending_sector",
    198: "offline_uncorrectable",
    199: "udma_crc_error_count",
    231: "ssd_life_left",
    241: "total_lbas_written",
    242: "total_lbas_read",
}

# NVMe sağlık günlüğü alanları -> metrik adı.
NVME_FIELDS = {
    "percentage_used": "nvme_percentage_used",
    "available_spare": "nvme_available_spare",
    "media_errors": "nvme_media_errors",
    "unsafe_shutdowns": "nvme_unsafe_shutdowns",
    "critical_warning": "nvme_critical_warning",
}


def smartctl(*args):
    r = subprocess.run([SMARTCTL, "--json", *args], capture_output=True,
                       text=True, timeout=60)
    # smartctl'ın çıkış kodu bit maskesidir; JSON okunabiliyorsa yeterli.
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {}


def esc(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()


class Out:
    def __init__(self):
        self.metrics = {}

    def add(self, name, labels, value):
        lab = ",".join(f'{k}="{esc(v)}"' for k, v in labels.items())
        self.metrics.setdefault("smartmon_" + name, []).append(f"{{{lab}}} {value}")

    def text(self):
        lines = []
        for name, samples in self.metrics.items():
            lines.append(f"# HELP {name} SMART metric {name[len('smartmon_'):]}")
            lines.append(f"# TYPE {name} gauge")
            lines.extend(name + s for s in samples)
        return "\n".join(lines) + "\n"


def collect_device(out, dev):
    name, dtype = dev.get("name"), dev.get("type", "auto")
    if not name:
        return
    d = smartctl("-a", "-d", dtype, name)
    if not d:
        return
    labels = {"disk": name, "type": dtype}
    out.add("device_active", labels, 1)
    info = dict(labels)
    info.update({
        "model_family": d.get("model_family", ""),
        "device_model": d.get("model_name") or d.get("scsi_model_name", ""),
        "serial_number": d.get("serial_number", ""),
        "firmware_version": d.get("firmware_version") or d.get("scsi_revision", ""),
    })
    out.add("device_info", info, 1)
    support = d.get("smart_support", {})
    out.add("device_smart_available", labels, int(bool(support.get("available"))))
    out.add("device_smart_enabled", labels, int(bool(support.get("enabled"))))
    status = d.get("smart_status")
    if status is not None:
        out.add("device_smart_healthy", labels, int(bool(status.get("passed"))))
    temp = d.get("temperature", {}).get("current")
    if temp:  # sanal diskler 0 bildirir; gerçek ölçüm değil
        out.add("temperature_celsius_raw_value", dict(labels, smart_id="194"), temp)
    hours = d.get("power_on_time", {}).get("hours")
    if hours is not None:
        out.add("power_on_hours_raw_value", dict(labels, smart_id="9"), hours)
    cycles = d.get("power_cycle_count")
    if cycles is not None:
        out.add("power_cycle_count_raw_value", dict(labels, smart_id="12"), cycles)
    for attr in d.get("ata_smart_attributes", {}).get("table", []):
        metric = ATA_ATTRS.get(attr.get("id"))
        if not metric or metric in ("temperature_celsius", "power_on_hours",
                                    "power_cycle_count"):
            continue
        lab = dict(labels, smart_id=str(attr["id"]))
        out.add(metric + "_value", lab, attr.get("value", 0))
        out.add(metric + "_raw_value", lab, attr.get("raw", {}).get("value", 0))
    nvme = d.get("nvme_smart_health_information_log", {})
    for field, metric in NVME_FIELDS.items():
        if field in nvme:
            out.add(metric, labels, nvme[field])
    out.add("smartctl_run", labels, int(time.time()))


def main():
    out = Out()
    scan = smartctl("--scan")
    version = ".".join(str(v) for v in scan.get("smartctl", {}).get("version", []))
    out.add("smartctl_version", {"version": version or "unknown"}, 1)
    for dev in scan.get("devices", []):
        try:
            collect_device(out, dev)
        except (subprocess.SubprocessError, OSError, KeyError, TypeError) as exc:
            print(f"{dev.get('name')}: {exc}", file=sys.stderr)
    os.makedirs(OUT_DIR, exist_ok=True)
    # Yarım dosyayı node_exporter okumasın diye aynı dizinde yazıp taşı.
    fd, tmp = tempfile.mkstemp(prefix=".smartmon.", dir=OUT_DIR)
    with os.fdopen(fd, "w") as f:
        f.write(out.text())
    os.chmod(tmp, 0o644)
    os.replace(tmp, OUT_FILE)


if __name__ == "__main__":
    main()
