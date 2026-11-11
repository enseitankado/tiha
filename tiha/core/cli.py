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
import json
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
EXIT_ACTION_UNAVAILABLE = 15
EXIT_BUSY = 16
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
    """stderr'e (terminaldeyse kırmızı) hata satırı; --json'da ayrıca
    stdout'a bir ``error`` olayı."""
    if _JSON_OUT is not None:
        _emit("error", message=message)
    print(_paint(message, "e", sys.stderr), file=sys.stderr)


# --- Makine çıktısı (--json) ------------------------------------------------------
#
# Her satır bir JSON nesnesi (JSON Lines); "type" alanı olayın türüdür.
# Sözleşme: docs/makine-arayuzu.md. stdout yalnız bu satırları taşır;
# adımların ya da alt süreçlerin kendiliğinden bastığı her şey stderr'e
# yönlendirilir ki ayrıştıran taraf bozulmasın.

PROTOCOL_VERSION = 1
_JSON_OUT = None


def _start_json() -> None:
    global _JSON_OUT
    if _JSON_OUT is not None:
        return
    sys.stdout.flush()
    fd = os.dup(1)
    os.dup2(2, 1)
    _JSON_OUT = os.fdopen(fd, "w", buffering=1, encoding="utf-8")
    console.SILENT = True


def _emit(kind: str, **data) -> None:
    rec = {"type": kind, **data}
    try:
        _JSON_OUT.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        _JSON_OUT.flush()
    except (BrokenPipeError, OSError):
        pass


def _progress_printer(secrets: list[str] | None = None):
    """Adımın ilerleme satırları: --json'da olay, değilse girintili satır.

    --json'da verilen gizli değerler (parolalar) satırlardan silinir:
    olaylar yönetim aracının görev kaydına yazılır."""
    if _JSON_OUT is not None:
        from .report_log import scrub_text

        hidden = secrets or []
        return lambda line: _emit("progress", text=scrub_text(str(line), hidden))
    return lambda line: print(f"  {line}")


def _secrets_of(params: dict | None, data: dict | None = None) -> list[str]:
    from .report_log import _secret_values

    return _secret_values(params, data)


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
        ("-x", "--calistir", "ADIM [EYLEM]", "cli.h_opt_action", ("--action",)),
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
    ("cli.h_sec_machine", [
        ("", "--json", "", "cli.h_opt_json", ()),
        ("", "--sema", "", "cli.h_opt_schema", ("--schema",)),
        ("", "--degerler", "DOSYA", "cli.h_opt_values", ("--values",)),
    ]),
]

_DESTS = {
    "--yardim": "help", "--surum": "version", "--listele": "list", "--renk": "renk",
    "--ayarlar": "params", "--onizle": "preview", "--uygula": "run",
    "--geri-al": "undo", "--calistir": "action", "--deger": "set", "--evet": "yes",
    "--toplu": "apply", "--bilgi": "info", "--on-ayar": "preset",
    "--yalniz": "only", "--atla": "skip",
    "--json": "json", "--sema": "schema", "--degerler": "values",
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
    ("sudo tiha -x {N:m06_remote_syslog}", "cli.h_ex_actions"),
    ("sudo tiha -x {N:m06_remote_syslog} log-sunucusunu-test-et", "cli.h_ex_action_test"),
    ("sudo tiha -x {S:m12_ahenk_reset} ahenk-teshisini-calistir", "cli.h_ex_action_ahenk"),
    ("sudo tiha -x {N:m03_otp_secrets} tum-pin-anahtarlarini-sil -e", "cli.h_ex_action_purge"),
    ("sudo tiha -b -f okul-a.json", "cli.h_ex_info"),
    ("sudo tiha -t -f okul-a.json", "cli.h_ex_apply"),
    ("sudo tiha -t -f okul-a.json --atla {N:m10_image_sanitize}", "cli.h_ex_apply_skip"),
    ("sudo tiha -t -f okul-a.json --yalniz {N:m07_time_sync},{N:m08_hostname}", "cli.h_ex_apply_only"),
    ("NO_COLOR=1 tiha -h > tiha-yardim.txt", "cli.h_ex_nocolor"),
    ("tiha --sema > tiha-sema.json", "cli.h_ex_schema"),
    ("ssh root@tahta 'tiha --json -a {N:m06_remote_syslog}'", "cli.h_ex_json_params"),
    ("echo '{\"root_password\": \"Gizli.Parola1\"}' | sudo tiha --json -u {N:m01_initial_passwords} --degerler -",
     "cli.h_ex_values"),
    ("curl -fsSL https://raw.githubusercontent.com/enseitankado/tiha/main/bootstrap.sh"
     " | bash -s -- -l", "cli.h_ex_bootstrap"),
]

