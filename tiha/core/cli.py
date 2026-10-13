"""Komut satırı modu — arayüzsüz çalışma.

Seçeneksiz ``tiha`` grafik sihirbazı açar. Seçeneklerle adımlar tek tek
listelenir, ayarları gösterilir, önizlenir, uygulanır ya da geri alınır;
ön ayar dosyasıyla toplu uygulama da yapılır. Adımlar sihirbazın sol
menüsündeki numaralarla (ya da kısa adlarıyla) anılır; numaralar
``all_modules()`` sırasından gelir, yardım örneklerindeki numaralar da
yazım anında oradan hesaplanır.

Yardım (``tiha -h``) GNU düzenindedir; renk yalnız terminalde ve
``NO_COLOR`` tanımlı değilken kullanılır (``--renk`` ile değiştirilebilir).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import textwrap
from pathlib import Path

from .. import __version__
from . import console
from .i18n import t
from .logger import get_logger
from .preset import import_preset
from .undo import Journal, JournalEntry
from .report_log import REPORT_PARAMS_KEY, redact_params

log = get_logger(__name__)


# Çıkış kodları (yardımda da listelenir).
EXIT_OK = 0
EXIT_STEP_FAILED = 1
EXIT_UNKNOWN_STEP = 11
EXIT_BAD_PARAM = 12
EXIT_NEEDS_CONFIRM = 13
EXIT_NOTHING_TO_UNDO = 14
EXIT_USAGE = 64


# --- Renk ---------------------------------------------------------------------

_COLOR_MODE = "otomatik"   # otomatik | her-zaman | hicbir-zaman


def _use_color(stream) -> bool:
    if _COLOR_MODE == "her-zaman":
        return True
    if _COLOR_MODE == "hicbir-zaman" or os.environ.get("NO_COLOR"):
        return False
    return stream.isatty() and os.environ.get("TERM", "") != "dumb"


_STYLES = {
    "h": "1;32",    # bölüm başlığı: kalın yeşil
    "o": "1;36",    # seçenek: kalın camgöbeği
    "m": "36",      # seçenek argümanı: camgöbeği
    "b": "1",       # kalın
    "d": "2",       # soluk (açıklama, yorum)
    "e": "1;31",    # hata: kalın kırmızı
    "w": "33",      # uyarı: sarı
}


def _paint(text: str, style: str, stream=None) -> str:
    if not _use_color(stream or sys.stdout):
        return text
    return f"\033[{_STYLES[style]}m{text}\033[0m"


def _err(message: str) -> None:
    """stderr'e (terminaldeyse kırmızı) hata satırı."""
    print(_paint(message, "e", sys.stderr), file=sys.stderr)


# --- Seçenekler ------------------------------------------------------------------

# (kısa, uzun, argüman, açıklama anahtarı, gizli eski adlar)
_OPTION_GROUPS: list[tuple[str, list[tuple[str, str, str, str, tuple[str, ...]]]]] = [
    ("cli.h_sec_general", [
        ("-h", "--yardim", "", "cli.h_opt_help", ("--help",)),
        ("-V", "--surum", "", "cli.h_opt_version", ("--version",)),
        ("-l", "--listele", "", "cli.h_opt_list", ("--list",)),
        ("", "--renk", "NE-ZAMAN", "cli.h_opt_color", ("--color",)),
    ]),
    ("cli.h_sec_step", [
        ("-a", "--ayarlar", "ADIM", "cli.h_opt_params", ("--params",)),
        ("-o", "--onizle", "ADIM", "cli.h_opt_preview", ("--preview",)),
        ("-u", "--uygula", "ADIM", "cli.h_opt_run", ("--run",)),
        ("-g", "--geri-al", "ADIM", "cli.h_opt_undo", ("--undo",)),
        ("-d", "--deger", "ANAHTAR=DEĞER", "cli.h_opt_set", ("--set",)),
        ("-e", "--evet", "", "cli.h_opt_yes", ("--yes",)),
    ]),
    ("cli.h_sec_preset", [
        ("-t", "--toplu", "", "cli.h_opt_apply", ("--apply",)),
        ("-b", "--bilgi", "", "cli.h_opt_info", ("--info",)),
        ("-f", "--on-ayar", "DOSYA", "cli.h_opt_preset", ("--preset",)),
        ("", "--yalniz", "ADIM,...", "cli.h_opt_only", ("--only",)),
        ("", "--atla", "ADIM,...", "cli.h_opt_skip", ("--skip",)),
    ]),
]

