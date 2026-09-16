"""Modül 17 — Başarım (Deneysel): oturum kalıntıları ve ETA Hafif Mod.

Ne yapar?
1. **Eski oturum kalıntılarını temizle.** Pardus ETAP'ta (Debian 12,
   systemd 252) ``systemd-logind`` varsayılan olarak
   ``KillUserProcesses=no`` ile çalışır: öğretmen oturumunu kapattığında
   o oturumdan kalan süreçler (kapatılmadan bırakılan Firefox/Chrome ve
   bütün alt süreçleri, kullanıcı servisleri) arka planda yaşamaya
   devam eder, oturum ``closing`` durumunda asılı kalır. Gün içinde
   birkaç öğretmen girip çıktığında bellek ve işlemci birikerek tükenir.
   Bu adım ``/etc/systemd/logind.conf.d/90-tiha-oturum-kalintilari.conf``
   ile ``KillUserProcesses=yes`` yazar; logind oturum kapanınca oturumun
   cgroup'undaki her süreci sonlandırır (çift fork ile "kaçan" süreçler
   de cgroup'ta kaldığı için yakalanır). systemd 252'de logind
   yapılandırmayı yeniden okuyamaz (``CanReload=no``) ve çalışan grafik
   oturumun altında yeniden başlatılması riskli olduğundan ayar **bir
   sonraki açılışta** etkin olur. İsteğe bağlı olarak şu an ``closing``
   durumunda asılı kalmış oturumlar ``loginctl terminate-session`` ile
   hemen kapatılır.

2. **ETA Hafif Mod.** Pardus'un ``eta-light-mode`` paketi (yoksa)
   kurulur ve paketin "Tüm kullanıcılara uygula" düzeni kurulur:
   seçilen ayarlar ``/etc/eta-light-mode/settings.json``'a, autostart
   girdisi ``/etc/xdg/autostart/``'a yazılır; her kullanıcı oturum
   açtığında ``eta-light-mode --apply-config`` ayarları kendi
   oturumuna uygular. Yazma işi paketin kendi yardımcısı
   (``Action.py enable``) ile yapılır. JSON'a yalnızca seçilen
   anahtarlar ``true`` olarak yazılır: ``false`` paketin gözünde
   "dokunma" değil "koda gömülü normal değere döndür" demektir.
   Çalışan oturuma canlı uygulanmaz (bkz. m14 postmortem: canlı
   Cinnamon/Nemo müdahaleleri gerçek donanımda güvenilmez); etki bir
   sonraki oturum açılışında görülür.

Neden deneysel?
İki mekanizma da gerçek tahta donanımında henüz doğrulanmadı.
``eta-light-mode``'un JSON anahtarları paketin resmî bir arayüzü değil;
sürüm ve anahtar adları her uygulamada denetlenir.

Geri al.
İlk uygulamadan önceki durum ``original.json``'a kaydedilir. Geri alma
yalnızca TiHA'nın dokunduğu parçaları o duruma döndürür: logind drop-in
silinir (ya da önceki içeriği geri yazılır), hafif mod dosyaları silinir
ya da yedekten geri yüklenir, paketi TiHA kurduysa ``apt-get purge``
edilir. logind değişikliği yine açılışta etkin olur. Hafif mod ayarları
bir kez uygulanmış kullanıcıların kişisel ayarları geri dönmez.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.utils import run_cmd, run_cmd_stream

log = get_logger(__name__)

# --- Oturum kalıntıları (systemd-logind) ------------------------------------

LOGIND_DROPIN = Path("/etc/systemd/logind.conf.d/90-tiha-oturum-kalintilari.conf")
# systemd drop-in'leri bütün dizinlerden dosya adına göre sıralayıp okur;
# sonra gelen kazanır. Çakışma taraması bu dizinlerin hepsine bakar.
LOGIND_DROPIN_DIRS = (
    Path("/etc/systemd/logind.conf.d"),
    Path("/run/systemd/logind.conf.d"),
    Path("/usr/local/lib/systemd/logind.conf.d"),
    Path("/usr/lib/systemd/logind.conf.d"),
)
LOGIND_DROPIN_CONTENT = """# TiHA — Başarım (Deneysel) adımı tarafından yazılmıştır.
# Kullanıcı oturumu kapandığında o oturumdan kalan bütün süreçler
# (kapatılmadan bırakılan tarayıcılar ve alt süreçleri dahil) sonlandırılır.
# systemd-logind bu dosyayı yalnızca başlarken (açılışta) okur.
[Login]
KillUserProcesses=yes
KillExcludeUsers=root
"""

# --- ETA Hafif Mod (eta-light-mode) -----------------------------------------

LIGHT_PKG = "eta-light-mode"
LIGHT_SRC = Path("/usr/share/eta/eta-light-mode/src")
LIGHT_ACTION = LIGHT_SRC / "Action.py"
LIGHT_SETTINGS_PY = LIGHT_SRC / "Settings.py"
LIGHT_SETTINGS = Path("/etc/eta-light-mode/settings.json")
LIGHT_AUTOSTART = Path("/etc/xdg/autostart/tr.org.eta.light-mode-autostart.desktop")
# JSON anahtarlarının ve Action.py davranışının okunup doğrulandığı sürümler.
LIGHT_TESTED_VERSIONS = ("0.2.3",)

# Form alanı → eta-light-mode ayar anahtarları. Yazı ve ikon küçültme
# başarım ayarı değil, 1600x900'ün büyüttüğü arayüzün telafisi; bu yüzden
# çözünürlük düşürmeyle birlikte gelir, tek başına seçilmez.
LIGHT_FIELDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("lm_effects", "Animasyonlar kapalı", ("effects",)),
    ("lm_compositor", "Tam ekran pencereler doğrudan çizilir", ("compositor",)),
    ("lm_thumbnails", "Resim önizlemeleri kapalı", ("thumbnails",)),
    ("lm_directory_counts", "Klasör öğesi sayımı kapalı", ("directory-item-counts",)),
    ("lm_app_monitoring", "Uygulama izleme kapalı", ("app-monitoring",)),
    ("lm_low_resolution", "Çözünürlük 1600x900 (+ yazı/ikon telafisi)",
     ("low-resolution", "text-scaling", "file-icon-size")),
    ("lm_low_refresh_rate", "Yenileme hızı 50 Hz", ("low-refresh-rate",)),
)

# İlk uygulamadan önceki durum ve dosya yedekleri (state_dir altında).
ORIGINAL_STATE = "original.json"
BACKUP_NAMES = {
    "logind_dropin": "logind-dropin.conf.orig",
    "light_settings": "light-settings.json.orig",
    "light_autostart": "light-autostart.desktop.orig",
}
BACKUP_TARGETS = {
    "logind_dropin": LOGIND_DROPIN,
    "light_settings": LIGHT_SETTINGS,
    "light_autostart": LIGHT_AUTOSTART,
}


def _as_bool(value: object) -> bool:
    """GUI ``"True"/"False"`` metni, CLI preset gerçek bool gönderir."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("true", "1", "yes", "on", "evet")


