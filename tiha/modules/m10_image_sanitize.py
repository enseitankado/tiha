"""Modül 10 — İmaj için sanitize (son adım).

**İş akışındaki yeri**

Normal iş akışımız şöyledir:

    1. Boş tahtaya Pardus ETAP temiz kurulum yapılır.
    2. etapadmin'e geçilir, TiHA bu komutla başlatılır.
    3. 1–9 arası adımlar sırasıyla uygulanır, ardından bu son adım.
    4. Tahta bir kez yeniden başlatılır (servisler test edilir).
    5. İmaj alma aracıyla (Clonezilla vb.) diskin imajı alınır.
    6. Bu imaj diğer tahtalara uygulanır.

**Bu adım iki iş yapar:**

1. *Tekil kimlik temizliği* — imajdan klonlanan tüm tahtalara aynı
   machine-id, SSH host anahtarı, NetworkManager UUID'si gitmesin diye
   bu kimlikleri sıfırlar (ilk açılışta her klon kendi setini üretir).

2. *Yer açma / iz silme* — imajın boyutunu küçültmek ve sahaya gizlilik
   bilgisi sızdırmamak için APT önbelleği, journald logları, kullanıcı
   önbellekleri, geçici dosyalar, kabuk geçmişleri, çöp kutuları vb.
   silinir. Yaklaşımı virt-sysprep, cloud-init clean, BleachBit ve
   benzeri açık kaynak araçlardan esinlenir.

**Geri al.** Sanitize'ın geri alınması anlamlı değildir (silinen
benzersiz kimliği geri üretemeyiz, silinen logları geri getiremeyiz).
Modül ``undo_supported = False`` olarak işaretlenmiştir.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.utils import run_cmd

log = get_logger(__name__)

# Locale temizliğinde tutulacak diller (LC_MESSAGES alt klasörleri).
# tr*, en*, C, POSIX kalır; gerisi silinir.
KEEP_LOCALES = ("tr", "en", "C", "POSIX")

REGEN_SSH_SERVICE = Path("/etc/systemd/system/tiha-first-boot-sshkeys.service")
REGEN_SSH_SCRIPT = Path("/usr/local/sbin/tiha-first-boot-sshkeys.sh")
REGEN_SSH_SENTINEL = Path("/var/lib/tiha/first-boot-sshkeys.done")

REGEN_SSH_SCRIPT_CONTENT = f"""#!/bin/bash
# TiHA — ilk açılışta SSH host anahtarlarını yeniden üretir.
set -euo pipefail
[[ -f {REGEN_SSH_SENTINEL} ]] && exit 0
rm -f /etc/ssh/ssh_host_*
ssh-keygen -A
mkdir -p "$(dirname {REGEN_SSH_SENTINEL})"
touch {REGEN_SSH_SENTINEL}
systemctl restart ssh || true
"""

REGEN_SSH_SERVICE_CONTENT = f"""[Unit]
Description=TiHA — İlk açılışta SSH host anahtarlarını üret
Before=ssh.service
ConditionPathExists=!{REGEN_SSH_SENTINEL}

[Service]
Type=oneshot
ExecStart={REGEN_SSH_SCRIPT}

