"""Modül 12 — Ahenk (LiderAhenk) ajan kimliğini sıfırla.

**Ne yapar?**
``ahenk`` servisini durdurur; ``/etc/ahenk/ahenk.conf`` içindeki
``uid``, ``parola`` (password) ve ``sunucu adresi`` (host) alanlarını;
``/etc/ahenk/config.d/messaging.conf`` içindeki Pulsar bağlantı
alanlarını (``pulsar_host``, ``pulsar_port``,
``tls_trust_certs_file_path``) boşaltır; yerel kayıt veritabanını
(``/etc/ahenk/ahenk.db``) siler; ahenk günlüğünü (``/var/log/ahenk.log``)
boşaltır. Paket kaldırılmaz, servis ``enabled`` bırakılır.

**Neden gerekir?**
İmajdan klonlanan tahtaların hepsi aynı ``uid``/``parola`` ile MEB
Pulsar broker'ına bağlanırsa Exclusive consumer çakışması ve
cross-board impersonation yaşanır. Bu adım, klon tahtaların ilk
açılışta kendi UUID'lerini üretip Lider sunucusuna kendi adlarıyla
yeniden kayıt olmasını sağlar.

**Güvenlik kemeri.** Yıkımdan önce ilgili dosyaların ham hâli
``STATE_DIR/m12_ahenk_backup/`` altına kopyalanır. İmaj alınmadan
vazgeçilirse Özet sayfasındaki **Geri al** düğmesi yedeği geri
yükler ve ``ahenk`` servisini yeniden başlatır; tahta Lider'e eski
kimliğiyle bağlanmaya devam eder.

**Önkoşul.** ahenk bu tahtada yüklü değilse adım atlanır (uygula
çağrıldığında sessizce "yapılacak iş yok" mesajıyla başarı döner).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.paths import STATE_DIR
from ..core.utils import run_cmd

log = get_logger(__name__)

AHENK_BACKUP_DIR = STATE_DIR / "m12_ahenk_backup"
AHENK_CONF = Path("/etc/ahenk/ahenk.conf")
AHENK_MSG_CONF = Path("/etc/ahenk/config.d/messaging.conf")
AHENK_DB = Path("/etc/ahenk/ahenk.db")
AHENK_LOG = Path("/var/log/ahenk.log")


def _truncate(path: Path) -> bool:
    try:
        if path.exists():
            path.write_text("", encoding="utf-8")
            return True
    except OSError as exc:
        log.warning("Boşaltılamadı %s: %s", path, exc)
    return False


def _rm(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError as exc:
        log.warning("Silinemedi %s: %s", path, exc)
        return False


class AhenkResetModule(Module):
    id = "m12_ahenk_reset"
    title = "Ahenk kimliği sıfırla"
    sidebar_title = "Ahenk kimliği"
    popup_on_success = True
    apply_hint = (
        "ahenk Pulsar bağlantısını keser; UID/parola/DB/günlük temizlenir, "
        "paket ve servis korunur."
    )
    rationale = (
        "Bir tahtanın imajı klonlanmadan önce LiderAhenk ajanının yerel "
        "kimliği sıfırlanmalıdır. Aksi hâlde tüm klonlar aynı UID/parola "
        "ile MEB Pulsar broker'ına bağlanır; aynı kimliğe sahip iki "
        "tahta Pulsar'da Exclusive consumer çakışması yaşar ve "
        "cross-board impersonation (bir tahta üzerinden gönderilen "
        "komutun başka bir tahtaya geçmesi) mümkün hâle gelir.\n\n"
        "Bu adım yalnızca o tahtaya özel verileri temizler: "
        "ahenk.conf'taki uid / parola / sunucu adresi alanları ile "
        "messaging.conf'taki Pulsar bağlantı alanları boşaltılır, "
        "yerel kayıt veritabanı (ahenk.db) silinir, ahenk günlüğü "
        "boşaltılır. Paket kaldırılmaz, servis devre dışı bırakılmaz; "
        "kurulum bütünlüğü korunur. Kopya tahta ilk açıldığında ahenk "
        "yeni UUID üretip Lider sunucusuna kendi adıyla yeniden kayıt "
        "olur — kimlik çakışması yaşanmaz.\n\n"
        "Güvenlik kemeri: yıkımdan önce dosyalar TiHA'nın özel "
        "klasörüne yedeklenir. İmajı almadan fikrinizi değiştirirseniz "
        "Özet sayfasındaki 'Geri al' düğmesi yedeği geri yükler ve "
        "ahenk servisini yeniden başlatır; tahta Lider'e eski "
        "kimliğiyle bağlanmaya devam eder."
    )
    undo_supported = True

    def preview(self) -> str:
        if not AHENK_CONF.exists():
            return (
                "ahenk bu tahtada yüklü değil — yapılacak bir iş yok.\n"
                "Uygula çalıştırıldığında adım atlanır."
            )

        msg_conf_present = AHENK_MSG_CONF.exists()
        db_present = AHENK_DB.exists()
        log_present = AHENK_LOG.exists()

        lines = [
            "Aşağıdaki ahenk dosyaları sıfırlanacak:",
            f"  • {AHENK_CONF} → uid / parola / sunucu adresi alanları boşaltılacak",
            (
                f"  • {AHENK_MSG_CONF} → Pulsar bağlantı alanları boşaltılacak"
                if msg_conf_present
                else f"  • {AHENK_MSG_CONF} → mevcut değil, atlanacak"
            ),
            (
                f"  • {AHENK_DB} → yerel kayıt veritabanı silinecek"
                if db_present
                else f"  • {AHENK_DB} → zaten yok"
            ),
            (
                f"  • {AHENK_LOG} → günlük dosyası boşaltılacak (eski uid/IP izleri)"
                if log_present
                else f"  • {AHENK_LOG} → mevcut değil"
            ),
            "",
            "Servis ahenk.service durdurulup tekrar 'enabled' bırakılır.",
            "Paket KALDIRILMAZ. Kopya tahta ilk açılışta yeni UUID üretip",
            "Lider'e kendi adıyla yeniden kayıt akışına girer.",
            "",
            "Kısmi geri alma:",
            f"  • Yıkımdan önce dosyalar {AHENK_BACKUP_DIR} altına yedeklenir.",
            "  • Özet sayfasındaki 'Geri al' yedeği geri yükler ve",
            "    servisi yeniden başlatır.",
        ]
        return "\n".join(lines)

    def apply(self, params: dict | None = None, progress: ProgressCallback | None = None) -> ApplyResult:
        if not AHENK_CONF.exists():
            return ApplyResult(
                True,
                "ahenk bu tahtada yüklü değil — adım atlandı.",
                data={"ahenk_backed_up": False, "skipped": True},
            )

        if progress:
            progress("ahenk servisi durduruluyor…")
        run_cmd(["systemctl", "stop", "ahenk.service"], check=False)

        # 1) Yedek al
        ahenk_backed_up = False
        if progress:
            progress(f"Yedek alınıyor: {AHENK_BACKUP_DIR}")
        try:
            AHENK_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(AHENK_CONF, AHENK_BACKUP_DIR / "ahenk.conf")
            if AHENK_MSG_CONF.exists():
                shutil.copy2(AHENK_MSG_CONF, AHENK_BACKUP_DIR / "messaging.conf")
            if AHENK_DB.exists():
                shutil.copy2(AHENK_DB, AHENK_BACKUP_DIR / "ahenk.db")
            ahenk_backed_up = True
        except OSError as exc:
            log.warning("ahenk kimlik yedeği alınamadı: %s", exc)

        # 2) ahenk.conf alanlarını boşalt
        if progress:
            progress("ahenk.conf: uid / parola / sunucu adresi boşaltılıyor")
        run_cmd(
            ["sed", "-i", "-E",
             r"s/^(uid|password|host)\s*=.*/\1 =/",
             str(AHENK_CONF)],
            check=False,
        )

        # 3) messaging.conf Pulsar alanları
        if AHENK_MSG_CONF.exists():
            if progress:
                progress("messaging.conf: Pulsar bağlantı alanları boşaltılıyor")
            run_cmd(
                ["sed", "-i", "-E",
                 r"s/^(pulsar_host|pulsar_port|tls_trust_certs_file_path)\s*=.*/\1 =/",
                 str(AHENK_MSG_CONF)],
                check=False,
            )

        # 4) Yerel kayıt veritabanı
        if AHENK_DB.exists():
            if progress:
                progress("ahenk.db siliniyor")
            _rm(AHENK_DB)

        # 5) Günlük dosyası
        if AHENK_LOG.exists():
            if progress:
                progress("ahenk.log boşaltılıyor")
            _truncate(AHENK_LOG)

        # 6) Servis enable bırak (sonraki açılışta otomatik başlasın)
        run_cmd(["systemctl", "enable", "ahenk.service"], check=False)

        details = (
            f"Yedek: {AHENK_BACKUP_DIR}\n"
            "Sıfırlanan alanlar: uid, parola, sunucu adresi, "
            "pulsar_host, pulsar_port, tls_trust_certs_file_path\n"
            "Silinen: ahenk.db (yerel kayıt veritabanı)\n"
            "Boşaltılan: ahenk.log\n"
            "Servis ahenk.service: enabled (otomatik başlatma açık)"
        )
        return ApplyResult(
            True,
            "ahenk kimliği sıfırlandı; kopya tahta ilk açılışta Lider'e "
            "yeniden kayıt akışına girer.",
            details=details,
            data={"ahenk_backed_up": ahenk_backed_up, "skipped": False},
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        data = data or {}
        if data.get("skipped"):
            return ApplyResult(
                False,
                "Bu uygulamada ahenk yüklü olmadığı için sıfırlama "
                "atlandı — geri yüklenecek bir şey yok.",
            )
        if not data.get("ahenk_backed_up") or not AHENK_BACKUP_DIR.is_dir():
            return ApplyResult(
                False,
                "Geri alınabilecek bir kayıt yok — yedek bulunamadı.",
            )

        conf_bak = AHENK_BACKUP_DIR / "ahenk.conf"
        msg_bak = AHENK_BACKUP_DIR / "messaging.conf"
        db_bak = AHENK_BACKUP_DIR / "ahenk.db"
        if not conf_bak.exists():
            return ApplyResult(False, "ahenk.conf yedeği bulunamadı.")

        restored: list[str] = []
        run_cmd(["systemctl", "stop", "ahenk.service"], check=False)
        try:
            AHENK_CONF.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(conf_bak, AHENK_CONF)
            restored.append("ahenk.conf")
            if msg_bak.exists():
                AHENK_MSG_CONF.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(msg_bak, AHENK_MSG_CONF)
                restored.append("messaging.conf")
            if db_bak.exists():
                shutil.copy2(db_bak, AHENK_DB)
                restored.append("ahenk.db")
        except OSError as exc:
            return ApplyResult(False, f"ahenk yedeği geri yüklenemedi: {exc}")

        run_cmd(["systemctl", "start", "ahenk.service"], check=False)
        shutil.rmtree(AHENK_BACKUP_DIR, ignore_errors=True)

        return ApplyResult(
            True,
            "ahenk kimliği geri yüklendi (" + ", ".join(restored) + ").",
            details=(
                "ahenk.service yeniden başlatıldı. Pulsar bağlantısı "
                "birkaç saniye içinde eski uid/parola ile yeniden kurulur."
            ),
        )
