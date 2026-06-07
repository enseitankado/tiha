"""Headless / CLI mod — GUI'siz batch apply.

Kullanım:

    tiha --list
        Modüllerin id ve adlarını sırasıyla listeler.

    tiha --apply --preset school-x.json
        Preset dosyasındaki parametre setlerini kullanarak adımları
        sırasıyla uygular. Sadece preset'te yer alan modüller
        çalıştırılır.

    tiha --apply --preset school-x.json --only m07_time_sync,m06_remote_syslog
        Yalnızca verilen id'leri (virgülle ayrılmış) uygular.

    tiha --apply --preset school-x.json --skip m10_image_sanitize
        Verilen id'leri atlayarak diğerlerini uygular.

    tiha --info --preset school-x.json
        Preset içeriğini insan-okur formatta gösterir; uygulamaz.

Exit kodları:
  0  başarı
  1  çalışma sırasında bir adım hata verdi (devam edip diğerlerine
     geçilir, sondaki sayım stderr'e yazılır)
  2  yetki sorunu (root + etapadmin değil)
  10 preset dosyası okunamadı
  11 verilen modül id'si tanımsız
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import __version__
from . import console
from .logger import get_logger
from .preset import import_preset
from .undo import Journal, JournalEntry

log = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tiha",
        description="TiHA — Tahta İmaj Hazırlık Aracı (CLI mod). "
                    "Hiçbir bayrak verilmezse GUI açılır.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Örnek: sudo tiha --apply --preset okul-a.json",
    )
    p.add_argument("--version", action="version", version=f"TiHA {__version__}")
    p.add_argument("--list", action="store_true",
                   help="Modülleri sıralı olarak listele ve çık.")
    p.add_argument("--apply", action="store_true",
                   help="Preset'teki modülleri sırasıyla uygula.")
    p.add_argument("--info", action="store_true",
                   help="Preset içeriğini göster; uygulama.")
    p.add_argument("--preset", type=Path,
                   help="Preset JSON dosyasının yolu.")
    p.add_argument("--only",
                   help="Yalnızca verilen modül id'lerini uygula "
                        "(virgülle ayır).")
    p.add_argument("--skip",
                   help="Verilen modül id'lerini atla (virgülle ayır).")
    return p


def is_cli_invocation(argv: list[str]) -> bool:
    """argv'de CLI mod bayraklarından biri varsa True."""
    cli_flags = {"--list", "--apply", "--info", "--version", "--help", "-h"}
    return any(a in cli_flags for a in argv)


def cmd_list() -> int:
    from ..modules import all_modules
    console.banner_open("TiHA Modülleri", f"v{__version__}")
    for idx, m in enumerate(all_modules(), 1):
        name = m.sidebar_title or m.title or m.id
        print(f"  {idx:2d}. {m.id:30s} {name}")
    return 0


def cmd_info(preset_path: Path) -> int:
    try:
        params_by_module = import_preset(preset_path)
    except (OSError, ValueError) as exc:
        print(f"HATA: preset okunamadı: {exc}", file=sys.stderr)
        return 10

    from ..modules import all_modules
    known = {m.id: m for m in all_modules()}

    console.banner_open("Preset İçeriği", str(preset_path))
    print(f"Toplam modül: {len(params_by_module)}\n")
    for mid, params in params_by_module.items():
        mod = known.get(mid)
        title = mod.title if mod else "(tanımsız modül!)"
        print(f"• {mid}  —  {title}")
        for k, v in params.items():
            print(f"    {k} = {v}")
        print()
    unknown = [mid for mid in params_by_module if mid not in known]
    if unknown:
        print(f"⚠ Tanımsız modül id'leri: {', '.join(unknown)}", file=sys.stderr)
        return 11
    return 0


def cmd_apply(
    preset_path: Path,
    only: set[str] | None,
    skip: set[str] | None,
) -> int:
    try:
        params_by_module = import_preset(preset_path)
    except (OSError, ValueError) as exc:
        print(f"HATA: preset okunamadı: {exc}", file=sys.stderr)
        return 10

    from ..modules import all_modules
    all_mods = all_modules()
    known = {m.id: m for m in all_mods}

    # Uygulanacak modülleri belirle — sıralama wizard sırasına göre
    targets = []
    for mod in all_mods:
        if mod.id not in params_by_module:
            continue
        if only and mod.id not in only:
            continue
        if skip and mod.id in skip:
            continue
        targets.append((mod, params_by_module[mod.id]))

    unknown_in_preset = [mid for mid in params_by_module if mid not in known]
    if unknown_in_preset:
        print(f"⚠ Preset'te tanımsız modül id'leri (atlanacak): "
              f"{', '.join(unknown_in_preset)}", file=sys.stderr)

    if not targets:
        print("Uygulanacak modül yok (filtre/yokluk).", file=sys.stderr)
        return 0

    console.banner_open("TiHA — CLI Apply", f"v{__version__}")
    print(f"Preset : {preset_path}")
    print(f"Hedef  : {len(targets)} modül\n")

    journal = Journal()
    failed_count = 0

    for idx, (mod, params) in enumerate(targets, 1):
        console.step(f"[{idx}/{len(targets)}] {mod.title}  ({mod.id})")

        def progress(line: str) -> None:
            print(f"  {line}")

        try:
            result = mod.apply_with_logging(params, progress=progress)
        except Exception as exc:
            result = None
            print(f"  ✗ İSTİSNA: {exc}", file=sys.stderr)
            failed_count += 1
            entry = JournalEntry.new(mod.id, mod.title)
            entry.summary = f"İstisna: {exc}"
            entry.status = "failed"
            journal.record(entry)
            continue

        # Journal'a kayıt
        entry = JournalEntry.new(mod.id, mod.title)
        entry.summary = result.summary
        entry.status = "applied" if result.success else "failed"
        entry.data = dict(result.data) if isinstance(result.data, dict) else {}
        journal.record(entry)

        if result.success:
            console.ok(result.summary)
        else:
            console.fail(result.summary)
            if result.details:
                for ln in result.details.splitlines():
                    print(f"  {ln}", file=sys.stderr)
            failed_count += 1

    console.banner_close(
        f"CLI Apply tamamlandı — başarı: {len(targets) - failed_count}, "
        f"hata: {failed_count}"
    )
    return 1 if failed_count else 0


def run(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list:
        return cmd_list()

    if args.info:
        if not args.preset:
            parser.error("--info için --preset gereklidir")
        return cmd_info(args.preset)

    if args.apply:
        if not args.preset:
            parser.error("--apply için --preset gereklidir")
        only = _csv_set(args.only) if args.only else None
        skip = _csv_set(args.skip) if args.skip else None
        return cmd_apply(args.preset, only, skip)

    parser.print_help()
    return 0


def _csv_set(s: str) -> set[str]:
    return {x.strip() for x in s.split(",") if x.strip()}
