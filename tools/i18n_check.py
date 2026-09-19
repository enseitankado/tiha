#!/usr/bin/env python3
"""Metin kataloğu denetimi.

Kaynak koddaki bütün ``t("...")`` çağrılarını bulur ve katalogla
(``tiha/locale/tr.toml``) karşılaştırır:

* kodda kullanılıp katalogda olmayan anahtarlar (HATA),
* çağrıda verilen değişkenlerle metindeki yer tutucuların uyuşmaması (HATA),
* anahtarı sabit metin olmayan çağrılar (UYARI — denetlenemez),
* katalogda olup kodda kullanılmayan anahtarlar (BİLGİ).

Kullanım::

    python3 tools/i18n_check.py                  # tüm proje, tr.toml
    python3 tools/i18n_check.py --catalog X.toml --files a.py b.py

Hata varsa çıkış kodu 1'dir.
"""

from __future__ import annotations

import argparse
import ast
import string
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tiha.core.i18n import _flatten  # noqa: E402


def _placeholders(text: str) -> set[str]:
    names: set[str] = set()
    for _lit, field, _spec, _conv in string.Formatter().parse(text):
        if field:
            names.add(field.split(".")[0].split("[")[0])
    return names


def _calls(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None
        )
        if name != "t" or not node.args:
            continue
        first = node.args[0]
        key = first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None
        kwargs = {kw.arg for kw in node.keywords if kw.arg}
        dynamic_kwargs = any(kw.arg is None for kw in node.keywords)
        yield node.lineno, key, kwargs, dynamic_kwargs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default=str(ROOT / "tiha" / "locale" / "tr.toml"))
    ap.add_argument("--files", nargs="*")
    ap.add_argument("--no-unused", action="store_true",
                    help="kullanılmayan anahtarları listeleme")
    args = ap.parse_args()

    with open(args.catalog, "rb") as fh:
        catalog = _flatten(tomllib.load(fh))

    files = [Path(f) for f in args.files] if args.files else sorted((ROOT / "tiha").rglob("*.py"))
    errors = 0
    used: set[str] = set()
    for path in files:
        for line, key, kwargs, dynamic in _calls(path):
            where = f"{path.relative_to(ROOT) if path.is_absolute() else path}:{line}"
            if key is None:
                print(f"UYARI  {where}: anahtar sabit metin değil, denetlenemedi")
                continue
            used.add(key)
            if key not in catalog:
                print(f"HATA   {where}: katalogda yok: {key}")
                errors += 1
                continue
            if dynamic:
                continue
            if not kwargs and ("{{" in catalog[key] or "}}" in catalog[key]):
                print(f"HATA   {where}: {key} değişkensiz çağrılıyor ama metinde "
                      "'{{' / '}}' var (ekranda çift görünür)")
                errors += 1
                continue
            wanted = _placeholders(catalog[key])
            if wanted != kwargs and (wanted or kwargs):
                print(f"HATA   {where}: {key} yer tutucular {sorted(wanted)} "
                      f"≠ verilen {sorted(kwargs)}")
                errors += 1

    if not args.no_unused and not args.files:
        for key in sorted(set(catalog) - used):
            print(f"BİLGİ  kullanılmayan anahtar: {key}")

    print(f"\n{len(used)} anahtar kullanımda, katalogda {len(catalog)} anahtar, {errors} hata.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
