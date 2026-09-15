"""GNOME anahtarlığı yardımcıları — bayatlamış anahtarlıkların yönetimi.

Neden bu modül var?
===================

TiHA parolaları ``passwd``/``chpasswd`` ile değil, doğrudan ``/etc/shadow``
dosyasına yazar (bkz. :mod:`tiha.modules.m01_initial_passwords`). Bu tercih
PAM politikalarını ve AppArmor kısıtlamalarını atlatır; ama bir yan etkisi
vardır: ``/etc/pam.d/common-password`` zincirinin sonundaki
``pam_gnome_keyring.so`` hiç çalışmaz.

Normal bir ``passwd`` akışında o modül **eski** parolayı görür ve
kullanıcının ``~/.local/share/keyrings/login.keyring`` dosyasını yeni
parolayla yeniden şifreler. Shadow'a doğrudan yazıldığında bu adım atlanır
ve anahtarlık eski parolayla şifreli kalır. Sonuç, kullanıcının bir daha
asla geçemeyeceği bir diyalogdur:

    "Bilgisayara giriş yapmak için kullandığınız parola artık giriş
     anahtarlığınızla uyuşmuyor."

Akış şöyle kilitlenir: girişte ``pam_gnome_keyring.so`` anahtarlığı **yeni**
parolayla açmayı dener ve başarısız olur; bu başarısızlık gcr'nin yukarıdaki
uyarıyı göstermesine yol açar. Kullanıcı diyaloğa güncel parolasını yazar
ama o parola anahtarlığın içindeki **eski** parolayla karşılaştırıldığı için
reddedilir. Eski parola hatırlanmıyorsa çıkış yolu yoktur.

Eski parolayı bilmediğimiz için anahtarlığı yeniden şifrelemek mümkün
değildir. Yaptığımız şey bayat dosyayı kenara almaktır: dosya ortadan
kalkınca ``pam_gnome_keyring.so`` bir sonraki girişte yenisini **yeni**
parolayla oluşturur ve uyuşmazlık ortadan kalkar.

İmaj bağlamında ikinci bir sorun
---------------------------------

Anahtarlık dosyaları makineye özgü sırlar taşır: Chrome Safe Storage
anahtarı, uygulamaların kaydettiği ağ/hesap parolaları ve PKCS#11 sertifika
deposu. İmajla onlarca tahtaya kopyalanırlarsa hem aynı sır bütün tahtalara
dağılır, hem de her klonda parola yeniden tanımlandığı anda yukarıdaki
uyuşmazlık tekrar doğar. Bu yüzden sanitize adımı (m10) anahtarlıkların
tamamını siler; ilk girişte her tahta kendi anahtarlığını üretir.

Parolasız anahtarlıklar
-----------------------

Parolası boş olan bir anahtarlık diskte **düz metin** INI olarak tutulur
(``[keyring]`` başlığıyla başlar, ``secret=`` alanları okunabilir).
Böyle bir dosya parola değişiminden etkilenmez, bu yüzden m01 ona
dokunmaz — ama imaja sızmaması gerektiği için sanitize onu da siler.
"""

from __future__ import annotations

import os
import pwd
import shutil
from pathlib import Path

from .logger import get_logger

log = get_logger(__name__)

# Kullanıcının ev dizinine göre anahtarlık dizini.
KEYRINGS_SUBDIR = Path(".local/share/keyrings")

# PAM tarafından giriş parolasıyla otomatik açılan tek anahtarlık.
LOGIN_KEYRING = "login.keyring"

# PKCS#11 deposu (sertifika / özel anahtar). Login anahtarlığının sırrıyla
# açıldığı için parola değişiminde o da bayatlar.
USER_KEYSTORE = "user.keystore"

# Hangi anahtarlığın "varsayılan" olduğunu tutan tek satırlık işaretçi.
DEFAULT_POINTER = "default"

# Şifreli gnome-keyring dosyalarının imzaları. Parolasız (düz metin)
# anahtarlıklar bunların yerine ``[keyring]`` INI başlığıyla başlar.
_KEYRING_MAGIC = b"GnomeKeyring"
_KEYSTORE_MAGIC = b"Gnome Keyring Store"


