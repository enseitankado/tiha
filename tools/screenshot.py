#!/usr/bin/env python3
"""TiHA ekran görüntüsü üretici.

Sihirbazı kök yetkisi olmadan açar (yalnız UI render edilir, hiçbir
şey uygulanmaz); her sayfayı sırayla görünür yapıp PNG olarak
``docs/images/`` altına kaydeder.

Kullanım:
    python3 tools/screenshot.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(PROJECT))

# ---- Yetki & dizin yamaları (importtan ÖNCE) ----------------------------
# tiha.core.paths sabitlerini /tmp altına yönlendirip yetki kontrolünü
# devre dışı bırakırız — hiçbir gerçek sistem yazısı yapılmaz.
from tiha.core import paths as _paths  # noqa: E402

_TMP = Path("/tmp/tiha-screenshot")
_TMP.mkdir(parents=True, exist_ok=True)

_paths.VAR_ROOT = _TMP
_paths.LOG_ROOT = _TMP / "log"
_paths.ETC_ROOT = _TMP / "etc"
_paths.STATE_DIR = _TMP / "state"
_paths.JOURNAL_FILE = _TMP / "journal.json"
_paths.LOG_FILE = _TMP / "tiha.log"


def _ensure_runtime_dirs() -> None:
    for d in (_paths.VAR_ROOT, _paths.LOG_ROOT, _paths.ETC_ROOT, _paths.STATE_DIR):
        d.mkdir(parents=True, exist_ok=True)


_paths.ensure_runtime_dirs = _ensure_runtime_dirs

from tiha.core import privilege as _priv  # noqa: E402

_priv.require_root_and_admin = lambda: (True, "")


# ---- GTK -----------------------------------------------------------------
import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from tiha.ui.main_window import TiHAWindow  # noqa: E402


OUT_DIR = PROJECT / "docs" / "images"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# Sayfa indekslerine kısa slug eşlemeleri. Sırasıyla:
# 0=welcome, 1..11=modül sayfaları, 12=özet
SLUGS = [
    "01-hosgeldiniz",
    "02-sistem-guncellemesi",
    "03-yerel-hesaplar",
    "04-otomatik-parola-temizligi",
    "05-toplu-pin-anahtari",
    "06-ssh-sunucusu",
    "07-samba-dosya-paylasimi",
    "08-merkezi-log-sunucusu",
    "09-zaman-senkronizasyonu",
    "10-benzersiz-hostname",
    "11-guc-yonetimi",
    "12-imaj-icin-sanitize",
    "13-ozet",
]


def _pump(duration: float = 0.0) -> None:
    """GTK olay döngüsünü kısa süre çevirir."""
    deadline = time.time() + duration
    while True:
        while Gtk.events_pending():
            Gtk.main_iteration()
        if time.time() >= deadline:
            return
        time.sleep(0.02)


def capture(window: Gtk.Window, target: Path) -> bool:
    """Pencerenin geçerli görünümünü PNG olarak kaydeder."""
    _pump(0.6)  # düzen + render için bekle
    gdk_win = window.get_window()
    if gdk_win is None:
        print(f"  ! pencere hazır değil: {target.name}", file=sys.stderr)
        return False
    w = gdk_win.get_width()
    h = gdk_win.get_height()
    pb = Gdk.pixbuf_get_from_window(gdk_win, 0, 0, w, h)
    if pb is None:
        print(f"  ! pixbuf alınamadı: {target.name}", file=sys.stderr)
        return False
    pb.savev(str(target), "png", [], [])
    print(f"  ✓ {target.name} ({w}×{h})")
    return True


def main() -> int:
    window = TiHAWindow()
    window.connect("destroy", lambda *_: Gtk.main_quit())
    window.set_default_size(1180, 760)
    window.show_all()
    window.present()
    _pump(0.8)

    pages = window.pages
    print(f"Toplam sayfa: {len(pages)}")
    captured = 0
    for idx, slug in enumerate(SLUGS):
        if idx >= len(pages):
            print(f"  · {slug} atlandı (sayfa yok)")
            continue
        print(f"[{idx + 1}/{len(SLUGS)}] {slug}")
        window._show_page_index(idx)
        # m09 (sistem güncellemesi) ve m03 (pin anahtarı) önizlemeleri
        # apt simulate / curl indirme çalıştırır; biraz fazla bekle.
        _pump(1.0)
        if capture(window, OUT_DIR / f"{slug}.png"):
            captured += 1
    print(f"\n{captured}/{len(SLUGS)} ekran görüntüsü {OUT_DIR} altına kaydedildi.")
    window.destroy()
    return 0


def _run() -> None:
    rc = main()
    GLib.idle_add(Gtk.main_quit)
    sys.exit(rc)


if __name__ == "__main__":
    GLib.idle_add(_run)
    Gtk.main()
