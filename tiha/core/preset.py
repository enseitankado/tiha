"""Preset export / import — adımların parametre setlerini JSON'a aktarır.

Bir okul için TiHA wizard'ında doldurulan değerleri (Samba paylaşım adı,
log sunucusu IP'si, NTP sunucuları, idle dakikası vb.) **bir dosyaya
kaydedip** başka tahtalarda aynı setup'ı tek tıkla uygulamak için.

Aynı format CLI mod (``tiha apply --preset school-x.json``) tarafından
da kullanılır.

**Şema (v1):**

```json
{
  "schema_version": 1,
  "tiha_version": "0.1.0",
  "exported_at": "2026-06-06T...",
  "exported_on_host": "etap-image",
  "modules": {
    "m07_time_sync": {"primary_ntp": "...", "timezone": "..."},
    "m06_remote_syslog": {"host": "...", "port": 514, "protocol": "udp"},
    ...
  }
}
```

Modül id'sinin altındaki dict, o modülün ``apply(params=...)`` çağrısına
geçirilen değerlerdir. Parolaları içerebilecek modüller (m01, m05) güvenlik
için **dışa aktarılırken otomatik atlanır**.
"""

from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from .logger import get_logger
from .undo import Journal

log = get_logger(__name__)

SCHEMA_VERSION = 1

# Bu modüllerin parametreleri parola/secret içerebilir; export'ta atlanır.
SENSITIVE_MODULES = {
    "m01_initial_passwords",
    "m05_samba_share",
}


def export_preset(
    modules_with_params: dict[str, dict],
    *,
    target: Path,
    include_sensitive: bool = False,
) -> Path:
    """Verilen modül parametre setini JSON dosyasına yazar.

    Args:
        modules_with_params: {module_id: params_dict}.
        target: Hedef dosya yolu. Üst dizin yoksa oluşturulur.
        include_sensitive: True ise parolalı modüller de dahil edilir
            (varsayılan False — kullanıcı sözleşmesi).

    Returns:
        Yazılan dosyanın yolu.
    """
    clean: dict[str, dict] = {}
    for mid, params in modules_with_params.items():
        if not isinstance(params, dict):
            continue
        if not include_sensitive and mid in SENSITIVE_MODULES:
            continue
        # Parola benzeri anahtarları herhangi bir modülde atla
        scrubbed = {
            k: v for k, v in params.items()
            if not _looks_secret(k)
        }
        if scrubbed:
            clean[mid] = scrubbed

    blob = {
        "schema_version": SCHEMA_VERSION,
        "tiha_version": __version__,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "exported_on_host": socket.gethostname(),
        "modules": clean,
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(blob, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    log.info("Preset dışa aktarıldı: %s (%d modül)", target, len(clean))
    return target


def import_preset(source: Path) -> dict[str, dict]:
    """Preset dosyasından {module_id: params} dict'i okur. Hatalı şemada
    ``ValueError`` fırlatır."""
    raw = json.loads(source.read_text(encoding="utf-8"))
    sv = raw.get("schema_version")
    if sv != SCHEMA_VERSION:
        raise ValueError(
            f"Desteklenmeyen schema_version: {sv} "
            f"(beklenen {SCHEMA_VERSION})"
        )
    mods = raw.get("modules")
    if not isinstance(mods, dict):
        raise ValueError("'modules' anahtarı eksik veya tip uyumsuz.")
    return {mid: (p or {}) for mid, p in mods.items() if isinstance(p, dict)}


def extract_from_journal(journal: Journal) -> dict[str, dict]:
    """Journal'da uygulanmış adımların ``data`` alanından preset üretmeye
    çalışır. Her modülün ``data`` formatı kendine özeldir; bu sadece
    "şu adımlar uygulandı" listesi olarak yararlıdır. Asıl parametre
    yakalama UI tarafında pages.py'ın **son apply parametre kümesini**
    biriktirmesiyle olur (export_preset doğrudan oradan beslenir)."""
    out: dict[str, dict] = {}
    for entry in journal.all():
        if entry.status != "applied":
            continue
        # data içinde "params" anahtarı varsa onu kullan
        params = entry.data.get("params") if isinstance(entry.data, dict) else None
        if isinstance(params, dict):
            out[entry.module_id] = params
        else:
            out.setdefault(entry.module_id, {})
    return out


def _looks_secret(key: str) -> bool:
    k = key.lower()
    return any(s in k for s in ("password", "passwd", "parola", "secret", "smbpass"))