def keyrings_dir(home: Path) -> Path:
    """Verilen ev dizinine ait anahtarlık dizini."""
    return home / KEYRINGS_SUBDIR


def _is_dir(path: Path) -> bool:
    """``Path.is_dir`` ama okunamayan yolda çökmez.

    ``/root`` gibi izin verilmeyen bir yolu stat etmek ``PermissionError``
    fırlatır. Bu modül önizleme metni üretirken bütün ev dizinlerini
    tarar; erişilemeyen bir dizin sayfayı çökertmemeli, "anahtarlık yok"
    gibi ele alınmalı.
    """
    try:
        return path.is_dir()
    except OSError:
        return False


def _is_file(path: Path) -> bool:
    """``Path.is_file`` ama okunamayan yolda çökmez (bkz. :func:`_is_dir`)."""
    try:
        return path.is_file()
    except OSError:
        return False


def home_of(username: str) -> Path | None:
    """Kullanıcının ev dizini; kullanıcı yoksa ``None``."""
    try:
        return Path(pwd.getpwnam(username).pw_dir)
    except KeyError:
        return None


def _ids_of(username: str) -> tuple[int, int] | None:
    try:
        entry = pwd.getpwnam(username)
    except KeyError:
        return None
    return entry.pw_uid, entry.pw_gid


