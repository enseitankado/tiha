"""Modül 0 — Donanım ön kontrol.

**Ne yapar?**
Tahtanın imaj alınmaya ve imajdan boot edilmeye uygun olup olmadığını
kontrol eder. Aşağıdakileri raporlar:

- **SMBIOS product_uuid** — boş/tümü-sıfır/tümü-FF ise ``eta-register``
  tahtayı sanal makine sanıp çalışmayı reddeder → **KRİTİK**
- **systemd-detect-virt** — Sanal makinede eta-register çalışmaz; bu
  ortamda imaj hazırlığı sorun değil ama imajı fiziksel tahtada test
  etmek gerekir → **UYARI**
- **Kablolu NIC MAC** — ``00:00:00:00:00:00`` ise çok nadir bir
  donanım sorunudur → **KRİTİK**
- **/etc/machine-id** — boşsa zaten temizdir; boş değil ise sanitize
  adımı tarafından temizlenecektir → **BİLGİ**

**Neden gerekir?**
Geliştiricilerin ``imajlamayı önermiyoruz`` tavsiyesi çoğunlukla bu
ön-kontrolle ekarte edilen edge-case'lerden kaynaklanır. Bu modül,
imaj almanın güvenli olup olmayacağını önceden söyler.

**Geri alma.** Yalnızca okuma yapar; geri alma gerekmez.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.utils import run_cmd

log = get_logger(__name__)

DMI = Path("/sys/class/dmi/id")
NET = Path("/sys/class/net")
MACHINE_ID = Path("/etc/machine-id")

UUID_ZERO_RE = re.compile(r"^[0-]+$")
UUID_F_RE = re.compile(r"^[f-]+$")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""


def _wired_mac() -> str | None:
    """İlk kablolu (ethernet) arayüzün MAC adresi."""
    if not NET.exists():
        return None
    for iface in sorted(NET.iterdir()):
        name = iface.name
        if name == "lo":
            continue
        if (iface / "wireless").exists():
            continue
        if not (iface / "device").exists():
            continue
        addr = _read(iface / "address")
        if addr:
            return addr
    return None


class PrecheckModule(Module):
    id = "m00_precheck"
    title = "Donanım ön kontrol"
    undo_supported = False
    streams_output = False
    rationale = (
        "Tahtanın imajdan dağıtılmaya uygun olup olmadığını sınar. Özellikle "
        "eta-register'ın imaj klonlarında çalışabilmesi için anakart "
        "bilgilerinin (product_uuid) dolu ve benzersiz olması gerekir. "
        "Bu adım yalnızca rapor üretir; sistemde değişiklik yapmaz."
    )

    def preview(self) -> str:
        return "Kontroller yalnızca okunur; sistemde değişiklik yapılmaz."

    def apply(self, params=None, progress=None) -> ApplyResult:
        rows: list[tuple[str, str, str]] = []  # (seviye, başlık, ayrıntı)

        # 1) product_uuid
        uuid = _read(DMI / "product_uuid").lower()
        if not uuid:
            rows.append(("KRİTİK", "SMBIOS product_uuid boş",
                         "eta-register bu tahtayı sanal makine sanar. "
                         "Anakart üreticisine başvurulması gerekebilir."))
        elif UUID_ZERO_RE.match(uuid.replace("-", "0")) or UUID_F_RE.match(uuid.replace("-", "f")):
            rows.append(("KRİTİK", f"SMBIOS product_uuid şüpheli: {uuid}",
                         "Tümü sıfır ya da tümü F. eta-register çalışmayacaktır."))
        else:
            rows.append(("TAMAM", f"SMBIOS product_uuid geçerli", f"product_uuid = {uuid}"))

        # 2) systemd-detect-virt
        virt = run_cmd(["systemd-detect-virt"]).stdout.strip()
        if virt and virt != "none":
            rows.append(("UYARI", f"Sanal makine ortamı: {virt}",
                         "Bu tahta sanal makinedir. TiHA burada çalışır ve imajı hazırlar; "
                         "fakat imajı sahaya almadan önce bir fiziksel tahtada mutlaka test edin."))
        else:
            rows.append(("TAMAM", "Gerçek donanım", "Sanal makine tespit edilmedi."))

        # 3) Kablolu MAC
        mac = _wired_mac()
        if not mac:
            rows.append(("UYARI", "Kablolu ethernet arayüzü bulunamadı",
                         "eta-register MAC tabanlı kayıt yapar; kablolu bağlantı önerilir."))
        elif mac == "00:00:00:00:00:00":
            rows.append(("KRİTİK", "MAC adresi tümüyle sıfır",
                         "Donanım sorunludur; eta-register kayıt açamaz."))
        else:
            rows.append(("TAMAM", "Kablolu MAC geçerli", f"MAC = {mac}"))

        # 4) machine-id
        mid = _read(MACHINE_ID)
        if not mid:
            rows.append(("BİLGİ", "/etc/machine-id boş", "Yeni bir machine-id ilk boot'ta üretilir."))
        else:
            rows.append(("BİLGİ", f"/etc/machine-id dolu", f"İmaj sanitize adımı bunu sıfırlayacaktır. ({mid[:12]}…)"))

        # Sonuç yorumu
        kritik = sum(1 for lvl, *_ in rows if lvl == "KRİTİK")
        uyari = sum(1 for lvl, *_ in rows if lvl == "UYARI")

        details = "\n".join(f"[{lvl}] {tit}\n    {det}" for lvl, tit, det in rows)

        if kritik:
            summary = f"{kritik} kritik sorun bulundu — imaj almayın."
            success = False
        elif uyari:
            summary = f"{uyari} uyarı var; devam edebilirsiniz ama dikkat edin."
            success = True
        else:
            summary = "Donanım imajlamaya uygun görünüyor."
            success = True

        log.info("Ön kontrol: %s", summary)
        return ApplyResult(success=success, summary=summary, details=details)

    def undo(self, data: dict) -> ApplyResult:
        return ApplyResult(True, "Bu adım yalnızca okur; geri alma gerekmez.")