_DESTS = {
    "--yardim": "help", "--surum": "version", "--listele": "list", "--renk": "renk",
    "--ayarlar": "params", "--onizle": "preview", "--uygula": "run",
    "--geri-al": "undo", "--deger": "set", "--evet": "yes",
    "--toplu": "apply", "--bilgi": "info", "--on-ayar": "preset",
    "--yalniz": "only", "--atla": "skip",
}

# Örnekler: (komut, açıklama anahtarı). {N:kimlik} yazım anında adımın
# sol menüdeki numarasına, {S:kimlik} kısa adına çevrilir.
_EXAMPLES: list[tuple[str, str]] = [
    ("sudo tiha", "cli.h_ex_gui"),
    ("tiha -l", "cli.h_ex_list"),
    ("tiha -h | less -R", "cli.h_ex_pager"),
    ("sudo tiha -a {N:m01_initial_passwords}", "cli.h_ex_params"),
    ("sudo tiha -o {N:m04_ssh_and_samba}", "cli.h_ex_preview"),
    ("sudo tiha -u {N:m09_system_update}", "cli.h_ex_update"),
    ("sudo tiha -u {S:m01_initial_passwords} -d reserve_count=10", "cli.h_ex_reserve"),
    ("sudo tiha -u {N:m01_initial_passwords} -d reserve_count=3 -e", "cli.h_ex_reserve_down"),
    (" sudo tiha -u {N:m01_initial_passwords} -d root_password='Gizli.Parola1'", "cli.h_ex_rootpw"),
    ("sudo tiha -u {N:m03_otp_secrets} -d teacher_names='AYŞE YILMAZ\\nMEHMET DEMİR'", "cli.h_ex_pin"),
    ("sudo tiha -u {S:m04_ssh_and_samba} -d enable_ssh=evet -d enable_samba=hayir", "cli.h_ex_ssh"),
    ("sudo tiha -u {N:m07_time_sync} -d timezone=Europe/Istanbul", "cli.h_ex_ntp"),
    ("sudo tiha -u {S:m11_power_management} -d auto_enabled=evet -d auto_hour=17 -d auto_minute=30",
     "cli.h_ex_power"),
    ("sudo tiha -g {N:m15_wake_on_lan}", "cli.h_ex_undo"),
    ("sudo tiha -b -f okul-a.json", "cli.h_ex_info"),
    ("sudo tiha -t -f okul-a.json", "cli.h_ex_apply"),
    ("sudo tiha -t -f okul-a.json --atla {N:m10_image_sanitize}", "cli.h_ex_apply_skip"),
    ("sudo tiha -t -f okul-a.json --yalniz {N:m07_time_sync},{N:m08_hostname}", "cli.h_ex_apply_only"),
    ("NO_COLOR=1 tiha -h > tiha-yardim.txt", "cli.h_ex_nocolor"),
    ("curl -fsSL https://raw.githubusercontent.com/enseitankado/tiha/main/bootstrap.sh"
     " | bash -s -- -l", "cli.h_ex_bootstrap"),
]

