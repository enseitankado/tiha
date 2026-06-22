#!/usr/bin/env python3
"""TiHA ekran görüntüsü üretici.

Sihirbazı kök yetkisi olmadan açar (yalnız UI render edilir, hiçbir
şey uygulanmaz); her sayfayı sırayla görünür yapıp PNG olarak
``docs/images/`` altına kaydeder.

Dosya adları sayfanın **modül kimliğinden** türetilir (sabit sıra
indeksi DEĞİL) — böylece modüller yeniden sıralandığında ya da yeni
adım eklendiğinde görüntüler doğru adla üretilmeye devam eder.

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


# Stack çocuk adı (welcome / modül id / summary) → dosya adı kökü.
# Numara sırası sihirbazdaki adım sırasıyla birebir aynıdır; README
# galerisi de bu adları kullanır.
SLUG_BY_NAME = {
    "welcome":                "00-hosgeldiniz",
    "m09_system_update":      "01-sistem-guncellemesi",
    "m01_initial_passwords":  "02-yerel-hesaplar",
    "m02_boot_password_wipe": "03-otomatik-parola-temizligi",
    "m03_otp_secrets":        "04-toplu-pin-anahtari",
    "m13_password_dialog":    "05-eba-qr-parola-diyalogu",
    "m04_ssh_server":         "06-ssh-sunucusu",
    "m05_samba_share":        "07-samba-dosya-paylasimi",
    "m06_remote_syslog":      "08-merkezi-log-sunucusu",
    "m07_time_sync":          "09-zaman-senkronizasyonu",
    "m08_hostname":           "10-dinamik-hostname",
    "m11_power_management":    "11-otomatik-kapanma",
    "m12_ahenk_reset":        "12-otomatik-ahenk-kaydi",
    "m14_bios_password":      "13-bios-yonetici-parolasi",
    "m10_image_sanitize":     "14-imaj-icin-sanitize",
    "summary":                "15-ozet",
}


def _pump(duration: float = 0.0) -> None:
    """GTK olay döngüsünü ``duration`` saniye boyunca çevirir.

    Her turda **tek** olay işleriz (``main_iteration_do(False)``);
    "tüm bekleyen olayları boşalt" yaklaşımı bir ``Gtk.Spinner``
    sürekli kare ürettiğinde (örn. m09 "kontrol ediliyor" satırı)
    ``events_pending()`` hiç False dönmediği için sonsuz döngüye
    girerdi. Zaman temelli sınır bunu engeller."""
    deadline = time.time() + duration
    while time.time() < deadline:
        if Gtk.events_pending():
            Gtk.main_iteration_do(False)
        else:
            time.sleep(0.01)


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


def _page_name(window: TiHAWindow, page) -> str | None:
    """Sayfanın stack içindeki adını döner (welcome / modül id / summary)."""
    try:
        return window.stack.child_get_property(page, "name")
    except Exception:
        mod = getattr(page, "module", None)
        return getattr(mod, "id", None)


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
    skipped: list[str] = []
    for idx, page in enumerate(pages):
        name = _page_name(window, page)
        slug = SLUG_BY_NAME.get(name)
        if slug is None:
            skipped.append(name or f"<idx {idx}>")
            print(f"  · {name} atlandı (slug eşlemesi yok)")
            continue
        print(f"[{idx + 1}/{len(pages)}] {name} → {slug}")
        window._show_page_index(idx)
        # Bazı önizlemeler arka planda apt simulate / curl / dpkg-query
        # çalıştırır; render'ın oturması için cömert bekle.
        _pump(1.6)
        if capture(window, OUT_DIR / f"{slug}.png"):
            captured += 1
    print(f"\n{captured} ekran görüntüsü {OUT_DIR} altına kaydedildi.")
    if skipped:
        print(f"Eşlenemeyen sayfalar: {', '.join(skipped)}")
    window.destroy()
    return 0


def _run() -> None:
    rc = main()
    GLib.idle_add(Gtk.main_quit)
    sys.exit(rc)


if __name__ == "__main__":
    GLib.idle_add(_run)
    Gtk.main()
