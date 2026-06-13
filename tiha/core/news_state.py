"""'Yenilikler' diyaloğunun bu bilgisayardaki kalıcı durumu.

TiHA `curl|bash` ile her seferinde geçici `/tmp/tiha.XXXX`'a indirildiği için
"bir daha gösterme" tercihi ya da "en son hangi sürümü gördüm?" bilgisi
uygulama klasöründe tutulamaz — sonraki çalıştırmada o klasör artık yoktur.

Bunun için `/var/lib/tiha/news_state.json` kullanılır:

  * `last_seen_version` — kullanıcının bu bilgisayarda en son gördüğü
    TiHA sürümü. None ise hiç görmemiş demek (ilk çalıştırma).
  * `suppress_news_dialog` — kullanıcı "bir daha gösterme" işaretini
    seçtiyse True olur; bundan sonra yenilik diyaloğu hiç açılmaz.
    (Sidebar güncelleme rozeti bağımsızdır; bu bayrağı görmez.)

Yetki: TiHA root yetkisinde çalıştığı için yazma her zaman başarılı olur.
Yazma sırasında bir hata olursa sessizce loglanır — uygulamanın çalışmasını
engellemez.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from .logger import get_logger
from .paths import VAR_ROOT

log = get_logger(__name__)

NEWS_STATE_FILE = VAR_ROOT / "news_state.json"


@dataclass
class NewsState:
    last_seen_version: str | None = None
    suppress_news_dialog: bool = False


def load() -> NewsState:
    """Diskten oku. Dosya yoksa/bozuksa varsayılan döner."""
    try:
        if not NEWS_STATE_FILE.exists():
            return NewsState()
        data = json.loads(NEWS_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.debug("news_state okunamadı, varsayılan döner: %s", exc)
        return NewsState()
    if not isinstance(data, dict):
        return NewsState()
    lsv = data.get("last_seen_version")
    return NewsState(
        last_seen_version=lsv if isinstance(lsv, str) else None,
        suppress_news_dialog=bool(data.get("suppress_news_dialog")),
    )


def save(state: NewsState) -> None:
    """Diske yaz. OSError sessizce loglanır (kritik değil)."""
    try:
        NEWS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        NEWS_STATE_FILE.write_text(
            json.dumps(asdict(state), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("news_state yazılamadı: %s", exc)
