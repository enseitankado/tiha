"""Komut satırı modu — arayüzsüz çalışma.

Seçeneksiz ``tiha`` grafik sihirbazı açar. Seçeneklerle:

* ``-l``: adımları listeler.
* ``-p / -n / -r / -u ADIM``: tek bir adımın parametrelerini gösterir,
  önizler, uygular ya da geri alır. Parametreler ``-o ANAHTAR=DEĞER`` ile
  verilir; verilmeyenler arayüzdeki gibi varsayılanlardan ve tahtanın şu
  anki durumundan doldurulur.
* ``--apply --preset DOSYA``: ön ayar dosyasındaki adımları sırayla uygular.

Yardım metni ve çıkış kodları: ``tiha -h`` (``cli.help_text``).
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


# Çıkış kodları (yardım metninde de listelenir).
EXIT_OK = 0
EXIT_STEP_FAILED = 1
EXIT_UNKNOWN_STEP = 11
EXIT_BAD_PARAM = 12
EXIT_NEEDS_CONFIRM = 13
EXIT_NOTHING_TO_UNDO = 14
EXIT_USAGE = 64


class _Parser(argparse.ArgumentParser):
    """Hata iletisi Türkçe; yardım metni elle yazılmış ``cli.help_text``."""

    def error(self, message: str) -> None:  # type: ignore[override]
        print(t("cli.usage_error", prog=self.prog, message=_tr_argparse(message)),
              file=sys.stderr)
        raise SystemExit(EXIT_USAGE)

    def print_help(self, file=None) -> None:  # type: ignore[override]
        print(t("cli.help_text", version=__version__), file=file or sys.stdout)


def _tr_argparse(message: str) -> str:
    """argparse'ın İngilizce hata iletilerini Türkçeye çevirir."""
    import re

    rules = (
        (r"^unrecognized arguments: (.*)$", "cli.err_unrecognized"),
        (r"^argument (\S+): expected one argument$", "cli.err_needs_value"),
        (r"^argument (\S+): ignored explicit argument '(.*)'$", "cli.err_no_value"),
        (r"^ambiguous option: (\S+) could match (.*)$", "cli.err_ambiguous"),
    )
    for pattern, key in rules:
        m = re.match(pattern, message)
        if m:
            return t(key, arg=m.group(1), extra=m.group(2) if m.lastindex and m.lastindex > 1 else "")
    return message


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="tiha", add_help=False)
    p.add_argument("-h", "--help", action="store_true")
    p.add_argument("-V", "--version", action="store_true")
    p.add_argument("-l", "--list", action="store_true")
    p.add_argument("-p", "--params", metavar="ADIM")
    p.add_argument("-n", "--preview", metavar="ADIM")
    p.add_argument("-r", "--run", metavar="ADIM")
    p.add_argument("-u", "--undo", metavar="ADIM")
    p.add_argument("-o", "--set", action="append", default=[], metavar="ANAHTAR=DEĞER")
    p.add_argument("-y", "--yes", action="store_true")
    # Ön ayarla toplu uygulama (eski arayüz, değişmedi)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--info", action="store_true")
    p.add_argument("--preset", type=Path)
    p.add_argument("--only")
    p.add_argument("--skip")
    return p


def is_cli_invocation(argv: list[str]) -> bool:
    """Herhangi bir argüman verildiyse komut satırı modu; yoksa arayüz."""
    return bool(argv)