_EXIT_CODES = [
    (EXIT_OK, "cli.h_exit_0"), (EXIT_STEP_FAILED, "cli.h_exit_1"), (2, "cli.h_exit_2"),
    (3, "cli.h_exit_3"), (10, "cli.h_exit_10"), (EXIT_UNKNOWN_STEP, "cli.h_exit_11"),
    (EXIT_BAD_PARAM, "cli.h_exit_12"), (EXIT_NEEDS_CONFIRM, "cli.h_exit_13"),
    (EXIT_NOTHING_TO_UNDO, "cli.h_exit_14"), (EXIT_USAGE, "cli.h_exit_64"),
]


def _fill_step_refs(text: str) -> str:
    from ..modules import all_modules

    mods = all_modules()
    index = {m.id: (i, m) for i, m in enumerate(mods, start=1)}

    def repl(match: re.Match) -> str:
        kind, mid = match.group(1), match.group(2)
        if mid not in index:
            return mid
        num, mod = index[mid]
        return str(num) if kind == "N" else step_slug(mod)

    return re.sub(r"\{([NS]):([a-z0-9_]+)\}", repl, text)


def _wrap_command(cmd: str, limit: int) -> list[str]:
    """Uzun örnek komutu seçenek sınırlarından `\\` ile böler."""
    if len(cmd) <= limit:
        return [cmd]
    parts = re.split(r" (?=-[a-z]\b|-- |\| )", cmd)
    lines = [parts[0]]
    for part in parts[1:]:
        if len(lines[-1]) + 1 + len(part) + 2 > limit:
            lines[-1] += " \\"
            lines.append("  " + part)
        else:
            lines[-1] += " " + part
    return lines


def help_text(stream=None) -> str:
    """GNU düzeninde, terminaldeyse renkli Türkçe yardım."""
    stream = stream or sys.stdout
    P = lambda text, style: _paint(text, style, stream)  # noqa: E731
    width = 79
    col = 30
    out: list[str] = []
    out.append(f"{P(t('cli.h_usage'), 'h')} {P('tiha', 'b')} [{P(t('cli.h_option_word'), 'm')}]...")
    out.append(" " * (len(t("cli.h_usage")) + 1)
               + f"{P('tiha', 'b')} {P('-u', 'o')} {P('ADIM', 'm')} "
               f"[{P('-d', 'o')} {P('ANAHTAR=DEĞER', 'm')}]... [{P('-e', 'o')}]")
    out.append("")
    for para in t("cli.h_description", version=__version__).split("\n\n"):
        out += textwrap.wrap(para, width, break_on_hyphens=False) + [""]
    out.append(t("cli.h_mandatory"))
    for title_key, options in _OPTION_GROUPS:
        out.append("")
        out.append(P(t(title_key), "h"))
        for short, long, meta, desc_key, _old in options:
            plain = f"  {short + ', ' if short else '    '}{long}{'=' + meta if meta else ''}"
            colored = (
                "  " + (P(short, "o") + ", " if short else "    ") + P(long, "o")
                + ("=" + P(meta, "m") if meta else "")
            )
            desc = textwrap.wrap(t(desc_key), width - col, break_on_hyphens=False)
            if len(plain) >= col - 1:
                out.append(colored)
                out += [" " * col + line for line in desc]
            else:
                out.append(colored + " " * (col - len(plain)) + desc[0])
                out += [" " * col + line for line in desc[1:]]
    for key in ("cli.h_sec_steps", "cli.h_sec_values", "cli.h_sec_auth"):
        out.append("")
        out.append(P(t(key), "h"))
        for i, para in enumerate(t(key + "_body").split("\n\n")):
            if i:
                out.append("")
            out += textwrap.wrap(para, width - 2, initial_indent="  ", subsequent_indent="  ",
                                 break_on_hyphens=False)
    out.append("")
    out.append(P(t("cli.h_sec_examples"), "h"))
    for cmd, desc_key in _EXAMPLES:
        lines = _wrap_command(_fill_step_refs(cmd), width - 4)
        out.append("  " + P("$", "d") + " " + P(lines[0], "b"))
        out += ["      " + P(line, "b") for line in lines[1:]]
        out += textwrap.wrap(_fill_step_refs(t(desc_key)), width - 6,
                             initial_indent="      ", subsequent_indent="      ",
                             break_on_hyphens=False)
    out.append("")
    out.append(P(t("cli.h_sec_exit"), "h"))
    for code, key in _EXIT_CODES:
        out.append(f"  {P(f'{code:>3}', 'b')}  {t(key)}")
    out.append("")
    out.append(t("cli.h_report_bugs"))
    return "\n".join(out)