_EXIT_CODES = [
    (EXIT_OK, "cli.h_exit_0"), (EXIT_STEP_FAILED, "cli.h_exit_1"), (2, "cli.h_exit_2"),
    (3, "cli.h_exit_3"), (10, "cli.h_exit_10"), (EXIT_UNKNOWN_STEP, "cli.h_exit_11"),
    (EXIT_BAD_PARAM, "cli.h_exit_12"), (EXIT_NEEDS_CONFIRM, "cli.h_exit_13"),
    (EXIT_NOTHING_TO_UNDO, "cli.h_exit_14"), (EXIT_ACTION_UNAVAILABLE, "cli.h_exit_15"),
    (EXIT_BUSY, "cli.h_exit_16"),
    (EXIT_USAGE, "cli.h_exit_64"),
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
            # Birden çok argüman alan seçenek "=" ile değil boşlukla yazılır.
            sep = " " if " " in meta else "="
            plain = f"  {short + ', ' if short else '    '}{long}{sep + meta if meta else ''}"
            colored = (
                "  " + (P(short, "o") + ", " if short else "    ") + P(long, "o")
                + (sep + P(meta, "m") if meta else "")
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
            elif dest == "action":
                p.add_argument(*flags, dest=dest, nargs="+", metavar="ADIM")
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
    single = [x for x in (args.params, args.preview, args.run, args.undo, args.action) if x]
    if len(single) > 1:
        parser.error(t("cli.one_action"))
    if args.action and len(args.action) > 2:
        parser.error(t("cli.action_too_many", extra=" ".join(args.action[2:])))
    if args.set and not (args.run or args.action):
        parser.error(t("cli.set_needs_run"))
    if (args.only or args.skip or args.preset) and not (args.apply or args.info):
        parser.error(t("cli.preset_needs_mode"))
    if args.values and not (args.run or args.action):
        parser.error(t("cli.values_needs_run"))
    return parser, args


def check_usage(argv: list[str]) -> None:
    """Seçenekleri yetki denetiminden önce doğrular; hatalıysa 64 ile çıkar."""
    _parse(argv)


def needs_root(argv: list[str]) -> bool:
    """-h, -V, -l ve --sema (yalnız bunlar) yetki istemez."""
    if not argv:
        return True
    _, args = _parse(argv)
    if args.help or args.version:
        return False
    acts = (args.params, args.preview, args.run, args.undo, args.action, args.set,
            args.yes, args.apply, args.info, args.preset, args.only, args.skip,
            args.values)
    return not ((args.list or args.schema) and not any(acts))


# --- Adım çözümleme ------------------------------------------------------------


def step_slug(mod) -> str:
    """Adımın kısa adı: arayüzdeki adından (ör. "Yerel hesaplar" → yerel-hesaplar)."""
    from .meb_data import branch_to_username

    name = mod.sidebar_title or mod.title or mod.id
    return branch_to_username(name.replace("&", " ")).replace("_", "-")


# Kısa adlar menüdeki addan türer; ad değişince eski kısa ad betikleri
# bozmasın diye burada yaşamaya devam eder. tools/cli_check.py yayımlanmış
# her adın hâlâ çözüldüğünü denetler ve eksikse buraya eklenmesini ister.
_STEP_ALIASES: dict[str, str] = {
    "otomatik-ahenk-kaydi": "m12_ahenk_reset",   # v0.1.79'da "Ahenk" oldu
}
# (adım kimliği, eski eylem adı) -> düğmenin şemadaki anahtarı.
_ACTION_ALIASES: dict[tuple[str, str], str] = {}


def resolve_step(ref: str):
    """Sol menüdeki numara, kısa ad ya da modül kimliğinden adımı bulur."""
    from ..modules import all_modules

    mods = all_modules()
    ref = ref.strip()
    if ref.isdigit():
        idx = int(ref)
        return mods[idx - 1] if 1 <= idx <= len(mods) else None
    low = ref.lower().replace("_", "-")
    alias = _STEP_ALIASES.get(low)
    for m in mods:
        if alias and m.id == alias:
            return m
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


# --- Düğme eylemleri ---------------------------------------------------------------

_TR_ASCII = str.maketrans("çğıöşüÇĞİÖŞÜâîûÂÎÛ", "cgiosuCGIOSUaiuAIU")


def action_slug(field: dict) -> str:
    """Düğmenin komut satırındaki adı, etiketinden: "Log Sunucusunu Test Et"
    → log-sunucusunu-test-et."""
    text = (field.get("label") or field["key"]).translate(_TR_ASCII).lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def action_fields(mod) -> list[dict]:
    """Adımın komut satırından çalıştırılabilen düğmeleri. Başka bir programı
    açan düğmeler (ör. "ETA Kayıt'ı aç") masaüstü istediği için dışarıda."""
    from ..ui.params import PARAMS_SCHEMA

    return [f for f in PARAMS_SCHEMA.get(mod.id, [])
            if f.get("type") == "button" and f.get("action") and not f.get("launch")]


def resolve_action(mod, ref: str) -> dict | None:
    """Eylem adı, şema anahtarı (remove-student) ya da eski bir addan düğmeyi bulur."""
    low = ref.strip().lower().replace("_", "-")
    alias = _ACTION_ALIASES.get((mod.id, low))
    for f in action_fields(mod):
        if low in (action_slug(f), f["key"].replace("_", "-")) or f["key"] == alias:
            return f
    return None


def _action_available(mod, field: dict) -> bool:
    """Arayüzde düğme gizliyse (visible_when) burada da çalıştırılmaz."""
    gate = field.get("visible_when")
    fn = getattr(mod, gate, None) if gate else None
    if not callable(fn):
        return True
    try:
        return bool(fn())
    except Exception as exc:
        log.warning("visible_when başarısız (%s): %s", gate, exc)
        return True


# --- Parametreler ----------------------------------------------------------------

_SKIP_TYPES = ("heading", "button", "readonly")
_TRUE = {"true", "1", "yes", "on", "evet", "e"}
_FALSE = {"false", "0", "no", "off", "hayir", "hayır", "h", ""}


def _schema_fields(mod) -> list[dict]:
    from ..ui.params import PARAMS_SCHEMA

    return [f for f in PARAMS_SCHEMA.get(mod.id, []) if f.get("type") not in _SKIP_TYPES]


def default_params(mod) -> dict[str, str]:
    """Arayüzün form açılışındaki değerler: şema varsayılanı, sistemden
    okunabiliyorsa onun yerine adımın default_from yönteminin döndürdüğü.

    Arayüz de aynı şemayı aynı sırayla okur; bir alanı sistemden dolduran
    mantık yalnız modülde yazılır, ikisinde birden çalışır."""
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
    return values


def parse_overrides(mod, items: list[str]) -> dict[str, str]:
    """-d ANAHTAR=DEĞER listesini doğrular ve arayüzün değer biçimine çevirir."""
    out: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        key = key.strip()
        if not sep or not key:
            _err(t("cli.bad_set", item=item))
            raise SystemExit(EXIT_BAD_PARAM)
        out[key] = _convert_value(mod, key, value, from_shell=True)
    return out


def read_values(mod, path: str) -> dict[str, str]:
    """--degerler DOSYA: {"anahtar": değer} JSON nesnesi; "-" standart girdi.

    Parolalar böylece komut satırına (ps çıktısına, kabuk geçmişine)
    yazılmadan verilir. Değerler -d'deki gibi doğrulanır."""
    try:
        text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        data = json.loads(text or "{}")
    except (OSError, ValueError) as exc:
        _err(t("cli.values_read_error", error=exc))
        raise SystemExit(EXIT_BAD_PARAM)
    if not isinstance(data, dict):
        _err(t("cli.values_not_object"))
        raise SystemExit(EXIT_BAD_PARAM)
    out: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, bool):
            value = "True" if value else "False"
        elif value is None:
            value = ""
        out[str(key)] = _convert_value(mod, str(key), str(value), from_shell=False)
    return out


def _convert_value(mod, key: str, value: str, *, from_shell: bool) -> str:
    fields = {f["key"]: f for f in _schema_fields(mod)}
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
    elif kind == "textarea" and from_shell:
        # Kabukta çok satır yazmak zor: "\n" satır sonu sayılır.
        value = value.replace("\\n", "\n")
    return value


# --- Tek adım komutları ----------------------------------------------------------


def _step_number(mod) -> int:
    from ..modules import all_modules

    return [m.id for m in all_modules()].index(mod.id) + 1


def _step_info(mod) -> dict:
    return {"number": _step_number(mod), "slug": step_slug(mod), "id": mod.id,
            "title": mod.title, "menu_title": mod.sidebar_title or mod.title}


def _is_secret(field: dict) -> bool:
    from .report_log import looks_secret

    return field.get("type") == "password" or looks_secret(field.get("key", ""))


# Şemadaki alan anahtarlarından tahtadan bağımsız olup makine arayüzüne
# aynen geçenler. Modül yöntemine işaret edenler (default_from, visible_when,
# label_from, enable_also_when) tahtada hesaplanır; -a --json sonuçlarını verir.
_PORTABLE_FIELD_KEYS = (
    "key", "type", "label", "help", "help_more", "required", "default", "options",
    "min", "max", "step", "placeholder", "hint", "enable_when_field", "enable_when_any",
    "enables", "locked", "allowed_chars", "remote_warning", "remote_warning_when",
    "machine_only",
)


def _field_view(field: dict) -> dict:
    """Alanın sihirbazdaki görünüşü (bkz. ui/layout.py ve presentation)."""
    from ..ui import layout

    kind = field.get("type", "text")
    view = {"widget": layout.WIDGETS.get(kind, "entry")}
    if kind in layout.COMPACT_FIELD_WIDTHS:
        # Kısa değerli kutu: sola yaslı, bu kadar karakter genişliğinde
        # (0: doğal genişlik). Diğer türler satırı doldurur.
        view["width_chars"] = field.get("width", layout.COMPACT_FIELD_WIDTHS[kind])
    else:
        view["fill_row"] = True
    if kind == "textarea":
        view["height_px"] = layout.LONG_TEXT_HEIGHT
        view["monospace"] = bool(field.get("monospace"))
    if kind == "password":
        view["show_toggle"] = field.get("show_toggle", True)
        view["strength_meter"] = bool(field.get("strength_below"))
    if field.get("help"):
        # Kural (ui/layout.help_display): "bubble" her zaman "?" balonunda (ek
        # "Ayrıntılar" metni var); "auto" HELP_BUBBLE_OVER_LINES satırı aşarsa
        # balonda, aşmazsa alanın altında; "folded" aşarsa balonda, aşmazsa
        # alanın altında kapalı bir "Açıklama" katlayıcısında.
        if field.get("help_more"):
            view["help_mode"] = "bubble"
        elif field.get("help_folded"):
            view["help_mode"] = "folded"
        else:
            view["help_mode"] = "auto"
        # Kuralın varsayılan pencerede (presentation.form.help_measure)
        # TiHA'nın kendi ölçümüyle çözülmüş hâli: yönetim aracı ölçmeden
        # kullanabilir.
        try:
            lines = layout.measure_help_lines(layout.help_full_text(field))
        except Exception as exc:  # noqa: BLE001 — Pango yoksa çözülmeden kalır
            log.warning("Açıklama ölçülemedi (%s): %s", field.get("key"), exc)
            lines = None
        if lines is not None:
            view["help_lines"] = lines
            view["help_display"] = layout.help_display(field, lines)
        view["help_bubble_text"] = layout.help_full_text(field)
        # "?" düğmesinin yeri (balonda gösterildiğinde).
        adjacent = kind == "bool" or kind in layout.COMPACT_FIELD_WIDTHS
        button = {"side": "after_control", "adjacent": adjacent,
                  "valign": "top" if kind == "textarea" else "center"}
        if kind == "textarea":
            button["margin_top_px"] = layout.HELP_BUTTON_TEXTAREA_MARGIN_TOP
        view["help_button"] = button
    if field.get("help_more"):
        view["help_more_label"] = field.get("help_more_label") or t("ui.pages.help_more_expander")
    if field.get("style"):
        view["style"] = field["style"]
    return view


def _field_schema(field: dict) -> dict:
    out = {k: field[k] for k in _PORTABLE_FIELD_KEYS if k in field}
    out["secret"] = _is_secret(field)
    out["board_dependent"] = any(field.get(k) for k in (
        "default_from", "visible_when", "label_from", "enable_also_when"))
    out["view"] = _field_view(field)
    if field.get("type") == "branch_accounts":
        # Değer bir JSON metnidir; seçenekler şemanın catalogs.meb_branslar'ında.
        out["catalog"] = "meb_branslar"
        out["value_format"] = {
            "school_type": "okul türü anahtarı (catalogs.meb_branslar.okul_turleri)",
            "branches": "seçilen branş adları (listede olmayan elle yazılmış ad da olur)",
            "unselected": "tahtada hesabı olup işareti kaldırılan branşlar (hesapları silinir)",
        }
    return out


def _action_schema(field: dict) -> dict:
    out = {"name": action_slug(field), "key": field["key"], "label": field.get("label", ""),
           "help": field.get("help", ""), "destructive": field.get("style") == "destructive",
           "board_dependent": bool(field.get("visible_when") or field.get("label_from"))}
    # Düğme de bir form satırıdır (ad sütununda etiket, kontrol sütununda
    # düğme); açıklamanın gösterimi alanlarla aynı kuralla (fields[].view).
    view = _field_view(field)
    out["view"] = {k: view[k] for k in (
        "widget", "help_mode", "help_lines", "help_display", "help_bubble_text",
        "help_button", "style") if k in view}
    if field.get("confirm"):
        out["confirm"] = {"title": field["confirm"].get("title", ""),
                          "message": field["confirm"].get("message", "")}
    if field.get("stream_size"):
        # Çıktı penceresinin sihirbazdaki boyutu (genişlik, yükseklik; piksel).
        out["output_window"] = list(field["stream_size"])
    extras = [{"label": e.get("label", ""), "desktop_only": bool(e.get("launch"))}
              for e in field.get("extra_buttons") or []]
    if extras:
        # Sayfada bu düğmenin sağında, aynı satırda duran ek düğmeler.
        # desktop_only: tahtanın ekranında bir program açar; uzaktan anlamı yok.
        out["row_buttons"] = extras
        # Eski ad (protokol 1 ile yayımlandı); row_buttons ile aynı.
        out["output_window_buttons"] = extras
    return out


def _presentation() -> dict:
    """Sihirbaz sayfasının yerleşimi ve biçimi: yönetim aracı formu görsel
    olarak benzer çizsin diye. Değerler ui/layout.py ve data/styles.css'ten."""
    from ..ui import layout
    from .password_strength import _LABEL_KEYS as _STRENGTH_LABELS

    return {
        "page_order": ["heading", "experimental_banner", "notice", "rationale", "preview",
                       "form", "result", "action_bar"],
        "page": {"margin_px": layout.PAGE_MARGIN, "row_spacing_px": layout.PAGE_ROW_SPACING},
        "heading": {"rationale_inline_max_sentences": layout.RATIONALE_INLINE_MAX_SENTENCES,
                    "rationale_dialog_button": "?",
                    "rationale_dialog_tip": t("ui.pages.rationale_toggle_tip"),
                    # Adımın view.rationale_mode'u "dialog" ise: başlığın
                    # sağında "?" düğmesi; tıklanınca açıklama bu pencerede.
                    "rationale_dialog": {
                        "button": {"text": "?", "style": "help_button",
                                   "side": "after_title", "spacing_px": 8,
                                   "valign": "center",
                                   "tooltip": t("ui.pages.rationale_toggle_tip")},
                        "modal": True,
                        "title": "{step.title}",
                        "size_px": list(layout.RATIONALE_DIALOG_SIZE),
                        "margin_px": layout.RATIONALE_DIALOG_MARGIN,
                        "content": ["heading (step.title, heading stili)",
                                    "rationale (rationale stili, dikey kaydırmalı)",
                                    "doc_url bağlantısı (varsa, doc_label metniyle)"],
                        "close_button": t("ui.main.close"),
                    }},
        "experimental_banner": {"text": t("ui.pages.experimental_banner"),
                                "style": "banner_warning"},
        "notice": {"styles": {"warning": "banner_warning", "info": "banner_info"}},
        "preview": {"title": t("ui.pages.preview_expander"), "collapsed": True,
                    "monospace": True, "max_height_px": layout.LONG_TEXT_HEIGHT,
                    "style": "preview", "title_style": "preview_expander"},
        "form": {
            "columns": ["label", "control"],
            "label_width_chars": layout.FORM_LABEL_CHARS,
            "column_spacing_px": layout.FORM_COLUMN_SPACING,
            "row_spacing_px": layout.FORM_ROW_SPACING,
            "help_column_px": layout.HELP_COLUMN_WIDTH,
            "help_bubble_over_lines": layout.HELP_BUBBLE_OVER_LINES,
            "help_bubble_button": "?",
            "help_folded_label": t("ui.pages.help_expander"),
            "section_style": "form_section",
            "help_style": "rationale",
            "compact_widths_chars": dict(layout.COMPACT_FIELD_WIDTHS),
            "long_text_height_px": layout.LONG_TEXT_HEIGHT,
            "help_markers": layout.HELP_MARKERS,
            # Açıklama satır sayısının ölçüm kuralı (view.help_lines bununla
            # hesaplanır). Sihirbaz, pencere yerleşince gerçek sütun
            # genişliğiyle yeniden ölçer: geniş pencerede daha az balon olur.
            "help_measure": {
                "font_family": layout.HELP_FONT_FAMILY,
                "font_size_pt": layout.HELP_FONT_PT,
                "width_px": layout.HELP_COLUMN_WIDTH,
                "wrap": "word_char",
                "text": "help_bubble_text, işaretler düz metne çevrilmiş ([[ankamon]] → Ankamon)",
                "bubble_over_lines": layout.HELP_BUBBLE_OVER_LINES,
                "dpi": 96,
            },
            # help_display "inline" / "folded": açıklama kontrol sütununda,
            # kontrolün altındaki satırda.
            "help_inline": {
                "position": "control sütunu, kontrolün altındaki satır",
                "style": "rationale",
                "folded_label": t("ui.pages.help_expander"),
                "folded_collapsed": True,
            },
            # help_display "bubble": kontrolün yanında "?" düğmesi, açıklama
            # tıklanınca balonda. Düğmenin yeri alan başına view.help_button'da.
            "help_bubble": {
                "button": {"text": "?", "style": "help_dot", "size_px": 20,
                           "shape": "circle", "spacing_px": layout.HELP_BUTTON_SPACING,
                           "tooltip": t("ui.pages.help_bubble_tip")},
                "popover": {
                    "anchor": "help button", "position": "bottom", "arrow": True,
                    "modal": True,
                    "close_on": ["outside_click", "button_click", "escape"],
                    "title": "{field.label}", "title_style": "help_popover_title",
                    "title_max_width_chars": layout.HELP_POPOVER_TITLE_MAX_CHARS,
                    "body": "{field.view.help_bubble_text}", "body_style": "help_popover_body",
                    "body_min_width_chars": layout.HELP_POPOVER_BODY_MIN_CHARS,
                    "body_max_width_chars": layout.HELP_POPOVER_BODY_MAX_CHARS,
                    "spacing_px": layout.HELP_POPOVER_SPACING,
                    "style": "help_popover",
                    # Balonda help_more ayrı bir katlayıcı değildir; metnin
                    # devamıdır (boş satırla ayrılır).
                    "help_more_folded": False,
                },
            },
        },
        "previous_apply": {"text": t("ui.pages.previous_apply", when="{when}", summary="{summary}"),
                           "when_format": "%d.%m.%Y %H:%M",
                           "undo_button": t("ui.pages.undo_step"),
                           "style": "banner_info"},
        "action_bar": {
            "buttons": [
                {"id": "back", "label": t("ui.main.btn_back")},
                {"id": "apply", "label": t("ui.main.btn_apply"), "style": "suggested"},
                {"id": "next", "label": t("ui.main.btn_next")},
            ],
            "apply_hint": t("ui.main.apply_hint", hint="{hint}"),
            "apply_hint_style": "apply_hint",
        },
        "sidebar": {
            # -l --json'daki status.state → işaret, stil rolü, ipucu ({summary}).
            "marks": {state: {"mark": mark,
                              "style": {"tiha-step-status-ok": "step_status_ok",
                                        "tiha-step-status-fail": "step_status_fail"}.get(css, ""),
                              "tooltip": t(tip, summary="{summary}") if tip else ""}
                      for state, (mark, css, tip) in layout.SIDEBAR_MARKS.items()},
            "mark_style": "step_status",
            "active_style": "step_active",
        },
        "password_strength": {
            # Puanı TiHA hesaplar (core/password_strength.py); burada yalnız görünüşü.
            "text": t("ui.pages.strength", label="{label}", score="{score}"),
            "labels": [t(k) for k in _STRENGTH_LABELS],
            "colors": list(layout.STRENGTH_COLORS),
            "style": "rationale",
        },
        "output_window": {"default_size": list(layout.STREAM_DEFAULT_SIZE),
                          "monospace": True, "working_text": t("ui.pages.working_row")},
        "styles": layout.css_tokens(),
    }


def _catalogs() -> dict:
    """Bazı alanların seçenek kaynakları (ör. branş seçici)."""
    from .meb_data import _DATA_FILE

    try:
        return {"meb_branslar": json.loads(Path(_DATA_FILE).read_text(encoding="utf-8"))}
    except (OSError, ValueError) as exc:
        log.warning("Branş kataloğu okunamadı: %s", exc)
        return {}


def _step_schema(mod) -> dict:
    from ..ui.params import PARAMS_SCHEMA

    fields = [f for f in PARAMS_SCHEMA.get(mod.id, []) if f.get("type") != "button"]
    info = _step_info(mod)
    from ..ui import layout

    info.update({
        "rationale": mod.rationale, "apply_hint": mod.apply_hint,
        "undo_supported": mod.undo_supported, "experimental": mod.experimental,
        "view": {
            # "inline": açıklama sayfada; "dialog": başlığın yanında "?" düğmesi.
            "rationale_mode": layout.rationale_mode(mod),
            "doc_url": mod.doc_url, "doc_label": mod.doc_label or (
                t("ui.pages.doc_link_default") if mod.doc_url else ""),
            # Uygula'da canlı çıktı penceresi açılır.
            "output_window": bool(mod.streams_output),
            # Önizleme hizalı tablo: satır kaydırılmaz.
            "preview_tabular": bool(getattr(mod, "preview_tabular", False)),
            # Sayfaya gelince kendiliğinden uygulanır; Uygula düğmesi yok.
            "auto_apply": bool(mod.auto_apply),
        },
        "scope": mod.scope, "remote_warning": mod.remote_warning,
        # Tahtada rastgele üretilen gizli değerler (PIN anahtarları): birden çok
        # tahtada aynı olsun isteniyorsa source_file bir tahtadan alınıp diğerlerine
        # field ile verilir (docs/makine-arayuzu.md, "Ortak gizli değerler").
        "shared_secrets": getattr(mod, "shared_secrets", None),
        "undo_remote_warning": mod.undo_remote_warning,
        "fields": [_field_schema(f) for f in fields],
        "actions": [_action_schema(f) for f in action_fields(mod)],
    })
    return info


def cmd_schema() -> int:
    """--sema: bütün adımların form tanımı (tek JSON nesnesi). Tahtaya
    bakmaz; aynı sürümün her tahtada aynı çıktısı olur."""
    from ..modules import all_modules

    doc = {"protocol": PROTOCOL_VERSION, "tiha_version": __version__,
           "presentation": _presentation(),
           "steps": [_step_schema(m) for m in all_modules()],
           "catalogs": _catalogs()}
    if _JSON_OUT is not None:
        _emit("schema", **doc)
    else:
        print(json.dumps({"type": "schema", **doc}, ensure_ascii=False, indent=2, default=str))
    return EXIT_OK


def _board_state(mod) -> dict:
    """Tahtadaki o anki durum: alan değerleri (gizliler maskeli), alanın ve
    düğmenin görünürlüğü, dinamik düğme etiketleri."""
    def call(name):
        fn = getattr(mod, name, None) if name else None
        if not callable(fn):
            return None
        try:
            return fn()
        except Exception as exc:
            log.warning("%s başarısız: %s", name, exc)
            return None

    current = default_params(mod)
    fields = []
    for f in _schema_fields(mod):
        val = current.get(f["key"], "")
        item = {"key": f["key"]}
        if _is_secret(f):
            item["value"] = None
            item["is_set"] = bool(val)
        else:
            item["value"] = val
        if f.get("visible_when"):
            item["available"] = bool(call(f["visible_when"]))
        if f.get("enable_also_when"):
            item["enabled_also"] = bool(call(f["enable_also_when"]))
        fields.append(item)
    actions = []
    for f in action_fields(mod):
        item = {"name": action_slug(f), "available": _action_available(mod, f)}
        label = call(f.get("label_from"))
        if isinstance(label, str) and label:
            item["label"] = label
        actions.append(item)
    state = {"fields": fields, "actions": actions}
    notice = call("notice")
    if notice:
        # Sayfanın üstündeki vurgulu not (presentation.notice.styles).
        state["notice"] = {"kind": notice[0], "text": notice[1]}
    entry = Journal().last_applied(mod.id)
    if entry is not None:
        # "Daha önce uygulandı" şeridi (presentation.previous_apply).
        state["previous_apply"] = {"when": entry.timestamp,
                                   "summary": entry.summary or ""}
    return state


def cmd_params(mod) -> int:
    if _JSON_OUT is not None:
        state = _board_state(mod)
        _emit("params", step=_step_schema(mod), **state)
        return EXIT_OK
    num = _step_number(mod)
    print(_paint(f"{num}. {mod.title}", "h") + "  " + _paint(f"({step_slug(mod)})", "d"))
    fields = _schema_fields(mod)
    if not fields:
        print(t("cli.params_none"))
        if action_fields(mod):
            print()
            _print_actions(mod, num)
        return EXIT_OK
    current = default_params(mod)
    for f in fields:
        kind = f.get("type", "text")
        val = current.get(f["key"], "")
        if _is_secret(f):
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
    if action_fields(mod):
        print()
        _print_actions(mod, num)
    return EXIT_OK


def _print_actions(mod, num: int) -> None:
    print(_paint(t("cli.actions_title"), "h"))
    for f in action_fields(mod):
        notes = []
        if f.get("confirm"):
            notes.append(t("cli.actions_needs_yes"))
        if not _action_available(mod, f):
            notes.append(t("cli.actions_unavailable"))
        print()
        print("  " + _paint(action_slug(f), "o")
              + ("  " + _paint("[" + "; ".join(notes) + "]", "m") if notes else ""))
        print("      " + f.get("label", ""))
    print()
    print(_paint(t("cli.actions_hint", num=num), "d"))


_TYPE_KEYS = {"bool", "text", "password", "spin", "number", "select", "textarea",
              "branch_accounts", "file"}


def cmd_preview(mod) -> int:
    if _JSON_OUT is None:
        console.banner_open(f"{_step_number(mod)}. {mod.title}", t("cli.preview_title"))
    try:
        text = mod.preview()
    except Exception as exc:
        _err(t("cli.exception_line", error=exc))
        return EXIT_STEP_FAILED
    if _JSON_OUT is not None:
        _emit("preview", step=_step_info(mod), text=text)
    else:
        print(text)
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


def _lock_busy(result) -> bool:
    from .module import LOCK_BUSY_KEY

    return isinstance(result.data, dict) and bool(result.data.get(LOCK_BUSY_KEY))


def _show_result(mod, result, *, show_details: bool = True,
                 params: dict | None = None) -> int:
    """Sonucu basar (ya da --json'da result olayı) ve çıkış kodunu döner."""
    streamed = getattr(result, "output_streamed", False)
    if _lock_busy(result):
        code = EXIT_BUSY
    elif result.success or result.not_applicable:
        code = EXIT_OK
    else:
        code = EXIT_STEP_FAILED
    if _JSON_OUT is not None:
        from .report_log import redact_data, scrub_text

        data = result.data if isinstance(result.data, dict) else {}
        hidden = _secrets_of(params, data)
        withheld = bool(getattr(result, "contains_secrets", False))
        clean = lambda text: scrub_text(text or "", hidden)  # noqa: E731
        _emit("result", step=_step_info(mod), success=bool(result.success),
              not_applicable=bool(result.not_applicable), summary=clean(result.summary),
              details="" if (streamed or withheld) else clean(result.details),
              warning=clean(result.warning),
              copyable="" if (streamed or withheld) else clean(result.copyable),
              secrets_withheld=withheld,
              data=redact_data(data), exit_code=code)
        return code
    if show_details and not streamed:
        if result.details:
            print(result.details)
        if result.copyable and result.copyable != result.details:
            print(result.copyable)
    if result.success:
        console.ok(result.summary)
    elif result.not_applicable:
        console.note(result.summary)
    else:
        console.fail(result.summary)
    if result.warning and not streamed:
        print(f"⚠ {result.warning}", file=sys.stderr)
    return code


def _needs_confirm(conf: dict, assume_yes: bool) -> bool:
    """Onay gerektiren durumu bildirir; -e yoksa True (çağıran 13 ile çıkar)."""
    if _JSON_OUT is not None and not assume_yes:
        _emit("confirm_required", title=conf.get("title", ""),
              message=conf.get("message", ""))
    print(f"⚠ {conf.get('title', '')}", file=sys.stderr)
    if conf.get("message"):
        print(conf["message"], file=sys.stderr)
    if not assume_yes:
        print(_paint(t("cli.needs_yes"), "w", sys.stderr), file=sys.stderr)
        return True
    return False


def _gather_params(mod, overrides: list[str], values_path: str | None) -> dict[str, str]:
    params = default_params(mod)
    if values_path:
        params.update(read_values(mod, values_path))
    params.update(parse_overrides(mod, overrides))
    return params


def cmd_run(mod, overrides: list[str], assume_yes: bool, values_path: str | None = None) -> int:
    params = _gather_params(mod, overrides, values_path)

    # Arayüzdeki onay pencerelerinin karşılığı (ör. hesap silme).
    pre_check = getattr(mod, "pre_apply_check", None)
    if callable(pre_check):
        for conf in pre_check(params) or []:
            if _needs_confirm(conf, assume_yes):
                return EXIT_NEEDS_CONFIRM
            params.update(conf.get("params") or {})

    if _JSON_OUT is not None:
        _emit("start", command="run", step=_step_info(mod))
    else:
        console.banner_open(f"{_step_number(mod)}. {mod.title}", f"v{__version__}")

    journal = Journal()
    try:
        result = mod.apply_with_logging(params, progress=_progress_printer(_secrets_of(params)))
    except Exception as exc:
        _err(t("cli.exception_line", error=exc))
        entry = JournalEntry.new(mod.id, mod.title)
        entry.summary = t("cli.exception_summary", error=exc)
        entry.status = "failed"
        journal.record(entry)
        return EXIT_STEP_FAILED
    if not _lock_busy(result):
        _record(journal, mod, params, result)
    return _show_result(mod, result, params=params)


def cmd_action(mod, ref: str | None, overrides: list[str], assume_yes: bool,
               values_path: str | None = None) -> int:
    """-x ADIM [EYLEM]: adımın sayfasındaki bir düğmeyi çalıştırır; eylem
    verilmezse adımın eylemlerini listeler."""
    from .module import run_locked

    num = _step_number(mod)
    if not action_fields(mod):
        _err(t("cli.actions_none", step=mod.title))
        return EXIT_BAD_PARAM
    if not ref:
        if _JSON_OUT is not None:
            _emit("actions", step=_step_info(mod),
                  actions=[dict(_action_schema(f), available=_action_available(mod, f))
                           for f in action_fields(mod)])
            return EXIT_OK
        print(_paint(f"{num}. {mod.title}", "h") + "  " + _paint(f"({step_slug(mod)})", "d"))
        print()
        _print_actions(mod, num)
        return EXIT_OK
    field = resolve_action(mod, ref)
    if field is None:
        _err(t("cli.unknown_action", action=ref, step=mod.title,
               actions=", ".join(action_slug(f) for f in action_fields(mod))))
        return EXIT_BAD_PARAM
    label = field.get("label", "")
    if not _action_available(mod, field):
        _err(t("cli.action_unavailable", action=label))
        return EXIT_ACTION_UNAVAILABLE

    # Düğmeler formdaki değerleri kullanır (ör. BIOS parolası, log sunucusu).
    params = _gather_params(mod, overrides, values_path)

    if field.get("confirm") and _needs_confirm(field["confirm"], assume_yes):
        return EXIT_NEEDS_CONFIRM

    if _JSON_OUT is not None:
        _emit("start", command="action", step=_step_info(mod), action=action_slug(field))
    else:
        console.banner_open(f"{num}. {mod.title}", label)

    progress = _progress_printer(_secrets_of(params))
    func = getattr(mod, field["action"])

    def call():
        # Arayüzle aynı çağrı sırası: eski imzalı eylemler de çalışsın.
        try:
            return func(params=params, progress=progress)
        except TypeError:
            try:
                return func(progress=progress)
            except TypeError:
                return func()

    try:
        result = run_locked(t("core.oplock.what_action", title=mod.title, label=label), call)
    except Exception as exc:
        log.exception("Düğme eylemi başarısız: %s.%s", mod.id, field["action"])
        _err(t("cli.exception_line", error=exc))
        return EXIT_STEP_FAILED

    if not _lock_busy(result):
        from .report_log import ActionLog
        try:
            ActionLog().record(
                module_id=mod.id, title=mod.title, action=field["action"], label=label,
                success=bool(result.success), summary=result.summary or "", params=params,
                data=result.data if isinstance(result.data, dict) else {},
            )
        except Exception as exc:  # rapor kaydı işlemi bozmasın
            log.warning("Eylem kaydı yazılamadı: %s", exc)
    return _show_result(mod, result, params=params)


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
        if _JSON_OUT is not None:
            _emit("prompt_answered", title=prompt.get("title", ""),
                  message=prompt.get("message", ""), answer="yes" if assume_yes else "no")
    if _JSON_OUT is not None:
        _emit("start", command="undo", step=_step_info(mod))
    else:
        console.banner_open(f"{_step_number(mod)}. {mod.title}", t("cli.undo_title"))
    try:
        result = mod.undo_with_logging(entry.data, undo_params)
    except Exception as exc:
        _err(t("cli.exception_line", error=exc))
        return EXIT_STEP_FAILED
    if result.success:
        journal.mark_undone(mod.id)
    if _JSON_OUT is None and result.success:
        console.ok(result.summary)
        return EXIT_OK
    return _show_result(mod, result, show_details=False)


def _board_status(mods) -> dict[str, dict] | None:
    """Her adımın bu tahtadaki durumu (sihirbazın sol menüsündeki işaret).
    Günlük yalnız root'a açık; root değilsek None."""
    if os.geteuid() != 0:
        return None
    from .undo import step_state

    latest = Journal().latest_per_module()
    out = {}
    for m in mods:
        entry = latest.get(m.id)
        item = {"state": step_state(m, entry)}
        if entry is not None:
            item["summary"] = entry.summary or ""
            item["when"] = entry.timestamp
        out[m.id] = item
    return out


def cmd_list() -> int:
    from ..modules import all_modules
    if _JSON_OUT is not None:
        mods = all_modules()
        status = _board_status(mods)
        steps = []
        for m in mods:
            item = dict(_step_info(m), scope=m.scope, undo_supported=m.undo_supported)
            if status is not None:
                item["status"] = status[m.id]
            steps.append(item)
        # status: yalnız root ile (tek çağrıda bütün adımların bu tahtadaki
        # durumu); root değilse status_available false.
        _emit("steps", tiha_version=__version__, protocol=PROTOCOL_VERSION,
              status_available=status is not None, steps=steps)
        return EXIT_OK
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
    unknown = [mid for mid in params_by_module if mid not in known]

    if _JSON_OUT is not None:
        _emit("preset", path=str(preset_path),
              steps=[{"step": _step_info(known[mid]),
                      "params": redact_params(mid, params)}
                     for mid, params in params_by_module.items() if mid in known],
              unknown=unknown)
        return 11 if unknown else 0

    console.banner_open(t("cli.info_title"), str(preset_path))
    print(t("cli.info_total", count=len(params_by_module)))
    for mid, params in params_by_module.items():
        mod = known.get(mid)
        title = f"{_step_number(mod)}. {mod.title}" if mod else f"{mid} {t('cli.unknown_module')}"
        print(f"• {title}")
        for k, v in params.items():
            print(f"    {k} = {v}")
        print()
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
        if _JSON_OUT is not None:
            _emit("batch_result", ok=0, failed=0, unknown=unknown_in_preset)
        return 0

    if _JSON_OUT is not None:
        _emit("start", command="batch", preset=str(preset_path),
              steps=[_step_info(m) for m, _p in targets], unknown=unknown_in_preset)
    else:
        console.banner_open(t("cli.apply_title"), f"v{__version__}")
        print(t("cli.apply_preset", path=preset_path))
        print(t("cli.apply_targets", count=len(targets)))

    journal = Journal()
    failed_count = 0
    busy = False

    for idx, (mod, params) in enumerate(targets, 1):
        if _JSON_OUT is not None:
            _emit("step_start", step=_step_info(mod), index=idx, total=len(targets))
        else:
            console.step(f"[{idx}/{len(targets)}] {_step_number(mod)}. {mod.title}")

        try:
            result = mod.apply_with_logging(params,
                                            progress=_progress_printer(_secrets_of(params)))
        except Exception as exc:
            _err(t("cli.exception_line", error=exc))
            failed_count += 1
            entry = JournalEntry.new(mod.id, mod.title)
            entry.summary = t("cli.exception_summary", error=exc)
            entry.status = "failed"
            journal.record(entry)
            continue

        if _lock_busy(result):
            # Başka bir TiHA işlemi sürüyor: kalan adımlar da aynı yere çarpar.
            _show_result(mod, result, params=params)
            failed_count += 1
            busy = True
            break
        _record(journal, mod, params, result)
        if _show_result(mod, result, params=params) != EXIT_OK:
            if _JSON_OUT is None and result.details:
                for ln in result.details.splitlines():
                    print(f"  {ln}", file=sys.stderr)
            failed_count += 1

    if _JSON_OUT is not None:
        _emit("batch_result", ok=len(targets) - failed_count, failed=failed_count,
              unknown=unknown_in_preset)
    else:
        console.banner_close(
            t("cli.apply_done", ok=len(targets) - failed_count, failed=failed_count)
        )
    if busy:
        return EXIT_BUSY
    return 1 if failed_count else 0


def prepare(argv: list[str]) -> None:
    """--json verildiyse makine çıktısını en başta açar; böylece seçenek
    ve yetki hataları da olay olarak gider."""
    if "--json" in argv:
        _start_json()


def finish(code: int) -> int:
    """--json'da son olay: ``end``. Ayrıştıran taraf yarıda kesilen
    çalışmayı bunun yokluğundan anlar."""
    if _JSON_OUT is not None:
        _emit("end", exit_code=code)
    return code


def early_error(message: str, code: int) -> int:
    """Komut çalışmadan önceki hata (ör. yetki yok)."""
    if _JSON_OUT is not None:
        _emit("error", message=message)
        finish(code)
    print(message, file=sys.stderr)
    return code


def run(argv: list[str]) -> int:
    prepare(argv)
    try:
        code = _run(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else EXIT_USAGE
    return finish(code)


def _run(argv: list[str]) -> int:
    parser, args = _parse(argv)

    if args.help:
        parser.print_help()
        return EXIT_OK
    if args.version:
        if _JSON_OUT is not None:
            _emit("version", tiha_version=__version__, protocol=PROTOCOL_VERSION)
        else:
            print(f"TiHA {__version__}")
        return EXIT_OK
    if args.schema:
        return cmd_schema()
    if args.list:
        return cmd_list()

    if args.params:
        return cmd_params(_need_step(args.params))
    if args.preview:
        return cmd_preview(_need_step(args.preview))
    if args.run:
        return cmd_run(_need_step(args.run), args.set, args.yes, args.values)
    if args.undo:
        return cmd_undo(_need_step(args.undo), args.yes)
    if args.action:
        ref = args.action[1] if len(args.action) > 1 else None
        return cmd_action(_need_step(args.action[0]), ref, args.set, args.yes, args.values)

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
