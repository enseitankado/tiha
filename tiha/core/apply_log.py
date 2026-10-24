"""TiHA uygulama kaydı: dosyaya, sistem günlüğüne ve klonda Tiha-Mon'a.

TiHA'nın bu tahtada yaptığı her işlem (adım uygulama, geri alma, düğme
işlemleri) sonucu ve ayrıntısıyla iki yere yazılır:

* ``/var/lib/tiha/apply-log.jsonl`` — kalıcı kayıt. Dizin imaj
  temizliğinde korunur; dosya imajla bütün klonlara gider.
* Sistem günlüğü (journald/syslog) — program adı ``tiha``, tesis
  ``local5``, her satır "TiHA" ile başlar: ``journalctl -t tiha``.

Parolalar kayda girmez: parametreler ``redact_params`` ile maskelenir,
özet ve ayrıntı metinlerindeki parola değerleri ``***`` ile değiştirilir.

İmaj öncesi temizlik bir servis kurar (``tiha-apply-log-send.service``).
Klonun ilk açılışında (kaynak tahtanın MAC'lerinden hiçbiri yoksa) kaydın
tamamı klonun kendi sistem günlüğüne "TiHA imaj kaydı" olarak yazılır.
Merkezi log adımının profili ``local5``'i iletiyorsa (Bakım, Kapsamlı)
rsyslog bunları diskte bekleterek Tiha-Mon'a iletir; iletmiyorsa
(Güvenlik) servis kaydı ayrıca doğrudan Tiha-Mon'a gönderir. Böylece
Tiha-Mon'a her satır bir kez gider. Sunucuya ulaşılamazsa sonraki
açılışta yeniden denenir.
"""

from __future__ import annotations

import json
import socket
from datetime import datetime
from pathlib import Path

from .logger import get_logger
from .paths import RSYSLOG_CONF, STATE_DIR, VAR_ROOT

log = get_logger(__name__)

APPLY_LOG_FILE = VAR_ROOT / "apply-log.jsonl"
# Kaynak tahtanın MAC'leri. Klon kimlik servisi kendi imza dosyasını
# klonun MAC'leriyle değiştirdiği için gönderici ayrı bir kopya kullanır.
SOURCE_MAC_FILE = STATE_DIR / "imaged-mac-apply-log"
SENT_MARKER = VAR_ROOT / "apply-log.sent"
SENDER_SCRIPT = Path("/usr/local/sbin/tiha-apply-log-send.py")
SENDER_SERVICE = Path("/etc/systemd/system/tiha-apply-log-send.service")
SYSLOG_TAG = "tiha"

# Tek kaydın ayrıntısı en çok bu kadar karakter saklanır.
_MAX_DETAILS = 6000
# Dosya bu boyutu aşarsa en eski kayıtların yarısı atılır.
_MAX_FILE_BYTES = 2 * 1024 * 1024

# Kayıt satırlarını biçimleyen kod. Hem TiHA (canlı sistem günlüğü) hem
# klondaki gönderici betik aynı kodu kullanır; betiğe metin olarak gömülür.
LINES_CODE = r'''
ACTIONS = {"apply": "uygulandı", "undo": "geri alındı", "button": "düğme"}
STATUSES = {"ok": "BAŞARILI", "failed": "BAŞARISIZ", "skipped": "ATLANDI"}
MAX_DETAIL_LINES = 40


def entry_lines(e, prefix, origin=""):
    """Bir kaydın sistem günlüğü satırları: [(önem, metin), ...]."""
    status = e.get("status", "")
    sev = {"ok": 6, "failed": 3, "skipped": 5}.get(status, 6)
    step = f"{e['step']}. " if e.get("step") else ""
    action = ACTIONS.get(e.get("action"), e.get("action", ""))
    if e.get("action") == "button" and e.get("label"):
        action = f"düğme '{e['label']}'"
    dur = f" ({e['duration']} sn)" if e.get("duration") is not None else ""
    head = (f"{prefix} {step}{e.get('title') or e.get('module', '')} · {action} · "
            f"{STATUSES.get(status, status)}{dur} · {e.get('summary', '')}")
    out = [(sev, head + (f" · {origin}" if origin else ""))]
    if e.get("warning"):
        out.append((4, f"{prefix}   uyarı: {e['warning']}"))
    params = e.get("params") or {}
    if params:
        shown = ", ".join(f"{k}={v}" for k, v in params.items())
        out.append((sev, f"{prefix}   ayarlar: {shown}"))
    lines = [ln for ln in (e.get("details") or "").splitlines() if ln.strip()]
    for ln in lines[:MAX_DETAIL_LINES]:
        out.append((sev, f"{prefix}   | {ln}"))
    if len(lines) > MAX_DETAIL_LINES:
        out.append((sev, f"{prefix}   | … {len(lines) - MAX_DETAIL_LINES} satır daha"))
    return out
'''
_ns: dict = {}
exec(LINES_CODE, _ns)
entry_lines = _ns["entry_lines"]

