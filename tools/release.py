#!/usr/bin/env python3
"""TiHA otomatik sürüm artırımı ve changelog üreteci.

GitHub Actions (`.github/workflows/release.yml`) tarafından her main push'unda
çalıştırılır. Yerelden de `python3 tools/release.py --dry-run` ile karar önizlemesi
yapılabilir.

Karar mantığı — her commit subject + body'sine bakılır:

  * "[skip release]" işaretçisi      → bu commit bumplamaz
  * "[major]" veya "BREAKING CHANGE" → major
  * "[minor]" veya "feat(...)!?:" öneki → minor
  * "docs:", "chore:", "style:", "test:", "ci:", "build:", "refactor:" öneki
    ya da "README" ile başlayan subject → bumplamaz (çok minör değişiklik)
  * Diğer her şey                    → patch (varsayılan)

Tüm commit'ler bumplamıyorsa hiç release çıkmaz (workflow skip eder).
Aksi halde en yüksek seviye uygulanır.

Çıktılar:
  * tiha/__init__.py içindeki __version__ güncellenir
  * pyproject.toml içindeki version güncellenir
  * Proje köküne RELEASE_NOTES.md yazılır (gh release create --notes-file için)
  * GITHUB_OUTPUT'a skip/new_version/old_version/level yazılır
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT_PY = ROOT / "tiha" / "__init__.py"
PYPROJECT = ROOT / "pyproject.toml"
NOTES_FILE = ROOT / "RELEASE_NOTES.md"
REPO_SLUG = "enseitankado/tiha"

VERSION_RE_INIT = re.compile(r'^(__version__\s*=\s*")([^"]+)(".*)$', re.M)
VERSION_RE_PYPROJECT = re.compile(r'^(version\s*=\s*")([^"]+)(".*)$', re.M)

FEAT_RE = re.compile(r"^feat(\([^)]+\))?!?:", re.IGNORECASE)
SKIP_PREFIX_RE = re.compile(
    r"^(docs|chore|style|test|ci|build|refactor)(\([^)]+\))?:",
    re.IGNORECASE,
)
README_PREFIX_RE = re.compile(r"^README\b", re.IGNORECASE)
SKIP_TAG_RE = re.compile(r"\[skip release\]", re.IGNORECASE)
MINOR_TAG_RE = re.compile(r"\[minor\]", re.IGNORECASE)
MAJOR_TAG_RE = re.compile(r"\[major\]", re.IGNORECASE)
BREAKING_RE = re.compile(r"BREAKING CHANGE", re.IGNORECASE)

LEVEL_SKIP = 0
LEVEL_PATCH = 1
LEVEL_MINOR = 2
LEVEL_MAJOR = 3
LEVEL_NAMES = {0: "skip", 1: "patch", 2: "minor", 3: "major"}


def run(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, cwd=str(ROOT), text=True).strip()


def get_last_tag() -> str | None:
    """En son vX.Y.Z formatlı git tag'ini döner. Yoksa None."""
    try:
        return run(
            ["git", "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*"]
        )
    except subprocess.CalledProcessError:
        return None


def get_current_version() -> str:
    txt = INIT_PY.read_text(encoding="utf-8")
    m = VERSION_RE_INIT.search(txt)
    if not m:
        sys.exit("tiha/__init__.py içinde __version__ bulunamadı")
    return m.group(2)


def parse_commits(since: str | None) -> list[tuple[str, str, str]]:
    """`since`..HEAD aralığındaki commit'leri (sha, subject, body) listesi olarak döner.

    `since` None ise tüm geçmiş döner. Merge commit'leri atlanır.
    """
    # Ayraçlar: subprocess argümanı içinde NUL geçemez; tek seferde okumayı
    # mümkün kıldığı için commit'ler arası `<<TIHA_REC>>` ve alan ayracı
    # `<<TIHA_FLD>>` kullanılır.
    sep_field = "<<TIHA_FLD>>"
    sep_record = "<<TIHA_REC>>"
    fmt = f"%H{sep_field}%s{sep_field}%b{sep_record}"
    if since:
        cmd = ["git", "log", f"{since}..HEAD", "--no-merges", f"--format={fmt}"]
    else:
        cmd = ["git", "log", "--no-merges", f"--format={fmt}"]
    output = run(cmd)
    records: list[tuple[str, str, str]] = []
    if not output:
        return records
    for chunk in output.split(sep_record):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        parts = chunk.split(sep_field)
        if len(parts) < 2:
            continue
        sha = parts[0]
        subject = parts[1]
        body = parts[2] if len(parts) > 2 else ""
        records.append((sha, subject, body))
    return records


