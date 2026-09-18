"""Özet raporunun kayıt katmanı.

Özet sayfasındaki "bu imajda neler yaptınız" raporu, kullanıcının hangi
seçeneklerle ne yaptığını bilmek zorunda. Günce (``journal.json``) bunu
genel olarak tutmuyordu: bir adımın ``data`` alanı yalnız o modülün geri
alma için sakladığı şeydi, form parametreleri yoktu. Ayrıca formdaki
düğme eylemleri (hesap silme, BIOS parolası ayarlama…) günceye hiç
girmiyordu.

Bu modül iki şey sağlar:

* ``redact_params`` — uygulanan form parametrelerinin gizli değerleri
  maskelenmiş kopyası. Günce ``data``'sına ``REPORT_PARAMS_KEY`` altında
  yazılır. Parolanın kendisi değil, yalnız "girildi mi" bilgisi kalır;
  günce dosyası klonlanan imajın içinde de durabilir.
* ``ActionLog`` — düğme eylemlerinin ayrı, kalıcı kaydı
  (``/var/lib/tiha/actions.json``). Ayrı dosya olmasının sebebi, düğme
  eylemlerinin geri alınabilir "adım" olmaması: günceye girselerdi
  modülün son kaydı olur, Özet'teki "Geri al" düğmesi yanlış veriyle
  çalışırdı.

Parametreleri yeni bir ``JournalEntry`` alanı yerine ``data`` içindeki
ayrılmış bir anahtarda tutuyoruz: günce ``JournalEntry(**e)`` ile
yükleniyor, yeni bir alan eski sürümlerin günceyi okumasını tamamen
bozardı.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .logger import get_logger
from .paths import ACTIONS_FILE, ensure_runtime_dirs

log = get_logger(__name__)

# Günce data'sında raporun okuduğu parametre kopyası. Alt çizgiyle başlar:
# modüllerin kendi anahtarlarıyla çakışmasın.
REPORT_PARAMS_KEY = "_rapor_params"
# Gizli bir alan doluysa değerin yerine yazılan işaret.
SECRET_MARK = "***"

# Adı parola/anahtar çağrıştıran alanlar tipinden bağımsız maskelenir.
_SECRET_HINTS = (
    "password", "passwd", "parola", "secret", "smbpass", "pin_key",
    "otp_key", "token", "private",
)


def looks_secret(key: str) -> bool:
    k = key.lower()
    return any(h in k for h in _SECRET_HINTS)


def _schema_fields(module_id: str) -> dict[str, dict]:
    try:
        from ..ui import params as params_schema  # saf veri; GTK yüklemez
    except Exception:  # pragma: no cover - paket bozuksa rapor yine çalışsın
        return {}
    return {f["key"]: f for f in params_schema.get(module_id) if "key" in f}


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return str(value)


def redact_params(module_id: str, params: dict | None) -> dict:
    """Formdan gelen parametrelerin rapora yazılabilir kopyası.

    Parola tipindeki ve adı gizli bilgi çağrıştıran alanlar dolu ise
    ``SECRET_MARK``, boş ise ``""`` olur. Düğme ve başlık alanları atlanır.
    """
    fields = _schema_fields(module_id)
    out: dict = {}
    for key, value in (params or {}).items():
        spec = fields.get(key, {})
        kind = spec.get("type")
        if kind in ("button", "heading"):
            continue
        if kind == "password" or looks_secret(key):
            out[key] = SECRET_MARK if str(value or "").strip() else ""
            continue
        out[key] = _json_safe(value)
    return out


def redact_data(data: dict | None) -> dict:
    """Modülün ApplyResult.data'sından rapora yarayacak, gizli olmayan kısım."""
    out: dict = {}
    for key, value in (data or {}).items():
        if looks_secret(str(key)):
            out[key] = SECRET_MARK if value else ""
            continue
        out[key] = _json_safe(value)
    return out


# --- Düğme eylemleri ---------------------------------------------------------


@dataclass
class ActionRecord:
    module_id: str
    title: str
    action: str             # modül metodunun adı
    label: str              # düğmenin görünen yazısı
    timestamp: str
    success: bool
    summary: str = ""
    params: dict = field(default_factory=dict)   # maskelenmiş
    data: dict = field(default_factory=dict)     # maskelenmiş


class ActionLog:
    """Düğme eylemlerinin kalıcı, salt eklemeli kaydı."""

    def __init__(self, path: Path = ACTIONS_FILE) -> None:
        self.path = path
        self._records: list[ActionRecord] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Eylem kaydı okunamadı (%s): %s", self.path, exc)
            return
        known = set(ActionRecord.__dataclass_fields__)
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            # Gelecekte eklenecek alanlar eski sürümü bozmasın diye süzülür.
            try:
                self._records.append(
                    ActionRecord(**{k: v for k, v in item.items() if k in known})
                )
            except TypeError:
                continue

    def _save(self) -> None:
        try:
            ensure_runtime_dirs()
            self.path.write_text(
                json.dumps([asdict(r) for r in self._records], indent=2,
                           ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            log.error("Eylem kaydı yazılamadı: %s", exc)

    def record(
        self,
        *,
        module_id: str,
        title: str,
        action: str,
        label: str,
        success: bool,
        summary: str = "",
        params: dict | None = None,
        data: dict | None = None,
    ) -> ActionRecord:
        rec = ActionRecord(
            module_id=module_id,
            title=title,
            action=action,
            label=label,
            timestamp=datetime.now(timezone.utc).isoformat(),
            success=bool(success),
            summary=summary or "",
            params=redact_params(module_id, params),
            data=redact_data(data),
        )
        self._records.append(rec)
        self._save()
        return rec

    def all(self) -> list[ActionRecord]:
        return list(self._records)

    def for_module(self, module_id: str) -> list[ActionRecord]:
        return [r for r in self._records if r.module_id == module_id]