def _parse(argv: list[str]):
    """Seçenekleri çözer ve birbirleriyle uyumunu denetler; hatada 64."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.help or args.version:
        return parser, args
    single = [x for x in (args.params, args.preview, args.run, args.undo) if x]
    if len(single) > 1:
        parser.error(t("cli.one_action"))
    if args.set and not args.run:
        parser.error(t("cli.set_needs_run"))
    return parser, args


def check_usage(argv: list[str]) -> None:
    """Seçenekleri yetki denetiminden önce doğrular; hatalıysa 64 ile çıkar."""
    _parse(argv)


def needs_root(argv: list[str]) -> bool:
    """-h, -V ve -l (yalnız bunlar) yetki istemez."""
    if not argv:
        return True
    _, args = _parse(argv)
    if args.help or args.version:
        return False
    acts = (args.params, args.preview, args.run, args.undo, args.set,
            args.yes, args.apply, args.info, args.preset, args.only, args.skip)
    return not (args.list and not any(acts))


# --- Adım çözümleme ------------------------------------------------------------


def step_slug(mod) -> str:
    """Adımın kısa adı: arayüzdeki adından (ör. "Yerel hesaplar" → yerel-hesaplar)."""
    from .meb_data import branch_to_username

    name = mod.sidebar_title or mod.title or mod.id
    return branch_to_username(name.replace("&", " ")).replace("_", "-")


def resolve_step(ref: str):
    """Sıra no, kimlik, mNN ya da kısa addan modülü bulur; yoksa None."""
    from ..modules import all_modules

    mods = all_modules()
    ref = ref.strip()
    if ref.isdigit():
        idx = int(ref)
        return mods[idx - 1] if 1 <= idx <= len(mods) else None
    low = ref.lower().replace("_", "-")
    for m in mods:
        mid = m.id.lower()
        if low in (mid.replace("_", "-"), mid.split("_", 1)[0],
                   mid.split("_", 1)[-1].replace("_", "-"), step_slug(m)):
            return m
    return None


def _need_step(ref: str):
    mod = resolve_step(ref)
    if mod is None:
        print(t("cli.unknown_step", ref=ref), file=sys.stderr)
        raise SystemExit(EXIT_UNKNOWN_STEP)
    return mod


# --- Parametreler ----------------------------------------------------------------

_SKIP_TYPES = ("heading", "button", "readonly")
_TRUE = {"true", "1", "yes", "on", "evet", "e"}
_FALSE = {"false", "0", "no", "off", "hayir", "hayır", "h", ""}


def _schema_fields(mod) -> list[dict]:
    from ..ui.params import PARAMS_SCHEMA

    return [f for f in PARAMS_SCHEMA.get(mod.id, []) if f.get("type") not in _SKIP_TYPES]


def default_params(mod) -> dict[str, str]:
    """Arayüzün form açılışındaki değerler: şema varsayılanı → sistemden
    okunan değer (default_from) → adımın mevcut yapılandırması."""
    values: dict[str, str] = {}
    for f in _schema_fields(mod):
        value = f.get("default", "")
        source = f.get("default_from")
        fn = getattr(mod, source, None) if source else None
        if callable(fn):
            try:
                got = fn()
            except Exception as exc:
                log.warning("default_from başarısız (%s): %s", source, exc)
            else:
                if got is not None:
                    value = got
        values[f["key"]] = "" if value is None else str(value)
    # Arayüzde formu adımın kendi yapılandırmasından dolduran iki adım.
    if mod.id == "m06_remote_syslog":
        try:
            from ..modules.m06_remote_syslog import LOG_PROFILES, _parse_config

            config = _parse_config() or {}
            if config:
                values["syslog_host"] = config.get("host", values.get("syslog_host", ""))
                values["syslog_port"] = str(config.get("port", 514))
                values["syslog_proto"] = config.get("proto", "udp")
                prof = LOG_PROFILES.get(config.get("profile", "bakim"))
                if prof:
                    values["log_profile"] = prof["label"]
        except Exception as exc:
            log.warning("m06 yapılandırması okunamadı: %s", exc)
    if mod.id == "m11_power_management":
        fn = getattr(mod, "get_current_config", None)
        if callable(fn):
            try:
                values.update({k: str(v) for k, v in (fn() or {}).items() if k in values})
            except Exception as exc:
                log.warning("m11 yapılandırması okunamadı: %s", exc)
    return values


def parse_overrides(mod, items: list[str]) -> dict[str, str]:
    """-o ANAHTAR=DEĞER listesini doğrular ve arayüzün değer biçimine çevirir."""
    fields = {f["key"]: f for f in _schema_fields(mod)}
    out: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        key = key.strip()
        if not sep or not key:
            print(t("cli.bad_set", item=item), file=sys.stderr)
            raise SystemExit(EXIT_BAD_PARAM)
        field = fields.get(key)
        if field is None:
            print(t("cli.unknown_param", key=key, step=mod.id,
                    keys=", ".join(fields) or "-"), file=sys.stderr)
            raise SystemExit(EXIT_BAD_PARAM)
        kind = field.get("type", "text")
        if kind == "bool":
            low = value.strip().lower()
            if low in _TRUE:
                value = "True"
            elif low in _FALSE:
                value = "False"
            else:
                print(t("cli.bad_bool", key=key, value=value), file=sys.stderr)
                raise SystemExit(EXIT_BAD_PARAM)
        elif kind in ("spin", "number"):
            try:
                value = str(int(value.strip()))
            except ValueError:
                print(t("cli.bad_number", key=key, value=value), file=sys.stderr)
                raise SystemExit(EXIT_BAD_PARAM)
        elif kind == "select":
            options = field.get("options") or []
            if value not in options:
                print(t("cli.bad_option", key=key, value=value,
                        options=" | ".join(options)), file=sys.stderr)
                raise SystemExit(EXIT_BAD_PARAM)
        elif kind == "textarea":
            # Kabukta çok satır yazmak zor: "\n" satır sonu sayılır.
            value = value.replace("\\n", "\n")
        out[key] = value
    return out


# --- Tek adım komutları ----------------------------------------------------------


def cmd_params(mod) -> int:
    from .report_log import looks_secret

    console.banner_open(f"{mod.title}  ({mod.id})", f"v{__version__}")
    print(t("cli.params_short_name", slug=step_slug(mod)))
    fields = _schema_fields(mod)
    if not fields:
        print(t("cli.params_none"))
        return EXIT_OK
    current = default_params(mod)
    for f in fields:
        kind = f.get("type", "text")
        val = current.get(f["key"], "")
        if kind == "password" or looks_secret(f["key"]):
            val = t("cli.params_secret") if val else ""
        print()
        print(f"  {f['key']}  [{t('cli.type_' + kind) if kind in _TYPE_KEYS else kind}]")
        print(f"      {f.get('label', '')}")
        if kind == "select":
            print("      " + t("cli.params_options", options=" | ".join(f.get("options") or [])))
        print("      " + t("cli.params_current", value=val if val != "" else t("cli.params_empty")))
    print()
    print(t("cli.params_hint", slug=step_slug(mod)))
    return EXIT_OK


_TYPE_KEYS = {"bool", "text", "password", "spin", "number", "select", "textarea",
              "branch_accounts", "file"}


def cmd_preview(mod) -> int:
    console.banner_open(f"{mod.title}  ({mod.id})", t("cli.preview_title"))
    try:
        print(mod.preview())
    except Exception as exc:
        print(t("cli.exception_line", error=exc), file=sys.stderr)
        return EXIT_STEP_FAILED
    return EXIT_OK


def _record(journal, mod, params: dict, result) -> None:
    entry = JournalEntry.new(mod.id, mod.title)
    entry.summary = result.summary
    entry.status = (
        "skipped" if result.not_applicable
        else "applied" if result.success else "failed"
    )
    entry.data = dict(result.data) if isinstance(result.data, dict) else {}
    entry.data[REPORT_PARAMS_KEY] = redact_params(mod.id, params)
    journal.record(entry)


def cmd_run(mod, overrides: list[str], assume_yes: bool) -> int:
    params = default_params(mod)
    params.update(parse_overrides(mod, overrides))

    # Arayüzdeki onay pencerelerinin karşılığı (ör. hesap silme).
    pre_check = getattr(mod, "pre_apply_check", None)
    if callable(pre_check):
        for conf in pre_check(params) or []:
            print(f"⚠ {conf.get('title', '')}", file=sys.stderr)
            if conf.get("message"):
                print(conf["message"], file=sys.stderr)
            if not assume_yes:
                print(t("cli.needs_yes"), file=sys.stderr)
                return EXIT_NEEDS_CONFIRM
            params.update(conf.get("params") or {})

    console.banner_open(f"{mod.title}  ({mod.id})", f"v{__version__}")

    def progress(line: str) -> None:
        print(f"  {line}")

    journal = Journal()
    try:
        result = mod.apply_with_logging(params, progress=progress)
    except Exception as exc:
        print(t("cli.exception_line", error=exc), file=sys.stderr)
        entry = JournalEntry.new(mod.id, mod.title)
        entry.summary = t("cli.exception_summary", error=exc)
        entry.status = "failed"
        journal.record(entry)
        return EXIT_STEP_FAILED
    _record(journal, mod, params, result)
    if result.details:
        print(result.details)
    if result.success:
        console.ok(result.summary)
    elif result.not_applicable:
        console.note(result.summary)
    else:
        console.fail(result.summary)
    if result.warning:
        print(f"⚠ {result.warning}", file=sys.stderr)
    return EXIT_OK if (result.success or result.not_applicable) else EXIT_STEP_FAILED


def cmd_undo(mod, assume_yes: bool) -> int:
    if not mod.undo_supported:
        print(t("cli.undo_unsupported", step=mod.title), file=sys.stderr)
        return EXIT_NOTHING_TO_UNDO
    journal = Journal()
    entry = journal.last_applied(mod.id)
    if entry is None:
        print(t("cli.undo_nothing", step=mod.title), file=sys.stderr)
        return EXIT_NOTHING_TO_UNDO
    undo_params = None
    prompt = mod.pre_undo_prompt(entry.data)
    if prompt:
        print(f"? {prompt.get('title', '')}", file=sys.stderr)
        # Onaysız güvenli seçenek (ör. ek hesapları silme) seçilir.
        undo_params = prompt.get("yes_params" if assume_yes else "no_params", {})
        print(t("cli.undo_prompt_choice_yes" if assume_yes else "cli.undo_prompt_choice_no"),
              file=sys.stderr)
    console.banner_open(f"{mod.title}  ({mod.id})", t("cli.undo_title"))
    try:
        result = mod.undo_with_logging(entry.data, undo_params)
    except Exception as exc:
        print(t("cli.exception_line", error=exc), file=sys.stderr)
        return EXIT_STEP_FAILED
    if result.success:
        journal.mark_undone(mod.id)
        console.ok(result.summary)
        return EXIT_OK
    console.fail(result.summary)
    return EXIT_STEP_FAILED


def cmd_list() -> int:
    from ..modules import all_modules
    console.banner_open(t("cli.list_title"), f"v{__version__}")
    print(t("cli.list_header"))
    for idx, m in enumerate(all_modules(), 1):
        name = m.sidebar_title or m.title or m.id
        print(f"  {idx:2d}  {step_slug(m):26s} {m.id:24s} {name}")
    print()
    print(t("cli.list_hint"))
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
        entry.status = (
            "skipped" if result.not_applicable
            else "applied" if result.success else "failed"
        )
        entry.data = dict(result.data) if isinstance(result.data, dict) else {}
        entry.data[REPORT_PARAMS_KEY] = redact_params(mod.id, params)
        journal.record(entry)

        if result.success:
            console.ok(result.summary)
        elif result.not_applicable:
            console.note(result.summary)
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
    parser, args = _parse(argv)

    if args.help:
        parser.print_help()
        return EXIT_OK
    if args.version:
        print(f"TiHA {__version__}")
        return EXIT_OK
    if args.list:
        return cmd_list()

    if args.params:
        return cmd_params(_need_step(args.params))
    if args.preview:
        return cmd_preview(_need_step(args.preview))
    if args.run:
        return cmd_run(_need_step(args.run), args.set, args.yes)
    if args.undo:
        return cmd_undo(_need_step(args.undo), args.yes)

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

    parser.error(t("cli.no_action"))
    return EXIT_USAGE


def _csv_set(s: str) -> set[str]:
    return {x.strip() for x in s.split(",") if x.strip()}
