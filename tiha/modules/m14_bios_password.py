"""Modül 14 — BIOS yönetici parolası (eta-112 entegrasyonu).

Bu adım klon makinelerin **ilk açılışında** BIOS yönetici parolasını
istenen değere ayarlayan tek-seferlik bir boot servisi imaja gömer.
Kaynak tahtada (imajı aldığınız tahta) parola DEĞİŞMEZ; m12'deki
MAC imzası mekanizması burada da yeniden kullanılır:

  * MAC eşit → kaynak tahta → işlem yok
  * MAC farklı → klon → eta-112 ile parola ayarla, sentinel yaz,
    servis kendini disable et, parolayı içeren scripti sil.

Donanım desteği: eta-112 yalnızca önceden kalibre edilmiş AMI Aptio
BIOS sürümlerinde çalışır (örn. Faz 2 Vestel Gri — VESTEL 14MB37C1 /
L0.30). Wizard zamanında ``eta-112 bios info --json`` ile model
sorgulanır; desteklenmiyorsa kullanıcıya yalın bir not gösterilir ve
apply başarısız sonuçlanır (servis kurulmaz).

eta-112 aracı hem wizard tarafında (model sorgu + mevcut parolayı
oku) hem de klonda (parola ayarla) gerekir; bu yüzden:

  * Wizard'da bulunduğunda doğrudan oradan çağrılır.
  * Apply sırasında ``/usr/local/sbin/tiha-eta-112.py``'a kopyalanır
    ve klon makinedeki boot servisi bunu çağırır.

eta-112'yi bulma sırası m03'ün ``_ensure_eta_otp_cli`` ile aynıdır
(bootstrap → cache → GitHub'dan tek-deneme indirme).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ..core.i18n import t
from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.paths import STATE_DIR, VAR_ROOT
from ..core.utils import run_cmd

log = get_logger(__name__)


# --- eta-112 araç yerleşimi --------------------------------------------------

ETA_112_RAW_BASE = "https://raw.githubusercontent.com/enseitankado/eta-112/main"
ETA_112_FILE = "eta-112.py"
ETA_112_CACHE_DIR = VAR_ROOT / "eta-112"

_eta_112_path: Path | None = None
_eta_112_download_attempted: bool = False


def _eta_112_download(dest_dir: Path) -> bool:
    """Aracı GitHub'dan ``dest_dir`` altına indirir; başarılıysa True döner."""
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("eta-112 için %s oluşturulamadı: %s", dest_dir, exc)
        return False
    target = dest_dir / ETA_112_FILE
    url = f"{ETA_112_RAW_BASE}/{ETA_112_FILE}"
    res = run_cmd(["curl", "-fsSL", "-o", str(target), url], timeout=60)
    if not res.ok:
        log.warning("eta-112 indirilemedi: %s", (res.stderr or "").strip())
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    try:
        target.chmod(0o755)
    except OSError:
        pass
    return target.is_file()


def _ensure_eta_112(*, allow_download: bool = True) -> Path | None:
    """eta-112.py'nin tam yolunu döner; gerekirse indirir.

    Sıra m03'teki ``_ensure_eta_otp_cli`` ile aynı: bellek →
    ``TIHA_ETA_112_DIR`` (bootstrap.sh) → yerel önbellek → GitHub'dan
    bir kez indirme denemesi.

    ``allow_download=False`` (preview/form prefill yolları) GitHub
    indirmesini atlar — UI thread'inde ≤60 sn timeout takılmasın diye.
    """
    global _eta_112_path, _eta_112_download_attempted

    if _eta_112_path is not None and _eta_112_path.is_file():
        return _eta_112_path

    dir_env = os.environ.get("TIHA_ETA_112_DIR")
    if dir_env:
        candidate = Path(dir_env) / ETA_112_FILE
        if candidate.is_file():
            _eta_112_path = candidate
            return candidate

    cached = ETA_112_CACHE_DIR / ETA_112_FILE
    if cached.is_file():
        _eta_112_path = cached
        os.environ["TIHA_ETA_112_DIR"] = str(ETA_112_CACHE_DIR)
        return cached

    if not allow_download or _eta_112_download_attempted:
        return None
    _eta_112_download_attempted = True
    if _eta_112_download(ETA_112_CACHE_DIR) and cached.is_file():
        _eta_112_path = cached
        os.environ["TIHA_ETA_112_DIR"] = str(ETA_112_CACHE_DIR)
        return cached
    return None


# --- eta-112 sorgu yardımcıları (wizard zamanı) -----------------------------

