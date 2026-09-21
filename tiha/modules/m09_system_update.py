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
from ..core.i18n import t
from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.os_release import pretty_name, release_codename
from ..core.privilege import invoking_username
from ..core.utils import run_cmd, run_cmd_stream

log = get_logger(__name__)

# Pardus ETAP 23 ana depoları. MEB'in dağıttığı ETAP 23 imajlarında
# birincil depo ``depo.etap.org.tr`` olarak gelir; eski dağıtımlarda
# (ve genel Pardus kurulumlarında) ``depo.pardus.org.tr`` kullanılır.
# Burada modern, ETAP-merkezli URL'ler liste başına alındı —
# ``fix_repositories`` ana depo satırı bulunmayan bir sources.list'e bu
# kümeyi ekler (mevcut satırlara dokunmadan).
PARDUS_ETAP_REPOS = [
    "deb http://depo.etap.org.tr/etap yirmiuc main contrib non-free non-free-firmware",
    "deb http://depo.etap.org.tr/pardus yirmiuc main contrib non-free non-free-firmware",
    "deb http://depo.etap.org.tr/pardus yirmiuc-deb main contrib non-free non-free-firmware",
    "deb http://depo.etap.org.tr/guvenlik yirmiuc-deb main contrib non-free non-free-firmware",
]

# Sağlık kontrolünde ana depo sayılacak host'lar. Hem yeni MEB ETAP
# domaini hem de eski Pardus genel depo domaini kabul edilir.
SOURCES_LIST = Path("/etc/apt/sources.list")

_MAIN_REPO_HOSTS = ("depo.etap.org.tr", "depo.pardus.org.tr")

# ETAP 23 için kabul edilebilir suite adları: bazı eski imajlar
# ``etap-yirmiuc`` kullanırken günceller ``yirmiuc``/``yirmiuc-deb``
# olarak görünür.
_MAIN_REPO_SUITES = ("yirmiuc", "yirmiuc-deb", "etap-yirmiuc", "etap-yirmiuc-deb")

# Sürüm → (yazılacak ana depo satırları, ana depo sayılan suite adları).
# Depo onarımı YALNIZ burada tanımlı sürümlerde yapılır. Tanımsız bir
# sürümde (ör. ETAP 24) sources.list'e dokunulmaz: yoksa yeni sürümün
# depo satırları silinip yerine eski sürümünkiler yazılırdı.
_RELEASE_REPOS: dict[str, tuple[list[str], tuple[str, ...]]] = {
    "yirmiuc": (PARDUS_ETAP_REPOS, _MAIN_REPO_SUITES),
}


def _line_is_main_repo(line: str, suites: tuple[str, ...] = _MAIN_REPO_SUITES) -> bool:
    """Verilen sources.list satırı ETAP'a ait bir ana depo mu?"""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    if not any(host in stripped for host in _MAIN_REPO_HOSTS):
        return False
    # Suite alanı tipik olarak host'tan sonraki ilk kelime; tam kelime
    # eşleşmesi için token bazlı kontrol.
    tokens = stripped.split()
    return any(suite in tokens for suite in suites)