class _Parser(argparse.ArgumentParser):
    """Hata iletisi Türkçe; yardım ``help_text()`` ile üretilir."""

    def error(self, message: str) -> None:  # type: ignore[override]
        _err(t("cli.usage_error_line", prog=self.prog, message=_tr_argparse(message)))
        print(t("cli.usage_error_hint", prog=self.prog), file=sys.stderr)
        raise SystemExit(EXIT_USAGE)

    def print_help(self, file=None) -> None:  # type: ignore[override]
        stream = file or sys.stdout
        print(help_text(stream), file=stream)


def _tr_argparse(message: str) -> str:
    """argparse'ın İngilizce hata iletilerini Türkçeye çevirir."""
    rules = (
        (r"^unrecognized arguments: (.*)$", "cli.err_unrecognized"),
        (r"^argument (\S+): expected one argument$", "cli.err_needs_value"),
        (r"^argument (\S+): ignored explicit argument '(.*)'$", "cli.err_no_value"),
        (r"^ambiguous option: (\S+) could match (.*)$", "cli.err_ambiguous"),
        (r"^argument (\S+): invalid choice: '(.*?)'.*$", "cli.err_bad_choice"),
    )
    legacy = {o for _t, opts in _OPTION_GROUPS for *_x, old in opts for o in old}
    for pattern, key in rules:
        m = re.match(pattern, message)
        if m:
            # "-u/--uygula/--run" → "-u/--uygula": eski İngilizce adlar gizli kalsın.
            arg = "/".join(x for x in m.group(1).split("/") if x not in legacy)
            return t(key, arg=arg, extra=m.group(2) if m.lastindex and m.lastindex > 1 else "")
    return message


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="tiha", add_help=False)
    for _title, options in _OPTION_GROUPS:
        for short, long, meta, _desc, old in options:
            flags = [f for f in (short, long, *old) if f]
            dest = _DESTS[long]
            if dest == "set":
                p.add_argument(*flags, dest=dest, action="append", default=[], metavar=meta)
            elif dest == "renk":
                p.add_argument(*flags, dest=dest, default="otomatik",
                               choices=("otomatik", "her-zaman", "hicbir-zaman",
                                        "auto", "always", "never"))
            elif dest == "preset":
                p.add_argument(*flags, dest=dest, type=Path, metavar=meta)
            elif meta:
                p.add_argument(*flags, dest=dest, metavar=meta)
            else:
                p.add_argument(*flags, dest=dest, action="store_true")
    return p


def is_cli_invocation(argv: list[str]) -> bool:
    """Herhangi bir argüman verildiyse komut satırı modu; yoksa arayüz."""
    return bool(argv)


