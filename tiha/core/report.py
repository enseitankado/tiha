"""Özet sayfasının "bu imajda neler yaptınız" raporu.

Amaç kullanıcıyı, imajı onlarca tahtaya yaymadan önce **en az bir klon
tahtada** yaptığı her değişikliği sınamaya yöneltmek: gözden kaçan tek bir
ayrıntı, imajın kopyalandığı tahta sayısı kadar ayrı ayrı düzeltme demek.

Rapor dört parçadan oluşur:

1. Giriş paragrafı — kaç adımın uygulandığı ve neden klonda test gerektiği.
2. Adım başına "yaptıklarınız" maddeleri ve "klonda şunu deneyin"
   listesi. Her adımın anlatıcısı ``report_steps`` modülündedir; formdaki
   seçeneklerin her birleşimini ayrı cümleye çevirir.
3. Adımlar arası uyarılar — sıra (ör. imaj temizliğinden sonra yapılan
   değişiklik), ilişki (ör. PIN diyaloğu ama PIN anahtarı yok) ve eksik
   adımlar (ör. imaj temizliği hiç yapılmadı).
4. Kapanış cümlesi.

Kaynaklar: günce (``journal.json``; her modülün son kaydı) ve düğme
eylemleri kaydı (``actions.json``). Parametreler ``report_log`` ile
maskelenmiş olarak saklanır; raporda hiçbir parola geçmez.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .i18n import t
from .report_log import REPORT_PARAMS_KEY, ActionLog, ActionRecord
from .undo import Journal, JournalEntry

CLOSING = t("core.report.closing")

# Yazdırılabilir rapor stili: siyah-beyaz baskıda da okunur, mürekkep ve
# kâğıt harcamaz (renkli zemin yok, sıkı satır aralığı, dar kenar).
_REPORT_CSS = """
@page { size: A4; margin: 12mm 13mm; }
* { box-sizing: border-box; }
body { font-family: "DejaVu Sans", "Liberation Sans", Arial, sans-serif;
       font-size: 9.5pt; line-height: 1.32; color: #111; margin: 0 auto;
       max-width: 190mm; padding: 8px; }
