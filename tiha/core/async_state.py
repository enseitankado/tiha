"""Yeniden kullanılabilir thread-safe async değer önbelleği.

TiHA modülleri ``preview()`` veya sayfa açılışında bazen yavaş senkron
işler yapar: ``apt-get -s -q full-upgrade`` (~3 sn), GitHub'dan dosya
indirme (60 sn timeout), dpkg sorgusu, vb. Bu işler GTK ana iş
parçacığında doğrudan çağrıldığında UI donar.

``AsyncValue`` bu kalıbı tek bir sınıfta toplar: ilk istekte arka
planda bir worker thread başlar, sonuç cache'lenir; sonraki istekler
hemen cache'ten okur. Worker biterken kayıtlı tüm callback'ler
``GLib.idle_add`` aracılığıyla UI thread'inde tetiklenir.

Tipik kullanım::

    _ssh_installed = AsyncValue(lambda: _is_package_installed("openssh-server"))

    # Modülün prefetch metodu (sayfa açıldığında main_window çağırır)
    def prefetch_preview_state(self, on_ready) -> None:
        _ssh_installed.get_async(on_ready)

    # preview() içinde — bloke etmez
    def preview(self) -> str:
        cached = _ssh_installed.cached()
        if cached is None:
            return "openssh-server durumu kontrol ediliyor…"
        return "kurulu" if cached else "kurulu değil"
"""

from __future__ import annotations

import threading
from typing import Callable, Generic, TypeVar

from gi.repository import GLib

from .logger import get_logger

log = get_logger(__name__)

T = TypeVar("T")


class AsyncValue(Generic[T]):
    """Tek bir senkron hesabın thread-safe önbelleği.

    İlk çağrıda worker başlatır; sonraki eşzamanlı istekler aynı
    worker'a piggyback yapar. Sonuç gelince tüm bekleyen callback'ler
    UI thread'inde sırayla tetiklenir.
    """

    def __init__(self, compute: Callable[[], T], name: str | None = None) -> None:
        self._compute = compute
        self._name = name or getattr(compute, "__name__", "anon")
        self._lock = threading.Lock()
        self._cache: T | None = None
        self._in_progress = False
        self._callbacks: list[Callable[[T | None], object]] = []

    # ---- okuyucular ----------------------------------------------------

    def cached(self) -> T | None:
        """Cache değerini döner; cache yoksa None."""
        with self._lock:
            return self._cache

    def in_progress(self) -> bool:
        """Şu an worker çalışıyor mu?"""
        with self._lock:
            return self._in_progress

    # ---- async tetikleyici --------------------------------------------

    def get_async(self, callback: Callable[[T | None], object] | None = None) -> T | None:
        """Cache varsa hemen döner. Yoksa arka planda worker başlatır
        ve None döner. Worker bitince, varsa ``callback(value)``
        ``GLib.idle_add`` ile UI thread'inde tetiklenir.

        Aynı anda gelen birden fazla istek tek worker'a piggyback yapar.
        """
        with self._lock:
            if self._cache is not None:
                if callback is not None:
                    GLib.idle_add(callback, self._cache)
                return self._cache
            if callback is not None:
                self._callbacks.append(callback)
            if self._in_progress:
                return None
            self._in_progress = True

        def _worker() -> None:
            try:
                value: T | None = self._compute()
            except Exception as exc:
                log.warning("AsyncValue[%s] worker hatası: %s", self._name, exc)
                value = None
            with self._lock:
                self._cache = value
                self._in_progress = False
                callbacks = list(self._callbacks)
                self._callbacks.clear()
            for cb in callbacks:
                GLib.idle_add(cb, value)

        threading.Thread(
            target=_worker, daemon=True, name=f"AsyncValue[{self._name}]"
        ).start()
        return None

    # ---- yardımcılar --------------------------------------------------

    def invalidate(self) -> None:
        """Cache'i temizler; bir sonraki ``get_async`` yeniden hesaplar."""
        with self._lock:
            self._cache = None
