"""TiHA'nın ürettiği bilgi dosyalarını yalnız etapadmin'e açık tutar.

TiHA root olarak çalışır; yazdığı her dosya varsayılan umask ile başkalarına
okunur (0644) çıkıyordu: günlükler, PIN kâğıtları, kaydedilen raporlar ve
presetler. Bu dosyalarda öğretmen adları, PIN anahtarları ve tahtanın
güvenlik yapılandırması bulunur. Kural:

* **Kullanıcının seçtiği yere kaydedilen dosyalar** (rapor, preset, PIN
  kâğıdı "Kaydet"): sahibi TiHA'yı çalıştıran kullanıcı (genelde
  etapadmin), dosya 0600, açılan klasör 0700 — yalnız sahibi okur, taşır,
  siler; grup ve diğer hesaplar göremez. Kayıt pencereleri o kullanıcının
  Masaüstü'nde açılır.
* **TiHA'nın veri dizini** (``/var/lib/tiha`` ve bütün alt dizinleri:
  günlük (öğretmen adları, adım seçenekleri), eylem kaydı, yedekler, PIN
  anahtarları ve kâğıtları, araç önbellekleri): yalnız root — sahibi
  root:root, dizin 0700, dosya 0600 (çalıştırılabilir dosya 0700). Bu dizin
  imajla bütün klonlara gider; yönetici olmayan hesaplar hiçbirine
  erişemez.
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
USER_DIR_MODE = 0o700
STATE_FILE_MODE = 0o600
STATE_DIR_MODE = 0o700
SYSTEM_FILE_MODE = 0o640
SYSTEM_DIR_MODE = 0o750


def owner_ids() -> tuple[int, int] | None:
    """etapadmin'in (uid, gid) çifti; hesap yoksa None."""
    try:
        entry = pwd.getpwnam(OWNER)
    except KeyError:
        return None
    return entry.pw_uid, entry.pw_gid


def user_ids() -> tuple[int, int] | None:
    """TiHA'yı çalıştıran kullanıcının (uid, gid) çifti (sudo/pkexec
    öncesi hesap); root'sa ya da bulunamazsa etapadmin'inki."""
    try:
        from .privilege import invoking_username
        entry = pwd.getpwnam(invoking_username())
        if entry.pw_uid != 0:
            return entry.pw_uid, entry.pw_gid
    except (KeyError, ImportError):
        pass
    return owner_ids()


def user_desktop_dir() -> Path:
    """Çalıştıran kullanıcının Masaüstü klasörü (XDG ayarından; yoksa
    Masaüstü/Desktop, o da yoksa ev dizini)."""
    ids = user_ids()
    try:
        home = Path(pwd.getpwuid(ids[0]).pw_dir) if ids else Path.home()
    except KeyError:
        home = Path.home()
    try:
        for line in (home / ".config/user-dirs.dirs").read_text(encoding="utf-8").splitlines():
            if line.startswith("XDG_DESKTOP_DIR="):
                value = line.split("=", 1)[1].strip().strip('"').replace("$HOME", str(home))
                if Path(value).is_dir():
                    return Path(value)
    except OSError:
        pass
    for name in ("Masaüstü", "Desktop"):
        if (home / name).is_dir():
            return home / name
    return home


def make_user_dir(path: Path) -> Path:
    """Kullanıcının seçtiği yerde klasör açar: sahibi kullanıcı, 0700."""
    path = Path(path)
    path.mkdir(mode=USER_DIR_MODE, parents=False, exist_ok=True)
    os.chmod(path, USER_DIR_MODE)
    ids = user_ids()
    if ids and _is_root():
        os.chown(path, *ids, follow_symlinks=False)
    return path


def protect_state_tree(root: Path | None = None) -> int:
    """TiHA'nın veri dizinini (varsayılan ``/var/lib/tiha``) ve bütün alt
    dizinlerini yalnız root'a açar: root:root, dizin 0700, dosya 0600
    (çalıştırma biti olan dosya 0700). Sembolik bağlar izlenmez ve
    değiştirilmez. Kilitlenen öğe sayısını döner."""
    if root is None:
        from .paths import VAR_ROOT
        root = VAR_ROOT
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        return 0
    count = 0

    def lock(path: str, mode: int) -> None:
        nonlocal count
        try:
            if os.path.islink(path):
                return
            if _is_root():
                os.chown(path, 0, 0, follow_symlinks=False)
            os.chmod(path, mode)
            count += 1
        except OSError:
            pass

    lock(str(root), STATE_DIR_MODE)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for d in dirnames:
            lock(os.path.join(dirpath, d), STATE_DIR_MODE)
        for f in filenames:
            path = os.path.join(dirpath, f)
            try:
                executable = bool(os.lstat(path).st_mode & 0o100)
            except OSError:
                continue
            lock(path, 0o700 if executable else STATE_FILE_MODE)
    return count


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


def write_user_file(path: Path, text: str | bytes) -> None:
    """Kullanıcının seçtiği yere dosyayı çalıştıran kullanıcıya ait, 0600
    yazar (``bytes`` verilirse ikili, ör. PNG).

    Geçici dosya O_EXCL ve 0600 ile açılır, sahibi kullanıcı yapılır,
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
        binary = isinstance(text, (bytes, bytearray))
        with (os.fdopen(fd, "wb") if binary else os.fdopen(fd, "w", encoding="utf-8")) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, USER_FILE_MODE)
        ids = user_ids()
        if ids and _is_root():
            os.chown(tmp, *ids)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
