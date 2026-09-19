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
from .i18n import t
from .logger import get_logger
from .preset import import_preset
from .undo import Journal, JournalEntry
from .report_log import REPORT_PARAMS_KEY, redact_params

log = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tiha",
        description=t("cli.description"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=t("cli.epilog"),
    )
    p.add_argument("--version", action="version", version=f"TiHA {__version__}")
    p.add_argument("--list", action="store_true",
                   help=t("cli.help_list"))
    p.add_argument("--apply", action="store_true",
                   help=t("cli.help_apply"))
    p.add_argument("--info", action="store_true",
                   help=t("cli.help_info"))
    p.add_argument("--preset", type=Path,
                   help=t("cli.help_preset"))
    p.add_argument("--only",
                   help=t("cli.help_only"))
    p.add_argument("--skip",
                   help=t("cli.help_skip"))
    return p


def is_cli_invocation(argv: list[str]) -> bool:
    """argv'de CLI mod bayraklarından biri varsa True."""
    cli_flags = {"--list", "--apply", "--info", "--version", "--help", "-h"}
    return any(a in cli_flags for a in argv)


def cmd_list() -> int:
    from ..modules import all_modules
    console.banner_open(t("cli.list_title"), f"v{__version__}")
    for idx, m in enumerate(all_modules(), 1):
        name = m.sidebar_title or m.title or m.id
        print(f"  {idx:2d}. {m.id:30s} {name}")
    return 0


def cmd_info(preset_path: Path) -> int:
    try:
        params_by_module = import_preset(preset_path)
    except (OSError, ValueError) as exc:
        print(t("cli.preset_read_error", error=exc), file=sys.stderr)
        return 10

    from ..modules import all_modules
    known = {m.id: m for m in all_modules()}

    console.banner_open(t("cli.info_title"), str(preset_path))
    print(t("cli.info_total", count=len(params_by_module)))
    for mid, params in params_by_module.items():
        mod = known.get(mid)
        title = mod.title if mod else t("cli.unknown_module")
        print(f"• {mid}  —  {title}")
        for k, v in params.items():
            print(f"    {k} = {v}")
        print()
    unknown = [mid for mid in params_by_module if mid not in known]
    if unknown:
        print(t("cli.unknown_ids", ids=", ".join(unknown)), file=sys.stderr)
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
        print(t("cli.preset_read_error", error=exc), file=sys.stderr)
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
        print(t("cli.unknown_ids_skipped", ids=", ".join(unknown_in_preset)),
              file=sys.stderr)

    if not targets:
        print(t("cli.nothing_to_apply"), file=sys.stderr)
        return 0

    console.banner_open(t("cli.apply_title"), f"v{__version__}")
    print(t("cli.apply_preset", path=preset_path))
    print(t("cli.apply_targets", count=len(targets)))

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
            print(t("cli.exception_line", error=exc), file=sys.stderr)
            failed_count += 1
            entry = JournalEntry.new(mod.id, mod.title)
            entry.summary = t("cli.exception_summary", error=exc)
            entry.status = "failed"
            journal.record(entry)
            continue

        # Journal'a kayıt
        entry = JournalEntry.new(mod.id, mod.title)
        entry.summary = result.summary
        entry.status = "applied" if result.success else "failed"
        entry.data = dict(result.data) if isinstance(result.data, dict) else {}
        entry.data[REPORT_PARAMS_KEY] = redact_params(mod.id, params)
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
        t("cli.apply_done", ok=len(targets) - failed_count, failed=failed_count)
    )
    return 1 if failed_count else 0


def run(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list:
        return cmd_list()

    if args.info:
        if not args.preset:
            parser.error(t("cli.info_needs_preset"))
        return cmd_info(args.preset)

    if args.apply:
        if not args.preset:
            parser.error(t("cli.apply_needs_preset"))
        only = _csv_set(args.only) if args.only else None
        skip = _csv_set(args.skip) if args.skip else None
        return cmd_apply(args.preset, only, skip)

    parser.print_help()
    return 0


def _csv_set(s: str) -> set[str]:
    return {x.strip() for x in s.split(",") if x.strip()}
