"""Modül 9 — Sistem güncellemesi.

**Ne yapar?**
Repository sağlığı kontrol eder, eksik ana depoları ekler, bozuk dosyaları
temizler, ardından ``apt-get update``, ``apt-get full-upgrade -y``,
``apt-get autoremove -y`` ve ``apt-get clean`` komutlarını sırayla çalıştırır.
Çıktı ekrana **canlı olarak** akar.

**Repository sağlığı:**
- Ana Pardus ETAP depolarının varlığını kontrol eder
- Eksikse /etc/apt/sources.list dosyasını düzeltir
- Bozuk .broken.* dosyalarını temizler

**Neden gerekir?**
İmaj alındıktan sonra sahaya dağıtılacak tahtaların en güncel yama
seviyesinde çıkmaları güvenlik ve kararlılık için tercih edilir.

**Geri al.** Paket yükseltmeleri otomatik olarak geri alınamaz; modül
``undo_supported = False`` ile işaretlenmiştir.
"""

from __future__ import annotations

import glob
import subprocess
from pathlib import Path

from ..core.async_state import AsyncValue
from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.privilege import invoking_username
from ..core.utils import run_cmd, run_cmd_stream

log = get_logger(__name__)

# Pardus ETAP 23 ana depoları. MEB'in dağıttığı ETAP 23 imajlarında
# birincil depo ``depo.etap.org.tr`` olarak gelir; eski dağıtımlarda
# (ve genel Pardus kurulumlarında) ``depo.pardus.org.tr`` kullanılır.
# Burada modern, ETAP-merkezli URL'ler liste başına alındı —
# ``fix_repositories`` boş ya da bozuk bir sources.list'i bu kümeyle
# yeniden yazar.
PARDUS_ETAP_REPOS = [
    "deb http://depo.etap.org.tr/etap yirmiuc main contrib non-free non-free-firmware",
    "deb http://depo.etap.org.tr/pardus yirmiuc main contrib non-free non-free-firmware",
    "deb http://depo.etap.org.tr/pardus yirmiuc-deb main contrib non-free non-free-firmware",
    "deb http://depo.etap.org.tr/guvenlik yirmiuc-deb main contrib non-free non-free-firmware",
]

# Sağlık kontrolünde ana depo sayılacak host'lar. Hem yeni MEB ETAP
# domaini hem de eski Pardus genel depo domaini kabul edilir.
_MAIN_REPO_HOSTS = ("depo.etap.org.tr", "depo.pardus.org.tr")

# ETAP 23 için kabul edilebilir suite adları: bazı eski imajlar
# ``etap-yirmiuc`` kullanırken günceller ``yirmiuc``/``yirmiuc-deb``
# olarak görünür.
_MAIN_REPO_SUITES = ("yirmiuc", "yirmiuc-deb", "etap-yirmiuc", "etap-yirmiuc-deb")


def _line_is_main_repo(line: str) -> bool:
    """Verilen sources.list satırı ETAP'a ait bir ana depo mu?"""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    if not any(host in stripped for host in _MAIN_REPO_HOSTS):
        return False
    # Suite alanı tipik olarak host'tan sonraki ilk kelime; tam kelime
    # eşleşmesi için token bazlı kontrol.
    tokens = stripped.split()
    return any(suite in tokens for suite in _MAIN_REPO_SUITES)


def check_repository_health() -> dict:
    """Repository sağlığını kontrol eder."""
    issues = {
        "missing_main_repos": False,
        "broken_files": [],
        "empty_sources_list": False,
    }

    # sources.list dosyasını kontrol et
    sources_list = Path("/etc/apt/sources.list")
    if not sources_list.exists() or sources_list.stat().st_size == 0:
        issues["empty_sources_list"] = True
        issues["missing_main_repos"] = True
    else:
        content = sources_list.read_text()
        if not any(_line_is_main_repo(line) for line in content.splitlines()):
            issues["missing_main_repos"] = True

    # Bozuk repository dosyalarını bul
    broken_files = glob.glob("/etc/apt/sources.list.d/*.broken.*")
    if broken_files:
        issues["broken_files"] = broken_files

    return issues