def _parse(argv: list[str]):
    """Seçenekleri çözer ve birbirleriyle uyumunu denetler; hatada 64."""
    global _COLOR_MODE
    # Hata iletileri de --renk'e uysun: önce yalnız onu oku.
    for i, arg in enumerate(argv):
        if arg.startswith(("--renk=", "--color=")):
            _COLOR_MODE = arg.split("=", 1)[1]
        elif arg in ("--renk", "--color") and i + 1 < len(argv):
            _COLOR_MODE = argv[i + 1]
    _COLOR_MODE = {"auto": "otomatik", "always": "her-zaman", "never": "hicbir-zaman"}.get(
        _COLOR_MODE, _COLOR_MODE)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.help or args.version:
        return parser, args
    single = [x for x in (args.params, args.preview, args.run, args.undo) if x]
    if len(single) > 1:
        parser.error(t("cli.one_action"))
    if args.set and not args.run:
        parser.error(t("cli.set_needs_run"))
    if (args.only or args.skip or args.preset) and not (args.apply or args.info):
        parser.error(t("cli.preset_needs_mode"))
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
    """Sol menüdeki numara, kısa ad ya da modül kimliğinden adımı bulur."""
    from ..modules import all_modules

    mods = all_modules()
    ref = ref.strip()
    if ref.isdigit():
        idx = int(ref)
        return mods[idx - 1] if 1 <= idx <= len(mods) else None
    low = ref.lower().replace("_", "-")
    for m in mods:
        mid = m.id.lower()
        # Kimlik ön ayar dosyalarıyla uyum için kabul edilir; kullanıcıya
        # numara ve kısa ad gösterilir (m04 gibi iç kodlar menüdeki
        # numarayla karışmasın diye kabul edilmez).
        if low in (mid.replace("_", "-"), step_slug(m)):
            return m
    return None


def _need_step(ref: str):
    mod = resolve_step(ref)
    if mod is None:
        _err(t("cli.unknown_step", ref=ref))
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
            _err(t("cli.bad_set", item=item))
            raise SystemExit(EXIT_BAD_PARAM)
        field = fields.get(key)
        if field is None:
            _err(t("cli.unknown_param", key=key, step=mod.title,
                   keys=", ".join(fields) or "-"))
            raise SystemExit(EXIT_BAD_PARAM)
        kind = field.get("type", "text")
        if kind == "bool":
            low = value.strip().lower()
            if low in _TRUE:
                value = "True"
            elif low in _FALSE:
                value = "False"
            else:
                _err(t("cli.bad_bool", key=key, value=value))
                raise SystemExit(EXIT_BAD_PARAM)
        elif kind in ("spin", "number"):
            try:
                value = str(int(value.strip()))
            except ValueError:
                _err(t("cli.bad_number", key=key, value=value))
                raise SystemExit(EXIT_BAD_PARAM)
        elif kind == "select":
            options = field.get("options") or []
            if value not in options:
                _err(t("cli.bad_option", key=key, value=value,
                       options=" | ".join(options)))
                raise SystemExit(EXIT_BAD_PARAM)
        elif kind == "textarea":
            # Kabukta çok satır yazmak zor: "\n" satır sonu sayılır.
            value = value.replace("\\n", "\n")
        out[key] = value
    return out


# --- Tek adım komutları ----------------------------------------------------------


def _step_number(mod) -> int:
    from ..modules import all_modules

    return [m.id for m in all_modules()].index(mod.id) + 1


def cmd_params(mod) -> int:
    from .report_log import looks_secret

    num = _step_number(mod)
    print(_paint(f"{num}. {mod.title}", "h") + "  " + _paint(f"({step_slug(mod)})", "d"))
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
        kind_text = t("cli.type_" + kind) if kind in _TYPE_KEYS else kind
        print()
        print("  " + _paint(f["key"], "o") + "  " + _paint(f"[{kind_text}]", "m"))
        print("      " + f.get("label", ""))
        if kind == "select":
            print("      " + t("cli.params_options", options=" | ".join(f.get("options") or [])))
        shown = val if val != "" else t("cli.params_empty")
        print("      " + t("cli.params_current", value=_paint(shown, "b")))
    print()
    print(_paint(t("cli.params_hint", num=num), "d"))
    return EXIT_OK


_TYPE_KEYS = {"bool", "text", "password", "spin", "number", "select", "textarea",
              "branch_accounts", "file"}