_step_numbers: dict[str, int] | None = None


def _step_number(module_id: str) -> int | None:
    """Adımın sol menüdeki numarası."""
    global _step_numbers
    if _step_numbers is None:
        try:
            from ..modules import all_modules

            _step_numbers = {
                m.id: i for i, m in enumerate(all_modules(), start=1)
            }
        except Exception:  # noqa: BLE001 — numara yalnız bilgi amaçlı
            _step_numbers = {}
    return _step_numbers.get(module_id)


def record(
    *,
    module_id: str,
    title: str,
    action: str,
    status: str,
    summary: str = "",
    details: str = "",
    warning: str = "",
    params: dict | None = None,
    data: dict | None = None,
    duration: float | None = None,
    label: str = "",
) -> None:
    """Bir işlemi kayda ekler. Hata TiHA'yı durdurmaz.

    ``action``: ``apply`` | ``undo`` | ``button``.
    ``status``: ``ok`` | ``failed`` | ``skipped``.
    """
    try:
        from .. import __version__
        from .report_log import _secret_values, redact_params, scrub_text

        secrets = _secret_values(params, data)
        details = scrub_text(details or "", secrets)
        if len(details) > _MAX_DETAILS:
            details = details[:_MAX_DETAILS].rstrip() + "\n…"
        entry = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "tiha": __version__,
            "host": socket.gethostname(),
            "module": module_id,
            "step": _step_number(module_id),
            "title": title,
            "action": action,
            "label": label,
            "status": status,
            "summary": scrub_text(summary or "", secrets),
            "warning": scrub_text(warning or "", secrets),
            "details": details,
            "params": redact_params(module_id, params),
            "duration": round(duration, 2) if duration is not None else None,
        }
        _append(json.dumps(entry, ensure_ascii=False, default=str))
    except Exception as exc:  # noqa: BLE001
        log.warning("Uygulama kaydı yazılamadı: %s", exc)
        return
    _to_syslog(entry_lines(entry, "TiHA:"))


def _to_syslog(lines: list[tuple[int, str]]) -> None:
    """Satırları sistem günlüğüne yazar (program adı ``tiha``, local5)."""
    try:
        import syslog

        syslog.openlog(SYSLOG_TAG, 0, syslog.LOG_LOCAL5)
        for sev, text in lines:
            syslog.syslog(sev, text)
        syslog.closelog()
    except Exception as exc:  # noqa: BLE001
        log.warning("Sistem günlüğüne yazılamadı: %s", exc)


