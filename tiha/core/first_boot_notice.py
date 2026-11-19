"""Klonun ilk açılışında yapılanları ekranda gösteren pencere.

İmajdan çıkan tahta ilk açılışında arka planda birçok iş yapar (makine
kimliği, SSH anahtarları, tahta adı, Ahenk kaydı, BIOS parolası, TiHA
kaydının gönderilmesi...). Bunların sonucu yalnız günlüklerde kalmasın
diye imaj öncesi temizlik bir pencere kurar:

* Klon kimlik servisi tahtayı klon olarak tanıyınca ``/run/tiha``
  altına bir işaret bırakır (``CLONE_MARKER``). İşaret geçici dosya
  sisteminde durur; yalnız o açılışta vardır.
* ``tiha-ilk-acilis.service`` bu işaret varsa ekran (LightDM'in X
  sunucusu) açılınca pencereyi gösterir. Pencere root olarak, LightDM'in
  X yetki dosyasıyla açılır; giriş ekranında da açık bir oturumda da
  görünür.
* Pencere ilk açılış servislerinin durumunu systemd'den canlı okur
  (bekliyor, sürüyor, tamam, hata, gerekmedi) ve her birinin günlüğe
  yazdığı son satırı sonuç olarak gösterir. Hepsi bitince 5 saniye geri
  sayıp kendiliğinden kapanır.

Kaynak tahtada ve klonun sonraki açılışlarında işaret oluşmadığı için
pencere açılmaz.
"""

from __future__ import annotations

import json
from pathlib import Path

from .i18n import t
from .logger import get_logger
from .utils import run_cmd

log = get_logger(__name__)

NOTICE_SCRIPT = Path("/usr/local/sbin/tiha-ilk-acilis.py")
NOTICE_SERVICE = Path("/etc/systemd/system/tiha-ilk-acilis.service")
CLONE_MARKER = Path("/run/tiha/ilk-acilis-klon")
LOG_TAG = "tiha-ilk-acilis"
COUNTDOWN_SECONDS = 5
# Ekranın açılması en çok bu kadar beklenir.
DISPLAY_WAIT_SECONDS = 300
# Pencere açıldıktan sonra servislerin bitmesi en çok bu kadar beklenir;
# süre dolunca bitmeyenler "arka planda sürüyor" diye gösterilip geri
# sayım başlar.
MAX_WAIT_SECONDS = 240


def _tracked_units() -> list[tuple[str, str]]:
    """Pencerede izlenen servisler ve başlıkları (gösterim sırasıyla).

    Bir servis tahtada kurulu ve etkin değilse pencere onu listelemez.
    """
    from ..modules.m03_otp_secrets import GREETER_SERVICE_PATH
    from ..modules.m08_hostname import FIRST_BOOT_SERVICE as HOSTNAME_SERVICE
    from ..modules.m10_image_sanitize import IDENTITY_SERVICE, REGEN_SSH_SERVICE
    from ..modules.m12_ahenk_reset import RECLAIM_SERVICE
    from ..modules.m14_bios_password import FIRST_BOOT_SERVICE as BIOS_SERVICE
    from ..modules.m15_wake_on_lan import WOL_SERVICE
    from .apply_log import SENDER_SERVICE
    from .paths import BOOT_WIPE_SERVICE

    order = [
        (IDENTITY_SERVICE, t("m10.notice.unit.identity")),
        (REGEN_SSH_SERVICE, t("m10.notice.unit.ssh_keys")),
        (HOSTNAME_SERVICE, t("m10.notice.unit.hostname")),
        (BOOT_WIPE_SERVICE, t("m10.notice.unit.password_wipe")),
        (RECLAIM_SERVICE, t("m10.notice.unit.ahenk")),
        (BIOS_SERVICE, t("m10.notice.unit.bios")),
        (WOL_SERVICE, t("m10.notice.unit.wake_on_lan")),
        (GREETER_SERVICE_PATH, t("m10.notice.unit.greeter_cache")),
        (SENDER_SERVICE, t("m10.notice.unit.apply_log")),
    ]
    return [(path.name, title) for path, title in order]


