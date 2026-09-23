#!/usr/bin/env python3
"""TiHA hata raporlarını ntfy.sh'tan toplar, gruplar ve GitHub'a aktarır.

Tahtalardaki TiHA, hata olduğunda anonim bir raporu ntfy.sh'taki sabit
konuya gönderir (bkz. tiha/core/crash_report.py). ntfy.sh mesajları
yalnız ~12 saat tutar; bu araç onları geliştirici makinesinde bir dosyada
biriktirir ve aynı hatayı parmak iziyle ("iz") tek satırda toplar.

Kullanım (normal kullanıcı olarak, root gerekmez):

    tools/hata-raporlari.py topla            # yeni raporları indir
    tools/hata-raporlari.py ozet             # iz başına özet tablo (önce toplar)
    tools/hata-raporlari.py ozet --gun 7 --surum 0.1.60 --yalniz-yeni
    tools/hata-raporlari.py goster <iz>      # bir hatanın örnek raporu ve dağılımı
    tools/hata-raporlari.py issue <iz>       # GitHub'da hata kaydı aç (gh gerekir)
    tools/hata-raporlari.py kapat <iz>       # izi "ele alındı" işaretle, özette gizle
    tools/hata-raporlari.py zamanlayici-kur  # 3 saatte bir otomatik topla (systemd --user)

Veriler: ~/.local/share/tiha-hata-raporlari/ (raporlar.jsonl, durum.json).
Varsayılan olarak "kurulum: geliştirme" raporları (run-dev.sh) özette
gösterilmez; --gelistirme ile görünür.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap
import time
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tiha.core.crash_report import NTFY_TOPIC, NTFY_URL  # noqa: E402

DATA = Path.home() / ".local/share/tiha-hata-raporlari"
STORE = DATA / "raporlar.jsonl"
STATE = DATA / "durum.json"
REPO = "enseitankado/tiha"


# --- Kayıt -------------------------------------------------------------------


def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _parse(msg: dict) -> dict:
    """ntfy iletisini alanlara ayırır (rapor gövdesi 'alan: değer' satırları)."""
    body = msg.get("message", "")
    head, _, trace = body.partition("\n---\n")
    fields = {}
    for line in head.splitlines():
        key, sep, value = line.partition(": ")
        if sep:
            fields[key.strip()] = value.strip()
    iz = fields.get("iz") or next(
        (t[3:] for t in msg.get("tags", []) if t.startswith("iz-")), "?")
    return {
        "id": msg.get("id"),
        "time": msg.get("time", 0),
        "iz": iz,
        "hata": fields.get("hata", msg.get("title", "")),
        "adim": fields.get("adım", "-"),
        "surum": fields.get("sürüm", "?"),
        "sistem": fields.get("sistem", "?"),
        "kaynak": fields.get("kaynak", "?"),
        "kurulum": fields.get("kurulum", "?"),
        "iz_zinciri": trace,
    }


def load() -> list[dict]:
    if not STORE.exists():
        return []
    out = []
    for line in STORE.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def collect(quiet: bool = False) -> int:
    """ntfy.sh'tan son indirilenden sonraki raporları çeker."""
    state = _state()
    since = state.get("son_id") or "all"
    url = f"{NTFY_URL}/{NTFY_TOPIC}/json?poll=1&since={since}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as exc:
        print(f"ntfy.sh'a ulaşılamadı: {exc}", file=sys.stderr)
        return 0
    known = {r["id"] for r in load()}
    new = []
    for line in raw.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("event") != "message" or msg.get("id") in known:
            continue
        new.append(_parse(msg))
    if new:
        DATA.mkdir(parents=True, exist_ok=True)
        with STORE.open("a", encoding="utf-8") as f:
            for r in new:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        state["son_id"] = new[-1]["id"]
    state["son_toplama"] = int(time.time())
    _save_state(state)
    if not quiet:
        print(f"{len(new)} yeni rapor indirildi (toplam {len(known) + len(new)}).")
    return len(new)


# --- Görünümler --------------------------------------------------------------


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m %H:%M") if ts else "-"


def _groups(reports: list[dict]) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = defaultdict(list)
    for r in reports:
        g[r["iz"]].append(r)
    return g


def _filter(reports: list[dict], args) -> list[dict]:
    cutoff = time.time() - args.gun * 86400 if args.gun else 0
    out = []
    for r in reports:
        if r["time"] < cutoff:
            continue
        if not args.gelistirme and r.get("kurulum") == "geliştirme":
            continue
        if args.surum and r["surum"] != args.surum:
            continue
        out.append(r)
    return out


def cmd_ozet(args) -> None:
    if not args.yerel:
        collect(quiet=True)
    state = _state()
    closed = state.get("kapali", {})
    issues = state.get("issue", {})
    groups = _groups(_filter(load(), args))
    rows = []
    for iz, items in groups.items():
        if iz in closed and not args.hepsi:
            # Kapatıldıktan sonra yeni sürümde yeniden görülürse yine göster.
            if all(r["time"] <= closed[iz] for r in items):
                continue
        first = min(r["time"] for r in items)
        last = max(r["time"] for r in items)
        if args.yalniz_yeni and first < time.time() - 86400:
            continue
        rows.append((last, iz, items, first))
    if not rows:
        print("Gösterilecek hata yok.")
        return
    rows.sort(key=lambda x: (-len(x[2]), -x[0]))
    print(f"{'iz':8}  {'adet':>4}  {'son':11}  {'ilk':11}  {'adım':4}  {'sürümler':14}  hata")
    for last, iz, items, first in rows:
        versions = ",".join(sorted({r['surum'] for r in items}))[:14]
        mark = " [#" + str(issues[iz]).rsplit("/", 1)[-1] + "]" if iz in issues else ""
        print(f"{iz:8}  {len(items):>4}  {_when(last):11}  {_when(first):11}  "
              f"{items[-1]['adim']:4}  {versions:14}  {items[-1]['hata'][:70]}{mark}")
    print(f"\n{len(rows)} farklı hata, {sum(len(r[2]) for r in rows)} rapor. "
          "Ayrıntı: goster <iz>")