[Install]
WantedBy=multi-user.target
"""


def _truncate(path: Path) -> bool:
    try:
        if path.exists():
            path.write_text("", encoding="utf-8")
            return True
    except OSError as exc:
        log.warning("Boşaltılamadı %s: %s", path, exc)
    return False


def _rm(path: Path) -> bool:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
        return True
    except OSError as exc:
        log.warning("Silinemedi %s: %s", path, exc)
        return False


def _empty_dir(path: Path) -> int:
    """Klasörün içindeki her şeyi siler (klasörün kendisi kalır). Silinen
    girdi sayısını döner."""
    if not path.is_dir() or path.is_symlink():
        return 0
    n = 0
    for child in path.iterdir():
        if _rm(child):
            n += 1
    return n


def _glob_remove(root: Path, patterns: list[str]) -> int:
    """root altında verilen glob desenlerine uyan dosyaları siler."""
    n = 0
    if not root.exists():
        return 0
    for pattern in patterns:
        for match in root.glob(pattern):
            if _rm(match):
                n += 1
    return n


def _disk_used_kb() -> int:
    """`/` üzerinde kullanılan disk alanı (KB). Hata olursa 0 döner."""
    try:
        st = os.statvfs("/")
        return ((st.f_blocks - st.f_bfree) * st.f_frsize) // 1024
    except OSError:
        return 0


def _human_kb(kb: int) -> str:
    if kb >= 1024 * 1024:
        return f"{kb / 1024 / 1024:.2f} GB"
    if kb >= 1024:
        return f"{kb / 1024:.1f} MB"
    return f"{kb} KB"


# Chromium tabanlı tarayıcılar (Chrome, Chromium, Edge, Brave, Vivaldi, Opera)
# için ~/.config altındaki ortak yerleşim yolları.
CHROMIUM_CONFIG_DIRS = (
    ".config/google-chrome",
    ".config/chromium",
    ".config/microsoft-edge",
    ".config/BraveSoftware/Brave-Browser",
    ".config/vivaldi",
    ".config/opera",
    ".config/yandex-browser",
)

# Chromium profilinde önbellek/oturum içeren alt klasörler.
CHROMIUM_CACHE_SUBDIRS = (
    "Cache", "Code Cache", "GPUCache", "Service Worker",
    "IndexedDB", "Local Storage", "Session Storage",
    "Sessions", "Network", "DawnGraphiteCache", "DawnWebGPUCache",
    "Application Cache", "File System", "blob_storage",
)

# Chromium profilinde kişisel veri içeren tek dosyalar (DB + journal/wal).
# Bu listedeki her ad için "<ad>", "<ad>-journal", "<ad>-shm", "<ad>-wal"
# varyantları da silinir.
CHROMIUM_DATA_FILES = (
    "Cookies", "History", "Web Data", "Login Data", "Login Data For Account",
    "Visited Links", "Top Sites", "Favicons", "Shortcuts", "QuotaManager",
    "Network Action Predictor", "Trust Tokens", "Reporting and NEL",
    "Affiliation Database", "Media History",
)


def _clean_browser_data(home: Path) -> int:
    """Bir ev dizinindeki tarayıcı önbellek ve kişisel verilerini siler.

    Hem ``~/.cache/<tarayıcı>`` hem ``~/.config/<tarayıcı>`` altındaki
    önbellekler, hem de gezinti geçmişi/cookies gibi kullanıcıya özel
    veri dosyaları silinir. Tarayıcı tercihleri (yer imleri için
    Chromium "Bookmarks" dosyası dahil) korunur — silinenler ya geçici
    önbellek ya da imaja sızdırılmaması gereken kişisel izlerdir.
    """
    n = 0

    # ----- Firefox / SeaMonkey ----------------------------------------
    firefox_root = home / ".mozilla" / "firefox"
    if firefox_root.is_dir():
        for profile in firefox_root.iterdir():
            if not profile.is_dir():
                continue
            # Önbellekler
            for sub in ("cache2", "startupCache", "OfflineCache",
                        "thumbnails", "shader-cache"):
                n += _empty_dir(profile / sub)
            # IndexedDB / Storage / Service Worker kayıtları
            for sub in ("storage", "datareporting", "saved-telemetry-pings",
                        "minidumps", "crashes"):
                n += _empty_dir(profile / sub)
            # Oturum yedekleri
            n += _empty_dir(profile / "sessionstore-backups")
            n += _empty_dir(profile / "bookmarkbackups")
            # Kişisel veri dosyaları (sqlite + sidecar + jsonlz4)
            for fname in ("cookies.sqlite", "cookies.sqlite-shm",
                          "cookies.sqlite-wal",
                          "places.sqlite", "places.sqlite-shm",
                          "places.sqlite-wal",
                          "formhistory.sqlite", "webappsstore.sqlite",
                          "permissions.sqlite", "content-prefs.sqlite",
                          "favicons.sqlite", "favicons.sqlite-shm",
                          "favicons.sqlite-wal",
                          "sessionstore.jsonlz4", "sessionCheckpoints.json",
                          "previous.jsonlz4", "recovery.jsonlz4",
                          "downloads.json"):
                if _rm(profile / fname):
                    n += 1

    # ~/.cache altındaki Mozilla önbelleği (~/.cache/* zaten yukarıda
    # temizleniyor; yine de güvene alıyoruz)
    n += _empty_dir(home / ".cache" / "mozilla")

    # ----- Chromium türevleri -----------------------------------------
    for browser_path in CHROMIUM_CONFIG_DIRS:
        browser_root = home / browser_path
        if not browser_root.is_dir():
            continue
        # ~/.config/<browser> altında "Default" + "Profile N" alt
        # klasörleri kullanıcı profilleridir; kök dizinde "GrShaderCache"
        # gibi paylaşılan önbellekler de bulunur.
        for shared in ("GrShaderCache", "GraphiteDawnCache", "ShaderCache",
                       "component_crx_cache", "extensions_crx_cache",
                       "Crashpad", "Greaselion"):
            n += _empty_dir(browser_root / shared)

        for profile in browser_root.iterdir():
            if not profile.is_dir():
                continue
            if profile.name != "Default" and not profile.name.startswith("Profile "):
                continue
            # Cache klasörleri
            for sub in CHROMIUM_CACHE_SUBDIRS:
                n += _empty_dir(profile / sub)
            # Kişisel veri tek dosyaları
            for base in CHROMIUM_DATA_FILES:
                for suffix in ("", "-journal", "-shm", "-wal"):
                    if _rm(profile / f"{base}{suffix}"):
                        n += 1

    # ~/.cache altındaki Chromium türevleri (~/.cache/* zaten yukarıda
    # temizleniyor; güvene alıyoruz)
    for cache_name in ("google-chrome", "chromium", "microsoft-edge",
                       "BraveSoftware", "vivaldi", "opera",
                       "yandex-browser"):
        n += _empty_dir(home / ".cache" / cache_name)

    return n


class ImageSanitizeModule(Module):
    id = "m10_image_sanitize"
    title = "İmaj için sanitize"
    apply_hint = (
        "Son adım: tekil kimlikler ve izler temizlenir — imaj alınabilir."
    )
    popup_on_success = True
    rationale = (
        "İmaj almadan önce çalıştırılan ZORUNLU son adım. Aksi hâlde "
        "imajdan çıkan bütün tahtalarda aynı machine-id, aynı SSH host "
        "anahtarı, aynı NetworkManager UUID'si ve aynı kabuk geçmişi "
        "olur — bu bir işe yaramaz mı? Yarar — ama funcationel görünse de "
        "journald'ın dağınık davranışı, ssh bağlantılarının 'host key "
        "değişti' uyarısı, WiFi parolasının 50 tahtaya aynen sızması "
        "gibi sonuçlar doğar. TiHA burada tüm bu tekil kimlikleri "
        "temizler, ilk boot'ta kendi benzersiz SSH anahtarını üreten bir "
        "servis bırakır; ve tahtanın artık imaj alınmaya hazır olmasını "
        "sağlar. Geri alma anlamlı değildir — silinen kimliği üretemeyiz."
    )
    undo_supported = False

    def preview(self) -> str:
        return (
            "Bu SON adımdır. Uygulandıktan sonra tahta imaj alınmaya hazırdır.\n"
            "Aşağıdaki kategoriler temizlenecek (geri alınamaz):\n\n"
            "Tekil kimlikler (her klon kendi setini üretir):\n"
            "  • /etc/machine-id ve /var/lib/dbus/machine-id\n"
            "  • /etc/ssh/ssh_host_*\n"
            "  • /etc/NetworkManager/system-connections/* (WiFi parolaları dahil)\n"
            "  • DHCP/DHCP6 lease dosyaları\n"
            "  • /var/lib/systemd/random-seed\n\n"
            "Yer açan ve iz silen temizlikler:\n"
            "  • APT önbelleği ve indirilmiş .deb paketleri\n"
            "  • Yetim paketler (apt-get autoremove --purge)\n"
            "  • Kalıntı yapılandırmalar (rc-state paketler dpkg --purge)\n"
            "  • systemd journal (boyut 1 KB'a indirilir)\n"
            "  • /var/log altındaki dosyalar (yapı korunur, içerik boşaltılır)\n"
            "  • Crash raporları (/var/crash, /var/lib/whoopsie)\n"
            "  • Mail/cups/anacron spool kuyrukları\n"
            "  • man / fontconfig / debconf / lightdm önbellekleri\n"
            "  • dpkg yedek dosyaları (*.dpkg-old, *.dpkg-dist, *.ucf-*)\n"
            "  • Tüm kullanıcıların ~/.cache, ~/.local/share/Trash, çeşitli\n"
            "    geçmiş dosyaları (.bash_history, .lesshst, .viminfo, .python_history)\n"
            "  • Web tarayıcı önbellekleri ve gezinti verileri\n"
            "    (Firefox, Chrome, Chromium, Edge, Brave, Vivaldi, Opera, Yandex)\n"
            "    — gezinti geçmişi, çerezler, indirme geçmişi, oturumlar, IndexedDB;\n"
            "      tarayıcı tercihleri ve yer imleri korunur\n"
            "  • /tmp ve /var/tmp içerikleri\n"
            "  • Kullanılmayan diller için locale dosyaları\n"
            f"    ({', '.join(KEEP_LOCALES)} dışındakiler /usr/share/locale altından silinir)\n\n"
            "Uyguladıktan sonra: doğrudan imaj alma aracınızı (Clonezilla vb.) "
            "çalıştırabilirsiniz."
        )

    def apply(self, params: dict | None = None, progress: ProgressCallback | None = None) -> ApplyResult:
        ops: list[str] = []
        before_kb = _disk_used_kb()

        if progress:
            progress("İmaj sanitizasyon başlatılıyor...")

        # ===== 1) Tekil kimlikler =====================================
        if progress:
            progress("Tekil kimlikler temizleniyor...")
        # machine-id
        _truncate(Path("/etc/machine-id"))
        _rm(Path("/var/lib/dbus/machine-id"))
        ops.append("machine-id temizlendi")

        # SSH host anahtarları + ilk açılışta üretme servisi
        for p in Path("/etc/ssh").glob("ssh_host_*"):
            _rm(p)
        REGEN_SSH_SCRIPT.write_text(REGEN_SSH_SCRIPT_CONTENT, encoding="utf-8")
        REGEN_SSH_SCRIPT.chmod(0o755)
        REGEN_SSH_SERVICE.write_text(REGEN_SSH_SERVICE_CONTENT, encoding="utf-8")
        run_cmd(["systemctl", "daemon-reload"])
        run_cmd(["systemctl", "enable", REGEN_SSH_SERVICE.name])
        ops.append("SSH host anahtarları silindi, ilk açılışta yenilenecek")

        # NetworkManager bağlantıları
        nm_dir = Path("/etc/NetworkManager/system-connections")
        if nm_dir.exists():
            n = _empty_dir(nm_dir)
            if n:
                ops.append(f"{n} NetworkManager bağlantı dosyası silindi")

        # DHCP lease dosyaları (her klon kendi lease'ını alacak)
        n = _glob_remove(Path("/var/lib/dhcp"), ["*.leases", "*.leases~"])
        n += _glob_remove(Path("/var/lib/NetworkManager"), ["*.lease", "*.leases"])
        if n:
            ops.append(f"{n} DHCP lease dosyası silindi")

        # systemd random-seed (sonraki açılışta yeniden üretilir)
        if _rm(Path("/var/lib/systemd/random-seed")):
            ops.append("systemd random-seed sıfırlandı")

        # ===== 2) APT önbelleği ve paket temizliği ====================
        if progress:
            progress("APT önbelleği ve yetim paketler temizleniyor...")
        # rc-state paketleri (silindi ama config kalmış)
        rc_pkgs = run_cmd(["bash", "-lc",
                           "dpkg -l | awk '/^rc/ {print $2}'"]).stdout.split()
        if rc_pkgs:
            run_cmd(["dpkg", "--purge", *rc_pkgs])
            ops.append(f"{len(rc_pkgs)} kalıntı paket purge edildi")

        # autoremove + clean
        env = {"DEBIAN_FRONTEND": "noninteractive"}
        run_cmd(["apt-get", "autoremove", "--purge", "-y"], env=env, timeout=600)
        run_cmd(["apt-get", "clean"], env=env, timeout=120)
        # apt arşiv dizinini iyice süpür
        n = _glob_remove(Path("/var/cache/apt/archives"),
                         ["*.deb", "partial/*", "lock"])
        if n:
            ops.append(f"APT arşivinden {n} öğe silindi")
        # apt list cache (sonraki apt update yeniden çeker)
        n = _empty_dir(Path("/var/lib/apt/lists"))
        if n:
            ops.append(f"APT paket listesi temizlendi ({n} dosya)")

        # ===== 3) Journal & loglar ====================================
        if progress:
            progress("Sistem logları ve journal temizleniyor...")
        # Journald: önce rotate, sonra boyut 1K'a indir
        run_cmd(["journalctl", "--rotate"])
        run_cmd(["journalctl", "--vacuum-size=1K"])
        ops.append("systemd journal vakumlandı")

        # /var/log altındaki tüm dosyalar (yapıyı koruyarak boşalt)
        # Rotated/eski .gz, .1, .2 vb. dosyaları tamamen sil
        log_root = Path("/var/log")
        if log_root.exists():
            removed = 0
            truncated = 0
            for item in log_root.rglob("*"):
                if not item.is_file() or item.is_symlink():
                    continue
                if "tiha" in item.name:  # aktif oturum logu
                    continue
                name = item.name
                if name.endswith((".gz", ".xz", ".bz2", ".zst", ".old")) or \
                   any(name.endswith(f".{i}") for i in range(1, 10)):
                    if _rm(item):
                        removed += 1
                else:
                    if _truncate(item):
                        truncated += 1
            ops.append(f"/var/log: {truncated} dosya boşaltıldı, {removed} eski dosya silindi")

        # Installer logları (Debian kurulum izleri)
        n = _glob_remove(Path("/var/log"), ["installer/*", "installer"])
        if n:
            ops.append("Debian installer logları silindi")

        # ===== 4) Crash & telemetri ===================================
        n = _empty_dir(Path("/var/crash"))
        n += _glob_remove(Path("/var/lib/whoopsie"), ["*"])
        n += _glob_remove(Path("/var/lib/apport"), ["coredump/*"])
        if n:
            ops.append(f"{n} crash/telemetri öğesi silindi")

        # ===== 5) Spool kuyrukları ====================================
        spool_clean = 0
        for spool in [
            Path("/var/spool/mail"),
            Path("/var/mail"),
            Path("/var/spool/cups"),
            Path("/var/spool/anacron"),
            Path("/var/spool/cron/crontabs"),
            Path("/var/spool/cron/atjobs"),
            Path("/var/spool/cron/atspool"),
        ]:
            spool_clean += _empty_dir(spool)
        if spool_clean:
            ops.append(f"Spool kuyruklarından {spool_clean} öğe silindi")

        # ===== 6) Sistem önbellekleri =================================
        cache_clean = 0
        for cache_dir in [
            Path("/var/cache/man"),
            Path("/var/cache/fontconfig"),
            Path("/var/cache/debconf"),
            Path("/var/cache/PackageKit"),
            Path("/var/cache/lightdm"),
            Path("/var/cache/cups"),
            Path("/var/lib/PackageKit"),
        ]:
            cache_clean += _empty_dir(cache_dir)
        if cache_clean:
            ops.append(f"Sistem önbelleklerinden {cache_clean} öğe silindi")

        # cloud-init varsa kendi temizliğini çalıştır
        if shutil.which("cloud-init"):
            run_cmd(["cloud-init", "clean", "--logs", "--seed"])
            ops.append("cloud-init durumu sıfırlandı")

        # ===== 7) dpkg yedek/diff dosyaları ===========================
        # Ayar paketleri yükseltmesinde kalan eski/distro/kullanıcı sürümleri
        result = run_cmd(["bash", "-lc",
                          "find /etc /var -name '*.dpkg-old' -o "
                          "-name '*.dpkg-dist' -o -name '*.dpkg-new' -o "
                          "-name '*.ucf-old' -o -name '*.ucf-dist' "
                          "2>/dev/null | wc -l"])
        n = int(result.stdout.strip() or "0")
        if n:
            run_cmd(["bash", "-lc",
                     "find /etc /var \\( -name '*.dpkg-old' -o "
                     "-name '*.dpkg-dist' -o -name '*.dpkg-new' -o "
                     "-name '*.ucf-old' -o -name '*.ucf-dist' \\) -delete"])
            ops.append(f"{n} dpkg/ucf yedek dosyası silindi")

        # ===== 8) Locale temizliği ====================================
        locale_root = Path("/usr/share/locale")
        if locale_root.is_dir():
            removed = 0
            for entry in locale_root.iterdir():
                if not entry.is_dir():
                    continue
                # Tutulacak: tr*, en*, C, POSIX (örn. tr_TR, en_US.UTF-8)
                if any(entry.name == k or entry.name.startswith(k + "_") or
                       entry.name.startswith(k + ".")
                       for k in KEEP_LOCALES):
                    continue
                if _rm(entry):
                    removed += 1
            if removed:
                ops.append(f"{removed} kullanılmayan locale dizini silindi")

        # ===== 9) Tüm kullanıcıların ev dizinleri =====================
        if progress:
            progress("Kullanıcı ev dizinleri temizleniyor (cache, geçmiş, tarayıcı verileri)...")
        homes: list[Path] = [Path("/root")]
        home_root = Path("/home")
        if home_root.is_dir():
            for h in home_root.iterdir():
                if h.is_dir() and not h.is_symlink():
                    homes.append(h)

        history_files = [
            ".bash_history", ".zsh_history", ".sh_history",
            ".lesshst", ".viminfo", ".wget-hs", ".python_history",
            ".node_repl_history", ".mysql_history", ".psql_history",
            ".rediscli_history",
        ]
        cache_subdirs_total = 0
        history_total = 0
        trash_total = 0
        browser_total = 0
        for home in homes:
            # Geçmiş dosyaları
            for hist in history_files:
                if _rm(home / hist):
                    history_total += 1
            # Cache klasörü tamamen
            cache_subdirs_total += _empty_dir(home / ".cache")
            # Çöp kutusu
            trash_total += _empty_dir(home / ".local" / "share" / "Trash")
            # Eski thumbnail klasörleri (XDG öncesi)
            _rm(home / ".thumbnails")
            # Recently used dosyaları
            _rm(home / ".local" / "share" / "recently-used.xbel")
            _rm(home / ".recently-used")
            _rm(home / ".recently-used.xbel")
            # Tarayıcı önbellek + kişisel veri (Firefox, Chrome, Edge,
            # Brave, Chromium, Vivaldi, Opera, Yandex)
            browser_total += _clean_browser_data(home)
        ops.append(
            f"{len(homes)} ev dizininde: {history_total} geçmiş dosyası "
            f"silindi, {cache_subdirs_total} cache öğesi temizlendi, "
            f"{trash_total} çöp kutusu öğesi silindi"
        )
        if browser_total:
            ops.append(
                f"Tarayıcılardan (Firefox/Chrome/Edge/Brave/...) "
                f"{browser_total} önbellek/veri öğesi silindi"
            )

        # ===== 10) /tmp ve /var/tmp ===================================
        tmp_n = 0
        for parent in (Path("/tmp"), Path("/var/tmp")):
            tmp_n += _empty_dir(parent)
        ops.append(f"/tmp ve /var/tmp: {tmp_n} öğe silindi")

        # ===== Disk farkı ============================================
        if progress:
            progress("Sanitizasyon tamamlanıyor, disk kullanımı hesaplanıyor...")

        after_kb = _disk_used_kb()
        freed_kb = max(0, before_kb - after_kb)
        freed_str = _human_kb(freed_kb) if freed_kb else "ölçülemedi"

        return ApplyResult(
            True,
            f"İmaj sanitize tamamlandı; ~{freed_str} alan boşaltıldı.",
            details="\n".join(f"• {o}" for o in ops),
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        return ApplyResult(False, "Bu işlem geri alınamaz.")
