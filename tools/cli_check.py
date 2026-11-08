#!/usr/bin/env python3
"""Komut satırı ile sihirbazın uyum denetimi.

Komut satırı adımları ve ayarları sihirbazla aynı kaynaktan (``all_modules()``
ve ``ui/params.py`` şeması) okur; yeni adım ve alanlar kendiliğinden gelir.
Bu araç kendiliğinden gelmeyen durumları yakalar:

* komut satırının tanımadığı bir alan türü (HATA — düz metin sayılır,
  doğrulanmaz),
* eylem yöntemi olmayan ya da komut satırında adı çakışan bir düğme,
  masaüstü programı açmayan bir ek düğme (HATA),
* yayımlanmış bir kısa adın ya da eylem adının artık çözülmemesi (HATA —
  adım ya da düğme yeniden adlandırılmış; eski ad ``cli._STEP_ALIASES`` /
  ``cli._ACTION_ALIASES``'a eklenmeli),
* ``tools/cli_names.json``'a kaydedilmemiş yeni bir ad (HATA —
  ``--guncelle`` ile kaydedilir),
* ``tiha -h`` örneklerinde tanımsız adım, ayar ya da eylem (HATA),
* makine arayüzünün form tanımının (``tiha --sema``) bir adım için
  üretilememesi ya da geçersiz ``scope`` (HATA),
* formu adıma özel kodla dolduran yeni bir ``module.id ==`` dalı (HATA —
  doldurma ``default_from`` ile modülde yazılmalı; yalnız arayüze ait
  bir davranışsa aşağıdaki listeye gerekçesiyle eklenir).

Kullanım::

    python3 tools/cli_check.py               # denetle
    python3 tools/cli_check.py --guncelle    # yeni adları cli_names.json'a ekle

Hata varsa çıkış kodu 1'dir.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tiha.core import cli  # noqa: E402
from tiha.modules import all_modules  # noqa: E402
from tiha.ui.params import PARAMS_SCHEMA  # noqa: E402

NAMES_FILE = ROOT / "tools" / "cli_names.json"

# Arayüzde adıma özel olup komut satırında karşılığı gerekmeyen dallar:
# (dosya, modül kimliği) -> izin verilen sayı.
_UI_ONLY_BRANCHES = {
    # BIOS parolası kutusuna yalnız A-Z/0-9 yazılabilmesi (giriş süzgeci);
    # modül aynı denetimi _validate_password ile kendisi de yapıyor.
    ("tiha/ui/pages.py", "m14_bios_password"): 2,
}


def _load_names() -> dict:
    if NAMES_FILE.exists():
        return json.loads(NAMES_FILE.read_text(encoding="utf-8"))
    return {"steps": {}, "actions": {}}


def check_types(errors: list[str]) -> None:
    known = set(cli._TYPE_KEYS) | set(cli._SKIP_TYPES)
    for mid, fields in PARAMS_SCHEMA.items():
        for f in fields:
            kind = f.get("type", "text")
            if kind not in known:
                errors.append(f"{mid}.{f.get('key')}: '{kind}' türünü komut satırı "
                              "tanımıyor (cli._TYPE_KEYS ve parse_overrides)")


def check_buttons(errors: list[str]) -> None:
    for mod in all_modules():
        slugs = Counter()
        for f in PARAMS_SCHEMA.get(mod.id, []):
            if f.get("type") != "button":
                continue
            action = f.get("action")
            if not action or not callable(getattr(mod, action, None)):
                errors.append(f"{mod.id}.{f.get('key')}: düğmenin eylem yöntemi yok ({action})")
            for extra in f.get("extra_buttons") or []:
                if not extra.get("launch"):
                    errors.append(f"{mod.id}.{f.get('key')}: ek düğme '{extra.get('label')}' "
                                  "komut satırından çalıştırılamaz (yalnız launch ek "
                                  "düğmeleri desteklenir)")
        for f in cli.action_fields(mod):
            slug = cli.action_slug(f)
            if not slug:
                errors.append(f"{mod.id}.{f['key']}: eylem adı boş çıkıyor")
            slugs[slug] += 1
        for slug, n in slugs.items():
            if n > 1:
                errors.append(f"{mod.id}: '{slug}' eylem adı {n} düğmede aynı")


def current_names() -> dict:
    steps = {cli.step_slug(m): m.id for m in all_modules()}
    actions = {m.id: {cli.action_slug(f): f["key"] for f in cli.action_fields(m)}
               for m in all_modules()}
    return {"steps": steps, "actions": {k: v for k, v in actions.items() if v}}


def check_names(errors: list[str], update: bool) -> None:
    recorded = _load_names()
    for slug, mid in recorded.get("steps", {}).items():
        mod = cli.resolve_step(slug)
        if mod is None or mod.id != mid:
            errors.append(f"kısa ad '{slug}' artık {mid} adımına çözülmüyor: "
                          f'cli._STEP_ALIASES\'a "{slug}": "{mid}" ekleyin')
    mods = {m.id: m for m in all_modules()}
    for mid, acts in recorded.get("actions", {}).items():
        mod = mods.get(mid)
        for slug, key in acts.items():
            field = cli.resolve_action(mod, slug) if mod else None
            if field is None or field["key"] != key:
                errors.append(f"{mid}: eylem adı '{slug}' artık {key} düğmesine çözülmüyor: "
                              f'cli._ACTION_ALIASES\'a ("{mid}", "{slug}"): "{key}" ekleyin')

    now = current_names()
    new_steps = {s: m for s, m in now["steps"].items() if s not in recorded.get("steps", {})}
    new_actions = {mid: {s: k for s, k in acts.items()
                         if s not in recorded.get("actions", {}).get(mid, {})}
                   for mid, acts in now["actions"].items()}
    new_actions = {k: v for k, v in new_actions.items() if v}
    if not (new_steps or new_actions):
        return
    if update:
        recorded.setdefault("steps", {}).update(new_steps)
        for mid, acts in new_actions.items():
            recorded.setdefault("actions", {}).setdefault(mid, {}).update(acts)
        NAMES_FILE.write_text(json.dumps(recorded, ensure_ascii=False, indent=2,
                                         sort_keys=True) + "\n", encoding="utf-8")
        print(f"BİLGİ  {NAMES_FILE.name} güncellendi: "
              f"{len(new_steps)} kısa ad, {sum(map(len, new_actions.values()))} eylem adı")
        return
    for slug in new_steps:
        errors.append(f"yeni kısa ad '{slug}' kaydedilmemiş (--guncelle)")
    for mid, acts in new_actions.items():
        for slug in acts:
            errors.append(f"{mid}: yeni eylem adı '{slug}' kaydedilmemiş (--guncelle)")


def check_examples(errors: list[str]) -> None:
    keys = {m.id: {f["key"] for f in cli._schema_fields(m)} for m in all_modules()}
    for cmd, desc_key in cli._EXAMPLES:
        for mid in re.findall(r"\{[NS]:([a-z0-9_]+)\}", cmd + cli.t(desc_key)):
            if mid not in keys:
                errors.append(f"örnek '{cmd}': tanımsız adım kimliği {mid}")
        words = shlex.split(cli._fill_step_refs(cmd).split("|")[0])
        step = None
        for i, w in enumerate(words):
            nxt = words[i + 1] if i + 1 < len(words) else ""
            if w in ("-a", "-o", "-u", "-g", "-x"):
                step = cli.resolve_step(nxt)
                if step is None:
                    errors.append(f"örnek '{cmd}': tanımsız adım {nxt}")
                elif w == "-x" and i + 2 < len(words) and not words[i + 2].startswith("-"):
                    if cli.resolve_action(step, words[i + 2]) is None:
                        errors.append(f"örnek '{cmd}': {step.id} adımında "
                                      f"'{words[i + 2]}' eylemi yok")
            elif w == "-d" and step is not None:
                key = nxt.partition("=")[0]
                if key not in keys[step.id]:
                    errors.append(f"örnek '{cmd}': {step.id} adımında '{key}' ayarı yok")


def check_branches(errors: list[str]) -> None:
    cli_src = (ROOT / "tiha/core/cli.py").read_text(encoding="utf-8")
    for mid in re.findall(r"\bmod(?:ule)?\.id\s*==\s*\"([a-z0-9_]+)\"", cli_src):
        errors.append(f"tiha/core/cli.py: {mid} için adıma özel dal; değeri modülde "
                      "default_from ile üretin")
    rel = "tiha/ui/pages.py"
    src = (ROOT / rel).read_text(encoding="utf-8")
    found = Counter(re.findall(r"\bself\.module\.id\s*==\s*\"([a-z0-9_]+)\"", src))
    for mid, n in found.items():
        allowed = _UI_ONLY_BRANCHES.get((rel, mid), 0)
        if n > allowed:
            errors.append(f"{rel}: {mid} için {n} adıma özel dal (izinli {allowed}). Form "
                          "dolduruyorsa modülde default_from'a taşıyın; yalnız arayüze "
                          "aitse tools/cli_check.py _UI_ONLY_BRANCHES'a gerekçesiyle ekleyin")


def check_schema(errors: list[str]) -> None:
    """Makine arayüzünün form tanımı (tiha --sema) her adım için üretilmeli."""
    from tiha.ui import layout

    try:
        json.dumps(cli._presentation(), ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"--sema presentation üretilemedi: {exc}")
    # styles.css'te seçici yeniden adlandırılırsa şemadaki renk sessizce kaybolmasın.
    missing = sorted(set(layout.STYLE_ROLES) - set(layout.css_tokens()))
    if missing:
        errors.append("data/styles.css'te bulunamayan görünüş rolleri: "
                      + ", ".join(f"{r} ({layout.STYLE_ROLES[r]})" for r in missing))
    for kind in {f.get("type", "text") for v in PARAMS_SCHEMA.values() for f in v}:
        if kind not in layout.WIDGETS:
            errors.append(f"'{kind}' türünün arayüz öğesi ui/layout.py WIDGETS'ta yok")
    for mod in all_modules():
        try:
            json.dumps(cli._step_schema(mod), ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{mod.id}: --sema tanımı üretilemedi: {exc}")
        if mod.scope not in ("both", "image"):
            errors.append(f"{mod.id}: scope '{mod.scope}' geçersiz (both | image)")
        for f in PARAMS_SCHEMA.get(mod.id, []):
            if f.get("remote_warning_when") and not f.get("remote_warning"):
                errors.append(f"{mod.id}.{f.get('key')}: remote_warning_when var, "
                              "remote_warning yok")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--guncelle", action="store_true",
                    help="yeni kısa ad ve eylem adlarını cli_names.json'a ekle")
    args = ap.parse_args()
    errors: list[str] = []
    check_types(errors)
    check_buttons(errors)
    check_names(errors, args.guncelle)
    check_examples(errors)
    check_branches(errors)
    check_schema(errors)
    for e in errors:
        print(f"HATA   {e}")
    names = current_names()
    print(f"\n{len(names['steps'])} adım, {sum(map(len, names['actions'].values()))} eylem, "
          f"{len(cli._EXAMPLES)} örnek denetlendi, {len(errors)} hata.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
