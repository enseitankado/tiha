"""Çalışılan tahtanın donanım + işletim sistemi özeti.

Amaç: TiHA'nın karşılama sayfasında görülebilecek küçük bir "bu tahta"
tablosu. Değerlerin çoğu ``/sys``, ``/proc`` ve ``/etc/os-release``
üzerinden root gerektirmeden okunur; hiçbir dış komut çağrılmaz (root
kimliği yoksa dmidecode zaten çalıştırılamazdı, ama DMI'ın sysfs
kopyaları herkese açıktır).

eta-112 aracı BIOS donanım detaylarını (üretici, model, BIOS sürümü)
sysfs DMI'dan okur; burada da aynı yaklaşım kullanılıyor.

Bütün alanlar Best-effort: bulunamayan alan boş metin döner ve UI
tarafında "?" ile gösterilir. Fonksiyon çağrısı toplam < 10 ms tutar
(hiçbir subprocess yok).
"""

from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass
from pathlib import Path

from .os_release import pretty_name

_DMI = Path("/sys/devices/virtual/dmi/id")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _dmi(name: str) -> str:
    return _read(_DMI / name)


def _cpu_model() -> str:
    """/proc/cpuinfo 'model name' — birden çok çekirdek olsa da adı aynı."""
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith("model name"):
            _, _, value = line.partition(":")
            return value.strip()
    return ""


def _cpu_cores() -> int:
    """Görünen çekirdek sayısı (HT dahil)."""
    try:
        return os.cpu_count() or 0
    except OSError:
        return 0


def _memory_bytes() -> int:
    """/proc/meminfo MemTotal (kilobyte → bayt)."""
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    m = re.search(r"^MemTotal:\s+(\d+)\s*kB", text, re.MULTILINE)
    return int(m.group(1)) * 1024 if m else 0


def _disk_bytes_primary() -> int:
    """İlk fiziksel blok cihazın toplam bayt boyutu.

    /sys/block/<name>/size 512 baytlık sektör sayısıdır. Loop/RAM disk,
    optik ortamlar dışta bırakılır; kalan en büyüğü döner.
    """
    root = Path("/sys/block")
    if not root.is_dir():
        return 0
    best = 0
    for entry in root.iterdir():
        name = entry.name
        if name.startswith(("loop", "ram", "sr", "zram", "fd")):
            continue
        # nvme0n1 / sda / mmcblk0 gibi.
        sectors = _read(entry / "size")
        try:
            size = int(sectors) * 512
        except ValueError:
            continue
        if size > best:
            best = size
    return best


def _primary_iface_and_mac() -> tuple[str, str]:
    """Varsayılan rota arayüzü ve MAC. Yoksa ('', '')."""
    try:
        text = Path("/proc/net/route").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""
    iface = ""
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "00000000":
            iface = parts[0]
            break
    if not iface:
        # /sys/class/net altında lo dışı ilk arayüzü seç.
        try:
            for entry in sorted(Path("/sys/class/net").iterdir()):
                if entry.name != "lo":
                    iface = entry.name
                    break
        except OSError:
            return "", ""
    mac = _read(Path("/sys/class/net") / iface / "address") if iface else ""
    return iface, mac


def _display_resolution() -> str:
    """Fiziksel ekran çözünürlüğü — sysfs drm fb0'dan okur.

    Xrandr yok: karşılama sayfası oturum açılmadan önce de görülebilir
    ve ayrıca çıkış subprocess yasağı var. drm framebuffer resolution
    "1920,1080" biçimindedir.
    """
    virt = Path("/sys/class/graphics/fb0/virtual_size")
    text = _read(virt)
    if text and "," in text:
        w, _, h = text.partition(",")
        w = w.strip()
        h = h.strip()
        if w.isdigit() and h.isdigit():
            return f"{w}×{h}"
    # DRM connector fallback: ilk connected connector'ın modes[0].
    drm = Path("/sys/class/drm")
    if drm.is_dir():
        for entry in sorted(drm.iterdir()):
            if not entry.is_dir():
                continue
            status = _read(entry / "status")
            if status != "connected":
                continue
            modes = _read(entry / "modes")
            if modes:
                first = modes.splitlines()[0].strip()
                # "1920x1080" — genelde ilk satır tercihli/doğal moddur.
                if "x" in first:
                    w, _, h = first.partition("x")
                    return f"{w}×{h}"
    return ""


def _kernel_release() -> str:
    try:
        return os.uname().release
    except OSError:
        return ""


def _desktop_env() -> str:
    """XDG_CURRENT_DESKTOP veya DESKTOP_SESSION — TiHA sudo ile açılınca
    bu değişkenler pkexec/sudo tarafından temizlenmiş olabilir; boş
    dönerse UI tarafında "?" gösterilir."""
    return (
        os.environ.get("XDG_CURRENT_DESKTOP")
        or os.environ.get("DESKTOP_SESSION")
        or ""
    ).strip()


def _human_bytes(value: int) -> str:
    """1 073 741 824 → '1,0 GiB' (Türkçe ondalık, ikili birim)."""
    if value <= 0:
        return ""
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    idx = 0
    v = float(value)
    while v >= 1024 and idx < len(units) - 1:
        v /= 1024
        idx += 1
    if idx == 0:
        return f"{int(v)} B"
    return f"{v:.1f} {units[idx]}".replace(".", ",")


@dataclass
class HostInfo:
    manufacturer: str
    model: str
    bios_version: str
    bios_date: str
    cpu_model: str
    cpu_cores: int
    memory_bytes: int
    disk_bytes: int
    display: str
    hostname: str
    iface: str
    mac: str
    os_name: str
    kernel: str
    desktop: str

    @property
    def memory_human(self) -> str:
        return _human_bytes(self.memory_bytes)

    @property
    def disk_human(self) -> str:
        return _human_bytes(self.disk_bytes)

    @property
    def cpu_summary(self) -> str:
        parts = [self.cpu_model] if self.cpu_model else []
        if self.cpu_cores:
            parts.append(f"({self.cpu_cores} çekirdek)")
        return " ".join(parts)

    @property
    def bios_summary(self) -> str:
        if self.bios_version and self.bios_date:
            return f"{self.bios_version} · {self.bios_date}"
        return self.bios_version or self.bios_date

    @property
    def model_summary(self) -> str:
        # "Board Name" bilgisayarın anakart modelini verir; product_name
        # ise donanım ürününü. Genelde product_name daha okunaklı.
        if self.model and self.manufacturer:
            return f"{self.manufacturer} {self.model}"
        return self.model or self.manufacturer

    @property
    def net_summary(self) -> str:
        if self.iface and self.mac:
            return f"{self.mac} ({self.iface})"
        return self.mac or self.iface


def collect() -> HostInfo:
    """Tahtanın anlık donanım+OS özetini derler."""
    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = ""
    iface, mac = _primary_iface_and_mac()
    return HostInfo(
        manufacturer=_dmi("sys_vendor") or _dmi("board_vendor"),
        model=_dmi("product_name") or _dmi("board_name"),
        bios_version=_dmi("bios_version"),
        bios_date=_dmi("bios_date"),
        cpu_model=_cpu_model(),
        cpu_cores=_cpu_cores(),
        memory_bytes=_memory_bytes(),
        disk_bytes=_disk_bytes_primary(),
        display=_display_resolution(),
        hostname=hostname,
        iface=iface,
        mac=mac,
        os_name=pretty_name(),
        kernel=_kernel_release(),
        desktop=_desktop_env(),
    )