def cmd_preview(mod) -> int:
    console.banner_open(f"{_step_number(mod)}. {mod.title}", t("cli.preview_title"))
    try:
        print(mod.preview())
    except Exception as exc:
        _err(t("cli.exception_line", error=exc))
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
                print(_paint(t("cli.needs_yes"), "w", sys.stderr), file=sys.stderr)
                return EXIT_NEEDS_CONFIRM
            params.update(conf.get("params") or {})

    console.banner_open(f"{_step_number(mod)}. {mod.title}", f"v{__version__}")

    def progress(line: str) -> None:
        print(f"  {line}")

    journal = Journal()
    try:
        result = mod.apply_with_logging(params, progress=progress)
    except Exception as exc:
        _err(t("cli.exception_line", error=exc))
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
        _err(t("cli.undo_unsupported", step=mod.title))
        return EXIT_NOTHING_TO_UNDO
    journal = Journal()
    entry = journal.last_applied(mod.id)
    if entry is None:
        _err(t("cli.undo_nothing", step=mod.title))
        return EXIT_NOTHING_TO_UNDO
    undo_params = None
    prompt = mod.pre_undo_prompt(entry.data)
    if prompt:
        print(f"? {prompt.get('title', '')}", file=sys.stderr)
        # Onaysız güvenli seçenek (ör. ek hesapları silme) seçilir.
        undo_params = prompt.get("yes_params" if assume_yes else "no_params", {})
        print(t("cli.undo_prompt_choice_yes" if assume_yes else "cli.undo_prompt_choice_no"),
              file=sys.stderr)
    console.banner_open(f"{_step_number(mod)}. {mod.title}", t("cli.undo_title"))
    try:
        result = mod.undo_with_logging(entry.data, undo_params)
    except Exception as exc:
        _err(t("cli.exception_line", error=exc))
        return EXIT_STEP_FAILED
    if result.success:
        journal.mark_undone(mod.id)
        console.ok(result.summary)
        return EXIT_OK
    console.fail(result.summary)
    return EXIT_STEP_FAILED


def cmd_list() -> int:
    from ..modules import all_modules
    print(_paint(t("cli.list_title", version=__version__), "h"))
    print(_paint(t("cli.list_header"), "d"))
    for idx, m in enumerate(all_modules(), 1):
        name = m.sidebar_title or m.title or m.id
        print(f"  {_paint(f'{idx:>2}', 'o')}  {_paint(step_slug(m).ljust(27), 'm')} {name}")
    print()
    print(_paint(t("cli.list_hint"), "d"))
    return 0


def cmd_info(preset_path: Path) -> int:
    try:
        params_by_module = import_preset(preset_path)
    except (OSError, ValueError) as exc:
        _err(t("cli.preset_read_error", error=exc))
        return 10

    from ..modules import all_modules
    known = {m.id: m for m in all_modules()}

    console.banner_open(t("cli.info_title"), str(preset_path))
    print(t("cli.info_total", count=len(params_by_module)))
    for mid, params in params_by_module.items():
        mod = known.get(mid)
        title = f"{_step_number(mod)}. {mod.title}" if mod else f"{mid} {t('cli.unknown_module')}"
        print(f"• {title}")
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
        _err(t("cli.preset_read_error", error=exc))
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
        console.step(f"[{idx}/{len(targets)}] {_step_number(mod)}. {mod.title}")

        def progress(line: str) -> None:
            print(f"  {line}")

        try:
            result = mod.apply_with_logging(params, progress=progress)
        except Exception as exc:
            result = None
            _err(t("cli.exception_line", error=exc))
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
        only = _step_ids(args.only) if args.only else None
        skip = _step_ids(args.skip) if args.skip else None
        return cmd_apply(args.preset, only, skip)

    parser.error(t("cli.no_action"))
    return EXIT_USAGE


def _step_ids(value: str) -> set[str]:
    """--yalniz / --atla: virgülle ayrılmış numara, kısa ad ya da kimlikler."""
    return {_need_step(x).id for x in _csv_set(value)}


def _csv_set(s: str) -> set[str]:
    return {x.strip() for x in s.split(",") if x.strip()}
