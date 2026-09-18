"""TiHA'nın ürettiği bilgi dosyalarını yalnız etapadmin'e açık tutar.

TiHA root olarak çalışır; yazdığı her dosya varsayılan umask ile başkalarına
okunur (0644) çıkıyordu: günlükler, PIN kâğıtları, kaydedilen raporlar ve
presetler. Bu dosyalarda öğretmen adları, PIN anahtarları ve tahtanın
güvenlik yapılandırması bulunur. Kural:

* **Kullanıcının seçtiği yere kaydedilen dosyalar** (rapor, preset, PIN
  kâğıdı "Kaydet"): sahibi etapadmin, 0600 — etapadmin dosyasını okur,
  taşır, siler; başka hesap göremez.
* **Sistem dizinlerindeki bilgi dosyaları** (``/var/log/tiha``,
  ``/var/lib/tiha`` altındaki PIN kâğıtları): sahibi root, grubu
  etapadmin; dosya 0640, dizin 0750. etapadmin okur ama değiştiremez
  (root'un yazdığı günlüğü tahrif edemez); başka hesap dizine giremez.

etapadmin hesabı yoksa dosyalar yalnız root'a açık kalır: sızdırmamak,
açılabilir olmaktan önemli.
"""

from __future__ import annotations

import os
import pwd
from pathlib import Path

OWNER = "etapadmin"
USER_FILE_MODE = 0o600
SYSTEM_FILE_MODE = 0o640
SYSTEM_DIR_MODE = 0o750


def owner_ids() -> tuple[int, int] | None:
    """etapadmin'in (uid, gid) çifti; hesap yoksa None."""
    try:
        entry = pwd.getpwnam(OWNER)
    except KeyError:
        return None
    return entry.pw_uid, entry.pw_gid


def _is_root() -> bool:
    return os.geteuid() == 0


def protect_system_path(path: Path) -> bool:
    """Sistem dizinindeki bilgi dosyasını/dizinini root:etapadmin yapar.

    Dosya 0640, dizin 0750. Sembolik bağlar izlenmez. Başarılıysa True.
    """
    try:
        if path.is_symlink() or not path.exists():
            return False
        ids = owner_ids()
        gid = ids[1] if ids else 0
        mode = SYSTEM_DIR_MODE if path.is_dir() else SYSTEM_FILE_MODE
        if not ids:
            mode &= 0o700  # etapadmin yoksa grup da okuyamasın
        os.chmod(path, mode)
        if _is_root():
            os.chown(path, 0, gid, follow_symlinks=False)
        return True
    except OSError:
        return False


def protect_system_tree(root: Path) -> int:
    """Dizini ve içindeki her şeyi ``protect_system_path`` ile kilitler."""
    count = 0
    if protect_system_path(root):
        count += 1
    try:
        entries = list(root.rglob("*"))
    except OSError:
        return count
    for entry in entries:
        if protect_system_path(entry):
            count += 1
    return count


def write_user_file(path: Path, text: str) -> None:
    """Kullanıcının seçtiği yere dosyayı etapadmin'e ait, 0600 yazar.

    Geçici dosya O_EXCL ve 0600 ile açılır, sahibi etapadmin yapılır,
    sonra hedefin yerine taşınır; dosya hiçbir an başkalarına açık olmaz.
    """
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, USER_FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, USER_FILE_MODE)
        ids = owner_ids()
        if ids and _is_root():
            os.chown(tmp, *ids)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
