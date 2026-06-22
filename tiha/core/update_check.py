"""GitHub'dan TiHA release bilgisini çek — sessiz, en fazla 3 sn süren kontrol.

**Sidebar güncelleme rozeti**'ni besler: çalışan kodun ``__version__``'ünden
daha yeni bir release var mı? Varsa ``UpdateInfo`` doldurulur. (Bootstrap'la
çalıştırıldığında her seferinde main'den indirildiği için bu durum nadirdir —
daha çok yerel `run-dev.sh` senaryosunda görünür.)

Ağ hatası, parse hatası veya zaman aşımı sessizce yutulur (uygulama
çalışmaya devam eder).
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import __version__
from .logger import get_logger

log = get_logger(__name__)

REPO = "enseitankado/tiha"
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
HTTP_TIMEOUT = 3
USER_AGENT = f"TiHA/{__version__} (+https://github.com/{REPO})"


@dataclass
class UpdateInfo:
    """Sidebar rozeti için — çalışan koddan yeni bir sürüm var."""

    latest_version: str        # "0.2.0"
    html_url: str              # Release sayfası
    current_version: str       # __version__
    # __version__'den yeni release'lerin sade gövdesi (en yeniden eskiye)
    body: str = ""
    newer_count: int = field(default=0)


@dataclass
class CheckResult:
    """Tek bir async kontrolün sonucu."""

    update: UpdateInfo | None = None    # sidebar badge için (None: güncel)


def _normalize(v: str) -> str:
    """'v0.1.0' → '0.1.0'."""
    return v.lstrip("vV").strip()


def _parse_version(v: str) -> tuple[int, ...]:
    """Semver-benzeri karşılaştırma için tuple döner. Hatalı parse → (0,)."""
    parts: list[int] = []
    for chunk in _normalize(v).split("."):
        # "0.2.0-rc1" gibi durumlar için ilk sayısal prefix
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        if not num:
            break
        parts.append(int(num))
    return tuple(parts) if parts else (0,)


def is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def _format_body(releases: list[dict[str, Any]]) -> str:
    """Release listesinden insan-okur metin oluşturur."""
    chunks: list[str] = []
    for r in releases:
        tag = _normalize(r.get("tag_name") or "")
        body = (r.get("body") or "").strip()
        title = f"v{tag}" if tag else (r.get("name") or "Sürüm")
        if body:
            chunks.append(f"### {title}\n\n{body}")
        else:
            chunks.append(f"### {title}\n\n(Bu sürüm için ayrıntı notu girilmemiş.)")
    return "\n\n".join(chunks)


def _fetch_releases_list() -> list[dict[str, Any]] | None:
    """Tüm yayınlanmış (draft/prerelease olmayan) release'leri yeniden eskiye
    sıralı döner. Hata durumunda None.
    """
    req = urllib.request.Request(
        RELEASES_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError, TimeoutError, OSError) as exc:
        log.debug("Releases çekilemedi: %s", exc)
        return None
    except Exception as exc:  # pragma: no cover — savunma amaçlı
        log.debug("Releases beklenmedik hata: %s", exc)
        return None

    if not isinstance(data, list):
        return None
    published = [
        r for r in data
        if isinstance(r, dict)
        and not r.get("draft")
        and not r.get("prerelease")
        and r.get("tag_name")
    ]
    published.sort(
        key=lambda r: _parse_version(r.get("tag_name", "")),
        reverse=True,
    )
    return published


def _analyze_for_badge(releases: list[dict[str, Any]]) -> UpdateInfo | None:
    """Sidebar rozeti için — `__version__`'den yeni release var mı?"""
    if not releases:
        return None
    latest = releases[0]
    latest_tag = _normalize(latest.get("tag_name", ""))
    if not latest_tag or not is_newer(latest_tag, __version__):
        return None
    newer = [
        r for r in releases
        if is_newer(_normalize(r.get("tag_name", "")), __version__)
    ]
    return UpdateInfo(
        latest_version=latest_tag,
        html_url=latest.get("html_url") or f"https://github.com/{REPO}/releases",
        current_version=__version__,
        body=_format_body(newer),
        newer_count=len(newer),
    )


def fetch_latest() -> UpdateInfo | None:
    """Yalnız badge analizi için kısa yol — release listesini çek + analiz."""
    releases = _fetch_releases_list()
    if releases is None:
        return None
    return _analyze_for_badge(releases)


def check_async(
    callback: Callable[[CheckResult], None],
) -> None:
    """Arka planda releases listesini çek, sidebar rozeti analizini yap.

    UI thread'inden çağrılır; callback de UI thread'inde çalıştırılır
    (GLib.idle_add ile). Ağ hatası vs.'de boş ``CheckResult`` ile çağrılır.
    """
    def worker():
        releases = _fetch_releases_list()
        if releases is None:
            result = CheckResult()
        else:
            result = CheckResult(update=_analyze_for_badge(releases))
        try:
            from gi.repository import GLib
            GLib.idle_add(lambda: callback(result) or False)
        except Exception:
            # GTK yoksa (test ortamı) direkt çağır
            callback(result)

    t = threading.Thread(target=worker, daemon=True, name="tiha-update-check")
    t.start()
