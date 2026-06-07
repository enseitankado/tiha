"""GitHub'da yeni TiHA sürümü var mı? — sessiz, en fazla 3 sn süren kontrol.

Sidebar'da rozet göstermek için kullanılır. Ağ hatası, parse hatası
veya zaman aşımı sessizce ``None`` döner; uygulama çalışmaya devam eder.

Repo varsayılan olarak ``enseitankado/tiha`` olarak sabittir.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .. import __version__
from .logger import get_logger

log = get_logger(__name__)

REPO = "enseitankado/tiha"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
HTTP_TIMEOUT = 3
USER_AGENT = f"TiHA/{__version__} (+https://github.com/{REPO})"


@dataclass
class UpdateInfo:
    """Yeni sürüm bilgisi."""

    latest_version: str        # "0.2.0"
    html_url: str              # Release sayfası
    current_version: str       # __version__


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


def fetch_latest() -> UpdateInfo | None:
    """GitHub releases/latest API çağrısı. Hata durumunda None."""
    req = urllib.request.Request(
        API_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError, TimeoutError, OSError) as exc:
        log.debug("Güncelleme kontrolü atlandı: %s", exc)
        return None
    except Exception as exc:  # pragma: no cover — savunma amaçlı
        log.debug("Güncelleme kontrolünde beklenmeyen hata: %s", exc)
        return None

    tag = body.get("tag_name") or body.get("name") or ""
    url = body.get("html_url") or f"https://github.com/{REPO}/releases"
    if not tag:
        return None
    latest = _normalize(tag)
    if not is_newer(latest, __version__):
        return None
    return UpdateInfo(
        latest_version=latest,
        html_url=url,
        current_version=__version__,
    )


def check_async(callback: Callable[[UpdateInfo | None], None]) -> None:
    """Arka planda kontrolü başlat, sonuç gelince callback'i çağır.

    UI thread'inden çağrılır; callback de UI thread'inde çalıştırılır
    (GLib.idle_add ile). Hata durumunda callback(None) ile çağrılır
    (UI taraf rozeti basitçe göstermez).
    """
    def worker():
        info = fetch_latest()
        try:
            from gi.repository import GLib
            GLib.idle_add(lambda: callback(info) or False)
        except Exception:
            # GTK yoksa (test ortamı) direkt çağır
            callback(info)

    t = threading.Thread(target=worker, daemon=True, name="tiha-update-check")
    t.start()
