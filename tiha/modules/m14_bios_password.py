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
        return {}, "eta-112.py bulunamadı (env, cache ve indirme başarısız)"
    try:
        proc = subprocess.run(
            ["python3", str(script), "bios", *args, "--json"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log.warning("eta-112 %s çalıştırılamadı: %s", args, exc)
        return {}, f"çalıştırılamadı: {exc}"

    raw = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    # eta-112 destek dışı durumlarda exit=1 + JSON çıktı verir; biz exit
    # kodunu yutuyoruz, JSON içeriğine bakıyoruz.
    if not raw:
        return {}, (
            f"stdout boş geldi (exit={proc.returncode}).\n"
            f"stderr: {err[:500]}" if err else
            f"stdout ve stderr boş (exit={proc.returncode})"
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
        return {}, (
            f"JSON parse hatası (exit={proc.returncode}).\n"
            f"stdout: {raw[:500]}\nstderr: {err[:500]}"
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
        return "", "Parola yalnızca BÜYÜK harf (I hariç) ve rakamdan oluşmalı."
    if len(norm) < pw_min:
        return norm, f"Parola en az {pw_min} karakter olmalı (verilen: {len(norm)})."
    if len(norm) > pw_max:
        return norm, f"Parola en fazla {pw_max} karakter olabilir (verilen: {len(norm)})."
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


def _build_eta_112_argv(supervisor_pw: str, protection_mode: str) -> list[str]:
    """Klon ilk açılışta eta-112'ye verilecek tam argv listesi.

    * supervisor_pw boş ise → ``bios clear yonetici --json`` (parola
      temizlenir, BIOS koruması fiilen kalkar — parola olmadığında
      koruma byte'ının değeri pratik olarak etkili olmaz).
    * supervisor_pw dolu ise → ``bios set --yonetici PW --koruma MODE --json``.
      protection_mode "always" veya "setup" olmalıdır.
    """
    if not supervisor_pw:
        return ["bios", "clear", "yonetici", "--json"]
    return [
        "bios", "set",
        "--yonetici", supervisor_pw,
        "--koruma", protection_mode,
        "--json",
    ]


def _build_first_boot_script(supervisor_pw: str, protection_mode: str) -> str:
    argv = _build_eta_112_argv(supervisor_pw, protection_mode)
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


# --- Modül ------------------------------------------------------------------

class BiosPasswordModule(Module):
    id = "m14_bios_password"
    title = "BIOS yönetici parolası"
    sidebar_title = "BIOS parolası"
    # Açıklayıcı bir etiket KOYMUYORUZ — link metni doğrudan URL olsun.
    # (UI rationale'ın hemen altına 🔗 + URL satırı çizer.)
    doc_url = "https://github.com/enseitankado/eta-112"
    doc_label = "https://github.com/enseitankado/eta-112"
    apply_hint = (
        "Klonun ilk açılışında BIOS yönetici parolasını ayarlayacak "
        "tek-seferlik boot servisi imaja gömülür."
    )
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
    rationale = (
        "Bu adım, klon makinelerin ilk açılışında BIOS yönetici "
        "parolasını sizin belirlediğiniz değere ayarlayan tek-seferlik "
        "bir boot servisi imaja yerleştirir. Kaynak tahtada (yani "
        "şu an üzerinde çalıştığınız tahta) BIOS parolasına standart "
        "Uygula AKIŞINDA DOKUNULMAZ — değişiklik sadece klonda ilk "
        "açılışta yapılır, başarıyla tamamlandığında servis kendini "
        "disable eder ve parolayı içeren script silinir.\n\n"
        "Yalnız bu makinenin BIOS parolasını şimdi değiştirmek/temizlemek "
        "isterseniz “Bu makinenin BIOS parolasını ayarla” düğmesini "
        "kullanın — bu düğme klon servisi kurmaz; yalnızca buradaki "
        "donanıma yazar.\n\n"
        "Parola kutusu BOŞ uygulanırsa parola temizlenir (BIOS koruması "
        "fiilen kalkar) — bu hem klon servisinin hem de “şimdi uygula” "
        "düğmesinin davranışıdır.\n\n"
        "Koruma modu: yönetici parolası her açılışta mı yoksa yalnız "
        "BIOS setup'a girilirken mi sorulsun? Aşağıdan seçin; eta-112 "
        "bu ayarı doğrudan yazıyor.\n\n"
        "Bu adım YALNIZCA eta-112 tarafından kalibre edilmiş donanım "
        "modellerinde uygulanabilir (Faz 2 Vestel Gri vb.). Donanım "
        "desteklenmiyorsa form gizlenir.\n\n"
        "Mevcut parolaları manuel olarak okumak/değiştirmek veya yeni "
        "donanım sürümlerini kalibre etmek için aracın GitHub sayfasını "
        "kullanabilirsiniz."
    )
    undo_supported = True

    def preview(self) -> str:
        info = query_bios_info()
        if not info:
            self._supported_cache = None
            return (
                "Bu adımın hazırlık bilgisi henüz alınmadı.\n\n"
                "Aşağıdaki “Mevcut yönetici parolasını oku” düğmesine "
                "basarak eta-112'yi indirip donanımı sorgulayabilirsiniz; "
                "böylece form da donanım desteğine göre güncellenir."
            )
        self._supported_cache = bool(info.get("supported"))
        if not info.get("supported"):
            board = info.get("board") or "(tespit edilemedi)"
            bios = info.get("bios") or "(tespit edilemedi)"
            return (
                "❌ Bu donanım eta-112 tarafından DESTEKLENMİYOR.\n\n"
                f"  Anakart: {board}\n"
                f"  BIOS:    {bios}\n\n"
                "Bu adım uygulanmaz; uygula tıklansa bile servis kurulmaz. "
                "Diğer adımlara devam edebilirsiniz."
            )
        model = info.get("model") or "(adsız)"
        chip = info.get("chip") or "(yok)"
        pw_min = info.get("pw_min") or 4
        pw_max = info.get("pw_max") or 12
        passwords = query_bios_passwords()
        current = passwords.get("supervisor") if passwords.get("ok") else None
        prot = passwords.get("protection") if passwords.get("ok") else None
        prot_label = {
            "always": "her açılışta sorulur",
            "setup":  "yalnızca BIOS ayarlarına girilirken sorulur",
        }.get(prot, "(okunamadı)")
        mac = _primary_mac() or "(tespit edilemedi)"
        lines = [
            "✓ Donanım destekleniyor.",
            f"  Model:           {model}",
            f"  Flash çipi:      {chip}",
            f"  Parola uzunluğu: {pw_min}-{pw_max} karakter, BÜYÜK A-Z 0-9",
            "",
            f"  Mevcut yönetici parolası: {current or '(ayarlanmamış)'}",
            f"  Mevcut koruma modu:       {prot_label}",
            "",
            "Bu adımda yapılacaklar:",
            f"  • Kaynak MAC ({mac}) → {IMAGED_MAC_FILE}",
            f"  • eta-112 → {BUNDLED_ETA_112}",
            f"  • Boot scripti → {FIRST_BOOT_SCRIPT} (chmod 700, parola gömülü)",
            f"  • Systemd unit → {FIRST_BOOT_SERVICE}",
            f"  • systemctl enable {FIRST_BOOT_SERVICE_NAME}",
            "",
            "Klon makinedeki davranış (yalnızca ilk açılışta):",
            "  ┌── Sentinel mevcut ────────► çık (zaten yapıldı)",
            "  ├── İmza yok ──────────────► çık (klon değil/uygulanmamış)",
            "  ├── MAC eşit ──────────────► çık (kaynak tahta)",
            "  └── MAC farklı (klon)",
            "       └── eta-112 set --yonetici PASS",
            "            ✓ → sentinel yaz, servisi disable et, parola scriptini sil",
            "            ✗ → sentinel yazma; sonraki boot tekrar dene",
            "",
            "Geri al: boot scripti + servis + sentinel + paketlenmiş eta-112 silinir.",
        ]
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
            progress("eta-112 aracı hazırlanıyor...")
        eta_script = _ensure_eta_112()
        if not eta_script:
            return ApplyResult(
                False,
                "eta-112.py bulunamadı; indirilemedi de.",
                details=(
                    f"Beklenen yerler: $TIHA_ETA_112_DIR, {ETA_112_CACHE_DIR}\n"
                    f"İnternet bağlantısı veya {ETA_112_RAW_BASE} erişimi yok olabilir."
                ),
            )

        # 2) Donanım desteği
        if progress:
            progress("Donanım modeli sorgulanıyor (bios info --json)...")
        info, info_debug = _eta_112_call(["info"], allow_download=False)
        if not info:
            return ApplyResult(
                False,
                "eta-112 'info' komutu beklenen JSON çıktısını vermedi.",
                details=info_debug or "(boş çıktı)",
            )
        if not info.get("supported"):
            self._supported_cache = False
            return ApplyResult(
                False,
                "Bu donanım eta-112 tarafından desteklenmiyor; adım uygulanmaz.",
                details=(
                    f"Anakart: {info.get('board')}\n"
                    f"BIOS:    {info.get('bios')}\n"
                    f"Hata:    {info.get('error') or '-'}"
                ),
            )
        self._supported_cache = True
        pw_min = int(info.get("pw_min") or 4)
        pw_max = int(info.get("pw_max") or 12)

        # 3) Parola doğrulama. Boş parola = "klonda parolayı temizle"
        # niyeti — error yerine clear yoluna gireriz.
        pw, err = _validate_password(raw_pw, pw_min, pw_max)
        if err:
            return ApplyResult(False, err)
        clear_mode = (pw == "")
        if progress:
            if clear_mode:
                progress("Parola kutusu boş — klonda 'bios clear yonetici' "
                         "çalıştırılacak (BIOS koruması kalkar).")
            else:
                progress(
                    f"Klona gömülecek komut: bios set --yonetici <{len(pw)} kr> "
                    f"--koruma {protection}"
                )

        # 4) MAC imzası — m12 paylaşımlı (idempotent)
        mac = _primary_mac()
        if not mac:
            return ApplyResult(
                False,
                "Birincil ağ arayüzünün MAC adresi tespit edilemedi; "
                "klon tespiti için imza yazılamaz.",
            )
        try:
            IMAGED_MAC_FILE.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ApplyResult(False, f"İmza dizini oluşturulamadı: {exc}")
        wrote_mac = False
        if not IMAGED_MAC_FILE.exists():
            try:
                IMAGED_MAC_FILE.write_text(mac + "\n", encoding="utf-8")
                wrote_mac = True
                if progress:
                    progress(f"MAC imzası yazıldı: {IMAGED_MAC_FILE} = {mac}")
            except OSError as exc:
                return ApplyResult(False, f"İmza dosyası yazılamadı: {exc}")
        else:
            if progress:
                progress(f"MAC imzası zaten mevcut: {IMAGED_MAC_FILE} (paylaşımlı)")

        # 5) eta-112'yi sisteme kopyala
        try:
            BUNDLED_ETA_112.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(eta_script, BUNDLED_ETA_112)
            BUNDLED_ETA_112.chmod(0o755)
        except OSError as exc:
            return ApplyResult(False, f"eta-112 sisteme kopyalanamadı: {exc}")
        if progress:
            progress(f"eta-112 kopyalandı: {BUNDLED_ETA_112}")

        # 6) First-boot script + service
        try:
            FIRST_BOOT_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
            FIRST_BOOT_SCRIPT.write_text(
                _build_first_boot_script(pw, protection), encoding="utf-8",
            )
            os.chmod(FIRST_BOOT_SCRIPT, 0o700)  # parola düz metin → root only
            FIRST_BOOT_SERVICE.write_text(
                _build_first_boot_service(), encoding="utf-8",
            )
            FIRST_BOOT_SERVICE.chmod(0o644)
        except OSError as exc:
            return ApplyResult(False, f"Boot servis dosyaları yazılamadı: {exc}")
        if progress:
            progress(f"Boot scripti yazıldı: {FIRST_BOOT_SCRIPT} (chmod 700)")
            progress(f"Systemd unit yazıldı: {FIRST_BOOT_SERVICE}")

        # 7) Daemon reload + enable
        run_cmd(["systemctl", "daemon-reload"], check=False)
        en = run_cmd(
            ["systemctl", "enable", FIRST_BOOT_SERVICE_NAME], check=False,
        )
        if not en.ok:
            return ApplyResult(
                False,
                f"{FIRST_BOOT_SERVICE_NAME} enable edilemedi.",
                details=en.stderr,
                data={
                    "wrote_mac": wrote_mac, "model": info.get("model"),
                    "pw_len": len(pw),
                },
            )
        if progress:
            progress(f"{FIRST_BOOT_SERVICE_NAME} enable edildi.")

        # Önceki bir kurulumdan kalan sentinel varsa kaldır — bu apply
        # yeni parolanın klonda ayarlanmasını garantilemek istiyor.
        try:
            FIRST_BOOT_SENTINEL.unlink(missing_ok=True)
        except OSError:
            pass

        action_summary = (
            "klonda parola TEMİZLENECEK (bios clear yonetici)"
            if clear_mode else
            f"klonda parola AYARLANACAK ({len(pw)} kr, koruma={protection})"
        )
        details = (
            f"Model:    {info.get('model')}\n"
            f"Anakart:  {info.get('board')}  ·  BIOS: {info.get('bios')}\n"
            f"Plan:     {action_summary}\n"
            f"MAC:      {mac}\n"
            f"Script:   {FIRST_BOOT_SCRIPT} (chmod 700)\n"
            f"Servis:   {FIRST_BOOT_SERVICE}\n"
            "Klon ilk açılışta MAC değişikliğini görüp eta-112'yi çağıracak; "
            "başarıdan sonra servis disable olur ve parolayı içeren script silinir. "
            "Kaynak tahtanın BIOS'una bu akışta dokunulmadı."
        )
        summary = (
            "BIOS parola TEMİZLEME servisi imaja gömüldü; "
            "klonun ilk açılışında çalışacak."
            if clear_mode else
            "BIOS parola AYARLAMA servisi imaja gömüldü; "
            "klonun ilk açılışında çalışacak."
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
            progress("eta-112 aracı hazırlanıyor...")
        script = _ensure_eta_112(allow_download=True)
        if not script:
            return ApplyResult(
                False,
                "eta-112 aracı indirilemedi.",
                details=(
                    "İnternet bağlantısı ya da "
                    f"{ETA_112_RAW_BASE} erişimi yok olabilir."
                ),
            )
        if progress:
            progress(f"eta-112 hazır: {script}")
            progress("Donanım modeli sorgulanıyor (bios info --json)...")
        info, info_debug = _eta_112_call(["info"], allow_download=False)
        if not info:
            return ApplyResult(
                False,
                "eta-112 'info' beklenen JSON çıktısını vermedi.",
                details=info_debug or "(boş çıktı)",
            )
        if not info.get("supported"):
            self._supported_cache = False
            board = info.get("board") or "(?)"
            bios = info.get("bios") or "(?)"
            return ApplyResult(
                False,
                "Bu donanım eta-112 tarafından desteklenmiyor.",
                details=f"Anakart: {board}\nBIOS: {bios}",
            )
        self._supported_cache = True
        if progress:
            progress(f"Model: {info.get('model')}  ·  "
                     f"Parola: {info.get('pw_min')}-{info.get('pw_max')} A-Z 0-9")
            progress("Mevcut parolalar okunuyor (bios read --json)...")
        pwds, read_debug = _eta_112_call(["read"], allow_download=False, timeout=45)
        if not pwds:
            return ApplyResult(
                False,
                "eta-112 'read' beklenen JSON çıktısını vermedi.",
                details=read_debug or "(boş çıktı)",
            )
        if not pwds.get("ok"):
            return ApplyResult(
                False,
                f"BIOS parolaları okunamadı: {pwds.get('error') or 'bilinmeyen hata'}",
            )
        supervisor = pwds.get("supervisor") or ""
        protection = pwds.get("protection")
        prot_label = {
            "always": "her açılışta sorulur",
            "setup":  "yalnızca BIOS ayarlarına girilirken sorulur",
        }.get(protection, "(okunamadı)")
        if progress:
            progress(f"Yönetici parolası: {supervisor or '(ayarlı değil)'}")
            progress(f"Koruma modu:       {prot_label}")
        # data['protection_mode'] sadece eta-112 sözleşmesindeki iki
        # değerden biriyse döndürürüz — combo'yu doğru index'e çekmek
        # için UI tarafı buna bakar.
        data: dict = {"supervisor_password": supervisor}
        if protection in ("always", "setup"):
            data["protection_mode"] = protection
        return ApplyResult(
            True,
            (f"Mevcut yönetici parolası: {supervisor}"
             if supervisor else "BIOS'ta yönetici parolası ayarlı değil."),
            details=(
                f"Model: {info.get('model')}\n"
                f"Koruma modu: {prot_label}\n"
                "Bu değerler aşağıdaki form alanlarına otomatik yazıldı; "
                "değiştirmek isterseniz üzerine yeni değeri girin."
            ),
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
            progress("eta-112 aracı hazırlanıyor...")
        script = _ensure_eta_112(allow_download=True)
        if not script:
            return ApplyResult(
                False,
                "eta-112 aracı indirilemedi.",
                details=(
                    "İnternet bağlantısı ya da "
                    f"{ETA_112_RAW_BASE} erişimi yok olabilir."
                ),
            )

        if progress:
            progress("Donanım modeli sorgulanıyor (bios info --json)...")
        info, info_debug = _eta_112_call(["info"], allow_download=False)
        if not info:
            return ApplyResult(
                False,
                "eta-112 'info' beklenen JSON çıktısını vermedi.",
                details=info_debug or "(boş çıktı)",
            )
        if not info.get("supported"):
            self._supported_cache = False
            return ApplyResult(
                False,
                "Bu donanım eta-112 tarafından desteklenmiyor; işlem yapılmaz.",
                details=(
                    f"Anakart: {info.get('board')}\n"
                    f"BIOS:    {info.get('bios')}"
                ),
            )
        self._supported_cache = True
        pw_min = int(info.get("pw_min") or 4)
        pw_max = int(info.get("pw_max") or 12)

        pw, err = _validate_password(raw_pw, pw_min, pw_max)
        if err:
            return ApplyResult(False, err)

        if pw == "":
            argv = ["clear", "yonetici"]
            human = "BIOS yönetici parolası TEMİZLENİYOR..."
        else:
            argv = ["set", "--yonetici", pw, "--koruma", protection]
            human = (
                f"BIOS yönetici parolası AYARLANIYOR "
                f"(uzunluk {len(pw)}, koruma {protection})..."
            )
        if progress:
            progress(human)
            progress("(flash'a yazılıyor; bu birkaç saniye sürebilir)")

        # Yazma + doğrulama — eta-112 kendi içinde yapıyor. 180 sn yeter.
        result, debug = _eta_112_call(argv, allow_download=False, timeout=180)
        if not result:
            return ApplyResult(
                False,
                "eta-112 yazma komutu beklenen JSON çıktısını vermedi.",
                details=debug or "(boş çıktı)",
            )
        if not result.get("ok"):
            return ApplyResult(
                False,
                f"BIOS işlemi başarısız: "
                f"{result.get('error') or 'bilinmeyen hata'}",
            )
        verified = result.get("verified", True)
        changed = result.get("changed", True)
        if not changed:
            return ApplyResult(
                True,
                "Değişiklik yapılmadı — BIOS zaten istenen durumdaydı.",
                data={"clear_mode": (pw == ""), "protection": protection},
            )
        if not verified:
            return ApplyResult(
                False,
                "Yazıldı ama doğrulama tutmadı — BIOS okumayı tekrar "
                "denemek için sayfayı tazeleyin.",
            )
        if pw == "":
            summary = (
                "Bu makinenin BIOS yönetici parolası temizlendi. "
                "BIOS değişikliğinin tamamen etkili olması için "
                "makineyi YENİDEN BAŞLATIN."
            )
        else:
            summary = (
                f"Bu makinenin BIOS yönetici parolası ayarlandı "
                f"(koruma: {protection}). BIOS değişikliğinin etkili olması "
                "için makineyi YENİDEN BAŞLATIN."
            )
        return ApplyResult(
            True,
            summary,
            details=(
                "İşlem 'eta-112 bios "
                f"{' '.join(argv)}' ile gerçekleştirildi.\n"
                "Bu adımdaki Uygula akışı KURMADI — yalnız bu makineye yazıldı."
            ),
            data={"clear_mode": (pw == ""), "protection": protection},
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        data = data or {}
        notes: list[str] = []

        run_cmd(
            ["systemctl", "disable", FIRST_BOOT_SERVICE_NAME], check=False,
        )
        if _rm(FIRST_BOOT_SERVICE):
            notes.append(f"{FIRST_BOOT_SERVICE} silindi")
        if _rm(FIRST_BOOT_SCRIPT):
            notes.append(f"{FIRST_BOOT_SCRIPT} silindi (parola yok edildi)")
        if _rm(BUNDLED_ETA_112):
            notes.append(f"{BUNDLED_ETA_112} silindi")
        if _rm(FIRST_BOOT_SENTINEL):
            notes.append(f"{FIRST_BOOT_SENTINEL} silindi")
        run_cmd(["systemctl", "daemon-reload"], check=False)

        # MAC imzasını YALNIZCA biz yazdıysak sil — m12 de paylaşıyor.
        if data.get("wrote_mac") and _rm(IMAGED_MAC_FILE):
            notes.append(f"{IMAGED_MAC_FILE} silindi (yalnız m14 yazmıştı)")

        return ApplyResult(
            True,
            "BIOS parola servisi geri alındı.",
            details="\n".join(f"• {n}" for n in notes) if notes else None,
        )