def _append(line: str) -> None:
    APPLY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    new = not APPLY_LOG_FILE.exists()
    with APPLY_LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    if new:
        APPLY_LOG_FILE.chmod(0o600)
    if APPLY_LOG_FILE.stat().st_size > _MAX_FILE_BYTES:
        lines = APPLY_LOG_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
        APPLY_LOG_FILE.write_text("".join(lines[len(lines) // 2:]), encoding="utf-8")


def entry_count() -> int:
    try:
        with APPLY_LOG_FILE.open(encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return 0


# --- Klonun ilk açılışında gönderim ------------------------------------------

SENDER_SCRIPT_CONTENT = r'''#!/usr/bin/python3
# TiHA — klonun ilk açılışında TiHA uygulama kaydını klonun sistem
# günlüğüne yazar ve Merkezi log sunucusuna (Tiha-Mon) ulaştırır.
# İmaj öncesi temizlik adımı kurar. Kaynak tahtada hiçbir şey yapmaz.
import json
import re
import socket
import sys
import syslog
import time
from datetime import datetime
from pathlib import Path

LOG = Path("@@LOG@@")
MACS = Path("@@MACS@@")
SENT = Path("@@SENT@@")
LOCAL_DONE = Path("@@SENT@@.local")
RSYSLOG = Path("@@RSYSLOG@@")
TAG = "@@TAG@@"
FACILITY = 21  # local5
WAIT_SECONDS = 60
@@LINES_CODE@@

def note(msg):
    syslog.openlog(TAG, 0, syslog.LOG_LOCAL5)
    syslog.syslog(syslog.LOG_NOTICE, msg if msg.startswith("TiHA") else f"TiHA: {msg}")
    print(f"[{TAG}] {msg}", file=sys.stderr)


def local_macs():
    macs = set()
    root = Path("/sys/class/net")
    for d in root.iterdir() if root.is_dir() else []:
        if d.name == "lo" or not (d / "device").exists():
            continue
        try:
            if (d / "addr_assign_type").read_text().strip() not in ("0", ""):
                continue
            mac = (d / "address").read_text().strip().lower()
        except OSError:
            continue
        if mac and mac != "00:00:00:00:00:00":
            macs.add(mac)
    return macs


def remote_config():
    try:
        return RSYSLOG.read_text(encoding="utf-8")
    except OSError:
        return ""


def target(text):
    host = re.search(r'target="([^"]+)"', text)
    port = re.search(r'port="(\d+)"', text)
    proto = re.search(r'protocol="(\w+)"', text)
    if not host:
        return None
    return (host.group(1), int(port.group(1)) if port else 514,
            (proto.group(1) if proto else "udp").lower())


def forwards_local5(text):
    # Profil satırları: "<seçici>   call tiha_remote". local5 bilgi
    # düzeyinde iletiliyorsa rsyslog kaydı zaten Tiha-Mon'a taşır.
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "call tiha_remote" not in line:
            continue
        for part in line.split()[0].split(";"):
            facs, _, pri = part.partition(".")
            if pri in ("*", "debug", "info") and (
                    "*" in facs.split(",") or "local5" in facs.split(",")):
                return True
    return False


def all_lines(entries):
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    total = len(entries)
    src = entries[-1].get("host", "?")
    yield 6, now, (f"TiHA imaj kaydı başlıyor: {total} işlem, kaynak tahta {src}, "
                   f"TiHA {entries[-1].get('tiha', '?')}, "
                   f"{entries[0].get('ts', '?')} – {entries[-1].get('ts', '?')}")
    for n, e in enumerate(entries, 1):
        ts = e.get("ts") or now
        origin = f"kaynak tahtada {ts.replace('T', ' ')[:16]} ({e.get('host', '?')})"
        for sev, text in entry_lines(e, f"TiHA imaj kaydı [{n}/{total}]:", origin):
            yield sev, ts, text
    yield 6, now, f"TiHA imaj kaydı bitti: {total} işlem."


def write_local(lines):
    syslog.openlog(TAG, 0, syslog.LOG_LOCAL5)
    for sev, _ts, text in lines:
        syslog.syslog(sev, text)
        time.sleep(0.003)
    syslog.closelog()


def frame(sev, ts, host, text):
    # RFC 5424: zaman damgası işlemin kaynak tahtada yapıldığı an.
    text = text.replace("\n", " ").strip()[:4000]
    return f"<{FACILITY * 8 + sev}>1 {ts} {host} {TAG} - - - {text}"


def send(dest, items):
    host, port, proto = dest
    if proto == "tcp":
        with socket.create_connection((host, port), timeout=15) as s:
            for item in items:
                s.sendall((item + "\n").encode("utf-8"))
                time.sleep(0.005)
    else:
        addr = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)[0]
        with socket.socket(addr[0], socket.SOCK_DGRAM) as s:
            for item in items:
                s.sendto(item.encode("utf-8")[:8000], addr[4])
                time.sleep(0.01)


def mark(path):
    path.write_text(datetime.now().astimezone().isoformat() + "\n")


def main():
    if SENT.exists():
        return 0
    try:
        source = {ln.strip().lower() for ln in MACS.read_text().split() if ln.strip()}
    except OSError:
        return 0
    deadline = time.monotonic() + WAIT_SECONDS
    mine = local_macs()
    while not mine and time.monotonic() < deadline:
        time.sleep(2)
        mine = local_macs()
    if not mine or mine & source:
        # Kaynak tahta (ya da ağ kartı yok): bir şey yapılmaz.
        return 0
    try:
        entries = [json.loads(ln) for ln in LOG.read_text(encoding="utf-8").splitlines()
                   if ln.strip()]
    except (OSError, ValueError) as exc:
        note(f"Uygulama kaydı okunamadı: {exc}")
        return 0
    if not entries:
        mark(SENT)
        return 0
    lines = list(all_lines(entries))

    # 1) Klonun kendi sistem günlüğüne (bir kez).
    if not LOCAL_DONE.exists():
        write_local(lines)
        mark(LOCAL_DONE)

    conf = remote_config()
    dest = target(conf)
    if dest is None:
        note(f"TiHA imaj kaydı ({len(entries)} işlem) sistem günlüğüne yazıldı; "
             "Merkezi log sunucusu ayarlı değil.")
        mark(SENT)
        return 0
    if forwards_local5(conf):
        # 2a) Log profili local5'i iletiyor: rsyslog diskte bekleterek iletir.
        note(f"TiHA imaj kaydı ({len(entries)} işlem) sistem günlüğüne yazıldı; "
             f"rsyslog {dest[0]}:{dest[1]} adresine iletecek.")
        mark(SENT)
        return 0

    # 2b) Profil iletmiyor (ör. Güvenlik): doğrudan gönder.
    host = socket.gethostname()
    items = [frame(sev, ts, host, text) for sev, ts, text in lines]
    last_exc = None
    while time.monotonic() < deadline + 240:
        try:
            send(dest, items)
            break
        except OSError as exc:
            last_exc = exc
            time.sleep(20)
    else:
        note(f"Tiha-Mon'a ulaşılamadı ({dest[0]}:{dest[1]}): {last_exc}; "
             "sonraki açılışta yeniden denenecek.")
        return 0
    mark(SENT)
    note(f"TiHA imaj kaydı ({len(entries)} işlem) {dest[0]}:{dest[1]}/{dest[2]} "
         "adresine doğrudan gönderildi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

SENDER_SERVICE_CONTENT = f"""[Unit]
Description=TiHA — Klonun ilk açılışında TiHA uygulama kaydını sistem günlüğüne yaz ve Tiha-Mon'a ulaştır
Wants=network-online.target
After=network-online.target tiha-first-boot-hostname.service tiha-clone-identity.service
ConditionPathExists={SOURCE_MAC_FILE}
ConditionPathExists=!{SENT_MARKER}

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 {SENDER_SCRIPT}
TimeoutStartSec=420

[Install]
WantedBy=multi-user.target
"""


def install_sender(source_macs: list[str]) -> bool:
    """Gönderici servisi kurar ve kaynak tahtanın MAC'lerini imzalar.

    Daha önce gönderilmiş işareti (bu tahta bir klonken kaldıysa) silinir;
    tahta artık yeni imajın kaynağıdır.
    """
    from .utils import run_cmd

    if not source_macs:
        return False
    try:
        SOURCE_MAC_FILE.parent.mkdir(parents=True, exist_ok=True)
        SOURCE_MAC_FILE.write_text("\n".join(source_macs) + "\n", encoding="utf-8")
        SENT_MARKER.unlink(missing_ok=True)
        SENT_MARKER.with_name(SENT_MARKER.name + ".local").unlink(missing_ok=True)
        script = (
            SENDER_SCRIPT_CONTENT
            .replace("@@LOG@@", str(APPLY_LOG_FILE))
            .replace("@@MACS@@", str(SOURCE_MAC_FILE))
            .replace("@@SENT@@", str(SENT_MARKER))
            .replace("@@RSYSLOG@@", str(RSYSLOG_CONF))
            .replace("@@TAG@@", SYSLOG_TAG)
            .replace("@@LINES_CODE@@", LINES_CODE)
        )
        SENDER_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
        SENDER_SCRIPT.write_text(script, encoding="utf-8")
        SENDER_SCRIPT.chmod(0o755)
        SENDER_SERVICE.write_text(SENDER_SERVICE_CONTENT, encoding="utf-8")
    except OSError as exc:
        log.error("Uygulama kaydı göndericisi kurulamadı: %s", exc)
        return False
    run_cmd(["systemctl", "daemon-reload"])
    return run_cmd(["systemctl", "enable", SENDER_SERVICE.name]).ok

