"""Anonim hata raporları (ntfy.sh).

TiHA'da yakalanmamış bir hata oluştuğunda ya da bir adım/düğme işlemi
istisnayla bittiğinde, geliştiriciye kısa ve ANONİM bir rapor gönderilir.
Rapor ntfy.sh'taki sabit bir konuya düşer; geliştirici ntfy uygulamasında
anlık görür, ``tools/hata-raporlari.py`` ile toplayıp gruplar.

Ne gider:
    hata türü ve (temizlenmiş) iletisi, TiHA'nın kendi kodundaki çağrı
    zinciri (dosya:satır işlev), hangi adımda olduğu, TiHA/Pardus/Python
    sürümü, hatanın parmak izi ("iz") ve geliştirme kopyası mı olduğu.

Ne GİTMEZ:
    bilgisayar adı, kullanıcı ve hesap adları, öğretmen adları, parolalar
    ve anahtarlar, IP/MAC/e-posta adresleri, dosya yollarındaki kişisel
    kısımlar, günlük dosyaları. İleti metni aşağıdaki kurallarla
    maskelenir; şüpheli her parça ``<...>`` ile değiştirilir.

ntfy.sh gönderenin IP adresini görür; bu engellenemez.

Kapatma: ``TIHA_HATA_RAPORU=0`` ortam değişkeni ya da
``/etc/tiha/hata-raporu-kapali`` dosyası.

Tasarım kuralları: rapor göndermek TiHA'yı asla bekletmez ya da
çökertmez (arka plan iş parçacığı, kısa zaman aşımı, bütün hatalar
yutulur); aynı hata bir tahtadan 24 saatte bir kez, bir oturumda en
fazla birkaç kez gider.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import sys
import threading
import time
import traceback
from pathlib import Path

NTFY_URL = "https://ntfy.sh"
# Kod herkese açık olduğundan konu adı da açıktır; rapor içeriği bu yüzden
# anonimdir. Toplayıcı araç (tools/hata-raporlari.py) aynı sabiti kullanır.
NTFY_TOPIC = "tiha-hata-nbzif69w943kd7bl6obf"

DISABLE_ENV = "TIHA_HATA_RAPORU"
DISABLE_FILE = Path("/etc/tiha/hata-raporu-kapali")
STATE_FILE = Path("/var/lib/tiha/hata-raporlari.json")

RESEND_AFTER = 24 * 3600
MAX_PER_SESSION = 5
SEND_TIMEOUT = 6
MAX_FRAMES = 12
MAX_MESSAGE_LEN = 300

# Kişisel olmayan, raporda kalması faydalı hesap adları.
_KNOWN_ACCOUNTS = {"root", "etapadmin", "ogretmen", "ogrenci", "lightdm", "nobody"}

_log = logging.getLogger("tiha.crash_report")
_lock = threading.Lock()
_sent_this_session: set[str] = set()
_busy = threading.local()
_installed = False


# --- Açık/kapalı -------------------------------------------------------------


def enabled() -> bool:
    if os.environ.get(DISABLE_ENV, "").strip().lower() in ("0", "false", "off", "hayir", "hayır", "kapali", "kapalı"):
        return False
    try:
        return not DISABLE_FILE.exists()
    except OSError:
        return True


# --- Anonimleştirme ---------------------------------------------------------


def _local_names() -> set[str]:
    """Maskelenecek yerel adlar: bilgisayar adı ve kişisel hesap adları."""
    names: set[str] = set()
    try:
        names.add(socket.gethostname())
    except OSError:
        pass
    try:
        import pwd
        for e in pwd.getpwall():
            if e.pw_uid >= 1000 and e.pw_name not in _KNOWN_ACCOUNTS:
                names.add(e.pw_name)
                gecos = (e.pw_gecos or "").split(",")[0].strip()
                if gecos:
                    names.add(gecos)
    except Exception:
        pass
    return {n for n in names if n and len(n) >= 3}


_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "<e-posta>"),
    (re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s'\"]+", re.I), "<url>"),
    (re.compile(r"\b([0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b", re.I), "<mac>"),
    (re.compile(r"\b\d{1,3}(\.\d{1,3}){3}\b"), "<ip>"),
    (re.compile(r"\b[0-9a-f]{0,4}(:[0-9a-f]{0,4}){3,7}\b", re.I), "<ip>"),
    (re.compile(r"/home/[^/\s'\"]+"), "/home/<hesap>"),
    (re.compile(r"/run/user/\d+"), "/run/user/<uid>"),
    # Anahtar, parola özeti, belirteç: uzun harf/rakam dizileri.
    (re.compile(r"\$[0-9a-z]{1,3}\$[^\s'\"]+", re.I), "<gizli>"),
    (re.compile(r"\b[A-Za-z0-9+/_=-]{20,}\b"), "<gizli>"),
    # TC kimlik, telefon vb.
    (re.compile(r"\b\d{7,}\b"), "<sayı>"),
    # BÜYÜK HARFLE yazılmış ad soyad (öğretmen listesi bu biçimde girilir).
    (re.compile(r"\b[A-ZÇĞİÖŞÜ]{2,}(\s+[A-ZÇĞİÖŞÜ]{2,})+\b"), "<ad>"),
    # Baş harfi büyük ardışık sözcükler (Ayşe Yılmaz): ad soyad olabilir.
    (re.compile(r"\b[A-ZÇĞİÖŞÜ][a-zçğıöşü]+(\s+[A-ZÇĞİÖŞÜ][a-zçğıöşü]+)+\b"), "<ad>"),
]
_QUOTED = re.compile(r"(['\"])(.*?)\1")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def anonymize(text: str, names: set[str] | None = None) -> str:
    """Metindeki kişisel/gizli olabilecek parçaları maskeler."""
    if not text:
        return ""
    names = _local_names() if names is None else names
    for name in sorted(names, key=len, reverse=True):
        text = re.sub(rf"(?<![\w.]){re.escape(name)}(?![\w])", "<hesap>", text)
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)

    # Tırnak içindeki değerler: yalnız kod tanımlayıcısına benzeyenler
    # kalır (KeyError: 'cursor_xorg_fix' gibi); ad, yol, cümle maskelenir.
    def _quoted(m: re.Match) -> str:
        inner = m.group(2)
        if _IDENT.match(inner) or (inner.startswith("<") and inner.endswith(">")):
            return m.group(0)
        return f"{m.group(1)}<metin>{m.group(1)}"

    text = _QUOTED.sub(_quoted, text)
    return text


def _frame_path(filename: str) -> str:
    """Çağrı zincirindeki dosya yolunun kişisel olmayan kısmı."""
    p = filename.replace("\\", "/")
    m = re.search(r"/(tiha/(?:core|ui|modules|[a-z_]+\.py).*)$", p)
    if m:
        return m.group(1)
    for marker in ("dist-packages/", "site-packages/"):
        if marker in p:
            return "<paket>/" + p.split(marker, 1)[1]
    if re.search(r"/python3\.\d+/", p):
        return "<python>/" + p.rsplit("/", 1)[-1]
    if p.startswith("<"):
        return p
    return "<dosya>/" + p.rsplit("/", 1)[-1]


def _step_of(frames: list[traceback.FrameSummary]) -> str:
    for fr in reversed(frames):
        m = re.search(r"tiha/modules/(m\d\d)_", fr.filename.replace("\\", "/"))
        if m:
            return m.group(1)
    return "-"


def _is_dev_checkout() -> bool:
    try:
        return (Path(__file__).resolve().parents[2] / ".git").exists()
    except Exception:
        return False


def build_report(exc_type, exc, tb, *, source: str, names: set[str] | None = None) -> dict:
    """Gönderilecek anonim raporu hazırlar (ntfy JSON gövdesi)."""
    from .. import __version__

    try:
        from .os_release import pretty_name
        system = pretty_name()
    except Exception:
        system = "?"
    names = _local_names() if names is None else names

    frames = traceback.extract_tb(tb) if tb is not None else []
    type_name = getattr(exc_type, "__name__", str(exc_type))
    message = anonymize(str(exc), names)[:MAX_MESSAGE_LEN]
    step = _step_of(frames)

    # Parmak izi: satır numarası olmadan (sürümler arası aynı hata tek grup).
    ours = [(_frame_path(f.filename), f.name) for f in frames if "/tiha/" in f.filename]
    basis = type_name + "|" + "|".join(f"{a}:{b}" for a, b in (ours or
            [(_frame_path(f.filename), f.name) for f in frames[-3:]]))
    fp = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:8]

    lines = [
        f"hata: {type_name}: {message}" if message else f"hata: {type_name}",
        f"adım: {step}",
        f"sürüm: {__version__}",
        # os-release adı kişisel değil; maskelenmez.
        f"sistem: {system} · Python {sys.version_info.major}.{sys.version_info.minor}",
        f"kaynak: {source}",
        f"kurulum: {'geliştirme' if _is_dev_checkout() else 'bootstrap'}",
        f"iz: {fp}",
        "---",
    ]
    for f in frames[-MAX_FRAMES:]:
        lines.append(f"{_frame_path(f.filename)}:{f.lineno} {f.name}")
        if f.line:
            # Kaynak kodun kendisi (depodaki sabit metin); çalışma anı
            # verisi taşımaz, maskelenmez.
            lines.append("    " + f.line.strip()[:160])
    body = "\n".join(lines)[:3900]
    return {
        "topic": NTFY_TOPIC,
        "title": f"TiHA {__version__} · {type_name} · {step}",
        "message": body,
        "tags": ["warning", f"iz-{fp}", f"v{__version__}", step],
        "fingerprint": fp,
    }


# --- Tekrar denetimi ve gönderim -------------------------------------------


def _should_send(fp: str) -> bool:
    with _lock:
        if fp in _sent_this_session or len(_sent_this_session) >= MAX_PER_SESSION:
            return False
        state: dict = {}
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        now = time.time()
        if now - float(state.get(fp, 0) or 0) < RESEND_AFTER:
            return False
        _sent_this_session.add(fp)
        state = {k: v for k, v in state.items() if now - float(v or 0) < 30 * 86400}
        state[fp] = now
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(state), encoding="utf-8")
            os.replace(tmp, STATE_FILE)
        except OSError:
            pass
        return True


def _post(payload: dict) -> None:
    import urllib.request

    data = {k: v for k, v in payload.items() if k != "fingerprint"}
    req = urllib.request.Request(
        NTFY_URL,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=SEND_TIMEOUT):
            pass
    except Exception:
        pass  # ağ yok, ntfy kapalı…: sessizce vazgeç


def report(exc_type, exc, tb, *, source: str, wait: bool = False) -> None:
    """Hatayı (açıksa, daha önce gönderilmediyse) arka planda gönderir."""
    if not enabled() or getattr(_busy, "on", False):
        return
    if exc_type is None or issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        return
    _busy.on = True
    try:
        payload = build_report(exc_type, exc, tb, source=source)
        if not _should_send(payload["fingerprint"]):
            return
        worker = threading.Thread(target=_post, args=(payload,), daemon=True)
        worker.start()
        if wait:
            worker.join(SEND_TIMEOUT + 1)
    except Exception:
        pass
    finally:
        _busy.on = False


# --- Kurulum ----------------------------------------------------------------


class _ExceptionLogHandler(logging.Handler):
    """``exc_info`` taşıyan ERROR kayıtlarını rapora çevirir (adım/düğme
    işlemlerinde yakalanıp loglanan istisnalar)."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.exc_info and record.exc_info[0] is not None:
            report(*record.exc_info, source="günlük")


def install() -> None:
    """Uygulama açılışında bir kez çağrılır."""
    global _installed
    if _installed:
        return
    _installed = True

    handler = _ExceptionLogHandler(level=logging.ERROR)
    logging.getLogger("tiha").addHandler(handler)

    previous = sys.excepthook

    def _excepthook(exc_type, exc, tb):
        report(exc_type, exc, tb, source="yakalanmamış", wait=True)
        previous(exc_type, exc, tb)

    sys.excepthook = _excepthook

    previous_thread = threading.excepthook

    def _thread_hook(args):
        report(args.exc_type, args.exc_value, args.exc_traceback, source="iş parçacığı")
        previous_thread(args)

    threading.excepthook = _thread_hook
