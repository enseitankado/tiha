"""Terminal oturumuna yazılan kullanıcı-dostu profesyonel çıktı.

TiHA'nın çağırıldığı terminalde son kullanıcı, teknik/debug iç
mesajları görmemeli. Bu modül yalnızca sade ve anlaşılır satırlar
basar (sihirbaz aç/kapa bildirimi, çalıştırılan her adımın sonucu).

Renkler yalnızca bir TTY'ye yazıyorsak ve ``NO_COLOR`` ortam değişkeni
tanımlı değilse devreye girer; log dosyasına/dosya çıktısına
yönlendirildiğinde renksiz, sade metin üretir.
"""

from __future__ import annotations

import os
import sys

_USE_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _c(code: str) -> str:
    return code if _USE_COLOR else ""


RESET  = _c("\033[0m")
DIM    = _c("\033[2m")
BOLD   = _c("\033[1m")
RED    = _c("\033[31m")
GREEN  = _c("\033[32m")
YELLOW = _c("\033[33m")
BLUE   = _c("\033[34m")
CYAN   = _c("\033[36m")
GRAY   = _c("\033[90m")


def _write(text: str = "") -> None:
    try:
        print(text, flush=True)
    except (BrokenPipeError, OSError):
        pass


def banner_open(title: str, version: str = "") -> None:
    """Uygulama açılış banner'ı."""
    header = f"{title}  {version}" if version else title
    line = "─" * max(len(header) + 4, 40)
    _write()
    _write(f"  {BOLD}{BLUE}{header}{RESET}")
    _write(f"  {DIM}{line}{RESET}")
    _write()


def banner_close(message: str = "Sihirbaz kapatıldı.") -> None:
    _write()
    _write(f"  {DIM}{message}{RESET}")
    _write()


def info(text: str) -> None:
    _write(f"  {BLUE}▸{RESET} {text}")


def step(title: str) -> None:
    """Bir işlem adımı başlıyor."""
    _write(f"  {BLUE}▸{RESET} {BOLD}{title}{RESET}")


def ok(summary: str) -> None:
    _write(f"    {GREEN}✓{RESET} {summary}")


def fail(summary: str) -> None:
    _write(f"    {RED}✗{RESET} {summary}")


def undone(title: str) -> None:
    _write(f"  {YELLOW}↶{RESET} Geri alındı: {title}")


def note(text: str) -> None:
    _write(f"    {DIM}{text}{RESET}")