h1 { font-size: 14pt; margin: 0 0 2px; }
h2 { font-size: 11pt; margin: 12px 0 4px; padding-bottom: 2px;
     border-bottom: 1px solid #999; }
h3 { font-size: 9.5pt; margin: 6px 0 1px; }
p { margin: 3px 0; }
.meta { font-size: 8.5pt; color: #444; margin-bottom: 6px; }
.intro { margin-bottom: 4px; }
ul { margin: 0 0 2px; padding-left: 16px; }
li { margin: 0; }
li.note { list-style: none; margin-left: -12px; padding-left: 12px;
          text-indent: -12px; font-style: italic; }
li.note::before { content: "! "; font-weight: bold; font-style: normal;
                 color: #c62828; }
.warn li::marker { color: #c62828; }
.warn { border: 1px solid #555; border-left: 4px solid #111;
        padding: 2px 8px 4px; margin: 8px 0; }
.warn h2 { border: none; margin-top: 4px; }
.step { break-inside: avoid; page-break-inside: avoid; }
ul.check { list-style: none; padding-left: 4px; }
ul.check li { padding-left: 17px; text-indent: -17px; }
ul.check li::before { content: "\\2610\\00a0\\00a0"; font-size: 10.5pt; }
/* Madde kimliği: sürüm soluk, bölüm.madde belirgin (0.1.66-3.2). */
.tid { font-family: "DejaVu Sans Mono", monospace; font-size: 8.5pt;
       white-space: nowrap; margin-right: 4px; }
.tid .ver { color: #999; }
.tid .num { font-weight: bold; }
/* Kabuk komutu: açık sarı zemin; yazdırırken de korunur. */
code.cmd { font-family: "DejaVu Sans Mono", monospace; font-size: 8.5pt;
           background: #fff3a6; padding: 0 3px; border-radius: 2px;
           -webkit-print-color-adjust: exact; print-color-adjust: exact; }
button.copy { font-size: 7.5pt; margin: 0 2px 0 3px; padding: 0 5px;
              border: 1px solid #bbb; border-radius: 3px; background: #fff;
              color: #333; cursor: pointer; vertical-align: 1px; }
button.copy:hover { background: #eef4ff; }
button.copy.done { color: #2e7d32; border-color: #2e7d32; }
@media print { button.copy { display: none; } }
.hint { font-size: 8.5pt; color: #444; }
.closing { margin-top: 10px; font-weight: bold; }
@media print { body { padding: 0; max-width: none; } }
"""


# --- Veri modeli -------------------------------------------------------------


@dataclass
class StepReport:
    module_id: str
    title: str
    order: int
    done: list[str] = field(default_factory=list)    # yaptıklarınız
    tests: list[str] = field(default_factory=list)   # klonda deneyin
    notes: list[str] = field(default_factory=list)   # bu adıma özel dikkat
    failed: bool = False
    skipped: bool = False     # bilinçli olarak atlandı (ör. kutu işaretsiz)
    experimental: bool = False


@dataclass
class Report:
    intro: str
    steps: list[StepReport]
    warnings: list[str]
    general_tests: list[str]
    closing: str = CLOSING

    @property
    def is_empty(self) -> bool:
        return not self.steps

    def test_groups(self) -> list[tuple[str, list[tuple[str, str]]]]:
        """Kontrol listesi: [(“3. Başlık”, [(“0.1.66-3.2”, madde), …]), …].

        Madde kimliği TiHA sürümü + bölüm.madde'dir; kâğıttan tek başına
        okunduğunda bile hangi sürümün raporuna ait olduğu bellidir."""
        from .. import __version__

        groups = [(s.title, s.tests) for s in self.steps if s.tests]
        if self.general_tests:
            groups.append((t("core.report.text.general"), self.general_tests))
        out = []
        for sec, (title, items) in enumerate(groups, start=1):
            out.append((
                f"{sec}. {title}",
                [(f"{__version__}-{sec}.{i}", item) for i, item in enumerate(items, start=1)],
            ))
        return out

    def to_text(self) -> str:
        """Panoya kopyalanabilir / dosyaya yazılabilir düz metin."""
        out = [self.intro, ""]
        if self.steps:
            out.append(t("core.report.text.section_done"))
            for s in self.steps:
                head = f"■ {s.title}"
                if s.failed:
                    head += t("core.report.text.failed_suffix")
                elif s.experimental and "deneysel" not in s.title.lower():
                    head += t("core.report.text.experimental_suffix")
                out.append(head)
                out += [f"  • {line}" for line in s.done]
                out += [f"  ! {line}" for line in s.notes]
            out.append("")
        if self.warnings:
            out.append(t("core.report.text.section_warnings"))
            out += [f"  ! {w}" for w in self.warnings]
            out.append("")
        groups = self.test_groups()
        if groups:
            out.append(t("core.report.text.section_tests"))
            for title, items in groups:
                out.append(f"■ {title}")
                out += [f"  ☐ [{tid}] {item}" for tid, item in items]
            out.append("")
        out.append(self.closing)
        return "\n".join(out)

    def to_html(self, meta: list[tuple[str, str]]) -> str:
        """Yazdırmaya uygun tek sayfa HTML. ``meta``: başlık altındaki
        (etiket, değer) satırları (tarih, tahta adı, TiHA sürümü…).

        Kâğıt dostu: A4, dar kenar boşluğu, 9.5pt, renkli zemin yok;
        adımlar sayfa arasında bölünmez, denenecekler kutucuklu liste."""
        from html import escape as e

        def step_title(s: StepReport) -> str:
            title = e(s.title)
            if s.failed:
                title += e(t("core.report.text.failed_suffix"))
            elif s.experimental and "deneysel" not in s.title.lower():
                title += e(t("core.report.text.experimental_suffix"))
            return title

        body: list[str] = []
        body.append(f"<h1>{e(t('core.report.html.title'))}</h1>")
        body.append('<p class="meta">' + " &nbsp;·&nbsp; ".join(
            f"<b>{e(k)}:</b> {e(v)}" for k, v in meta
        ) + "</p>")
        body.append(f'<p class="intro">{e(self.intro)}</p>')

        if self.warnings:
            body.append('<section class="warn">')
            body.append(f"<h2>{e(t('core.report.html.section_warnings'))}</h2><ul>")
            body += [f"<li>{e(w)}</li>" for w in self.warnings]
            body.append("</ul></section>")

        if self.steps:
            body.append(f"<h2>{e(t('core.report.html.section_done'))}</h2>")
            for s in self.steps:
                body.append('<div class="step">')
                body.append(f"<h3>{step_title(s)}</h3><ul>")
                body += [f"<li>{e(line)}</li>" for line in s.done]
                body += [f'<li class="note">{e(line)}</li>' for line in s.notes]
                body.append("</ul></div>")

        groups = self.test_groups()
        if groups:
            body.append(f"<h2>{e(t('core.report.html.section_tests'))}</h2>")
            body.append(f'<p class="hint">{e(t("core.report.html.tests_hint"))}</p>')
            for title, items in groups:
                body.append('<div class="step">')
                body.append(f'<h3>{e(title)}</h3><ul class="check">')
                for tid, item in items:
                    ver, _, num = tid.rpartition("-")
                    body.append(
                        f'<li><span class="tid"><span class="ver">{e(ver)}-</span>'
                        f'<span class="num">{e(num)}</span></span>{highlight_commands(e(item))}</li>'
                    )
                body.append("</ul></div>")

        body.append(f'<p class="closing">{e(self.closing)}</p>')

        return (
            "<!DOCTYPE html>\n"
            f'<html lang="{e(t("core.report.html.lang"))}"><head><meta charset="utf-8">'
            f"<title>{e(t('core.report.html.title'))}</title>"
            f"<style>{_REPORT_CSS}</style></head><body>\n"
            + "\n".join(body)
            + "\n<script>" + _COPY_JS.replace("{done}", t("core.report.html.copied"))
            + "</script>\n</body></html>\n"
        )


def highlight_commands(escaped: str) -> str:
    """HTML'e çevrilmiş metindeki `komut` parçalarını sarı zeminli koda
    çevirir; yanına panoya kopyalayan küçük bir düğme koyar."""
    import re
    label = t("core.report.html.copy")
    return re.sub(
        r"`([^`]+)`",
        rf'<code class="cmd">\1</code><button class="copy" type="button">{label}</button>',
        escaped,
    )


# Kopyala düğmeleri: önce Clipboard API, olmazsa (file:// ya da eski
# tarayıcı) seçip execCommand("copy").
_COPY_JS = """
document.querySelectorAll("button.copy").forEach(function (btn) {
  btn.addEventListener("click", function () {
    var text = btn.previousElementSibling.textContent;
    var old = btn.textContent;
    function ok() {
      btn.textContent = "{done}"; btn.classList.add("done");
      setTimeout(function () { btn.textContent = old; btn.classList.remove("done"); }, 1500);
    }
    function fallback() {
      var ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); ok(); } finally { ta.remove(); }
    }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(ok, fallback);
    } else { fallback(); }
  });
});
"""


# --- Anlatıcıya verilen bağlam ----------------------------------------------


def as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on", "evet")


def as_int(value: object, default: int | None = None) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


@dataclass
class StepContext:
    """Bir adımın rapora dönüşmesi için gereken her şey."""

    module_id: str
    title: str
    entry: JournalEntry | None          # son günce kaydı (yoksa yalnız eylem)
    actions: list[ActionRecord]          # bu modülün başarılı düğme eylemleri
    params: dict                         # maskelenmiş uygulama parametreleri
    data: dict                           # modülün ApplyResult.data'sı

    @property
    def applied(self) -> bool:
        return self.entry is not None and self.entry.status == "applied"

    @property
    def failed(self) -> bool:
        return self.entry is not None and self.entry.status == "failed"

    @property
    def has_params(self) -> bool:
        """Parametreler kayıtlı mı? (Rapor özelliğinden önceki kayıtlarda yok.)"""
        return bool(self.params)

    @property
    def summary(self) -> str:
        return (self.entry.summary if self.entry else "") or ""

    def p(self, key: str, default: object = None) -> object:
        return self.params.get(key, default)

    def flag(self, key: str, default: bool = False) -> bool:
        if key not in self.params:
            return default
        return as_bool(self.params[key])

    def num(self, key: str, default: int | None = None) -> int | None:
        return as_int(self.params.get(key), default)

    def text(self, key: str, default: str = "") -> str:
        value = self.params.get(key, default)
        return "" if value is None else str(value).strip()

    def secret_set(self, key: str) -> bool:
        """Maskelenmiş gizli alan doldurulmuş muydu?"""
        return bool(str(self.params.get(key, "") or "").strip())

    def action(self, name: str) -> list[ActionRecord]:
        return [a for a in self.actions if a.action == name]


Narrator = Callable[[StepContext, StepReport], None]


# --- Oluşturucu --------------------------------------------------------------


# Sistemi değiştirmeyen düğmeler (pencere açma, bağlantı/sunucu testi,
# değer okuma): rapora girmez. Yalnız bunlara tıklanmış bir adım "…kurdunuz"
# diye anlatılıyordu.
_INFO_ACTION_PREFIXES = ("launch_", "test_", "read_")


def _fallback(ctx: StepContext, rep: StepReport) -> None:
    """Anlatıcısı olmayan (ya da parametresi kaydedilmemiş) adımlar için."""
    if ctx.summary:
        rep.done.append(ctx.summary.rstrip(".") + ".")
    for a in ctx.actions:
        rep.done.append(t(
            "core.report.fallback_action", label=a.label, summary=a.summary.rstrip("."),
        ))
    rep.tests.append(t("core.report.fallback_test"))


def build_report(
    modules: list,
    journal: Journal | None = None,
    actions: ActionLog | None = None,
) -> Report:
    """Günce ve eylem kaydından raporu kurar.

    ``modules`` sihirbaz sırasındaki modül nesneleridir (başlık, sıra ve
    deneysel bilgisi oradan alınır).
    """
    journal = journal if journal is not None else Journal()
    actions = actions if actions is not None else ActionLog()
    latest = journal.latest_per_module()

    steps: list[StepReport] = []
    contexts: dict[str, StepContext] = {}
    for order, module in enumerate(modules):
        entry = latest.get(module.id)
        # Geri alınmış ya da bu tahtada uygulanamayan (ör. donanım
        # desteklenmiyor) adım imaja bir şey katmadı; raporda yer almaz.
        if entry is not None and entry.status in ("undone", "skipped"):
            entry = None
        mod_actions = [
            a for a in actions.for_module(module.id)
            if a.success and not a.action.startswith(_INFO_ACTION_PREFIXES)
        ]
        if entry is None and not mod_actions:
            continue
        data = dict(entry.data) if entry is not None and isinstance(entry.data, dict) else {}
        params = data.pop(REPORT_PARAMS_KEY, None) or {}
        ctx = StepContext(
            module_id=module.id,
            title=module.title,
            entry=entry,
            actions=mod_actions,
            params=params if isinstance(params, dict) else {},
            data=data,
        )
        rep = StepReport(
            module_id=module.id,
            title=module.title,
            order=order,
            failed=ctx.failed,
            experimental=bool(getattr(module, "experimental", False)),
        )
        if ctx.failed:
            report_steps.FAILED_NARRATORS.get(
                module.id, report_steps.narrate_failed,
            )(ctx, rep)
        else:
            narrator: Narrator = report_steps.NARRATORS.get(module.id, _fallback)
            try:
                narrator(ctx, rep)
            except Exception as exc:  # bir anlatıcı hatası raporu düşürmesin
                rep.done = []
                rep.tests = []
                rep.notes = [t("core.report.narrator_error", error=exc)]
                _fallback(ctx, rep)
            if not rep.done:
                _fallback(ctx, rep)
            if rep.experimental:
                rep.notes.append(t("core.report.experimental_note"))
        steps.append(rep)
        contexts[module.id] = ctx

    warnings = report_steps.cross_step_warnings(contexts, modules, journal)
    general = report_steps.general_tests(contexts) if steps else []
    return Report(
        intro=_intro(steps),
        steps=steps,
        warnings=warnings,
        general_tests=general,
    )


def _intro(steps: list[StepReport]) -> str:
    if not steps:
        return t("core.report.intro_empty")
    ok = [s for s in steps if not s.failed and not s.skipped]
    failed = [s for s in steps if s.failed]
    failed_part = (
        t("core.report.intro_failed_part", failed=len(failed)) if failed else ""
    )
    parts = [
        t("core.report.intro_applied", ok=len(ok), failed_part=failed_part),
        t("core.report.intro_why"),
    ]
    return " ".join(parts)


# Anlatıcılar dosya sonunda içe aktarılır: report_steps bu modüldeki
# sınıfları kullanıyor (başta içe aktarmak döngü yaratırdı). Fonksiyon
# içinde "geç" içe aktarmak ise mümkün değil: imaj temizliği (m10) /tmp'yi
# boşaltıyor ve bootstrap ile gelen TiHA /tmp altından çalışıyor; temizlikten
# sonra diskte okunacak kaynak dosya kalmıyor.
from . import report_steps  # noqa: E402
