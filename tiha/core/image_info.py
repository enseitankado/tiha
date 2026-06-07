"""İmaj metadata damgası.

Sanitize öncesinde (m10) çağrılır; ``/etc/tiha-image-info.json`` dosyasına
imajın hangi tarihte, hangi TiHA sürümüyle, hangi adımlar uygulanarak
hazırlandığını yazar. Saha tarafında "bu tahta hangi imajdan, ne zaman?"
sorusu bu tek dosyadan cevaplanır.

Sanitize ``/etc`` altını silmediği için dosya imaj boyunca korunur.
"""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from .logger import get_logger
from .undo import Journal

log = get_logger(__name__)

IMAGE_INFO_FILE = Path("/etc/tiha-image-info.json")


def collect_applied_steps(journal: Journal) -> list[dict]:
    """Journal'dan uygulanmış (applied/undone değil failed olmayan) adımları
    sade bir liste olarak çıkarır. Her giriş: id, title, timestamp."""
    out: list[dict] = []
    for entry in journal.all():
        if entry.status != "applied":
            continue
        out.append({
            "module_id": entry.module_id,
            "title": entry.title,
            "timestamp": entry.timestamp,
        })
    return out


def write_image_info(
    journal: Journal,
    *,
    extra: dict | None = None,
) -> Path:
    """``/etc/tiha-image-info.json`` dosyasını yazar. Üzerine yazar.

    Sahaya inecek imajın damgasıdır; herhangi bir tahtada
    ``cat /etc/tiha-image-info.json`` ile sürüm/akış görüntülenebilir.
    """
    info = {
        "tiha_version": __version__,
        "prepared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prepared_on_host": socket.gethostname(),
        "prepared_by": _invoking_user(),
        "applied_steps": collect_applied_steps(journal),
    }
    if extra:
        info.update(extra)

    IMAGE_INFO_FILE.parent.mkdir(parents=True, exist_ok=True)
    IMAGE_INFO_FILE.write_text(
        json.dumps(info, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    log.info("İmaj damgası yazıldı: %s (%d adım)", IMAGE_INFO_FILE, len(info["applied_steps"]))
    return IMAGE_INFO_FILE


def _invoking_user() -> str:
    """pkexec ile yükselmişsek orijinal kullanıcı; aksi hâlde geteuid'nin pwd kaydı."""
    for var in ("PKEXEC_UID", "SUDO_USER"):
        v = os.environ.get(var)
        if v:
            if var == "PKEXEC_UID":
                try:
                    import pwd
                    return pwd.getpwuid(int(v)).pw_name
                except (ValueError, KeyError):
                    pass
            else:
                return v
    try:
        import pwd
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        return f"uid:{os.geteuid()}"