def _mb(n_bytes: int) -> str:
    return f"{n_bytes / (1024 * 1024):.0f} MB"


# --- logind yardımcıları -----------------------------------------------------

def _runtime_kill_user_processes() -> bool | None:
    """logind'in **şu an çalışan** KillUserProcesses değeri (dosya değil)."""
    r = run_cmd(
        ["busctl", "get-property", "org.freedesktop.login1",
         "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
         "KillUserProcesses"],
        timeout=5,
    )
    if not r.ok:
        return None
    return r.stdout.strip() == "b true"


def _logind_conflicts() -> list[Path]:
    """Bizden sonra okunup KillUserProcesses'i ezen drop-in dosyaları."""
    conflicts = []
    for directory in LOGIND_DROPIN_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.conf")):
            if path.name <= LOGIND_DROPIN.name:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if re.search(r"^\s*KillUserProcesses\s*=", text, re.MULTILINE):
                conflicts.append(path)
    return conflicts


def _cgroup_anon_bytes(unit: str) -> int | None:
    """Birimin cgroup'unda tutulan anonim bellek (önbellek hariç)."""
    r = run_cmd(["systemctl", "show", unit, "-p", "ControlGroup", "--value"], timeout=5)
    cgroup = r.stdout.strip() if r.ok else ""
    if not cgroup:
        return None
    try:
        for line in Path(f"/sys/fs/cgroup{cgroup}/memory.stat").read_text().splitlines():
            key, _, value = line.partition(" ")
            if key == "anon":
                return int(value)
    except (OSError, ValueError):
        pass
    return None