def fix_repositories(progress=None) -> bool:
    """Repository sorunlarını düzeltir."""
    try:
        issues = check_repository_health()
        fixed_count = 0

        # Bozuk dosyaları temizle
        if issues["broken_files"]:
            if progress:
                progress(f"{len(issues['broken_files'])} bozuk repository dosyası siliniyor...")
            for broken_file in issues["broken_files"]:
                try:
                    Path(broken_file).unlink()
                    log.info("Bozuk dosya silindi: %s", broken_file)
                    fixed_count += 1
                except Exception as exc:
                    log.warning("Bozuk dosya silinemedi %s: %s", broken_file, exc)

        # Ana depoları düzelt
        if issues["missing_main_repos"]:
            if progress:
                progress("Ana Pardus ETAP depoları ekleniyor...")

            sources_list = Path("/etc/apt/sources.list")

            # Mevcut içeriği oku (varsa)
            existing_content = ""
            if sources_list.exists():
                existing_content = sources_list.read_text().strip()

            # Yeni içeriği oluştur
            new_content = []

            # Ana Pardus depolarını ekle
            new_content.extend(PARDUS_ETAP_REPOS)

            # Mevcut önemli 3. parti depoları koru (eğer valid ise) —
            # ana depo host'larını içeren satırlar yeniden eklenmesin,
            # diğer (chrome/docker/node) depo satırlarını koru.
            if existing_content:
                for line in existing_content.splitlines():
                    line = line.strip()
                    if (line and not line.startswith("#") and
                        not any(host in line for host in _MAIN_REPO_HOSTS) and
                        ("chrome" in line.lower() or "docker" in line.lower() or "node" in line.lower())):
                        new_content.append(line)

            # Dosyayı yaz
            sources_list.write_text("\n".join(new_content) + "\n")
            log.info("Ana Pardus ETAP depoları eklendi")
            fixed_count += 1

        if fixed_count > 0:
            if progress:
                progress(f"✅ {fixed_count} repository sorunu düzeltildi")
            return True
        else:
            if progress:
                progress("✅ Repository yapılandırması sağlıklı")
            return True

    except Exception as exc:
        log.error("Repository düzeltme hatası: %s", exc)
        if progress:
            progress(f"❌ Repository düzeltme hatası: {exc}")
        return False


def _compute_pending_update_count() -> int:
    """``apt-get -s -q full-upgrade`` çalıştırıp ``Inst `` satırlarını
    sayar. Senkron, ~3 sn sürer — yalnız arka plan worker'ından çağrılır."""
    result = run_cmd(
        ["apt-get", "-s", "-q", "full-upgrade"],
        check=False,
        timeout=60,
    )
    if not result.ok:
        return -1
    return sum(1 for line in result.stdout.splitlines() if line.startswith("Inst "))


# Modül seviyesi AsyncValue: cache + worker + callback yönetimi
# ``core/async_state.py`` içinden gelir. Apply başarısı sonrası
# ``invalidate()`` çağrılır.
_pending_updates = AsyncValue(_compute_pending_update_count, name="m09.pending")


