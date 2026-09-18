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

from .report_log import REPORT_PARAMS_KEY, ActionLog, ActionRecord
from .undo import Journal, JournalEntry

CLOSING = "Bu imajı yaymadan önce kapsamlı bir testten geçirmeyi unutmayın."


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

    def to_text(self) -> str:
        """Panoya kopyalanabilir / dosyaya yazılabilir düz metin."""
        out = [self.intro, ""]
        if self.steps:
            out.append("YAPTIKLARINIZ")
            for s in self.steps:
                head = f"■ {s.title}"
                if s.failed:
                    head += " (BAŞARISIZ)"
                elif s.experimental:
                    head += " (deneysel)"
                out.append(head)
                out += [f"  • {line}" for line in s.done]
                out += [f"  ! {line}" for line in s.notes]
            out.append("")
        if self.warnings:
            out.append("DİKKAT — ADIMLAR ARASI İLİŞKİLER")
            out += [f"  ! {w}" for w in self.warnings]
            out.append("")
        tests = [(s.title, s.tests) for s in self.steps if s.tests]
        if tests or self.general_tests:
            out.append("KLON TAHTADA DENEYİN")
            for title, items in tests:
                out.append(f"■ {title}")
                out += [f"  ☐ {t}" for t in items]
            if self.general_tests:
                out.append("■ Genel")
                out += [f"  ☐ {t}" for t in self.general_tests]
            out.append("")
        out.append(self.closing)
        return "\n".join(out)


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


def _fallback(ctx: StepContext, rep: StepReport) -> None:
    """Anlatıcısı olmayan (ya da parametresi kaydedilmemiş) adımlar için."""
    if ctx.summary:
        rep.done.append(ctx.summary.rstrip(".") + ".")
    for a in ctx.actions:
        rep.done.append(f"“{a.label}” işlemini çalıştırdınız: {a.summary.rstrip('.')}.")
    rep.tests.append(
        "Bu adımın yaptığı değişikliği klon tahtada yeniden başlattıktan sonra "
        "gözle doğrulayın."
    )


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
        if entry is not None and entry.status == "undone":
            entry = None
        mod_actions = [a for a in actions.for_module(module.id) if a.success]
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
                rep.notes = [f"Bu adım rapora dökülemedi ({exc})."]
                _fallback(ctx, rep)
            if not rep.done:
                _fallback(ctx, rep)
            if rep.experimental:
                rep.notes.append(
                    "Bu adım deneysel olarak işaretli: gerçek tahta donanımında "
                    "henüz yeterince doğrulanmadı. Klonda özellikle dikkatle deneyin."
                )
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
        return (
            "Bu tahtada TiHA ile henüz bir adım uygulanmadı. Adımları "
            "uyguladıkça burada imaja neyin girdiği ve bir klon tahtada "
            "neyin denenmesi gerektiği listelenecek."
        )
    ok = [s for s in steps if not s.failed]
    failed = [s for s in steps if s.failed]
    parts = [
        f"Bu tahtada TiHA ile {len(ok)} adımı uyguladınız"
        + (f"; {len(failed)} adım başarısız oldu" if failed else "")
        + ". Aşağıda imaja neyin girdiğini ve her değişikliğin bir klon "
        "tahtada nasıl sınanacağını bulacaksınız.",
        "Bu imaj çok sayıda tahtaya kopyalanacak. Burada gözden kaçan her "
        "ayrıntıyı, imajın yazıldığı tahta sayısı kadar ayrı ayrı düzeltmek "
        "zorunda kalırsınız. Bu yüzden imajı yaymadan önce en az bir klon "
        "tahtaya yazıp aşağıdaki denetimleri eksiksiz yapın.",
    ]
    return " ".join(parts)


# Anlatıcılar dosya sonunda içe aktarılır: report_steps bu modüldeki
# sınıfları kullanıyor (başta içe aktarmak döngü yaratırdı). Fonksiyon
# içinde "geç" içe aktarmak ise mümkün değil: imaj temizliği (m10) /tmp'yi
# boşaltıyor ve bootstrap ile gelen TiHA /tmp altından çalışıyor; temizlikten
# sonra diskte okunacak kaynak dosya kalmıyor.
from . import report_steps  # noqa: E402
