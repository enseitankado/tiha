"""İmaj metadata damgası.

Sanitize öncesinde (m10) çağrılır; ``/etc/tiha-image-info.json`` dosyasına
imajın hangi tarihte, hangi TiHA sürümüyle, hangi adımlar uygulanarak
hazırlandığını yazar. Saha tarafında "bu tahta hangi imajdan, ne zaman?"
sorusu bu tek dosyadan cevaplanır.

Sanitize ``/etc`` altını silmediği için dosya imaj boyunca korunur.

Dosya yalnız root tarafından okunabilir (0600): hazırlayan kişi, kaynak
tahtanın adı ve uygulanan güvenlik adımlarının listesi yetkisiz bir
kullanıcıya bilgi verir. Dosya hiçbir an daha geniş izinle var olmaz —
geçici dosya 0600 ile açılıp yerine taşınır — ve TiHA her başladığında
var olan dosyanın izni denetlenir (eski sürümler 0644 yazıyordu).
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
IMAGE_INFO_MODE = 0o600


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
    ``sudo cat /etc/tiha-image-info.json`` ile sürüm/akış görüntülenebilir.
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

    _write_private(
        IMAGE_INFO_FILE,
        json.dumps(info, indent=2, ensure_ascii=False) + "\n",
    )
    log.info("İmaj damgası yazıldı: %s (%d adım)", IMAGE_INFO_FILE, len(info["applied_steps"]))
    return IMAGE_INFO_FILE


def _write_private(path: Path, text: str) -> None:
    """Dosyayı yalnız root'un okuyabileceği şekilde, atomik olarak yazar.

    Geçici dosya O_EXCL ve 0600 ile açılır (umask daha geniş izin
    veremez), içerik yazılıp diske işlenir, sonra hedefin yerine taşınır.
    Böylece dosya hiçbir an başkalarına okunur hâlde bulunmaz.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, IMAGE_INFO_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, IMAGE_INFO_MODE)
        if os.geteuid() == 0:
            os.chown(tmp, 0, 0)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def protect_image_info(path: Path | None = None) -> bool:
    """Var olan imaj damgasının iznini 0600 root:root yapar.

    Eski TiHA sürümleri dosyayı 0644 yazıyordu; bu tahtalarda TiHA her
    başladığında düzeltilir. Değişiklik yaptıysa True döner.
    """
    path = path or IMAGE_INFO_FILE
    try:
        st = path.stat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.warning("İmaj damgası denetlenemedi: %s", exc)
        return False
    changed = False
    try:
        if st.st_mode & 0o777 != IMAGE_INFO_MODE:
            os.chmod(path, IMAGE_INFO_MODE)
            changed = True
        if os.geteuid() == 0 and (st.st_uid, st.st_gid) != (0, 0):
            os.chown(path, 0, 0)
            changed = True
    except OSError as exc:
        log.warning("İmaj damgasının izni düzeltilemedi: %s", exc)
        return False
    if changed:
        log.info("İmaj damgasının izni 0600 root:root yapıldı: %s", path)
    return changed


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