def _texts() -> dict[str, str]:
    # Yer tutucular ({done}, {name}...) betikte doldurulur.
    return {
        "window_title": t("m10.notice.window_title"),
        "eyebrow": t("m10.notice.eyebrow"),
        "headline": t("m10.notice.headline"),
        "subtitle": t("m10.notice.subtitle"),
        "pending": t("m10.notice.pending"),
        "running": t("m10.notice.running"),
        "ok": t("m10.notice.ok"),
        "failed": t("m10.notice.failed"),
        "skipped": t("m10.notice.skipped"),
        "background": t("m10.notice.background"),
        "progress": t("m10.notice.progress", done="{done}", total="{total}"),
        "working": t("m10.notice.working"),
        "all_done": t("m10.notice.all_done"),
        "done_with_errors": t("m10.notice.done_with_errors", count="{count}"),
        "closing": t("m10.notice.closing", seconds="{seconds}"),
        "close": t("m10.notice.close"),
        "hostname_detail": t("m10.notice.hostname_detail", name="{name}"),
    }


def render_script() -> str:
    return (
        SCRIPT_TEMPLATE
        .replace("@@UNITS@@", json.dumps(_tracked_units(), ensure_ascii=False))
        .replace("@@TEXTS@@", json.dumps(_texts(), ensure_ascii=False))
        .replace("@@MARKER@@", str(CLONE_MARKER))
        .replace("@@TAG@@", LOG_TAG)
        .replace("@@COUNTDOWN@@", str(COUNTDOWN_SECONDS))
        .replace("@@DISPLAY_WAIT@@", str(DISPLAY_WAIT_SECONDS))
        .replace("@@MAX_WAIT@@", str(MAX_WAIT_SECONDS))
    )


SERVICE_CONTENT = f"""[Unit]
Description=TiHA — Klonun ilk açılışında yapılanları ekranda göster
After=display-manager.service tiha-clone-identity.service
ConditionPathExists={CLONE_MARKER}

[Service]
Type=simple
ExecStart=/usr/bin/python3 {NOTICE_SCRIPT}

[Install]
WantedBy=graphical.target
"""


def install() -> bool:
    """Pencere betiğini ve servisini kurar (imaj öncesi temizlik)."""
    try:
        NOTICE_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
        NOTICE_SCRIPT.write_text(render_script(), encoding="utf-8")
        NOTICE_SCRIPT.chmod(0o755)
        NOTICE_SERVICE.write_text(SERVICE_CONTENT, encoding="utf-8")
    except OSError as exc:
        log.warning("İlk açılış penceresi kurulamadı: %s", exc)
        return False
    run_cmd(["systemctl", "daemon-reload"])
    return run_cmd(["systemctl", "enable", NOTICE_SERVICE.name]).ok


