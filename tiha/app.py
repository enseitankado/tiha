"""TiHA uygulama giriş noktası.

Ön yetki ve bağımlılık denetimlerini yapar, GTK ana penceresini açar.
"""

from __future__ import annotations

import sys

from .core.logger import get_logger
from .core.paths import ensure_runtime_dirs
from .core.privilege import require_root_and_admin

log = get_logger(__name__)


def main() -> int:
    """Süreç giriş noktası. Başarı durumunda ``0``, aksi hâlde >0 döner."""
    ok, reason = require_root_and_admin()
    if not ok:
        _emergency_dialog(reason)
        return 2

    try:
        ensure_runtime_dirs()
    except OSError as exc:
        _emergency_dialog(f"Çalışma dizini oluşturulamadı: {exc}")
        return 3

    # GTK yüklemeleri main fonksiyonun içinde — import sırasında ekran
    # (DISPLAY) yoksa uygulamanın çökmemesi için.
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    from .ui.main_window import TiHAWindow

    window = TiHAWindow()
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    Gtk.main()
    return 0


def _emergency_dialog(message: str) -> None:
    """GTK mevcutsa dialog kutusu, değilse stderr ile uyarı."""
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        dlg = Gtk.MessageDialog(
            transient_for=None,
            modal=True,
            destroy_with_parent=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="TiHA başlatılamadı",
        )
        dlg.format_secondary_text(message)
        dlg.run()
        dlg.destroy()
    except Exception:
        print(f"TiHA başlatılamadı: {message}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