def check_repository_health() -> dict:
    """Repository sağlığını kontrol eder."""
    codename = release_codename()
    issues = {
        "missing_main_repos": False,
        "broken_files": [],
        "empty_sources_list": False,
        # Sürüm tanınmıyorsa ana depo denetimi yapılmaz (neyin "doğru"
        # olduğunu bilmiyoruz); onarım da yapılmaz.
        "release_known": codename in _RELEASE_REPOS,
    }

    # sources.list dosyasını kontrol et
    sources_list = SOURCES_LIST
    if not sources_list.exists() or sources_list.stat().st_size == 0:
        issues["empty_sources_list"] = True
        issues["missing_main_repos"] = issues["release_known"]
    elif issues["release_known"]:
        suites = _RELEASE_REPOS[codename][1]
        content = sources_list.read_text()
        if not any(_line_is_main_repo(line, suites) for line in content.splitlines()):
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
                progress(t("m09.fix.deleting_broken", count=len(issues['broken_files'])))
            for broken_file in issues["broken_files"]:
                try:
                    Path(broken_file).unlink()
                    log.info("Bozuk dosya silindi: %s", broken_file)
                    fixed_count += 1
                except Exception as exc:
                    log.warning("Bozuk dosya silinemedi %s: %s", broken_file, exc)

        # Tanınmayan sürüm: depo ayarlarına dokunma, yalnız söyle.
        if not issues["release_known"]:
            if progress:
                progress(t("m09.fix.unknown_release", name=pretty_name()))
            log.warning("Tanınmayan sürüm (%s); depo onarımı atlandı", pretty_name())

        # Ana depoları düzelt: eksik satırlar dosyanın SONUNA eklenir;
        # mevcut satırlara (başka depolar, yorumlar) dokunulmaz.
        elif issues["missing_main_repos"]:
            if progress:
                progress(t("m09.fix.adding_main"))

            sources_list = SOURCES_LIST
            existing = sources_list.read_text() if sources_list.exists() else ""
            repo_lines = _RELEASE_REPOS[release_codename()][0]
            present = {" ".join(line.split()) for line in existing.splitlines()}
            missing = [line for line in repo_lines if line not in present]
            text = existing.rstrip("\n")
            if text:
                text += "\n\n"
            text += "# TiHA: eksik ana depolar eklendi\n" + "\n".join(missing) + "\n"
            sources_list.write_text(text)
            log.info("Ana Pardus ETAP depoları eklendi: %s", missing)
            fixed_count += 1

        if fixed_count > 0:
            if progress:
                progress(t("m09.fix.fixed", count=fixed_count))
            return True
        else:
            # Tanınmayan sürümde denetim yapılmadı; "sağlıklı" demek yanıltır.
            if progress and issues["release_known"]:
                progress(t("m09.fix.healthy"))
            return True

    except Exception as exc:
        log.error("Repository düzeltme hatası: %s", exc)
        if progress:
            progress(t("m09.fix.error", error=exc))
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
    title = t("m09.title")
    sidebar_title = t("m09.sidebar_title")
    apply_hint = t("m09.apply_hint")
    rationale = t("m09.rationale")
    undo_supported = False
    streams_output = True
    extra_links = [
        {"label": t("m09.links.pardus_update"), "action": "launch_pardus_update_gui_action"},
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
        # m08 stiliyle: hizalı key-value satırlar + girintili dash liste.
        repo_issues = check_repository_health()

        def _status(condition_bad: bool, ok_text: str, bad_text: str) -> str:
            return bad_text if condition_bad else f"{ok_text}"

        # Bekleyen güncelleme sayısı - cache'ten okunur. Cache yoksa
        # arka plan worker tetiklenir; sonuç gelince main_window
        # callback'i bu sayfayı yeniden çizdirir.
        cached = _pending_updates.get_async()
        count = -1 if cached is None else cached
        checking = cached is None and _pending_updates.in_progress()

        lines: list[str] = []
        if not repo_issues["release_known"]:
            main_status = t("m09.preview.main_repos_unknown")
        else:
            main_status = _status(repo_issues["missing_main_repos"],
                                  t("m09.preview.main_repos_ok"),
                                  t("m09.preview.main_repos_bad"))
        lines.append(t("m09.preview.main_repos", status=main_status))
        lines.append(t(
            "m09.preview.broken_files",
            status=(t("m09.preview.broken_files_count",
                      count=len(repo_issues['broken_files']))
                    if repo_issues["broken_files"]
                    else t("m09.preview.broken_files_none")),
        ))
        lines.append(t(
            "m09.preview.sources_list",
            status=_status(repo_issues["empty_sources_list"],
                           t("m09.preview.sources_list_ok"),
                           t("m09.preview.sources_list_bad")),
        ))
        if checking:
            lines.append(t("m09.preview.pending_checking"))
        elif count < 0:
            lines.append(t("m09.preview.pending_unknown"))
        elif count == 0:
            lines.append(t("m09.preview.pending_none"))
        else:
            lines.append(t("m09.preview.pending_count", count=count))
        lines.append("")
        if not repo_issues["release_known"]:
            lines.append(t("m09.preview.release_unknown_note", name=pretty_name()))
            lines.append("")
        lines.append(
            t("m09.preview.will_do") if repo_issues["release_known"]
            else t("m09.preview.will_do_no_repo")
        )
        if count > 0 or checking:
            lines.append(t("m09.preview.long_running"))
        return "\n".join(lines)

    def apply(self, params=None, progress: ProgressCallback | None = None) -> ApplyResult:
        env = {"DEBIAN_FRONTEND": "noninteractive"}
        failed: list[str] = []

        # 1. Repository sağlığını düzelt
        if progress:
            progress(t("m09.apply.checking_repos"))
        log.info("Repository sağlığı kontrol ediliyor...")

        if not fix_repositories(progress):
            return ApplyResult(
                False,
                t("m09.apply.repo_fix_failed"),
                details=t("m09.apply.repo_fix_failed_details"),
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
                progress(t("m09.apply.step_header", label=label))
            log.info("%s çalıştırılıyor…", label)
            result = run_cmd_stream(cmd, progress=progress, env=env, timeout=3600)
            if not result.ok:
                failed.append(label)
                if progress:
                    progress(t("m09.apply.step_failed", label=label, code=result.returncode))
                log.error("%s başarısız", label)

        # Paket envanteri değişti (veya değişmiş olabilir) — cache'i tazele.
        _pending_updates.invalidate()

        if failed:
            return ApplyResult(
                False,
                t("m09.apply.some_failed"),
                details=t("m09.apply.some_failed_details", steps=", ".join(failed)),
            )
        return ApplyResult(True, t("m09.apply.done"))

    def launch_pardus_update_gui_action(self, params: dict | None = None) -> ApplyResult:
        """Pardus Güncelleyici GUI'sini kullanıcının X oturumunda açar."""
        binary = Path("/usr/bin/pardus-update")
        if not binary.exists():
            return ApplyResult(
                False,
                t("m09.gui.not_found"),
                details=t("m09.gui.not_found_details", binary=binary),
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
                t("m09.gui.start_failed"),
                details=str(exc),
            )

        return ApplyResult(
            True,
            t("m09.gui.opened", user=user),
            details=t("m09.gui.opened_details"),
        )