def _sessions() -> list[dict[str, str]]:
    r = run_cmd(["loginctl", "list-sessions", "--no-legend"], timeout=5)
    if not r.ok:
        return []
    sessions = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        det = run_cmd(
            ["loginctl", "show-session", parts[0],
             "-p", "Id", "-p", "Name", "-p", "User", "-p", "State",
             "-p", "Service", "-p", "Timestamp", "-p", "Scope"],
            timeout=5,
        )
        if not det.ok:
            continue
        props = {}
        for prop_line in det.stdout.splitlines():
            key, sep, value = prop_line.partition("=")
            if sep:
                props[key] = value
        sessions.append(props)
    return sessions


def _lingering_sessions() -> list[dict]:
    """Kapanmış ama süreçleri kaldığı için ``closing``'de asılı oturumlar.

    Kullanıcının başka canlı oturumu yoksa ``user@UID.service``'in
    (pipewire, gvfs, portal vb.) belleği de o kalıntıya sayılır.
    """
    sessions = _sessions()
    live_uids = {
        s.get("User") for s in sessions if s.get("State") in ("active", "online")
    }
    result = []
    for s in sessions:
        try:
            uid = int(s.get("User", "0"))
        except ValueError:
            continue
        if s.get("State") != "closing" or uid < 1000:
            continue
        anon = _cgroup_anon_bytes(s.get("Scope") or f"session-{s.get('Id')}.scope") or 0
        if s.get("User") not in live_uids:
            anon += _cgroup_anon_bytes(f"user@{uid}.service") or 0
        result.append({
            "id": s.get("Id", ""),
            "name": s.get("Name", ""),
            "service": s.get("Service", ""),
            "since": s.get("Timestamp", ""),
            "anon": anon,
        })
    return result


# --- eta-light-mode yardımcıları ---------------------------------------------

def _pkg_version(name: str) -> str | None:
    r = run_cmd(["dpkg-query", "-W", "-f=${Status}\t${Version}", name], timeout=10)
    if not r.ok:
        return None
    status, _, version = r.stdout.partition("\t")
    return version.strip() if "install ok installed" in status else None


def _light_mode_keys() -> set[str]:
    """Kurulu paketin tanıdığı ayar anahtarları (yorum satırları hariç)."""
    keys: set[str] = set()
    try:
        text = LIGHT_SETTINGS_PY.read_text(encoding="utf-8")
    except OSError:
        return keys
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = re.search(r'"name":\s*"([a-z0-9-]+)"', line)
        if m:
            keys.add(m.group(1))
    return keys