def _find(iz: str) -> list[dict]:
    items = [r for r in load() if r["iz"].startswith(iz)]
    if not items:
        sys.exit(f"'{iz}' izli rapor yok (önce: topla).")
    return items


def _describe(items: list[dict]) -> str:
    latest = max(items, key=lambda r: r["time"])
    by_version = defaultdict(int)
    by_source = defaultdict(int)
    for r in items:
        by_version[r["surum"]] += 1
        by_source[r["kaynak"]] += 1
    return textwrap.dedent(f"""\
        Hata: {latest['hata']}
        İz: {latest['iz']} · Adım: {latest['adim']} · Rapor: {len(items)}
        İlk: {_when(min(r['time'] for r in items))} · Son: {_when(latest['time'])}
        Sürümler: {', '.join(f'{v} ({n})' for v, n in sorted(by_version.items()))}
        Kaynak: {', '.join(f'{k} ({n})' for k, n in by_source.items())}
        Sistem: {latest['sistem']}

        Çağrı zinciri (son rapor):
        """) + latest["iz_zinciri"]


def cmd_goster(args) -> None:
    print(_describe(_find(args.iz)))


def cmd_issue(args) -> None:
    items = _find(args.iz)
    iz = items[0]["iz"]
    state = _state()
    if iz in state.get("issue", {}) and not args.yine:
        sys.exit(f"Bu iz için zaten kayıt var: {state['issue'][iz]} (--yine ile yeniden açın)")
    latest = max(items, key=lambda r: r["time"])
    title = f"[hata raporu] {latest['hata'][:80]} (adım {latest['adim']}, iz {iz})"
    body = ("Anonim hata raporlarından otomatik oluşturuldu.\n\n```\n"
            + _describe(items) + "\n```\n")
    r = subprocess.run(
        ["gh", "issue", "create", "--repo", REPO, "--title", title, "--body", body],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        sys.exit(r.stderr.strip() or "gh issue create başarısız")
    url = r.stdout.strip().splitlines()[-1]
    state.setdefault("issue", {})[iz] = url
    _save_state(state)
    print(url)


def cmd_kapat(args) -> None:
    iz = _find(args.iz)[0]["iz"]
    state = _state()
    state.setdefault("kapali", {})[iz] = int(time.time())
    _save_state(state)
    print(f"{iz} kapatıldı; aynı hata yeniden gelirse özette tekrar görünür.")


def cmd_zamanlayici(args) -> None:
    unit_dir = Path.home() / ".config/systemd/user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / "tiha-hata-topla.service").write_text(textwrap.dedent(f"""\
        [Unit]
        Description=TiHA hata raporlarını ntfy.sh'tan topla

        [Service]
        Type=oneshot
        ExecStart={sys.executable} {Path(__file__).resolve()} topla
        """), encoding="utf-8")
    (unit_dir / "tiha-hata-topla.timer").write_text(textwrap.dedent("""\
        [Unit]
        Description=TiHA hata raporlarını 3 saatte bir topla

        [Timer]
        OnBootSec=2min
        OnUnitActiveSec=3h
        Persistent=true

        [Install]
        WantedBy=timers.target
        """), encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    r = subprocess.run(["systemctl", "--user", "enable", "--now", "tiha-hata-topla.timer"],
                       capture_output=True, text=True)
    print(r.stdout or r.stderr or "Zamanlayıcı kuruldu: tiha-hata-topla.timer (3 saatte bir).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("topla", help="yeni raporları indir")
    o = sub.add_parser("ozet", help="iz başına özet")
    o.add_argument("--gun", type=int, default=30, help="son N gün (0: hepsi)")
    o.add_argument("--surum", help="yalnız bu TiHA sürümü")
    o.add_argument("--gelistirme", action="store_true", help="run-dev.sh raporlarını da göster")
    o.add_argument("--yalniz-yeni", action="store_true", help="son 24 saatte ilk kez görülenler")
    o.add_argument("--hepsi", action="store_true", help="kapatılanları da göster")
    o.add_argument("--yerel", action="store_true", help="toplamadan, yalnız yerel kayıtlar")
    g = sub.add_parser("goster", help="bir hatanın ayrıntısı")
    g.add_argument("iz")
    i = sub.add_parser("issue", help="GitHub hata kaydı aç")
    i.add_argument("iz")
    i.add_argument("--yine", action="store_true")
    k = sub.add_parser("kapat", help="izi ele alındı işaretle")
    k.add_argument("iz")
    sub.add_parser("zamanlayici-kur", help="3 saatte bir otomatik toplama")
    args = ap.parse_args()
    {
        "topla": lambda a: collect(),
        "ozet": cmd_ozet,
        "goster": cmd_goster,
        "issue": cmd_issue,
        "kapat": cmd_kapat,
        "zamanlayici-kur": cmd_zamanlayici,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