def _eta_112_call(args: list[str], timeout: int = 30, *,
                  allow_download: bool = True) -> tuple[dict, str]:
    """eta-112 alt komutunu --json modunda çalıştırır.

    Döner: (parsed_dict, debug_text). debug_text raw stdout+stderr
    özetidir; parse hatası durumunda hatayı UI'a yansıtmak için kullanılır.
    Başarılı parse → ({...}, "").
    """
    script = _ensure_eta_112(allow_download=allow_download)
    if not script:
        return {}, t("m14.eta112.not_found")
    try:
        proc = subprocess.run(
            ["python3", str(script), "bios", *args, "--json"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log.warning("eta-112 %s çalıştırılamadı: %s", args, exc)
        return {}, t("m14.eta112.run_failed", error=exc)

    raw = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    # eta-112 destek dışı durumlarda exit=1 + JSON çıktı verir; biz exit
    # kodunu yutuyoruz, JSON içeriğine bakıyoruz.
    if not raw:
        return {}, (
            t("m14.eta112.stdout_empty", code=proc.returncode, stderr=err[:500])
            if err else
            t("m14.eta112.both_empty", code=proc.returncode)
        )
    # JSON çıktı genelde tek satır; bazı sürümlerde renkli print'lerle karışabilir.
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                return json.loads(s), ""
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(raw), ""
    except json.JSONDecodeError:
        log.warning("eta-112 JSON parse edilemedi: %s", raw[:200])
        return {}, t(
            "m14.eta112.json_error",
            code=proc.returncode, stdout=raw[:500], stderr=err[:500],
        )


def _eta_112_run(args: list[str], timeout: int = 30, *,
                 allow_download: bool = True) -> dict:
    """Eski API — sadece dict'i döner. _eta_112_call'a delege eder."""
    data, _ = _eta_112_call(args, timeout, allow_download=allow_download)
    return data


def query_bios_info(*, allow_download: bool = False) -> dict:
    """``bios info --json`` — destek bilgisi."""
    return _eta_112_run(["info"], allow_download=allow_download)


def query_bios_passwords(*, allow_download: bool = False) -> dict:
    """``bios read --json`` — mevcut parolalar (supervisor/user/previous)."""
    return _eta_112_run(["read"], allow_download=allow_download)


def read_current_supervisor() -> str:
    """Mevcut BIOS yönetici parolasını döner; tespit edilemezse boş string.

    UI formunu önceden doldurmak için pages.py tarafından çağrılır;
    hata/destek yok ya da eta-112 henüz indirilmemiş → sessiz boş."""
    data = query_bios_passwords(allow_download=False)
    if not data.get("ok") or not data.get("supported"):
        return ""
    val = data.get("supervisor")
    return val if isinstance(val, str) else ""


# --- Sistem yerleşimi -------------------------------------------------------

# m12 ile paylaşılan kaynak tahta imzası. Hangi modül önce uygularsa
# yazar; sonraki modül varsa dokunmaz.
IMAGED_MAC_FILE = STATE_DIR / "imaged-mac"

# Klona kopyalanacak eta-112 aracı (parola ayarlanması burada koşacak).
BUNDLED_ETA_112 = Path("/usr/local/sbin/tiha-eta-112.py")

# First-boot servis dosyaları
FIRST_BOOT_SCRIPT = Path("/usr/local/sbin/tiha-first-boot-bios.py")
FIRST_BOOT_SERVICE = Path("/etc/systemd/system/tiha-first-boot-bios.service")
FIRST_BOOT_SERVICE_NAME = FIRST_BOOT_SERVICE.name
FIRST_BOOT_SENTINEL = STATE_DIR / "first-boot-bios.done"


# --- Yardımcılar ------------------------------------------------------------

def _primary_mac() -> str | None:
    """Birincil arayüzün MAC'ini lower-case ``aa:bb:cc:dd:ee:ff`` formatında
    döner. m12'deki helper ile aynı mantık — refactor edilene kadar duplicate."""
    result = run_cmd(
        ["ip", "-o", "-4", "route", "show", "to", "default"], check=False,
    )
    iface = ""
    if result.ok and result.stdout:
        parts = result.stdout.split()
        for i, tok in enumerate(parts):
            if tok == "dev" and i + 1 < len(parts):
                iface = parts[i + 1]
                break
    if not iface or not Path(f"/sys/class/net/{iface}").is_dir():
        net_root = Path("/sys/class/net")
        if net_root.is_dir():
            for entry in sorted(net_root.iterdir()):
                if entry.name == "lo":
                    continue
                if (entry / "device").is_symlink():
                    iface = entry.name
                    break
    if not iface:
        return None
    addr_path = Path(f"/sys/class/net/{iface}/address")
    if not addr_path.is_file():
        return None
    try:
        return addr_path.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return None


def _rm(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError as exc:
        log.warning("Silinemedi %s: %s", path, exc)
        return False


def _validate_password(raw: str, pw_min: int, pw_max: int) -> tuple[str, str | None]:
    """BIOS parolasını eta-112'nin kabul ettiği biçime sokar (BÜYÜK A-Z 0-9, 'I' yasak).

    Döner: (normalize_pw, hata_mesaji_or_None).

    Boş giriş geçerlidir — caller (apply / local-set action) bunu
    "parolayı temizle" niyeti olarak yorumlar ve eta-112 'clear' yoluna
    girer.
    """
    if not raw:
        return "", None  # boş = clear niyeti
    # UI input mask zaten 'I'yi reddediyor; backend doğrulamada da süzelim.
    norm = "".join(c for c in raw.upper()
                   if c != "I" and (("A" <= c <= "Z") or ("0" <= c <= "9")))
    if not norm:
        return "", t("m14.validate.charset")
    if len(norm) < pw_min:
        return norm, t("m14.validate.too_short", min=pw_min, given=len(norm))
    if len(norm) > pw_max:
        return norm, t("m14.validate.too_long", max=pw_max, given=len(norm))
    return norm, None


def _normalize_protection(raw: str | None) -> str:
    """Form'dan gelen koruma seçeneğini eta-112'nin kabul ettiği iki
    sözleşmeye sabitler: 'always' (her açılışta) veya 'setup' (yalnız
    BIOS setup'a girerken). Tanımsız değerlerde varsayılan 'setup'
    (en zarar görmez seçenek).

    UI combobox Türkçe label gönderir; 'always' anahtar kelimesi
    label'da varsa always sayılır. Aksi halde setup.
    """
    if raw is None:
        return "setup"
    s = str(raw).strip().lower()
    if s == t("m14.params.protection_mode.opt_always").strip().lower():
        return "always"
    if "always" in s or "her açılışta" in s or "her aclista" in s:
        return "always"
    return "setup"


# --- Boot servisi Python betiği ---------------------------------------------
# Şablon @@PLACEHOLDER@@'lar dışında olduğu gibi diske yazılır. f-string
# kullanmıyoruz ki iç Python kodu çakışmasın. Parola düz metin olarak
# yer-tutucuyla içeri gömülür; script chmod 700 ve root sahibinde tutulur.
#
# ETA_112_ARGV: JSON listesi — eta-112'ye verilecek argümanlar. Parolanın
# kutuya boş bırakıldığı senaryoda ["bios","clear","yonetici","--json"];
# normalde ["bios","set","--yonetici","ABC123","--koruma","setup","--json"].
# Liste olarak gömerek shell escape sorunları ve "argv'de parola"
# riskini (ps üzerinden görünür) en aza indiriyoruz — yine de bu zaten
# klon-ilk-boot anı, parolanın imajdan başka yere sızdığı bir noktası yok.
FIRST_BOOT_SCRIPT_TEMPLATE = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tiha-first-boot-bios — klonda BIOS yönetici parolasını ayarlar / temizler.

Akış (m14_bios_password.py içinde anlatılır):
  1. Sentinel varsa veya MAC imzası yoksa çık.
  2. MAC değişmemişse (kaynak tahta) çık.
  3. Gömülü eta-112 argümanlarıyla çağrılır (set veya clear);
     başarılıysa sentinel yaz, servisi disable et, parolayı içeren
     bu scripti sil.
  4. Başarısızlıkta sentinel yazma — sonraki boot'ta tekrar dene.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SAVED_MAC_FILE = Path("@@SAVED_MAC_FILE@@")
SENTINEL_FILE  = Path("@@SENTINEL_FILE@@")
ETA_112_PATH   = "@@ETA_112_PATH@@"
SERVICE_NAME   = "@@SERVICE_NAME@@"
SELF_PATH      = Path("@@SELF_PATH@@")
ETA_112_ARGV   = json.loads('@@ETA_112_ARGV_JSON@@')  # noqa: S105 — root-only chmod 700
LOG_TAG        = "tiha-first-boot-bios"


def note(msg: str) -> None:
    try:
        subprocess.run(["logger", "-t", LOG_TAG, "--", msg], check=False)
    except Exception:
        pass
    print(f"[{LOG_TAG}] {msg}", file=sys.stderr)


def primary_mac() -> str | None:
    iface = ""
    try:
        out = subprocess.run(
            ["ip", "-o", "-4", "route", "show", "to", "default"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        parts = out.split()
        for i, tok in enumerate(parts):
            if tok == "dev" and i + 1 < len(parts):
                iface = parts[i + 1]
                break
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    if not iface or not Path(f"/sys/class/net/{iface}").is_dir():
        net_root = Path("/sys/class/net")
        if net_root.is_dir():
            for entry in sorted(net_root.iterdir()):
                if entry.name == "lo":
                    continue
                if (entry / "device").is_symlink():
                    iface = entry.name
                    break
    if not iface:
        return None
    addr_path = Path(f"/sys/class/net/{iface}/address")
    if not addr_path.is_file():
        return None
    try:
        return addr_path.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return None


def disable_self() -> None:
    try:
        subprocess.run(["systemctl", "disable", SERVICE_NAME], check=False)
    except FileNotFoundError:
        pass


def main() -> int:
    if SENTINEL_FILE.is_file():
        note("Sentinel mevcut, atlanıyor.")
        disable_self()
        return 0
    if not SAVED_MAC_FILE.is_file():
        note("MAC imzası yok; klon değil veya adım uygulanmamış.")
        return 0
    cur = primary_mac()
    if not cur:
        note("Birincil arayüzün MAC'i tespit edilemedi; atlanıyor.")
        return 0
    try:
        saved = SAVED_MAC_FILE.read_text(encoding="utf-8").strip().lower()
    except OSError as exc:
        note(f"İmza dosyası okunamadı: {exc}")
        return 0
    if cur == saved:
        note(f"MAC eşleşti ({cur}); kaynak tahta — BIOS parolasına dokunulmuyor.")
        return 0

    note(f"Klon tespit edildi ({saved} → {cur}); eta-112 çağrılıyor: {' '.join(ETA_112_ARGV)}")
    try:
        proc = subprocess.run(
            ["python3", ETA_112_PATH] + ETA_112_ARGV,
            capture_output=True, text=True, timeout=180, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        note(f"eta-112 çağrılamadı: {exc}")
        return 1

    raw = (proc.stdout or "").strip()
    data = {}
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if not data:
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {}

    if data.get("ok") and data.get("verified") is not False:
        note("BIOS yönetici parolası işlemi başarılı.")
        try:
            SENTINEL_FILE.parent.mkdir(parents=True, exist_ok=True)
            SENTINEL_FILE.write_text(f"done {cur}\\n", encoding="utf-8")
        except OSError as exc:
            note(f"Sentinel yazılamadı: {exc}")
        disable_self()
        # Parolayı içeren scripti sil — bir daha okunamaz.
        try:
            SELF_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        return 0

    err = data.get("error") or proc.stderr.strip() or "(belirtilmemiş)"
    note(f"BIOS işlemi başarısız: {err}. Sonraki açılışta tekrar denenir.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
'''


def _model_supports_protection_toggle(model_name: str | None) -> bool:
    """eta-112'nin ``--koruma`` bayrağını destekleyip desteklemediği.

    Faz 1 modelinde pwcheck NVRAM byte'ı henüz kalibre edilmediği için
    eta-112 ``set --koruma`` çağrısını reddeder. Bu modelde davranış
    örtüktür:
      * yalnız yönetici parolası → BIOS setup'a girerken sorar (setup)
      * yönetici + kullanıcı parolası → her açılışta sorar (always)

    Heuristik model adı kontrolüne dayanır; eta-112 ileride Faz 1 için
    pwcheck'i kalibre edip aynı modelde destek eklerse bu fonksiyonun
    yeniden değerlendirilmesi gerekir.
    """
    if not model_name:
        # Bilmiyorsak destekli varsay; eta-112 hata dönerse yine de
        # _eta_112_call sonucunu kullanıcıya gösteririz.
        return True
    return "Faz 1" not in model_name


def _build_eta_112_argv(
    supervisor_pw: str,
    protection_mode: str,
    *,
    supports_koruma: bool = True,
) -> list[str]:
    """Klon ilk açılışta (veya local set'te) eta-112'ye verilecek argv.

    * ``supervisor_pw`` boş → ``bios clear yonetici``. Hangi modelde
      olduğumuz fark etmez; parola olmadığında koruma byte'ı pratik
      olarak etkisizdir.
    * ``supervisor_pw`` dolu + ``supports_koruma=True`` → ``bios set
      --yonetici PW --koruma MODE``. Modern davranış (Faz 2 ve sonrası).
    * ``supervisor_pw`` dolu + ``supports_koruma=False`` (Faz 1) →
      protection_mode arka planda parola atama biçimine dönüşür:
        - "setup":   ``bios set --yonetici PW`` (kullanıcı boş → setup'ta sorar)
        - "always":  ``bios set --yonetici PW --kullanici PW`` (kullanıcı parolası
                     da var → her açılışta sorar)
      Kullanıcı parolasını yöneticininkinin aynısı yapıyoruz; aynısı
      olması teknik bir gereklilik değil ama kullanıcının ek bir
      parola hatırlamasına gerek bırakmıyor.
    """
    if not supervisor_pw:
        return ["bios", "clear", "yonetici", "--json"]
    if supports_koruma:
        return [
            "bios", "set",
            "--yonetici", supervisor_pw,
            "--koruma", protection_mode,
            "--json",
        ]
    # Faz 1 yolu
    argv = ["bios", "set", "--yonetici", supervisor_pw]
    if protection_mode == "always":
        argv += ["--kullanici", supervisor_pw]
    argv.append("--json")
    return argv


def _build_first_boot_script(
    supervisor_pw: str,
    protection_mode: str,
    *,
    supports_koruma: bool = True,
) -> str:
    argv = _build_eta_112_argv(
        supervisor_pw, protection_mode, supports_koruma=supports_koruma,
    )
    # JSON içine gömüleceği için tek-tırnak kaçışı şart değil (json.dumps
    # zaten çift-tırnak kullanıyor); şablonda da tek-tırnak içinde
    # parse edilecek.
    argv_json = json.dumps(argv, ensure_ascii=False)
    return (FIRST_BOOT_SCRIPT_TEMPLATE
            .replace("@@SAVED_MAC_FILE@@", str(IMAGED_MAC_FILE))
            .replace("@@SENTINEL_FILE@@", str(FIRST_BOOT_SENTINEL))
            .replace("@@ETA_112_PATH@@", str(BUNDLED_ETA_112))
            .replace("@@SERVICE_NAME@@", FIRST_BOOT_SERVICE_NAME)
            .replace("@@SELF_PATH@@", str(FIRST_BOOT_SCRIPT))
            .replace("@@ETA_112_ARGV_JSON@@", argv_json))


# --- systemd unit -----------------------------------------------------------

FIRST_BOOT_SERVICE_TEMPLATE = '''[Unit]
Description=TiHA — Klonun ilk açılışında BIOS yönetici parolasını ayarla
After=local-fs.target
ConditionPathExists=!@@SENTINEL@@

[Service]
Type=oneshot
ExecStart=@@SCRIPT@@
RemainAfterExit=no
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
'''


def _build_first_boot_service() -> str:
    return (FIRST_BOOT_SERVICE_TEMPLATE
            .replace("@@SENTINEL@@", str(FIRST_BOOT_SENTINEL))
            .replace("@@SCRIPT@@", str(FIRST_BOOT_SCRIPT)))


def _protection_label(protection: str | None) -> str:
    """eta-112'nin koruma değeri için ekranda gösterilen açıklama."""
    if protection == "always":
        return t("m14.protection.always")
    if protection == "setup":
        return t("m14.protection.setup")
    return t("m14.protection.unknown")


# --- Modül ------------------------------------------------------------------

class BiosPasswordModule(Module):
    id = "m14_bios_password"
    title = t("m14.title")
    sidebar_title = t("m14.sidebar_title")
    # Açıklayıcı bir etiket KOYMUYORUZ — link metni doğrudan URL olsun.
    # (UI rationale'ın hemen altına 🔗 + URL satırı çizer.)
    doc_url = "https://github.com/enseitankado/eta-112"
    doc_label = t("m14.doc_label")
    apply_hint = t("m14.apply_hint")
    popup_on_success = True

    # Donanım desteği bayrağı: None → henüz bilinmiyor, True/False → bilinen.
    # preview() ve action sırasında set edilir. ``params.py``'da bildirilen
    # ``visible_when: is_hardware_supported_cached`` formdaki parola alanı ve
    # "oku" düğmesini bu bayrağa göre gösterir/gizler.
    _supported_cache: bool | None = None

    def is_hardware_supported_cached(self) -> bool:
        """Form görünürlüğü için: bilinen donanım durumuna göre döner.

        ``visible_when`` form build sırasında preview()'tan ÖNCE
        çağrıldığı için cache None olursa burada sessizce
        ``query_bios_info(allow_download=False)`` deneriz. Cache'de
        eta-112 yoksa boş döner ve cache None kalır → True döneriz
        (alanlar görünür, kullanıcı "oku" düğmesine basıp tetikleyebilir).

        Üç durum:
          * True → destek var (göster)
          * False → destek YOK (gizle — kullanıcı isteği)
          * None → bilmiyoruz → True (göster)
        """
        if self._supported_cache is None:
            info = query_bios_info(allow_download=False)
            if info:
                self._supported_cache = bool(info.get("supported"))
        return self._supported_cache is not False
    rationale = t("m14.rationale")
    undo_supported = True

    def preview(self) -> str:
        info = query_bios_info()
        if not info:
            self._supported_cache = None
            return t("m14.preview.no_info")
        self._supported_cache = bool(info.get("supported"))
        if not info.get("supported"):
            board = info.get("board") or t("m14.preview.not_detected")
            bios = info.get("bios") or t("m14.preview.not_detected")
            return t("m14.preview.unsupported", board=board, bios=bios)
        model = info.get("model") or t("m14.preview.unnamed")
        chip = info.get("chip") or t("m14.preview.no_chip")
        pw_min = info.get("pw_min") or 4
        pw_max = info.get("pw_max") or 12
        supports_koruma = _model_supports_protection_toggle(info.get("model"))
        passwords = query_bios_passwords()
        current = passwords.get("supervisor") if passwords.get("ok") else None
        prot = passwords.get("protection") if passwords.get("ok") else None
        prot_label = _protection_label(prot)
        mac = _primary_mac() or t("m14.preview.not_detected")
        lines = [
            t(
                "m14.preview.supported",
                model=model, chip=chip, pw_min=pw_min, pw_max=pw_max,
                current=current or t("m14.preview.not_set"),
                protection=prot_label,
            ),
        ]
        if not supports_koruma:
            lines.append(t("m14.preview.faz1"))
        lines.append(t(
            "m14.preview.plan",
            mac=mac, mac_file=IMAGED_MAC_FILE, eta=BUNDLED_ETA_112,
            script=FIRST_BOOT_SCRIPT, unit=FIRST_BOOT_SERVICE,
            unit_name=FIRST_BOOT_SERVICE_NAME,
        ))
        return "\n".join(lines)

    def apply(
        self,
        params: dict | None = None,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        params = params or {}
        raw_pw = (params.get("supervisor_password") or "").strip()
        protection = _normalize_protection(params.get("protection_mode"))

        # 1) eta-112 erişimi
        if progress:
            progress(t("m14.common.preparing"))
        eta_script = _ensure_eta_112()
        if not eta_script:
            return ApplyResult(
                False,
                t("m14.apply.not_found"),
                details=t(
                    "m14.apply.not_found_details",
                    cache=ETA_112_CACHE_DIR, url=ETA_112_RAW_BASE,
                ),
            )

        # 2) Donanım desteği
        if progress:
            progress(t("m14.common.querying_model"))
        info, info_debug = _eta_112_call(["info"], allow_download=False)
        if not info:
            return ApplyResult(
                False,
                t("m14.apply.info_bad_json"),
                details=info_debug or t("m14.common.empty_output"),
            )
        if not info.get("supported"):
            self._supported_cache = False
            return ApplyResult(
                False,
                t("m14.apply.unsupported"),
                details=t(
                    "m14.apply.unsupported_details",
                    board=info.get("board"), bios=info.get("bios"),
                    error=info.get("error") or "-",
                ),
                not_applicable=True,
            )
        self._supported_cache = True
        pw_min = int(info.get("pw_min") or 4)
        pw_max = int(info.get("pw_max") or 12)
        supports_koruma = _model_supports_protection_toggle(info.get("model"))

        # 3) Parola doğrulama. Boş parola = "klonda parolayı temizle"
        # niyeti — error yerine clear yoluna gireriz.
        pw, err = _validate_password(raw_pw, pw_min, pw_max)
        if err:
            return ApplyResult(False, err)
        clear_mode = (pw == "")
        if progress:
            if clear_mode:
                progress(t("m14.apply.clear_note"))
            elif supports_koruma:
                progress(t("m14.apply.cmd_koruma", length=len(pw), protection=protection))
            else:
                # Faz 1 yolu: koruma byte'ı yok; davranış parola atamasıyla
                # belirleniyor. Kullanıcıya da bunu söyle.
                if protection == "always":
                    progress(t("m14.apply.cmd_faz1_always", length=len(pw)))
                else:
                    progress(t("m14.apply.cmd_faz1_setup", length=len(pw)))

        # 4) MAC imzası — m12 paylaşımlı (idempotent)
        mac = _primary_mac()
        if not mac:
            return ApplyResult(False, t("m14.apply.no_mac"))
        try:
            IMAGED_MAC_FILE.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ApplyResult(False, t("m14.apply.mac_dir_failed", error=exc))
        wrote_mac = False
        if not IMAGED_MAC_FILE.exists():
            try:
                IMAGED_MAC_FILE.write_text(mac + "\n", encoding="utf-8")
                wrote_mac = True
                if progress:
                    progress(t("m14.apply.mac_written", path=IMAGED_MAC_FILE, mac=mac))
            except OSError as exc:
                return ApplyResult(False, t("m14.apply.mac_write_failed", error=exc))
        else:
            if progress:
                progress(t("m14.apply.mac_exists", path=IMAGED_MAC_FILE))

        # 5) eta-112'yi sisteme kopyala
        try:
            BUNDLED_ETA_112.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(eta_script, BUNDLED_ETA_112)
            BUNDLED_ETA_112.chmod(0o755)
        except OSError as exc:
            return ApplyResult(False, t("m14.apply.copy_failed", error=exc))
        if progress:
            progress(t("m14.apply.copied", path=BUNDLED_ETA_112))

        # 6) First-boot script + service
        try:
            FIRST_BOOT_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
            FIRST_BOOT_SCRIPT.write_text(
                _build_first_boot_script(
                    pw, protection, supports_koruma=supports_koruma,
                ),
                encoding="utf-8",
            )
            os.chmod(FIRST_BOOT_SCRIPT, 0o700)  # parola düz metin → root only
            FIRST_BOOT_SERVICE.write_text(
                _build_first_boot_service(), encoding="utf-8",
            )
            FIRST_BOOT_SERVICE.chmod(0o644)
        except OSError as exc:
            return ApplyResult(False, t("m14.apply.files_failed", error=exc))
        if progress:
            progress(t("m14.apply.script_written", path=FIRST_BOOT_SCRIPT))
            progress(t("m14.apply.unit_written", path=FIRST_BOOT_SERVICE))

        # 7) Daemon reload + enable
        run_cmd(["systemctl", "daemon-reload"], check=False)
        en = run_cmd(
            ["systemctl", "enable", FIRST_BOOT_SERVICE_NAME], check=False,
        )
        if not en.ok:
            return ApplyResult(
                False,
                t("m14.apply.enable_failed", name=FIRST_BOOT_SERVICE_NAME),
                details=en.stderr,
                data={
                    "wrote_mac": wrote_mac, "model": info.get("model"),
                    "pw_len": len(pw),
                },
            )
        if progress:
            progress(t("m14.apply.enabled", name=FIRST_BOOT_SERVICE_NAME))

        # Önceki bir kurulumdan kalan sentinel varsa kaldır — bu apply
        # yeni parolanın klonda ayarlanmasını garantilemek istiyor.
        try:
            FIRST_BOOT_SENTINEL.unlink(missing_ok=True)
        except OSError:
            pass

        if clear_mode:
            action_summary = t("m14.apply.plan_clear")
        elif supports_koruma:
            action_summary = t("m14.apply.plan_set", length=len(pw), protection=protection)
        else:
            # Faz 1 örtük davranış açıklamasıyla
            mode_human = (
                t("m14.apply.faz1_always")
                if protection == "always"
                else t("m14.apply.faz1_setup")
            )
            action_summary = t("m14.apply.plan_faz1", length=len(pw), mode=mode_human)
        details = t(
            "m14.apply.details",
            model=info.get("model"), board=info.get("board"), bios=info.get("bios"),
            plan=action_summary, mac=mac, script=FIRST_BOOT_SCRIPT,
            service=FIRST_BOOT_SERVICE,
        )
        summary = (
            t("m14.apply.summary_clear") if clear_mode else t("m14.apply.summary_set")
        )
        return ApplyResult(
            True,
            summary,
            details=details,
            data={
                "wrote_mac": wrote_mac, "model": info.get("model"),
                "pw_len": len(pw), "clear_mode": clear_mode,
                "protection": protection,
            },
        )

    def read_current_supervisor_action(
        self,
        params: dict | None = None,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        """Form'daki "Mevcut yönetici parolasını oku" düğmesi.

        Gerekirse eta-112'yi GitHub'dan indirir (progress'le bildirir),
        ``bios info --json`` + ``bios read --json`` çağırır ve mevcut
        yönetici parolasını ``data['supervisor_password']`` olarak döner.
        UI tarafı bu değeri parola kutusuna doldurur.
        """
        if progress:
            progress(t("m14.common.preparing"))
        script = _ensure_eta_112(allow_download=True)
        if not script:
            return ApplyResult(
                False,
                t("m14.common.download_failed"),
                details=t("m14.common.download_failed_details", url=ETA_112_RAW_BASE),
            )
        if progress:
            progress(t("m14.read.ready", path=script))
            progress(t("m14.common.querying_model"))
        info, info_debug = _eta_112_call(["info"], allow_download=False)
        if not info:
            return ApplyResult(
                False,
                t("m14.common.info_bad_json"),
                details=info_debug or t("m14.common.empty_output"),
            )
        if not info.get("supported"):
            self._supported_cache = False
            board = info.get("board") or "(?)"
            bios = info.get("bios") or "(?)"
            return ApplyResult(
                False,
                t("m14.read.unsupported"),
                details=t("m14.read.unsupported_details", board=board, bios=bios),
            )
        self._supported_cache = True
        if progress:
            progress(t(
                "m14.read.model_line", model=info.get("model"),
                pw_min=info.get("pw_min"), pw_max=info.get("pw_max"),
            ))
            progress(t("m14.read.reading"))
        pwds, read_debug = _eta_112_call(["read"], allow_download=False, timeout=45)
        if not pwds:
            return ApplyResult(
                False,
                t("m14.read.read_bad_json"),
                details=read_debug or t("m14.common.empty_output"),
            )
        if not pwds.get("ok"):
            return ApplyResult(
                False,
                t("m14.read.read_failed", error=pwds.get("error") or t("m14.common.unknown_error")),
            )
        supervisor = pwds.get("supervisor") or ""
        protection = pwds.get("protection")
        prot_label = _protection_label(protection)
        if progress:
            progress(t("m14.read.supervisor", value=supervisor or t("m14.read.not_set")))
            progress(t("m14.read.protection", protection=prot_label))
        # data['protection_mode'] sadece eta-112 sözleşmesindeki iki
        # değerden biriyse döndürürüz — combo'yu doğru index'e çekmek
        # için UI tarafı buna bakar.
        data: dict = {"supervisor_password": supervisor}
        if protection in ("always", "setup"):
            data["protection_mode"] = protection
        return ApplyResult(
            True,
            (t("m14.read.current", value=supervisor)
             if supervisor else t("m14.read.none")),
            details=t("m14.read.details", model=info.get("model"), protection=prot_label),
            data=data,
        )

    def set_local_supervisor_action(
        self,
        params: dict | None = None,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        """Form'daki "Bu makinenin BIOS parolasını ayarla" düğmesi.

        Klon servisi KURMAZ; doğrudan üzerinde çalıştığımız tahtanın
        flash'ına yazar. Parola kutusu boşsa ``bios clear yonetici``
        çalıştırır — bu durumda BIOS yönetici parolası temizlenir ve
        koruma fiilen kalkar. Doluysa ``bios set --yonetici PW
        --koruma MODE`` çalıştırır.

        UYARI: bu işlem flash'ı doğrudan yazar — eta-112'nin kendi
        uyarısıyla "brick riski" taşır; geri alma yoktur.
        """
        params = params or {}
        raw_pw = (params.get("supervisor_password") or "").strip()
        protection = _normalize_protection(params.get("protection_mode"))

        if progress:
            progress(t("m14.common.preparing"))
        script = _ensure_eta_112(allow_download=True)
        if not script:
            return ApplyResult(
                False,
                t("m14.common.download_failed"),
                details=t("m14.common.download_failed_details", url=ETA_112_RAW_BASE),
            )

        if progress:
            progress(t("m14.common.querying_model"))
        info, info_debug = _eta_112_call(["info"], allow_download=False)
        if not info:
            return ApplyResult(
                False,
                t("m14.common.info_bad_json"),
                details=info_debug or t("m14.common.empty_output"),
            )
        if not info.get("supported"):
            self._supported_cache = False
            return ApplyResult(
                False,
                t("m14.local.unsupported"),
                details=t("m14.common.board_bios", board=info.get("board"), bios=info.get("bios")),
            )
        self._supported_cache = True
        pw_min = int(info.get("pw_min") or 4)
        pw_max = int(info.get("pw_max") or 12)
        supports_koruma = _model_supports_protection_toggle(info.get("model"))

        pw, err = _validate_password(raw_pw, pw_min, pw_max)
        if err:
            return ApplyResult(False, err)

        # _build_eta_112_argv 'bios' önekiyle başlayan TAM komut listesi
        # döner ("bios", ..., "--json"). _eta_112_call kendisi 'bios' ve
        # '--json' ekliyor; ortadaki gerçek alt-komut parçasını çıkartıp
        # ona veriyoruz.
        full_argv = _build_eta_112_argv(
            pw, protection, supports_koruma=supports_koruma,
        )
        call_args = full_argv[1:-1]  # 'bios' ve '--json' arasındaki kısım
        if pw == "":
            human = t("m14.local.clearing")
        elif supports_koruma:
            human = t("m14.local.setting", length=len(pw), protection=protection)
        else:
            mode_human = (
                t("m14.local.faz1_always")
                if protection == "always"
                else t("m14.local.faz1_setup")
            )
            human = t("m14.local.setting_faz1", mode=mode_human)
        if progress:
            progress(human)
            progress(t("m14.local.writing_flash"))

        # Yazma + doğrulama — eta-112 kendi içinde yapıyor. 180 sn yeter.
        result, debug = _eta_112_call(call_args, allow_download=False, timeout=180)
        if not result:
            return ApplyResult(
                False,
                t("m14.local.write_bad_json"),
                details=debug or t("m14.common.empty_output"),
            )
        if not result.get("ok"):
            return ApplyResult(
                False,
                t("m14.local.failed", error=result.get("error") or t("m14.common.unknown_error")),
            )
        verified = result.get("verified", True)
        changed = result.get("changed", True)
        if not changed:
            return ApplyResult(
                True,
                t("m14.local.unchanged"),
                data={"clear_mode": (pw == ""), "protection": protection},
            )
        if not verified:
            return ApplyResult(
                False,
                t("m14.local.not_verified"),
            )
        if pw == "":
            summary = t("m14.local.cleared")
        elif supports_koruma:
            summary = t("m14.local.set", protection=protection)
        else:
            faz1_human = (
                t("m14.apply.faz1_always")
                if protection == "always"
                else t("m14.apply.faz1_setup")
            )
            summary = t("m14.local.set_faz1", mode=faz1_human)
        return ApplyResult(
            True,
            summary,
            details=t("m14.local.details", argv=" ".join(full_argv)),
            data={"clear_mode": (pw == ""), "protection": protection},
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        data = data or {}
        notes: list[str] = []

        run_cmd(
            ["systemctl", "disable", FIRST_BOOT_SERVICE_NAME], check=False,
        )
        if _rm(FIRST_BOOT_SERVICE):
            notes.append(t("m14.undo.deleted", path=FIRST_BOOT_SERVICE))
        if _rm(FIRST_BOOT_SCRIPT):
            notes.append(t("m14.undo.script_deleted", path=FIRST_BOOT_SCRIPT))
        if _rm(BUNDLED_ETA_112):
            notes.append(t("m14.undo.deleted", path=BUNDLED_ETA_112))
        if _rm(FIRST_BOOT_SENTINEL):
            notes.append(t("m14.undo.deleted", path=FIRST_BOOT_SENTINEL))
        run_cmd(["systemctl", "daemon-reload"], check=False)

        # MAC imzasını YALNIZCA biz yazdıysak sil — m12 de paylaşıyor.
        if data.get("wrote_mac") and _rm(IMAGED_MAC_FILE):
            notes.append(t("m14.undo.mac_deleted", path=IMAGED_MAC_FILE))

        return ApplyResult(
            True,
            t("m14.undo.done"),
            details="\n".join(f"• {n}" for n in notes) if notes else None,
        )