def _read_light_settings() -> dict | None:
    try:
        data = json.loads(LIGHT_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class PerformanceModule(Module):
    id = "m17_performance"
    title = "Başarım (Deneysel)"
    sidebar_title = "Başarım (Deneysel)"
    experimental = True
    streams_output = True
    popup_on_success = True
    doc_url = (
        "https://github.com/enseitankado/tiha/blob/main/"
        "docs/m17-basarim-deneysel.md"
    )
    doc_label = "Başarım (Deneysel) — mekanizma, ölçümler ve tahtada deneme"
    apply_hint = (
        "Oturum kapanınca kalan süreçler sonlandırılır (açılıştan sonra); "
        "seçilirse ETA Hafif Mod tüm kullanıcılara uygulanır."
    )
    rationale = (
        "Bir öğretmen tarayıcısını kapatmadan oturumunu kapattığında "
        "Firefox, Chrome ve bütün alt süreçleri arka planda çalışmaya "
        "devam eder. Dört sekmeli bir tarayıcı 600 MB – 1 GB bellek tutar "
        "ve işlemci harcamayı sürdürür. Tahta gün içinde yeniden "
        "başlatılmadığında her yeni öğretmen oturumu bu yükün üstüne "
        "eklenir.\n\n"
        "\"Eski oturum kalıntılarını temizle\" seçeneği, oturum kapanınca o "
        "oturumdan kalan her süreci sistemin kendi oturum yöneticisine "
        "(systemd-logind) sonlandırtır. Ayar bir sonraki açılışta etkin "
        "olur.\n\n"
        "ETA Hafif Mod, Pardus'un düşük donanımlı tahtalar için hazırladığı "
        "eta-light-mode paketidir. Seçilen ayarlar tahtadaki bütün "
        "kullanıcılara her oturum açılışında uygulanır. Çözünürlük ve "
        "yenileme hızı düşürme ekranı ve kalem çizgisini bulanıklaştırır; "
        "önce tek bir tahtada deneyin."
    )

    # ------------------------------------------------------------------
    # Önizleme
    # ------------------------------------------------------------------

    def preview(self) -> str:
        lines = ["Oturum kalıntıları"]
        runtime = _runtime_kill_user_processes()
        dropin_ours = self._dropin_is_ours()
        if runtime is True:
            state = "etkin (oturum kapanınca süreçler sonlandırılıyor)"
        elif dropin_ours:
            state = "yapılandırıldı, açılışta etkin olacak"
        elif runtime is False:
            state = "kapalı (Pardus varsayılanı; süreçler asılı kalıyor)"
        else:
            state = "okunamadı"
        lines.append(f"  Durum            : {state}")

        lingering = _lingering_sessions()
        if lingering:
            total = sum(s["anon"] for s in lingering)
            lines.append(
                f"  Asılı oturum     : {len(lingering)} "
                f"(toplam ~{_mb(total)} bellek)"
            )
            for s in lingering:
                lines.append(
                    f"    - {s['name']} (oturum {s['id']}, {s['service']}, "
                    f"{s['since']}) ~{_mb(s['anon'])}"
                )
        else:
            lines.append("  Asılı oturum     : yok")

        lines += ["", "ETA Hafif Mod"]
        version = _pkg_version(LIGHT_PKG)
        lines.append(
            f"  Paket            : {version + ' kurulu' if version else 'kurulu değil (uygulanırsa kurulur)'}"
        )
        settings = _read_light_settings()
        if settings is not None and LIGHT_AUTOSTART.exists():
            active = sorted(k for k, v in settings.items() if v is True)
            lines.append(
                "  Tüm kullanıcılar : etkin — " + (", ".join(active) or "(seçili ayar yok)")
            )
        else:
            lines.append("  Tüm kullanıcılar : etkin değil")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Uygula
    # ------------------------------------------------------------------

    def apply(
        self,
        params: dict | None = None,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        p = dict(params or {})
        kill_processes = _as_bool(p.get("kill_user_processes"))
        terminate_now = _as_bool(p.get("terminate_lingering"))
        light_mode = _as_bool(p.get("light_mode_enabled"))

        if not (kill_processes or terminate_now or light_mode):
            return ApplyResult(False, "Hiçbir seçenek işaretlenmedi; değişiklik yapılmadı.")

        def say(line: str) -> None:
            if progress:
                progress(line)

        original = self._load_or_capture_original()
        summary: list[str] = []
        details: list[str] = []
        warnings: list[str] = []
        data: dict = {"params": p}

        # Parçalardan biri başarısız olsa da uygulanan parça "applied" olarak
        # günlüğe düşmeli; aksi hâlde UI geri al düğmesi göstermez ve
        # yazılmış ayar geri alınamaz kalır. Başarısızlık uyarı diyaloğuyla
        # bildirilir.
        failures: list[str] = []

        if kill_processes:
            ok, text = self._apply_logind(original, say, warnings)
            if ok:
                summary.append(text)
                details.append(f"logind: {LOGIND_DROPIN}")
                data["session_cleanup"] = True
            else:
                failures.append(text)

        if terminate_now:
            summary.append(self._terminate_lingering(say))

        if light_mode:
            keys = [
                key
                for field, _label, field_keys in LIGHT_FIELDS
                if _as_bool(p.get(field))
                for key in field_keys
            ]
            if not keys:
                failures.append(
                    "ETA Hafif Mod işaretli ama hiçbir alt ayar seçilmedi; "
                    "hafif mod uygulanmadı."
                )
            else:
                ok, text = self._apply_light_mode(original, keys, say, warnings)
                if ok:
                    summary.append(text)
                    details.append(f"Hafif mod: {LIGHT_SETTINGS} → {', '.join(keys)}")
                    data["light_mode_keys"] = keys
                else:
                    failures.append(text)

        self._save_original(original)
        if not summary:
            return ApplyResult(
                False, " ".join(failures), details="\n".join(warnings), data=data,
            )
        return ApplyResult(
            True,
            " ".join(summary),
            details="\n".join(details + failures + warnings),
            data=data,
            warning="\n\n".join(failures + warnings) or None,
        )

    def _apply_logind(self, original: dict, say, warnings: list[str]) -> tuple[bool, str]:
        say("==== Oturum kalıntıları: systemd-logind ====")
        try:
            current = LOGIND_DROPIN.read_text(encoding="utf-8") if LOGIND_DROPIN.exists() else None
            if current != LOGIND_DROPIN_CONTENT:
                LOGIND_DROPIN.parent.mkdir(parents=True, exist_ok=True)
                LOGIND_DROPIN.write_text(LOGIND_DROPIN_CONTENT, encoding="utf-8")
                LOGIND_DROPIN.chmod(0o644)
                say(f"Yazıldı: {LOGIND_DROPIN}")
            else:
                say(f"Zaten yazılı: {LOGIND_DROPIN}")
        except OSError as exc:
            return False, f"logind ayar dosyası yazılamadı: {exc}"
        original["touched"]["logind"] = True
        self._save_original(original)

        for path in _logind_conflicts():
            warnings.append(
                f"{path} dosyası KillUserProcesses ayarını TiHA'dan sonra "
                "okunarak ezebilir; içeriğini denetleyin."
            )

        if _runtime_kill_user_processes() is True:
            return True, "Oturum kalıntı temizliği etkin."
        say("systemd-logind yapılandırmayı yeniden okuyamıyor (systemd 252); "
            "ayar bir sonraki açılışta etkin olacak.")
        return True, "Oturum kalıntı temizliği yapılandırıldı (yeniden başlatınca etkin)."

    def _terminate_lingering(self, say) -> str:
        say("\n==== Asılı kalmış oturumlar ====")
        lingering = _lingering_sessions()
        if not lingering:
            say("Asılı oturum yok.")
            return "Asılı oturum bulunmadı."
        freed = 0
        closed = []
        for s in lingering:
            say(f"Sonlandırılıyor: {s['name']} (oturum {s['id']}) ~{_mb(s['anon'])}")
            r = run_cmd(["loginctl", "terminate-session", s["id"]], timeout=15)
            if r.ok:
                freed += s["anon"]
                closed.append(s["id"])
            else:
                say(f"  ✗ {r.stderr.strip() or 'terminate-session başarısız'}")
        # terminate-session asenkrondur; süreçlerin gerçekten gittiğini
        # kısa süre izleyip raporla.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            remaining = {s["id"] for s in _lingering_sessions()} & set(closed)
            if not remaining:
                break
            time.sleep(1)
        say(f"Kapatılan oturum: {len(closed)}, serbest kalan bellek ~{_mb(freed)}")
        return f"{len(closed)} asılı oturum kapatıldı (~{_mb(freed)})."

    def _apply_light_mode(
        self, original: dict, keys: list[str], say, warnings: list[str],
    ) -> tuple[bool, str]:
        say("\n==== ETA Hafif Mod ====")
        version = _pkg_version(LIGHT_PKG)
        if version is None:
            env = {"DEBIAN_FRONTEND": "noninteractive"}
            say("\n==== apt-get update ====")
            if not run_cmd_stream(["apt-get", "update"], progress=say, env=env, timeout=300).ok:
                return False, "apt-get update başarısız; eta-light-mode kurulamadı."
            say(f"\n==== apt-get install {LIGHT_PKG} ====")
            if not run_cmd_stream(
                ["apt-get", "install", "-y", LIGHT_PKG], progress=say, env=env, timeout=600,
            ).ok:
                return False, f"{LIGHT_PKG} kurulamadı."
            version = _pkg_version(LIGHT_PKG)
            if version is None:
                return False, f"{LIGHT_PKG} kurulumu doğrulanamadı."
        say(f"{LIGHT_PKG} sürümü: {version}")
        original["touched"]["light"] = True
        self._save_original(original)

        if version not in LIGHT_TESTED_VERSIONS:
            warnings.append(
                f"{LIGHT_PKG} {version} sürümü TiHA ile denenmedi "
                f"(denenen: {', '.join(LIGHT_TESTED_VERSIONS)}). Ayarların "
                "tahtada uygulandığını ilk oturumda gözle doğrulayın."
            )
        known = _light_mode_keys()
        unknown = [k for k in keys if k not in known]
        if unknown:
            warnings.append(
                "Kurulu eta-light-mode şu ayarları tanımıyor, atlandı: "
                + ", ".join(unknown)
            )
            keys[:] = [k for k in keys if k in known]
        if not keys:
            return False, "Seçilen hafif mod ayarlarının hiçbiri kurulu pakette yok."
        if not LIGHT_ACTION.is_file():
            return False, f"{LIGHT_ACTION} bulunamadı; paket yapısı değişmiş olabilir."

        payload = {k: True for k in keys}
        r = run_cmd(
            ["python3", str(LIGHT_ACTION), "enable"],
            input_data=json.dumps(payload), timeout=30,
        )
        if not r.ok:
            return False, f"eta-light-mode ayarları yazılamadı: {r.stderr.strip() or r.returncode}"
        if _read_light_settings() != payload or not LIGHT_AUTOSTART.exists():
            return False, "eta-light-mode ayarları yazıldı görünüyor ama doğrulanamadı."
        say(f"Yazıldı: {LIGHT_SETTINGS} ({', '.join(keys)})")
        say(f"Autostart: {LIGHT_AUTOSTART}")
        return True, "ETA Hafif Mod tüm kullanıcılara uygulandı (sonraki oturum açılışında)."

    # ------------------------------------------------------------------
    # Geri al
    # ------------------------------------------------------------------

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        original = self._load_original()
        if original is None:
            return ApplyResult(False, "Özgün durum kaydı bulunamadı; geri alınacak değişiklik yok.")
        done: list[str] = []
        errors: list[str] = []

        if original["touched"].get("logind"):
            if self._restore_or_remove("logind_dropin", original, errors):
                done.append("logind ayarı eski hâline döndü (yeniden başlatınca etkin)")

        if original["touched"].get("light"):
            light_ok = all([
                self._restore_or_remove("light_settings", original, errors),
                self._restore_or_remove("light_autostart", original, errors),
            ])
            if light_ok and not original.get("light_pkg_installed") and _pkg_version(LIGHT_PKG):
                r = run_cmd(
                    ["apt-get", "purge", "-y", LIGHT_PKG],
                    env={"DEBIAN_FRONTEND": "noninteractive"}, timeout=600,
                )
                if r.ok:
                    done.append(f"{LIGHT_PKG} kaldırıldı")
                else:
                    errors.append(f"{LIGHT_PKG} kaldırılamadı: {r.stderr.strip()}")
            if light_ok:
                done.append("ETA Hafif Mod tüm kullanıcılar için kapatıldı")

        if errors:
            return ApplyResult(False, "Geri alma kısmen başarısız.", details="\n".join(done + errors))
        shutil.rmtree(self.state_dir, ignore_errors=True)
        return ApplyResult(
            True,
            "; ".join(done) + "." if done else "Geri alınacak değişiklik yoktu.",
            details=(
                "Hafif mod ayarları bir kez uygulanmış kullanıcıların kişisel "
                "masaüstü ayarları (yazı boyutu, çözünürlük vb.) otomatik geri dönmez."
            ),
        )

    # ------------------------------------------------------------------
    # Özgün durum kaydı
    # ------------------------------------------------------------------

    def _dropin_is_ours(self) -> bool:
        try:
            return LOGIND_DROPIN.read_text(encoding="utf-8") == LOGIND_DROPIN_CONTENT
        except OSError:
            return False

    def _load_original(self) -> dict | None:
        try:
            data = json.loads((self.state_dir / ORIGINAL_STATE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        data.setdefault("touched", {})
        return data

    def _load_or_capture_original(self) -> dict:
        """İlk uygulamada dokunulacak her şeyin önceki hâlini yedekler.

        Sonraki uygulamalar kaydı değiştirmez; geri alma her zaman TiHA
        hiç dokunmadan önceki duruma döner.
        """
        existing = self._load_original()
        if existing is not None:
            return existing
        state_dir = self.ensure_state_dir()
        original: dict = {
            "light_pkg_installed": _pkg_version(LIGHT_PKG) is not None,
            "touched": {},
        }
        for name, target in BACKUP_TARGETS.items():
            original[f"{name}_existed"] = target.exists()
            if target.exists():
                shutil.copy2(target, state_dir / BACKUP_NAMES[name])
        self._save_original(original)
        return original

    def _save_original(self, original: dict) -> None:
        path = self.ensure_state_dir() / ORIGINAL_STATE
        path.write_text(json.dumps(original, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _restore_or_remove(self, name: str, original: dict, errors: list[str]) -> bool:
        target = BACKUP_TARGETS[name]
        try:
            if original.get(f"{name}_existed"):
                backup = self.state_dir / BACKUP_NAMES[name]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, target)
            elif target.exists():
                target.unlink()
        except OSError as exc:
            errors.append(f"{target}: {exc}")
            return False
        return True