def is_password_protected(path: Path) -> bool:
    """Dosya bir parolayla şifrelenmiş mi?

    Parolasız anahtarlıklar düz metin INI olarak tutulduğu için imzası
    tutmaz; onlar parola değişiminden etkilenmez.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(32)
    except OSError as exc:
        log.warning("Anahtarlık okunamadı %s: %s", path, exc)
        return False
    return head.startswith(_KEYRING_MAGIC) or head.startswith(_KEYSTORE_MAGIC)


def list_keyring_files(home: Path) -> list[Path]:
    """Anahtarlık dizinindeki tüm anahtarlık dosyalarını döner.

    ``default`` işaretçisi de listeye dahildir; dizin yoksa boş liste.
    """
    directory = keyrings_dir(home)
    if not _is_dir(directory):
        return []

    found: list[Path] = []
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        log.warning("Anahtarlık dizini listelenemedi %s: %s", directory, exc)
        return []

    for entry in entries:
        if entry.is_symlink() or not _is_file(entry):
            continue
        if entry.suffix == ".keyring" or entry.name in (USER_KEYSTORE, DEFAULT_POINTER):
            found.append(entry)
    return found


def stale_keyring_files(home: Path) -> list[Path]:
    """Parola değiştikten sonra artık açılamayacak dosyalar.

    ``login.keyring`` ve ``user.keystore`` doğrudan giriş parolasına
    bağlıdır. Kullanıcının kendi oluşturduğu diğer anahtarlıklar da
    pratikte giriş parolasıyla kurulduğu için şifreli olanları bayat
    kabul ederiz. Parolasız olanlara ve ``default`` işaretçisine
    burada dokunulmaz.
    """
    stale: list[Path] = []
    for path in list_keyring_files(home):
        if path.name == DEFAULT_POINTER:
            continue
        if path.name in (LOGIN_KEYRING, USER_KEYSTORE) or is_password_protected(path):
            stale.append(path)
    return stale


def _dangling_default(home: Path) -> Path | None:
    """``default`` işaretçisi var olmayan bir anahtarlığı gösteriyorsa onu döner.

    İşaretçi dosyası anahtarlığın ``.keyring`` uzantısız adını tutar.
    Gösterdiği dosya kenara alındıysa işaretçiyi bırakmak anlamsızdır.
    """
    pointer = keyrings_dir(home) / DEFAULT_POINTER
    if not _is_file(pointer):
        return None
    try:
        name = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        return pointer
    if not name:
        return pointer
    if _is_file(keyrings_dir(home) / f"{name}.keyring"):
        return None
    return pointer


def describe_keyrings(username: str) -> list[str]:
    """Önizleme metni için kullanıcının anahtarlık durumunu özetler."""
    home = home_of(username)
    if home is None:
        return []

    described: list[str] = []
    for path in list_keyring_files(home):
        if path.name == DEFAULT_POINTER:
            continue
        if is_password_protected(path):
            described.append(f"{path.name} (parola korumalı)")
        else:
            described.append(f"{path.name} (parolasız)")
    return described


def quarantine_stale_keyrings(username: str, backup_root: Path) -> list[str]:
    """Bayatlamış anahtarlıkları ``backup_root/<username>/`` altına taşır.

    Dosyalar silinmez, taşınır: modülün geri alma adımı ``/etc/shadow``'u
    eski hâline döndürdüğünde anahtarlıklar da eski parolayla yeniden
    geçerli olur, bu yüzden yerlerine konabilmeleri gerekir.

    Taşınan dosya adlarını döner.
    """
    home = home_of(username)
    if home is None or not _is_dir(keyrings_dir(home)):
        return []

    targets = stale_keyring_files(home)
    if not targets:
        return []

    dest_dir = backup_root / username
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error("Anahtarlık yedek dizini oluşturulamadı %s: %s", dest_dir, exc)
        return []

    moved: list[str] = []
    for path in targets:
        try:
            shutil.move(str(path), str(dest_dir / path.name))
        except (OSError, shutil.Error) as exc:
            log.warning("Anahtarlık kenara alınamadı %s: %s", path, exc)
            continue
        log.info("Bayat anahtarlık kenara alındı: %s -> %s", path, dest_dir)
        moved.append(path.name)

    # İşaretçi artık var olmayan bir adı gösteriyorsa onu da al; aksi
    # hâlde gnome-keyring her açılışta boşa düşen bir ada bakar.
    pointer = _dangling_default(home)
    if pointer is not None:
        try:
            shutil.move(str(pointer), str(dest_dir / pointer.name))
            moved.append(pointer.name)
        except (OSError, shutil.Error) as exc:
            log.warning("default işaretçisi kenara alınamadı: %s", exc)

    return moved


def restore_quarantined_keyrings(username: str, backup_root: Path) -> list[str]:
    """:func:`quarantine_stale_keyrings` ile alınan dosyaları yerine koyar."""
    home = home_of(username)
    src_dir = backup_root / username
    if home is None or not _is_dir(src_dir):
        return []

    dest_dir = keyrings_dir(home)
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error("Anahtarlık dizini oluşturulamadı %s: %s", dest_dir, exc)
        return []

    ids = _ids_of(username)
    restored: list[str] = []
    for path in sorted(src_dir.iterdir()):
        if not _is_file(path):
            continue
        dest = dest_dir / path.name
        try:
            shutil.move(str(path), str(dest))
        except (OSError, shutil.Error) as exc:
            log.warning("Anahtarlık geri konamadı %s: %s", path, exc)
            continue
        # Başka bir dosya sistemine taşınmışsa sahiplik root'a kaymış
        # olabilir; kullanıcı kendi anahtarlığını okuyamazsa anahtarlık
        # yine açılmaz.
        try:
            if ids is not None:
                os.chown(dest, *ids)
            os.chmod(dest, 0o644 if dest.name == DEFAULT_POINTER else 0o600)
        except OSError as exc:
            log.warning("Anahtarlık izinleri düzeltilemedi %s: %s", dest, exc)
        restored.append(path.name)

    if ids is not None:
        try:
            os.chown(dest_dir, *ids)
        except OSError as exc:
            log.warning("Anahtarlık dizini sahiplenemedi %s: %s", dest_dir, exc)

    return restored


def purge_keyrings(home: Path) -> int:
    """İmaj öncesi: ev dizinindeki tüm anahtarlık dosyalarını siler.

    Silinen anahtarlık kullanıcının ilk girişinde otomatik yeniden
    oluşturulur, bu yüzden klon tahtada eksik bir şey olmaz. Buradaki
    amaç hem makineye özgü sırların imaja gömülmesini önlemek, hem de
    klonda parola tanımlandığı anda doğacak "anahtarlığınızla uyuşmuyor"
    hatasının önünü kesmektir.

    Silinen dosya sayısını döner.
    """
    removed = 0
    for path in list_keyring_files(home):
        try:
            path.unlink()
        except OSError as exc:
            log.warning("Anahtarlık silinemedi %s: %s", path, exc)
            continue
        removed += 1
    return removed
