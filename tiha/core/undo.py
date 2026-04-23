"""Geri alma (undo) kayıt defteri.

TiHA, kullandığı her modülün "uygula" işleminin ardından bir günce
(:class:`JournalEntry`) kaydı tutar. Geri alma isteği geldiğinde defter
okunur ve ilgili modülün :meth:`Module.undo` metodu, kayıttaki anahtarlarla
çağrılır.

Defter düz JSON'dır; ``/var/lib/tiha/journal.json`` konumundadır.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .logger import get_logger
from .paths import JOURNAL_FILE, ensure_runtime_dirs

log = get_logger(__name__)


@dataclass
class JournalEntry:
    """Uygulanmış bir işlemin özeti."""

    module_id: str
    title: str
    timestamp: str
    status: str                  # "applied" | "undone" | "failed"
    summary: str = ""
    data: dict = field(default_factory=dict)  # Modül-özel durum bilgisi

    @classmethod
    def new(cls, module_id: str, title: str) -> "JournalEntry":
        return cls(
            module_id=module_id,
            title=title,
            timestamp=datetime.now(timezone.utc).isoformat(),
            status="applied",
        )


class Journal:
    """JSON tabanlı basit uzun ömürlü durum defteri."""

    def __init__(self, path: Path = JOURNAL_FILE) -> None:
        self.path = path
        self._entries: list[JournalEntry] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._entries = [JournalEntry(**e) for e in raw]
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            log.warning("Günce okunamadı (%s): %s", self.path, exc)
            self._entries = []

    def _save(self) -> None:
        try:
            ensure_runtime_dirs()
            self.path.write_text(
                json.dumps([asdict(e) for e in self._entries], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            log.error("Günce yazılamadı: %s", exc)

    # --- API ---------------------------------------------------------------

    def record(self, entry: JournalEntry) -> None:
        """Yeni bir işlem kaydı ekler ya da aynı id için günceller."""
        # Aynı modül id'li en son "applied" kaydı varsa, yeni kayıt onu
        # geçersiz kılacak şekilde eklenir (history yine korunur).
        self._entries.append(entry)
        self._save()

    def last_applied(self, module_id: str) -> JournalEntry | None:
        """Belirtilen modül için hâlâ geri alınabilir durumda son kaydı döndürür."""
        for entry in reversed(self._entries):
            if entry.module_id == module_id:
                if entry.status == "applied":
                    return entry
                return None
        return None

    def mark_undone(self, module_id: str) -> None:
        for entry in reversed(self._entries):
            if entry.module_id == module_id and entry.status == "applied":
                entry.status = "undone"
                self._save()
                return

    def all(self) -> list[JournalEntry]:
        return list(self._entries)