class SystemUpdateModule(Module):
    id = "m09_system_update"
    title = "Sistem güncellemesi (apt)"
    sidebar_title = "Sistem güncellemesi"
    apply_hint = (
        "Repository sağlığı düzeltilir, apt update + full-upgrade + temizlik çalışır (uzun sürer)."
    )
    rationale = (
        "Tahtadaki paketleri en güncel sürüme çıkarır. Güvenlik yamaları ve "
        "kararlılık düzeltmeleri için imaj öncesi tavsiye edilir. Çıktı "
        "ekranda canlı olarak akar; güncelleme uzun sürebilir.\n\n"
        "Bekleyen yükseltme yoksa bu adım atlanabilir; alttaki “İleri” "
        "düğmesi ya da soldaki listeden bir sonraki adıma geçebilirsiniz."
    )
    undo_supported = False
    streams_output = True
    extra_links = [
        {"label": "Pardus Güncelleyici'yi aç", "action": "launch_pardus_update_gui_action"},
    ]

    def pending_update_count(self) -> int:
        """Bloke etmeyen cache okuyucu. Cache yoksa arka plan worker'ı
        başlatır ve -1 döner; sonuç gelince UI yeniden tazelenir."""
        value = _pending_updates.get_async()
        return -1 if value is None else value

    def pending_update_count_async(self, callback) -> int:
        """Sonuç hazır olunca ``callback(value)`` ana thread'de çağrılır."""
        value = _pending_updates.get_async(callback)
        return -1 if value is None else value

    def prefetch_preview_state(self, on_ready=None) -> None:
        """Sayfa açıldığında arka planda apt sorgusunu tetikler."""
        _pending_updates.get_async(on_ready)

    def preview(self) -> str:
        # Repository sağlığını kontrol et
        repo_issues = check_repository_health()
        repo_status = []

        if repo_issues["missing_main_repos"]:
            repo_status.append("❌ Ana Pardus ETAP depoları eksik")
        else:
            repo_status.append("✅ Ana Pardus ETAP depoları mevcut")

        if repo_issues["broken_files"]:
            repo_status.append(f"⚠️ {len(repo_issues['broken_files'])} bozuk repository dosyası")
        else:
            repo_status.append("✅ Bozuk repository dosyası yok")

        if repo_issues["empty_sources_list"]:
            repo_status.append("❌ /etc/apt/sources.list boş veya eksik")

        # Bekleyen güncelleme sayısı — cache'ten okunur. Cache yoksa
        # arka plan worker tetiklenir (UI bloke olmadan); önizleme
        # "kontrol ediliyor" gösterir ve sonuç gelince main_window
        # callback'i bu sayfayı yeniden çizdirir.
        cached = _pending_updates.get_async()
        count = -1 if cached is None else cached
        checking = cached is None and _pending_updates.in_progress()

        preview_lines = ["🔍 Repository Durumu:"] + [f"  {status}" for status in repo_status]
        preview_lines.append("")

        if checking:
            preview_lines.extend([
                "📦 Bekleyen yükseltme sayısı arka planda kontrol ediliyor…",
                "",
                "Bu sayıya bakmadan da Uygula'ya basabilirsiniz; adım yine de",
                "repository onarımı + apt update → full-upgrade → autoremove → clean",
                "zincirini çalıştırır."
            ])
        elif count < 0:
            preview_lines.extend([
                "📦 Bekleyen yükseltme sayısı tespit edilemedi (apt erişilemedi).",
                "",
                "Uygula çalıştırıldığında:",
                "• Repository sorunları düzeltilir",
                "• apt update → full-upgrade → autoremove → clean"
            ])
        elif count == 0:
            preview_lines.extend([
                "📦 Bekleyen yükseltme yok. Sistem güncel görünüyor.",
                "",
                "Repository sorunları varsa düzeltilir, sonra bu adımı",
                "uygulamadan geçebilirsiniz."
            ])
        else:
            preview_lines.extend([
                f"📦 {count} paket için yükseltme bekleniyor.",
                "",
                "Uygula çalıştırıldığında:",
                "• Repository sorunları düzeltilir",
                "• apt update → full-upgrade → autoremove → clean",
                "• Uzun sürebilir"
            ])

        return "\n".join(preview_lines)

    def apply(self, params=None, progress: ProgressCallback | None = None) -> ApplyResult:
        env = {"DEBIAN_FRONTEND": "noninteractive"}
        failed: list[str] = []

        # 1. Repository sağlığını düzelt
        if progress:
            progress("\n==== Repository sağlığı kontrol ediliyor ====")
        log.info("Repository sağlığı kontrol ediliyor...")

        if not fix_repositories(progress):
            return ApplyResult(
                False,
                "Repository düzeltme başarısız oldu.",
                details="Repository yapılandırması düzeltilemedi. Manuel müdahale gerekebilir.",
            )

        # 2. Sistem güncellemesi adımları
        steps = [
            ("apt update", ["apt-get", "update"]),
            ("apt full-upgrade", ["apt-get", "full-upgrade", "-y"]),
            ("apt autoremove", ["apt-get", "autoremove", "-y"]),
            ("apt clean", ["apt-get", "clean"]),
        ]

        for label, cmd in steps:
            if progress:
                progress(f"\n==== {label} ====")
            log.info("%s çalıştırılıyor…", label)
            result = run_cmd_stream(cmd, progress=progress, env=env, timeout=3600)
            if not result.ok:
                failed.append(label)
                if progress:
                    progress(f"[HATA] {label} başarısız (çıkış kodu {result.returncode})")
                log.error("%s başarısız", label)

        # Paket envanteri değişti (veya değişmiş olabilir) — cache'i tazele.
        _pending_updates.invalidate()

        if failed:
            return ApplyResult(
                False,
                "Bazı güncelleme adımları başarısız oldu.",
                details="Başarısız olanlar: " + ", ".join(failed),
            )
        return ApplyResult(True, "Sistem güncel.")

    def launch_pardus_update_gui_action(self, params: dict | None = None) -> ApplyResult:
        """Pardus Güncelleyici GUI'sini kullanıcının X oturumunda açar."""
        binary = Path("/usr/bin/pardus-update")
        if not binary.exists():
            return ApplyResult(
                False,
                "Pardus Güncelleyici uygulaması bulunamadı.",
                details=f"{binary} mevcut değil; pardus-update paketi kurulu mu?",
            )

        user = invoking_username()
        try:
            subprocess.Popen(
                ["sudo", "-u", user, "env",
                 "DISPLAY=:0",
                 f"XAUTHORITY=/home/{user}/.Xauthority",
                 str(binary)],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return ApplyResult(
                False,
                "Pardus Güncelleyici başlatılamadı.",
                details=str(exc),
            )

        return ApplyResult(
            True,
            f"Pardus Güncelleyici '{user}' oturumunda açıldı.",
            details="Pencereyi kapattığınızda bu adımdaki bekleyen güncelleme sayısı yenilenir.",
        )
