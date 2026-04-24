"""TiHA uygulama giriş noktası.

Ön yetki ve bağımlılık denetimlerini yapar, GTK ana penceresini açar.
"""

from __future__ import annotations

import os
import sys

from . import __version__
from .core import console
from .core.logger import get_logger
from .core.paths import LOG_ROOT, ensure_runtime_dirs
from .core.privilege import require_root_and_admin

log = get_logger(__name__)


def _redirect_stderr_to_log() -> None:
    """Süreç stderr'ini log dosyasına yönlendirir.

    Neden? Kök yetkiyle çalışırken GTK/GLib/dconf, kullanıcı DBus
    oturumuna erişemediği için ``Error creating proxy``, ``dconf-WARNING``
    gibi teknik satırları stderr'e basıyor; bu son kullanıcının gördüğü
    terminal akışını kirletir. Satırları kaybetmemek için doğrudan
    ``/var/log/tiha/tiha-stderr.log`` dosyasına yönlendiriyoruz —
    gerekirse geliştirici tailer ile izleyebilir.

    ``TIHA_DEBUG`` tanımlıysa yönlendirme YAPILMAZ (ham stderr
    terminalde kalır).
    """
    if os.environ.get("TIHA_DEBUG"):
        return
    try:
        ensure_runtime_dirs()
    except OSError:
        return
    stderr_log = LOG_ROOT / "tiha-stderr.log"
    try:
        fd = os.open(
            str(stderr_log),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o644,
        )
        # Dosya başına TiHA oturum ayracı yaz (log okumayı kolaylaştırır)
        from datetime import datetime
        os.write(
            fd,
            f"\n----- TiHA oturum stderr başladı: "
            f"{datetime.now().isoformat(timespec='seconds')} (pid {os.getpid()}) -----\n"
            .encode("utf-8"),
        )
        # Python seviyesi
        sys.stderr.flush()
        # POSIX seviyesi — GTK/GLib gibi doğrudan fd 2'ye yazan bileşenler
        os.dup2(fd, 2)
        os.close(fd)
        # Python sys.stderr nesnesini de yeni fd'ye sardır
        sys.stderr = os.fdopen(2, "w", buffering=1)
    except OSError:
        # Yönlendirme başarısız olsa da uygulamayı engelleme
        pass


def main() -> int:
    """Süreç giriş noktası. Başarı durumunda ``0``, aksi hâlde >0 döner."""
    ok, reason = require_root_and_admin()
    if not ok:
        _emergency_dialog(reason)
        # Terminale de sade bir hata bas (debug değil)
        print(f"\n  HATA: {reason}\n", file=sys.stderr)
        return 2

    try:
        ensure_runtime_dirs()
    except OSError as exc:
        _emergency_dialog(f"Çalışma dizini oluşturulamadı: {exc}")
        print(f"\n  HATA: {exc}\n", file=sys.stderr)
        return 3

    console.banner_open("TiHA — Tahta İmaj Hazırlık Aracı", f"v{__version__}")
    console.info("Sihirbaz penceresi açılıyor…")

    # Terminali kirletecek GTK/GLib/dconf uyarılarını dosyaya yönlendir.
    # (Bundan önce tüm kullanıcıya-görür mesajlar çıktı.)
    _redirect_stderr_to_log()

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

    console.banner_close("Sihirbaz kapatıldı.")
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
