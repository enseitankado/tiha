"""Donanım ve dağıtım tespiti.

Sihirbazın karşılama ekranında gösterilecek "bu tahta hangi donanım ve
hangi dağıtım" bilgilerini toplar. Hassas/tekil kimlik toplamaz; yalnızca
üst düzey, paylaşılabilir bilgileri çeker.

Faz tespiti nominaldir; SMBIOS'taki model adı MEB/ETA belgelerindeki
"FATİH Faz 1 / 2 / 3" kategorilerine eşlenmeye çalışılır.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import t
from .logger import get_logger
from .utils import run_cmd

log = get_logger(__name__)

DMI = Path("/sys/class/dmi/id")
OS_RELEASE = Path("/etc/os-release")


@dataclass
class BoardInfo:
    """Kullanıcıya gösterilecek özet bilgi."""

    # Üretici (ör. "Vestel", "Arçelik", "LG")
    brand: str = field(default_factory=lambda: t("core.board.unknown"))
    model: str = field(default_factory=lambda: t("core.board.unknown"))
    # Faz 1 / 2 / 3 tanımı
    phase: str = field(default_factory=lambda: t("core.board.phase_unknown"))
    bios_version: str = ""
    distro_pretty: str = ""
    kernel: str = ""
    arch: str = ""
    is_vm: bool = False
    vm_type: str = ""

    def as_rows(self) -> list[tuple[str, str]]:
        """UI için hazır etiket+değer çiftleri döndürür."""
        rows = [
            (t("core.board.row_brand"), self.brand),
            (t("core.board.row_model"), self.model),
            (t("core.board.row_phase"), self.phase),
            (t("core.board.row_bios"), self.bios_version or "—"),
            (t("core.board.row_os"), self.distro_pretty or "—"),
            (t("core.board.row_kernel"), self.kernel or "—"),
            (t("core.board.row_arch"), self.arch or "—"),
        ]
        if self.is_vm:
            rows.append((
                t("core.board.row_runtime"),
                t("core.board.virtual_machine", vm_type=self.vm_type),
            ))
        return rows


# --- İç yardımcılar ---------------------------------------------------------

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""


def _parse_os_release() -> dict[str, str]:
    text = _read(OS_RELEASE)
    data: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        data[key.strip()] = value.strip().strip('"')
    return data


def _detect_phase(brand: str, model: str, bios: str) -> str:
    """Marka/model ipuçlarından Faz 1 / 2 / 3 tahmini.

    Kesin bir kural yoktur; bu bir iyi niyet eşleştirmesidir. Bulunamazsa
    "Tespit edilemedi" döner. Listeyi ilerde genişletmek için ipucu tablosu
    bu fonksiyondadır.
    """
    blob = " ".join((brand, model, bios)).lower()
    # Faz 3 örüntüleri (daha yeni tahtalar, tipik olarak 4K ve Android tabanlı,
    # ancak Pardus uyarlamaları da var)
    if re.search(r"\bfaz\s*3\b|\bphase\s*3\b|\bf3\b", blob):
        return t("core.board.phase_3")
    if re.search(r"\bfaz\s*2\b|\bphase\s*2\b|\bf2\b", blob):
        return t("core.board.phase_2")
    if re.search(r"\bfaz\s*1\b|\bphase\s*1\b|\bf1\b", blob):
        return t("core.board.phase_1")
    # Marka bazlı kaba kestirim: Vestel Faz 2/3 tipiktir.
    if "vestel" in blob:
        return t("core.board.phase_2_guess")
    if "arçelik" in blob or "arcelik" in blob or "grundig" in blob:
        return t("core.board.phase_3_guess")
    return t("core.board.phase_unknown")


def _detect_vm() -> tuple[bool, str]:
    result = run_cmd(["systemd-detect-virt"], check=False)
    kind = result.stdout.strip()
    if result.ok and kind and kind != "none":
        return True, kind
    return False, ""


# --- Genel API --------------------------------------------------------------

def detect() -> BoardInfo:
    """Tahtanın mevcut özetini üretir.

    Tüm okumalar salt-okuma niteliktedir; yan etki yaratmaz.
    """
    brand = _read(DMI / "sys_vendor") or _read(DMI / "board_vendor") or t("core.board.unknown")
    model = _read(DMI / "product_name") or _read(DMI / "board_name") or t("core.board.unknown")
    bios = _read(DMI / "bios_version")
    phase = _detect_phase(brand, model, bios)

    os_data = _parse_os_release()
    distro_pretty = os_data.get("PRETTY_NAME", "")

    uname = run_cmd(["uname", "-rm"], check=False).stdout.strip().split()
    kernel = uname[0] if uname else ""
    arch = uname[1] if len(uname) > 1 else ""

    is_vm, vm_type = _detect_vm()

    info = BoardInfo(
        brand=brand,
        model=model,
        phase=phase,
        bios_version=bios,
        distro_pretty=distro_pretty,
        kernel=kernel,
        arch=arch,
        is_vm=is_vm,
        vm_type=vm_type,
    )
    log.info("Tahta özeti: %s", info)
    return info
