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

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.privilege import invoking_username
from ..core.utils import run_cmd, run_cmd_stream

log = get_logger(__name__)

# Pardus ETAP ana depoları
PARDUS_ETAP_REPOS = [
    "deb https://depo.pardus.org.tr/pardus etap-yirmiuc main contrib non-free",
    "deb https://depo.pardus.org.tr/pardus etap-yirmiuc-deb main contrib non-free",
    "deb https://depo.pardus.org.tr/guvenlik etap-yirmiuc main contrib non-free",
]


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
        # Ana Pardus depolarını ara
        content = sources_list.read_text()
        has_main_repo = any("depo.pardus.org.tr" in line and "etap-yirmiuc" in line
                           for line in content.splitlines()
                           if not line.strip().startswith("#"))
        if not has_main_repo:
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

            # Mevcut önemli 3. parti depoları koru (eğer valid ise)
            if existing_content:
                for line in existing_content.splitlines():
                    line = line.strip()
                    if (line and not line.startswith("#") and
                        "depo.pardus.org.tr" not in line and
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


def pending_update_count() -> int:
    """Yükseltilebilir paket sayısı. Negatif → bilinmiyor (apt erişilemedi).

    apt'ın mevcut önbelleğini kullanır; ``apt-get update`` çalıştırmaz.
    Hızlıdır (genellikle <1 sn).
    """
    result = run_cmd(
        ["apt-get", "-s", "-q", "full-upgrade"],
        check=False,
        timeout=60,
    )
    if not result.ok:
        return -1
    return sum(1 for line in result.stdout.splitlines() if line.startswith("Inst "))


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
        return pending_update_count()

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

        # Bekleyen güncelleme sayısı
        count = pending_update_count()

        preview_lines = ["🔍 Repository Durumu:"] + [f"  {status}" for status in repo_status]
        preview_lines.append("")

        if count < 0:
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