SCRIPT_TEMPLATE = r'''#!/usr/bin/python3
# TiHA — klonun ilk açılışında arka planda yapılanları ekranda gösterir.
# Klon kimlik servisi tahtayı klon olarak tanıyınca @@MARKER@@ oluşur;
# servis yalnız o zaman çalışır. Pencere ilk açılış servislerinin durumunu
# systemd'den canlı okur, hepsi bitince birkaç saniye geri sayıp kapanır.
# "--deneme": servislere bakmadan örnek bir akış gösterir (görünümü
# denemek için).
import json
import os
import socket
import subprocess
import sys
import time

UNITS = json.loads(r"""@@UNITS@@""")
T = json.loads(r"""@@TEXTS@@""")
TAG = "@@TAG@@"
COUNTDOWN = @@COUNTDOWN@@
DISPLAY_WAIT = @@DISPLAY_WAIT@@
MAX_WAIT = @@MAX_WAIT@@
DEMO = "--deneme" in sys.argv

PENDING, RUNNING, OK, FAILED, SKIPPED, BACKGROUND = (
    "pending", "running", "ok", "failed", "skipped", "background")
FINAL = (OK, FAILED, SKIPPED)
XAUTH_CANDIDATES = ("/run/lightdm/root/:0", "/var/run/lightdm/root/:0")


def logline(msg):
    subprocess.run(["logger", "-t", TAG, "--", msg], check=False)


# --- servis durumu -----------------------------------------------------

def unit_props(unit):
    props = {}
    try:
        out = subprocess.run(
            ["systemctl", "show", unit, "--property=LoadState,ActiveState,"
             "Result,ConditionResult,ConditionTimestampMonotonic,"
             "UnitFileState"],
            capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return props
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            props[k] = v
    return props


def unit_state(props):
    active = props.get("ActiveState", "")
    if active in ("activating", "deactivating", "reloading"):
        return RUNNING
    if active == "failed" or props.get("Result", "success") != "success":
        return FAILED
    if props.get("ConditionTimestampMonotonic", "0") in ("0", ""):
        return PENDING
    if props.get("ConditionResult") == "no":
        return SKIPPED
    return OK


def last_line(unit):
    try:
        out = subprocess.run(
            ["journalctl", "-b", "-o", "cat", "-n", "5", "--no-pager",
             "_SYSTEMD_UNIT=" + unit],
            capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if not lines:
        return ""
    text = lines[-1]
    return text if len(text) <= 110 else text[:107] + "..."


def tracked():
    """Bu tahtada kurulu ve etkin olan izlenecek servisler."""
    rows = []
    for unit, title in UNITS:
        props = unit_props(unit)
        if props.get("LoadState") != "loaded":
            continue
        if props.get("UnitFileState") not in ("enabled", "enabled-runtime", "static"):
            continue
        rows.append((unit, title))
    return rows


class DemoSource:
    """--deneme: servisler yerine zamanla ilerleyen örnek bir akış."""

    def __init__(self):
        self.start = time.monotonic()
        self.rows = [(u, ti) for u, ti in UNITS]

    def state(self, index):
        age = time.monotonic() - self.start
        begin = index * 0.8
        if age < begin:
            return PENDING, ""
        if age < begin + 1.4:
            return RUNNING, ""
        if index == 5:
            return SKIPPED, ""
        return OK, "Örnek sonuç satırı"


# --- ekran ----------------------------------------------------------------

def wait_for_display():
    """LightDM'in X sunucusu açılana kadar bekler; ortam değişkenlerini döner."""
    deadline = time.monotonic() + DISPLAY_WAIT
    while time.monotonic() < deadline:
        xauth = next((p for p in XAUTH_CANDIDATES if os.path.exists(p)), "")
        if os.path.exists("/tmp/.X11-unix/X0") and xauth:
            env = dict(os.environ, DISPLAY=":0", XAUTHORITY=xauth, NO_AT_BRIDGE="1")
            ok = subprocess.run(["xset", "-q"], env=env, capture_output=True,
                                timeout=5).returncode == 0 if _has("xset") else True
            if ok:
                return env
        time.sleep(1)
    return None


def _has(cmd):
    return any(os.access(os.path.join(d, cmd), os.X_OK)
               for d in os.environ.get("PATH", "/usr/bin:/bin").split(":"))


def wake_screen(env):
    if not _has("xset"):
        return
    for args in (["xset", "dpms", "force", "on"], ["xset", "s", "reset"]):
        subprocess.run(args, env=env, capture_output=True, timeout=5, check=False)


CSS = b"""
.tiha-notice { background-color: #ffffff; border: 1px solid #b9c3cf; }
.tiha-header { background-color: #1f3b5c; padding: 20px 26px 18px 26px; }
.tiha-eyebrow { color: #9fb4cc; font-size: 8.5pt; font-weight: bold; letter-spacing: 1px; }
.tiha-headline { color: #ffffff; font-size: 15pt; font-weight: bold; }
.tiha-subtitle { color: #d7e0ea; font-size: 9.5pt; }
.tiha-host { color: #ffffff; background-color: rgba(255,255,255,0.12);
             border-radius: 3px; padding: 3px 9px; font-size: 9pt; font-weight: bold; }
.tiha-row { padding: 9px 26px; border-bottom: 1px solid #e7ebf0; }
.tiha-row-title { color: #1b2836; font-size: 10.5pt; }
.tiha-row-detail { color: #5f6f80; font-size: 8.5pt; }
.tiha-badge { min-width: 22px; min-height: 22px; border-radius: 11px;
              font-size: 9pt; font-weight: bold; color: #ffffff; }
.tiha-badge-ok { background-color: #2e7d4f; }
.tiha-badge-failed { background-color: #b3261e; }
.tiha-badge-pending { background-color: #ffffff; color: #9aa6b2; box-shadow: inset 0 0 0 1.5px #c3ccd6; }
.tiha-badge-skipped { background-color: #e3e8ee; color: #7a8796; }
.tiha-badge-background { background-color: #c77c02; }
.tiha-status { font-size: 9pt; font-weight: bold; }
.tiha-status-ok { color: #2e7d4f; }
.tiha-status-failed { color: #b3261e; }
.tiha-status-running { color: #1f3b5c; }
.tiha-status-pending, .tiha-status-skipped { color: #8794a3; }
.tiha-status-background { color: #a86802; }
.tiha-footer { background-color: #f3f5f8; padding: 12px 26px 14px 26px;
               border-top: 1px solid #dde3ea; }
.tiha-summary { color: #33414f; font-size: 9.5pt; }
.tiha-countdown { color: #1f3b5c; font-size: 9.5pt; font-weight: bold; }
progressbar.tiha-progress trough { min-height: 4px; background-color: #dfe5ec;
                                   border: none; border-radius: 2px; }
progressbar.tiha-progress progress { min-height: 4px; background-color: #1f3b5c;
                                     border: none; border-radius: 2px; }
button.tiha-close { background-image: none; background-color: #1f3b5c; color: #ffffff;
                    border: 1px solid #1f3b5c; border-radius: 3px; padding: 4px 18px;
                    box-shadow: none; text-shadow: none; }
button.tiha-close label { color: #ffffff; }
button.tiha-close:hover { background-color: #2b4e78; }
"""

BADGE_TEXT = {OK: "✓", FAILED: "✕", PENDING: "•",
              SKIPPED: "–", BACKGROUND: "…"}


def run_window(env, rows, demo):
    os.environ.update(env)
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk

    if not Gtk.init_check(sys.argv)[0]:
        logline("Ekrana bağlanılamadı; pencere gösterilmedi.")
        return None

    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def label(text, cls, xalign=0.0, wrap=False):
        lb = Gtk.Label(label=text)
        lb.set_xalign(xalign)
        lb.get_style_context().add_class(cls)
        if wrap:
            lb.set_line_wrap(True)
            lb.set_max_width_chars(60)
        return lb

    win = Gtk.Window(title=T["window_title"])
    win.set_decorated(False)
    win.set_keep_above(True)
    win.set_skip_taskbar_hint(True)
    win.set_type_hint(Gdk.WindowTypeHint.DIALOG)
    win.set_position(Gtk.WindowPosition.CENTER_ALWAYS)
    win.set_size_request(640, -1)
    win.set_resizable(False)
    win.get_style_context().add_class("tiha-notice")

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    win.add(outer)

    header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    header.get_style_context().add_class("tiha-header")
    header.pack_start(label(T["eyebrow"].upper(), "tiha-eyebrow"), False, False, 0)
    top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    top.pack_start(label(T["headline"], "tiha-headline"), True, True, 0)
    host = label(socket.gethostname(), "tiha-host", 0.5)
    host.set_valign(Gtk.Align.CENTER)
    top.pack_end(host, False, False, 0)
    header.pack_start(top, False, False, 2)
    header.pack_start(label(T["subtitle"], "tiha-subtitle", wrap=True), False, False, 2)
    outer.pack_start(header, False, False, 0)

    widgets = []
    for unit, title in rows:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row.get_style_context().add_class("tiha-row")
        icon = Gtk.Stack()
        icon.set_size_request(22, 22)
        icon.set_valign(Gtk.Align.CENTER)
        spinner = Gtk.Spinner()
        badge = label("", "tiha-badge", 0.5)
        badge.set_justify(Gtk.Justification.CENTER)
        icon.add_named(spinner, "spin")
        icon.add_named(badge, "badge")
        row.pack_start(icon, False, False, 0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        texts.set_valign(Gtk.Align.CENTER)
        texts.pack_start(label(title, "tiha-row-title"), False, False, 0)
        detail = label("", "tiha-row-detail", wrap=True)
        detail.set_no_show_all(True)
        texts.pack_start(detail, False, False, 0)
        row.pack_start(texts, True, True, 0)
        status = label("", "tiha-status", 1.0)
        status.set_valign(Gtk.Align.CENTER)
        row.pack_end(status, False, False, 0)
        outer.pack_start(row, False, False, 0)
        widgets.append({"unit": unit, "title": title, "icon": icon,
                        "spinner": spinner, "badge": badge, "detail": detail,
                        "status": status, "state": None, "text": ""})

    footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    footer.get_style_context().add_class("tiha-footer")
    progress = Gtk.ProgressBar()
    progress.get_style_context().add_class("tiha-progress")
    footer.pack_start(progress, False, False, 0)
    bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
    summary = label(T["working"], "tiha-summary")
    bottom.pack_start(summary, True, True, 0)
    countdown = label("", "tiha-countdown", 1.0)
    bottom.pack_start(countdown, False, False, 0)
    close = Gtk.Button(label=T["close"])
    close.get_style_context().add_class("tiha-close")
    close.connect("clicked", lambda *_: Gtk.main_quit())
    bottom.pack_end(close, False, False, 0)
    footer.pack_start(bottom, False, False, 0)
    outer.pack_end(footer, False, False, 0)

    state = {"remaining": None, "started": time.monotonic(), "result": {}}
    hostname_units = {u for u, _ in UNITS if u.startswith("tiha-hostname")}

    def show(w, st, text):
        if st == w["state"] and text == w["text"]:
            return
        w["state"], w["text"] = st, text
        w["status"].set_text(T[st])
        sctx = w["status"].get_style_context()
        bctx = w["badge"].get_style_context()
        for s in (PENDING, RUNNING, OK, FAILED, SKIPPED, BACKGROUND):
            sctx.remove_class("tiha-status-" + s)
            bctx.remove_class("tiha-badge-" + s)
        sctx.add_class("tiha-status-" + st)
        if st == RUNNING:
            w["spinner"].start()
            w["icon"].set_visible_child_name("spin")
        else:
            w["spinner"].stop()
            bctx.add_class("tiha-badge-" + st)
            w["badge"].set_text(BADGE_TEXT[st])
            w["icon"].set_visible_child_name("badge")
        if text:
            w["detail"].set_text(text)
            w["detail"].show()
        else:
            w["detail"].hide()

    def refresh():
        timed_out = time.monotonic() - state["started"] > MAX_WAIT
        for i, w in enumerate(widgets):
            if w["state"] in FINAL:
                continue
            if demo:
                st, text = demo.state(i)
            else:
                st = unit_state(unit_props(w["unit"]))
                # Biten birim bellekten atılırsa systemd onu hiç
                # çalışmamış gibi gösterir; az önce sürüyorsa bitmiştir.
                if st == PENDING and w["state"] == RUNNING:
                    st = OK
                text = ""
                if st in (OK, FAILED):
                    if w["unit"] in hostname_units:
                        text = T["hostname_detail"].format(name=socket.gethostname())
                        host.set_text(socket.gethostname())
                    else:
                        text = last_line(w["unit"])
            if timed_out and st not in FINAL:
                st = BACKGROUND
            show(w, st, text)
        done = sum(1 for w in widgets if w["state"] in FINAL)
        progress.set_fraction(done / len(widgets) if widgets else 1.0)
        return all(w["state"] in FINAL + (BACKGROUND,) for w in widgets)

    def finish():
        failed = sum(1 for w in widgets if w["state"] == FAILED)
        summary.set_text(T["done_with_errors"].format(count=failed)
                         if failed else T["all_done"])
        state["remaining"] = COUNTDOWN
        countdown.set_text(T["closing"].format(seconds=COUNTDOWN))
        GLib.timeout_add(1000, count_down)

    def count_down():
        raise_window()
        state["remaining"] -= 1
        if state["remaining"] <= 0:
            Gtk.main_quit()
            return False
        countdown.set_text(T["closing"].format(seconds=state["remaining"]))
        return True

    def raise_window():
        # Giriş ekranında pencere yöneticisi yoktur; giriş ekranının tam
        # ekran penceresi sonradan açılırsa bu pencerenin üstüne biner.
        gdk_window = win.get_window()
        if gdk_window is not None:
            gdk_window.raise_()

    def poll():
        raise_window()
        if refresh():
            finish()
            return False
        done = sum(1 for w in widgets if w["state"] in FINAL)
        summary.set_text(T["progress"].format(done=done, total=len(widgets)))
        return True

    win.show_all()
    if poll():
        GLib.timeout_add(500, poll)
    Gtk.main()
    return {w["unit"]: w["state"] for w in widgets}


def main():
    demo = DemoSource() if DEMO else None
    rows = demo.rows if demo else tracked()
    if not rows:
        return 0
    if DEMO and os.environ.get("DISPLAY"):
        env = dict(os.environ)
    else:
        env = wait_for_display()
    if not env:
        logline("Ekran açılmadı; ilk açılış penceresi gösterilmedi.")
        return 0
    wake_screen(env)
    result = run_window(env, rows, demo)
    if result is not None and not demo:
        logline("İlk açılış penceresi gösterildi: " + ", ".join(
            f"{unit}={st}" for unit, st in result.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''