def classify(subject: str, body: str) -> int:
    """Bir commit'in hangi bump seviyesine katkı sağladığını döner."""
    text = f"{subject}\n{body}"
    if SKIP_TAG_RE.search(text):
        return LEVEL_SKIP
    if MAJOR_TAG_RE.search(text) or BREAKING_RE.search(text):
        return LEVEL_MAJOR
    if MINOR_TAG_RE.search(text) or FEAT_RE.match(subject):
        return LEVEL_MINOR
    if SKIP_PREFIX_RE.match(subject) or README_PREFIX_RE.match(subject):
        return LEVEL_SKIP
    return LEVEL_PATCH


def bump(current: str, level: int) -> str:
    parts = current.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        sys.exit(f"Beklenmedik sürüm formatı: {current}")
    major, minor, patch = (int(p) for p in parts)
    if level == LEVEL_MAJOR:
        major += 1
        minor = 0
        patch = 0
    elif level == LEVEL_MINOR:
        minor += 1
        patch = 0
    elif level == LEVEL_PATCH:
        patch += 1
    else:
        sys.exit(f"bump() skip seviyesiyle çağrılamaz: {level}")
    return f"{major}.{minor}.{patch}"


def update_version_in_file(path: Path, regex: re.Pattern[str], new_version: str) -> None:
    txt = path.read_text(encoding="utf-8")
    new_txt, count = regex.subn(
        lambda m: f"{m.group(1)}{new_version}{m.group(3)}",
        txt,
        count=1,
    )
    if count != 1:
        sys.exit(f"{path} içinde sürüm satırı bulunamadı/güncellenemedi")
    path.write_text(new_txt, encoding="utf-8")


def clean_subject(subject: str) -> str:
    """Subject'ten araç içi işaretçileri (`[minor]` vb.) ayıklar."""
    s = MINOR_TAG_RE.sub("", subject)
    s = MAJOR_TAG_RE.sub("", s)
    s = SKIP_TAG_RE.sub("", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def write_release_notes(
    path: Path,
    new_version: str,
    base_version: str,
    has_previous_tag: bool,
    commits: list[tuple[str, str, str]],
) -> None:
    """Release gövdesini yazar. Sonradan okuyacak kişi sıradan bir kullanıcı —
    teknik etiketler değil, ne değiştiğini sade Türkçeyle özetler."""
    contributing = [
        (sha, subject) for sha, subject, body in commits
        if classify(subject, body) != LEVEL_SKIP
    ]
    lines: list[str] = ["## Bu sürümde neler değişti?", ""]
    if not contributing:
        lines.append("- (yalnızca dahili bakım değişiklikleri)")
    else:
        for sha, subject in contributing:
            lines.append(f"- {clean_subject(subject)} (`{sha[:7]}`)")
    lines.append("")
    if has_previous_tag:
        lines.append(
            f"Tüm değişiklikler: "
            f"https://github.com/{REPO_SLUG}/compare/v{base_version}...v{new_version}"
        )
    else:
        lines.append(
            f"Tüm commit'ler: "
            f"https://github.com/{REPO_SLUG}/commits/v{new_version}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_github_output(**kwargs: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    with open(out, "a", encoding="utf-8") as fp:
        for k, v in kwargs.items():
            fp.write(f"{k}={v}\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dry-run", action="store_true",
        help="Karar ve yeni sürümü yazdır; dosya/RELEASE_NOTES yazma.",
    )
    args = p.parse_args()

    last_tag = get_last_tag()
    current = get_current_version()
    base_version = last_tag.lstrip("vV") if last_tag else current

    commits = parse_commits(last_tag)
    if not commits:
        print(f"Bumplanacak commit yok (son tag: {last_tag or 'yok'}).")
        write_github_output(skip="true")
        return 0

    level = LEVEL_SKIP
    for _sha, subject, body in commits:
        level = max(level, classify(subject, body))

    if level == LEVEL_SKIP:
        print(
            f"Tüm commit'ler bump dışı (toplam {len(commits)}). "
            "Release çıkmıyor."
        )
        write_github_output(skip="true")
        return 0

    new_version = bump(base_version, level)
    print(
        f"Bump: {base_version} → {new_version} "
        f"({LEVEL_NAMES[level]}, {len(commits)} commit)"
    )

    if args.dry_run:
        return 0

    update_version_in_file(INIT_PY, VERSION_RE_INIT, new_version)
    update_version_in_file(PYPROJECT, VERSION_RE_PYPROJECT, new_version)
    write_release_notes(
        NOTES_FILE,
        new_version=new_version,
        base_version=base_version,
        has_previous_tag=last_tag is not None,
        commits=commits,
    )
    write_github_output(
        skip="false",
        new_version=new_version,
        old_version=base_version,
        level=LEVEL_NAMES[level],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
