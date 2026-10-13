"""Root oturumu uyarısı.

TiHA root parolası atadığında, root olarak açılan her etkileşimli
oturumda (SSH, ``su -``, ``sudo -i``, düz ``su``) terminalde kırmızı
başlıklı bir uyarı gösterilir.

İki parçadan oluşur:

* ``/etc/profile.d/tiha-root-banner.sh`` — giriş kabukları (SSH, ``su -``,
  ``sudo -i``) bunu ``/etc/profile`` üzerinden okur. POSIX ``sh`` ile de
  çalışacak biçimde yazılır.
* ``/root/.bashrc`` içine işaretli tek satır — giriş kabuğu olmayan düz
  ``su`` ``/etc/profile``'ı okumaz; bu satır aynı betiği çağırır.

Uyarı yalnız etkileşimli ve terminale bağlı oturumda basılır; ``scp``,
``rsync`` gibi komut çalıştıran bağlantılar etkilenmez. Aynı oturumda iki
kez basılmaması için bir ortam değişkeni kullanılır.
"""

from __future__ import annotations

from pathlib import Path

from .i18n import t
from .logger import get_logger

log = get_logger(__name__)

PROFILE_SCRIPT = Path("/etc/profile.d/tiha-root-banner.sh")
ROOT_BASHRC = Path("/root/.bashrc")
BASHRC_MARKER = "# tiha-root-banner"
BASHRC_LINE = (
    f"[ -r {PROFILE_SCRIPT} ] && . {PROFILE_SCRIPT}  {BASHRC_MARKER}"
)

# Kırmızı zeminli başlık ve alt şerit genişliği (karakter).
_WIDTH = 68
_RED_BAR = "\\033[1;97;41m"
_BOLD = "\\033[1m"
_YELLOW = "\\033[33m"
_RESET = "\\033[0m"


def _sh_quote(text: str) -> str:
    """Tek tırnak içinde güvenli sh metni."""
    return "'" + text.replace("'", "'\\''") + "'"


def banner_text() -> str:
    """printf %b'ye verilecek, ANSI renk kodlu uyarı metni."""
    title = t("core.root_banner.title")
    bullets = [
        t("core.root_banner.bullet_share"),
        t("core.root_banner.bullet_accounts"),
        t("core.root_banner.bullet_exit"),
    ]
    lines = [
        "",
        f"{_RED_BAR}  {title.ljust(_WIDTH - 2)}{_RESET}",
        "",
        f"   {_BOLD}{t('core.root_banner.lead')}{_RESET}",
        "",
        *[f"   {_YELLOW}• {b}{_RESET}" for b in bullets],
        "",
        f"{_RED_BAR}{' ' * _WIDTH}{_RESET}",
        "",
    ]
    return "\\n".join(lines) + "\\n"


def script_content() -> str:
    return (
        "# TiHA — root oturumu uyarısı. TiHA'nın Yerel hesaplar adımı root\n"
        "# parolası atadığında kurulur; adım geri alınınca kaldırılır.\n"
        "if [ \"$(id -u)\" = 0 ] && [ -z \"${TIHA_ROOT_BANNER_SHOWN:-}\" ]; then\n"
        "    case $- in\n"
        "        *i*)\n"
        "            if [ -t 1 ]; then\n"
        "                TIHA_ROOT_BANNER_SHOWN=1\n"
        "                export TIHA_ROOT_BANNER_SHOWN\n"
        f"                printf '%b' {_sh_quote(banner_text())}\n"
        "            fi\n"
        "            ;;\n"
        "    esac\n"
        "fi\n"
    )


def is_installed() -> bool:
    return PROFILE_SCRIPT.exists()


def install() -> bool:
    """Betiği yazar ve /root/.bashrc'ye çağrı satırını ekler."""
    try:
        PROFILE_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
        PROFILE_SCRIPT.write_text(script_content(), encoding="utf-8")
        PROFILE_SCRIPT.chmod(0o644)
    except OSError as exc:
        log.error("Root uyarısı yazılamadı %s: %s", PROFILE_SCRIPT, exc)
        return False
    try:
        current = ROOT_BASHRC.read_text(encoding="utf-8") if ROOT_BASHRC.exists() else ""
        if BASHRC_MARKER not in current:
            sep = "" if not current or current.endswith("\n") else "\n"
            ROOT_BASHRC.write_text(current + sep + BASHRC_LINE + "\n", encoding="utf-8")
    except OSError as exc:
        # Giriş kabuklarında uyarı yine çıkar; yalnız düz `su` kapsam dışı kalır.
        log.warning("/root/.bashrc güncellenemedi: %s", exc)
    return True


def remove() -> bool:
    """Betiği ve /root/.bashrc'deki çağrı satırını kaldırır."""
    ok = True
    try:
        if PROFILE_SCRIPT.exists():
            PROFILE_SCRIPT.unlink()
    except OSError as exc:
        log.error("Root uyarısı silinemedi %s: %s", PROFILE_SCRIPT, exc)
        ok = False
    try:
        if ROOT_BASHRC.exists():
            lines = ROOT_BASHRC.read_text(encoding="utf-8").splitlines(keepends=True)
            kept = [line for line in lines if BASHRC_MARKER not in line]
            if len(kept) != len(lines):
                ROOT_BASHRC.write_text("".join(kept), encoding="utf-8")
    except OSError as exc:
        log.warning("/root/.bashrc temizlenemedi: %s", exc)
        ok = False
    return ok
