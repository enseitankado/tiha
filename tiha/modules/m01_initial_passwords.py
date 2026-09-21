"""Modül 1 — Kullanıcı parolaları.

Ne yapar?
- Kullanıcının belirlediği yeni `root` ve `etapadmin` parolalarını uygular.
- İsteğe bağlı olarak `ogretmen` hesabı için parola belirler (alan boş
  bırakılırsa hesaba dokunulmaz).
- İsteğe bağlı olarak `ogretmen`/`ogrenci` ortak hesaplarını sistemden
  tamamen siler.
- İsteğe bağlı olarak "yedek hesap sayısı" kadar ``ogretmenN`` biçiminde
  boş yerel hesap açar (useradd + EBA standart cihaz grupları +
  parola kilitli). Eski kurulumlardan kalma ``ogretmen0N`` /
  ``ogretmen.N`` biçimindeki hesaplara dokunulmaz; o slot mevcut
  sayılır. PIN anahtarları için "Öğretmen PIN anahtarları" adımı
  ayrıca gerekir; o adım bu hesapları da otomatik yakalar.
- Parolalar SHA-512 hash olarak doğrudan `/etc/shadow` dosyasına yazılır.

Diğer hesaplara dokunulmaz; bu adım kimseyi kilitlemez.

Neden gerekir?
İmajdan onlarca tahtaya dağıtılacı bir kurulumda root ve etapadmin
parolalarının siz tarafından bilinçli olarak belirlenmiş olması gerekir;
varsayılan/önceden bilinen parolaların imajda kalmaması için.

Teknik not: Bu modül `chpasswd` yerine doğrudan `/etc/shadow` dosyasını
düzenler; böylece PAM politikaları ve AppArmor kısıtlamalarından etkilenmez.

GNOME anahtarlığı: Shadow'a doğrudan yazmanın bir yan etkisi vardır —
`pam_gnome_keyring.so` hiç çalışmadığı için kullanıcının anahtarlığı eski
parolayla şifreli kalır ve girişte asla geçilemeyen "parola artık giriş
anahtarlığınızla uyuşmuyor" diyaloğu çıkar. Bu modül, parolasını
değiştirdiği her kullanıcının bayatlamış anahtarlığını kenara alır;
böylece ilk girişte yenisi yeni parolayla otomatik oluşur. Ayrıntılı
gerekçe: :mod:`tiha.core.keyring`.

Geri al. Apply öncesi alınan `/etc/shadow` yedeği yerine yazılır;
böylece root, etapadmin ve ogretmen başta olmak üzere tüm hesapların
parolası `apply` öncesi haline döner. Bu apply çağrısında oluşturulan
yedek hesaplar (varsa) shadow restore'undan ÖNCE ``deluser --remove-home``
ile silinir; sistem tam olarak apply öncesi hâline döner. Kenara
alınan anahtarlıklar da yerlerine konur.
"""

from __future__ import annotations

import crypt
import json
import pwd
import subprocess
import time
from pathlib import Path

from ..core.keyring import (
    describe_keyrings,
    quarantine_stale_keyrings,
    restore_quarantined_keyrings,
)
from ..core.i18n import t
from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.privilege import invoking_username
from ..core.utils import backup_file, restore_file, run_cmd, user_exists

log = get_logger(__name__)

SHADOW = Path("/etc/shadow")

# Sistem kullanıcıları (silinebilir)
REMOVABLE_USERS = {"ogrenci", "ogretmen"}

# Kenara alınan anahtarlıkların modül durum dizini içindeki yeri.
KEYRING_BACKUP_SUBDIR = "keyrings"

# Önizlemede anahtarlık durumu gösterilecek hesaplar — bu modülün
# parolasını değiştirebildiği hesapların tamamı.
KEYRING_PREVIEW_USERS = ("root", "etapadmin", "ogretmen")


def _generate_password_hash(password: str) -> str:
    """SHA-512 ile parola hash'i üretir."""
    # SHA-512 salt ile hash üret
    salt = crypt.mksalt(crypt.METHOD_SHA512)
    return crypt.crypt(password, salt)


def _set_password_direct(user: str, password: str) -> tuple[bool, str]:
    """Parolayı doğrudan /etc/shadow dosyasına hash olarak yazar."""
    try:
        if not user_exists(user):
            return False, f"Kullanıcı bulunamadı: {user}"

        log.info("Kullanıcı '%s' için parola hash'i doğrudan shadow'a yazılıyor", user)

        # Hash üret
        password_hash = _generate_password_hash(password)
        log.debug("Hash üretildi, uzunluk: %d karakter", len(password_hash))

        # Shadow dosyasını oku
        try:
            with open(SHADOW, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except OSError as exc:
            return False, f"Shadow dosyası okunamadı: {exc}"

        # İlgili kullanıcının satırını bul ve güncelle
        user_found = False
        updated_lines = []

        for line in lines:
            if line.startswith(f"{user}:"):
                # Shadow format: username:password:lastchange:min:max:warn:inactive:expire:reserved
                fields = line.strip().split(':')
                if len(fields) >= 2:
                    # Parola hash'ini güncelle
                    fields[1] = password_hash
                    # Parola değişim tarihini güncelle (epoch günleri)
                    fields[2] = str(int(time.time() // 86400))  # bugünkü gün sayısı
                    updated_line = ':'.join(fields) + '\n'
                    updated_lines.append(updated_line)
                    user_found = True
                    log.debug("Kullanıcı '%s' shadow satırı güncellendi", user)
                else:
                    log.error("Shadow satırı bozuk format: %s", line.strip())
                    return False, f"Shadow dosyasında bozuk format: {user}"
            else:
                updated_lines.append(line)

        if not user_found:
            return False, f"Shadow dosyasında kullanıcı bulunamadı: {user}"

        # Güncellenmiş içeriği geri yaz
        try:
            with open(SHADOW, 'w', encoding='utf-8') as f:
                f.writelines(updated_lines)
            log.info("Kullanıcı '%s' parolası başarıyla shadow'a yazıldı", user)
            return True, "Başarılı"
        except OSError as exc:
            return False, f"Shadow dosyası yazılamadı: {exc}"

    except Exception as exc:
        log.error("Parola ayarlama sırasında beklenmeyen hata: %s", exc)
        return False, f"Beklenmeyen hata: {exc}"


def _set_password(user: str, password: str) -> bool:
    """Parolayı doğrudan shadow dosyasına hash olarak yazar."""
    success, message = _set_password_direct(user, password)
    if not success:
        log.error("'%s' için parola atanamadı: %s", user, message)
    else:
        log.info("'%s' için parola başarıyla atandı", user)
    return success


def _unlock_user(user: str) -> bool:
    result = run_cmd(["usermod", "-U", user])
    return result.ok


def backup_user_info(username: str, state_dir: Path) -> bool:
    """Kullanıcı bilgilerini (UID, home dir, vb.) yedekler."""
    if not user_exists(username):
        return False

    try:
        user_info = pwd.getpwnam(username)
        backup_data = {
            "username": user_info.pw_name,
            "uid": user_info.pw_uid,
            "gid": user_info.pw_gid,
            "home_dir": user_info.pw_dir,
            "shell": user_info.pw_shell,
            "gecos": user_info.pw_gecos,
        }

        backup_file_path = state_dir / f"{username}_backup.json"
        backup_file_path.write_text(json.dumps(backup_data, indent=2), encoding="utf-8")
        return True
    except Exception as exc:
        log.error("Kullanıcı bilgileri yedeklenemedi %s: %s", username, exc)
        return False


def remove_user_with_backup(username: str, state_dir: Path) -> bool:
    """Kullanıcıyı bilgilerini yedekleyerek siler."""
    if not user_exists(username):
        return False

    # Önce yedekle
    if not backup_user_info(username, state_dir):
        log.error("Kullanıcı yedeklenemedi, silme işlemi iptal edildi: %s", username)
        return False

    # Sil
    result = run_cmd(["deluser", "--remove-home", username])
    if result.ok:
        log.info("Kullanıcı başarıyla silindi: %s", username)
        return True
    else:
        log.error("Kullanıcı silinemedi %s: %s", username, result.stderr)
        return False


def restore_user(username: str, state_dir: Path) -> bool:
    """Yedekten kullanıcıyı geri yükler."""
    backup_file_path = state_dir / f"{username}_backup.json"
    if not backup_file_path.exists():
        return False

    try:
        backup_data = json.loads(backup_file_path.read_text(encoding="utf-8"))

        # Kullanıcı oluştur
        result = run_cmd([
            "useradd",
            "--uid", str(backup_data["uid"]),
            "--gid", str(backup_data["gid"]),
            "--home-dir", backup_data["home_dir"],
            "--shell", backup_data["shell"],
            "--comment", backup_data["gecos"],
            "--create-home",
            username
        ])

        if result.ok:
            log.info("Kullanıcı geri yüklendi: %s", username)
            return True
        else:
            log.error("Kullanıcı geri yüklenemedi %s: %s", username, result.stderr)
            return False
    except Exception as exc:
        log.error("Kullanıcı geri yükleme hatası %s: %s", username, exc)
        return False


def get_removable_user_status() -> dict[str, bool]:
    """Silinebilir kullanıcıların mevcut durumunu döndürür."""
    return {user: user_exists(user) for user in REMOVABLE_USERS}



# --- Branş hesapları -------------------------------------------------------
# Seçici değeri JSON: {"school_type", "branches": [seçili etiketler],
# "unselected": [listede görünüp işareti kaldırılmış etiketler]}. İşareti
# kaldırılan ve sistemde hesabı olan branş, uygulamada silinir.


def parse_branch_selection(raw: str | None) -> dict:
    """Seçici değerini çözer; bozuk/boş değerde boş sözlük."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def branch_accounts_to_delete(raw: str | None) -> list[str]:
    """İşareti kaldırılmış ve sistemde hesabı olan branşların hesap adları."""
    from ..core.meb_data import branch_to_username

    data = parse_branch_selection(raw)
    names: list[str] = []
    for label in data.get("unselected") or []:
        uname = branch_to_username(label)
        if uname and uname not in names and user_exists(uname):
            names.append(uname)
    return names


def _ensure_home(username: str) -> bool:
    """Hesabın ev dizini yoksa /etc/skel'den oluşturur (0700, sahibi hesap)."""
    try:
        home = Path(pwd.getpwnam(username).pw_dir)
    except KeyError:
        return False
    if home.is_dir():
        return True
    r = run_cmd(["mkhomedir_helper", username], check=False)
    if not r.ok or not home.is_dir():
        log.warning("Ev dizini oluşturulamadı '%s': %s", username, r.stderr.strip())
        return False
    return True


def _in_group(username: str, group: str) -> bool:
    import grp as _grp
    try:
        return username in _grp.getgrnam(group).gr_mem
    except KeyError:
        return False


def _remove_otp_secrets(names: list[str]) -> list[str]:
    """Silinen hesapların PIN anahtarlarını otp-secrets.json'dan çıkarır."""
    from .m03_otp_secrets import (
        OTP_SECRETS_FILE, OTPSecretsModule, harden_secret_store,
        load_secrets, save_secrets,
    )

    secrets = load_secrets()
    gone = [n for n in names if n in secrets]
    if not gone:
        return []
    state = OTPSecretsModule().ensure_state_dir()
    harden_secret_store(state)
    backup_file(OTP_SECRETS_FILE, state)
    for n in gone:
        del secrets[n]
    save_secrets(secrets)
    harden_secret_store(state)
    return gone

class InitialPasswordsModule(Module):
    id = "m01_initial_passwords"
    title = t("m01.title")
    sidebar_title = t("m01.sidebar_title")
    apply_hint = t("m01.apply_hint")
    rationale = t("m01.rationale")
    extra_links = [
        {"label": t("m01.extra_links.users_admin"), "action": "launch_users_admin_gui_action"},
    ]

    # ------------------------------------------------------------------
    # "Fazladan Hesapları Sil" — m03'ün aynı özelliğinin buradaki aynası.
    # Aynı sisteme dokunuyoruz: aksiyonu tek yerde tutmak için m03'ün
    # uygulamasına delege ediyoruz. Böylece iki adımdaki düğme davranışı
    # ayrışmıyor; label da aynı "X hesap, Y anahtar" formatını gösterir.
    # ------------------------------------------------------------------

    def _otp_delegate(self):
        """Lazy singleton — m03 metotları için gerçek OTP modülü örneği."""
        inst = getattr(self, "_otp_delegate_inst", None)
        if inst is None:
            from .m03_otp_secrets import OTPSecretsModule
            inst = OTPSecretsModule()
            self._otp_delegate_inst = inst
        return inst

    def label_remove_extra_users(self) -> str:
        return self._otp_delegate().label_remove_extra_users()

    def can_remove_extra_users(self) -> bool:
        return self._otp_delegate().can_remove_extra_users()

    def remove_extra_users_action(
        self, params: dict | None = None, progress=None,
    ) -> ApplyResult:
        return self._otp_delegate().remove_extra_users_action(
            params=params, progress=progress,
        )

    def pre_apply_check(self, params: dict) -> list[dict]:
        """Apply öncesi kullanıcı onayı gereken senaryoları döner.

        Şu an: yedek hesap sayısı 0 ayarlanmışken sistemde ogretmenN
        biçiminde mevcut hesap varsa, hepsinin (ev dizinleri ile
        birlikte) silinmesi için tek bir onay ister. Kullanıcı Evet
        derse params'a ``delete_all_reserve=True`` eklenir; apply
        bunu görünce silme akışını çalıştırır.
        """
        confirmations: list[dict] = []

        # İşareti kaldırılan branşların hesapları ev dizini ve PIN
        # anahtarıyla silinecek: geri alınamaz, önce sor.
        doomed = branch_accounts_to_delete(params.get("branch_accounts"))
        if doomed:
            confirmations.append({
                "title": t("m01.pre_apply.branch_delete_title", count=len(doomed)),
                "message": t(
                    "m01.pre_apply.branch_delete_message",
                    users=", ".join(doomed),
                ),
                "params": {},
            })

        try:
            reserve = int(params.get("reserve_count", 0) or 0)
        except (TypeError, ValueError):
            reserve = 0
        if reserve != 0:
            return confirmations
        from .m03_otp_secrets import list_reserve_accounts
        existing = list_reserve_accounts()
        if not existing:
            return confirmations
        preview = ", ".join(existing[:6])
        if len(existing) > 6:
            preview += f", … (+{len(existing) - 6})"
        confirmations.append({
            "title": t(
                "m01.pre_apply.reserve_purge_title", count=len(existing),
            ),
            "message": t(
                "m01.pre_apply.reserve_purge_message",
                count=len(existing), users=preview,
            ),
            "params": {"delete_all_reserve": "True"},
        })
        return confirmations

    def current_branch_accounts(self) -> str:
        """Branş seçicinin açılış değeri: sistemde zaten açılmış branş
        hesapları ve ait oldukları okul türü (JSON). Okul türü birden çok
        türe uyuyorsa son uygulamadaki seçim tercih edilir."""
        import json as _json
        from ..core.meb_data import detect_existing_selection

        preferred = ""
        try:
            from ..core.report_log import REPORT_PARAMS_KEY
            from ..core.undo import Journal

            last = Journal().last_applied(self.id)
            raw = ((last.data if last else {}) or {}).get(
                REPORT_PARAMS_KEY, {},
            ).get("branch_accounts") or ""
            if raw:
                preferred = _json.loads(raw).get("school_type") or ""
        except Exception as exc:  # defter yok/bozuk: yalnız hesaplara bak
            log.debug("Önceki branş seçimi okunamadı: %s", exc)
        selection = detect_existing_selection(preferred)
        if not selection:
            return ""
        return _json.dumps(selection, ensure_ascii=False)

    def suggested_reserve_count(self) -> int:
        """"Yedek hesap sayısı" kutusunun açılışta görüneceği değer.

        Sistemde ogretmen1 … ogretmen10 duruyorsa kutu 10 gelir; adım
        yeniden uygulandığında yönetici farkında olmadan 11. hesabı
        açmaz, mevcut hesaplar da (m03'ün PIN anahtarlarıyla birlikte)
        korunur. Eski kurulumlardaki 'ogretmen01' biçimi de aynı
        sayaça girer.
        """
        # Lazy import: m03 heavy imports (pyotp, requests). m01 açılışta
        # yavaşlamasın diye burada import ediyoruz.
        from .m03_otp_secrets import count_reserve_accounts
        return count_reserve_accounts()

    def preview(self) -> str:
        user_status = get_removable_user_status()

        # m03 içindeki hazır yardımcıyı kullan; sistemde ogretmen01 …
        # ogretmenNN varsa kaçıncıya kadar gittiğini söyle.
        from .m03_otp_secrets import count_reserve_accounts
        existing_reserve = count_reserve_accounts()

        lines: list[str] = []
        lines.append(t("m01.preview.common_accounts"))
        for user, exists in user_status.items():
            status = t("m01.preview.account_present") if exists else t("m01.preview.account_absent")
            lines.append(t("m01.preview.account_line", user=user, status=status))
        if not any(user_status.values()):
            lines.append(t("m01.preview.common_none"))

        lines.append("")
        lines.append(t("m01.preview.reserve_title"))
        if existing_reserve > 0:
            lines.append(t("m01.preview.reserve_existing", count=existing_reserve))
            lines.append(t("m01.preview.reserve_existing_hint"))
        else:
            lines.append(t("m01.preview.reserve_none"))
            lines.append(t("m01.preview.reserve_none_hint"))

        # Parola değişince bayatlayacak anahtarlıklar — kullanıcının
        # "ne olacak?" sorusunu uygulamadan önce cevaplamak için.
        keyring_lines: list[str] = []
        for username in KEYRING_PREVIEW_USERS:
            if not user_exists(username):
                continue
            entries = describe_keyrings(username)
            if entries:
                keyring_lines.append(f"    - {username}: {', '.join(entries)}")

        if keyring_lines:
            lines.append("")
            lines.append(t("m01.preview.keyrings_title"))
            lines.extend(keyring_lines)
            lines.append("")
            lines.append(t("m01.preview.keyrings_note"))

        return "\n".join(lines)

    def apply(self, params: dict | None = None, progress=None) -> ApplyResult:
        params = params or {}
        root_pw = params.get("root_password", "").strip()
        admin_pw = params.get("admin_password", "").strip()
        teacher_pw = params.get("teacher_password", "").strip()

        # En az bir aksiyon: parola belirtilsin, yedek hesap sayısı
        # sıfırdan büyük olsun, en az bir branş seçili olsun ya da
        # "tüm yedek hesapları sil" onayı verilsin (pre_apply_check).
        try:
            _reserve_hint = int(params.get("reserve_count", 0) or 0)
        except (TypeError, ValueError):
            _reserve_hint = 0
        _branch_sel = parse_branch_selection(params.get("branch_accounts"))
        _branch_delete = branch_accounts_to_delete(params.get("branch_accounts"))
        _branch_hint = len(_branch_sel.get("branches") or []) + len(_branch_delete)
        _delete_all_reserve = str(
            params.get("delete_all_reserve", "False"),
        ).lower() in ("true", "1", "yes", "on")
        if (not root_pw and not admin_pw and not teacher_pw
                and _reserve_hint <= 0 and _branch_hint <= 0
                and not _delete_all_reserve):
            return ApplyResult(
                success=False,
                summary=t("m01.apply.need_action"),
            )

        # Parola uzunluk kontrolü (sadece dolu olanlar için)
        if root_pw and len(root_pw) < 8:
            return ApplyResult(False, t("m01.apply.too_short", user="root"))
        if admin_pw and len(admin_pw) < 8:
            return ApplyResult(False, t("m01.apply.too_short", user="etapadmin"))
        if teacher_pw and len(teacher_pw) < 8:
            return ApplyResult(False, t("m01.apply.too_short", user="ogretmen"))

        # Yaygın parola listesi kontrolü — SecLists top10k. UI'da canlı
        # uyarı gösterilmiş olsa da apply zamanında sunucu tarafı
        # doğrulama olarak reddediyoruz.
        from ..core.password_strength import is_common
        for lbl, pw in (
            ("root", root_pw), ("etapadmin", admin_pw), ("ogretmen", teacher_pw),
        ):
            if pw and is_common(pw):
                return ApplyResult(
                    False,
                    t("m01.apply.too_common", user=lbl),
                )

        state = self.ensure_state_dir()
        # Önce /etc/shadow yedeği al — undo için tek başına yeterli.
        backup_file(SHADOW, state)

        # Sistem kullanıcılarını silme seçeneği
        remove_system_users = params.get("remove_system_users", False)
        removed_users = []

        if remove_system_users:
            user_status = get_removable_user_status()
            for username, exists in user_status.items():
                if exists:
                    if remove_user_with_backup(username, state):
                        removed_users.append(username)
                        log.info("Sistem kullanıcısı silindi: %s", username)

        # Sadece doldurulmuş parolaları ata
        results = {}
        ok_root = True
        ok_admin = True
        ok_teacher = True

        if root_pw:
            ok_root = _set_password("root", root_pw)
            results["root"] = ok_root

        if admin_pw:
            ok_admin = _set_password("etapadmin", admin_pw)
            results["etapadmin"] = ok_admin

        if teacher_pw and user_exists("ogretmen"):
            ok_teacher = _set_password("ogretmen", teacher_pw)
            results["ogretmen"] = ok_teacher
            # ogretmen hesabı kilitliyse parola ile giriş yapılabilmesi için aç
            _unlock_user("ogretmen")

        # Parolası gerçekten değişen her hesabın anahtarlığı artık
        # açılamaz durumdadır. Eski parolayı bilmediğimiz için yeniden
        # şifrelemek mümkün değil; dosyayı kenara alırız, böylece
        # pam_gnome_keyring bir sonraki girişte yenisini yeni parolayla
        # kurar. Silmiyoruz: undo eski shadow'u geri koyduğunda bu
        # anahtarlıklar tekrar geçerli hâle gelir.
        keyring_backup_root = state / KEYRING_BACKUP_SUBDIR
        keyrings_moved: dict[str, list[str]] = {}
        for username, ok in results.items():
            if not ok:
                continue
            moved = quarantine_stale_keyrings(username, keyring_backup_root)
            if moved:
                keyrings_moved[username] = moved

        # ---- Yedek hesap toplu silme ------------------------------------
        # Kullanıcı sayıyı 0'a çektiyse ve sistemde ogretmenN varsa
        # pre_apply_check bir onay diyaloğu göstermiş ve params'a
        # ``delete_all_reserve=True`` eklemiştir. Bu bloğa geldiğimizde
        # onay verilmiş demektir; hesapları ev dizinleriyle beraber sileriz.
        purged_reserve: list[str] = []
        if _delete_all_reserve:
            from .m03_otp_secrets import list_reserve_accounts
            existing_reserve = list_reserve_accounts()
            if existing_reserve and progress:
                progress(t(
                    "m01.apply.reserve_purge_start",
                    count=len(existing_reserve),
                ))
            for username in existing_reserve:
                r = run_cmd(["deluser", "--remove-home", username])
                if r.ok:
                    purged_reserve.append(username)
                    if progress:
                        progress(f"  ✗ {username}")
                else:
                    log.warning(
                        "Yedek hesap silinemedi '%s': %s",
                        username, r.stderr.strip(),
                    )
                    if progress:
                        progress(t(
                            "m01.apply.reserve_purge_failed", user=username,
                        ))

        # Silinen yedek hesapların PIN anahtarları imaja ölü sır olarak
        # gitmesin (branş silmedeki gibi).
        purged_reserve_secrets = _remove_otp_secrets(purged_reserve) if purged_reserve else []

        # ---- Yedek hesaplar ----------------------------------------------
        # Adım eskiden "Öğretmen PIN anahtarları" (m03) altındaydı.
        # Buraya taşındı ki hesap yaratma ile PIN üretme akışları
        # birbirinden ayrık ve yeniden çalıştırılabilir olsun. PIN üretimi
        # hâlâ m03'te; m03 apply anında sistemde bulduğu ogretmen01..NN
        # hesaplarını PIN listesine kendisi ekler.
        try:
            reserve = int(params.get("reserve_count", 0) or 0)
        except (TypeError, ValueError):
            reserve = 0

        created_reserve: list[str] = []
        skipped_reserve: list[str] = []
        if reserve > 0:
            # m03'ün user-account primitivelerini kullan; kod kopyalamak
            # yerine cross-modül import (döngüsel değil).
            from .m03_otp_secrets import create_user
            if progress:
                progress(t("m01.apply.reserve_preparing", count=reserve))
            for i in range(1, reserve + 1):
                # Yeni konvansiyon: sıfır önekli değil, sade 'ogretmenN'
                # ("Ogretmen N" display'i eta-otp-cli tarafından da aynı
                # sonuca normalize edilir). Eski kurulumlardan kalma
                # 'ogretmen0N', 'ogretmen.N' veya 'ogretmen.0N' hesapları
                # da varsa mevcut kabul et — o slotu yeniden açma.
                username = f"ogretmen{i}"
                full_name = f"Ogretmen {i}"
                legacy_variants = (
                    f"ogretmen{i:02d}",
                    f"ogretmen.{i}",
                    f"ogretmen.{i:02d}",
                )
                existing_variant: str | None = None
                if user_exists(username):
                    existing_variant = username
                else:
                    for v in legacy_variants:
                        if user_exists(v):
                            existing_variant = v
                            break
                if existing_variant:
                    skipped_reserve.append(existing_variant)
                    if progress:
                        progress(f"  ≈ {existing_variant}")
                    continue
                if create_user(username, full_name=full_name):
                    created_reserve.append(username)
                    if progress:
                        progress(f"  + {username}")
                else:
                    if progress:
                        progress(t("m01.apply.reserve_create_failed", user=username))
            # Yedek hesaplar da öğretmen hesabıdır: ogretmenler grubuna
            # girer ('@ogretmenler' ortak PIN'i yalnız üyelerde çalışır).
            from .m03_otp_secrets import ensure_ogretmenler_group, OGRETMENLER_GROUP
            if ensure_ogretmenler_group():
                for uname in created_reserve + skipped_reserve:
                    if _in_group(uname, OGRETMENLER_GROUP):
                        continue
                    r = run_cmd(["usermod", "-a", "-G", OGRETMENLER_GROUP, uname])
                    if not r.ok:
                        log.warning("'%s' ogretmenler grubuna eklenemedi: %s",
                                    uname, r.stderr.strip())
                        if progress:
                            progress(t("m01.apply.reserve_group_failed", user=uname))

        # ---- Branş hesapları ---------------------------------------------
        # Kullanıcı UI'dan bir okul türü ve o okulda ders okutan
        # branşların bir alt kümesini seçtiyse, her branş için ayrı
        # bir yerel hesap açılır. Kullanıcı adı MEB verisinden türetilir
        # (Türkçe → ASCII, boşluk → _). Görünen ad (GECOS) branşın
        # orijinal etiketidir — greeter ekranında böyle listelenir.
        # Seçili her hesabın ev dizini olur ve ogretmenler grubuna girer
        # (grup PIN'i ve öğretmen ayrıcalıkları). Listede görünüp işareti
        # kaldırılan branşın hesabı varsa ev dizini ve PIN anahtarıyla
        # silinir (pre_apply_check onay alır).
        created_branches: list[str] = []
        skipped_branches: list[str] = []
        grouped_branches: list[str] = []
        deleted_branches: list[str] = []
        failed_branch_deletes: list[str] = []
        bdata = _branch_sel
        if bdata.get("branches"):
            from .m03_otp_secrets import create_user, ensure_ogretmenler_group, OGRETMENLER_GROUP
            from ..core.meb_data import branch_to_username, school_label
            school_key = bdata.get("school_type") or ""
            branches = bdata.get("branches") or []
            if progress:
                progress(t(
                    "m01.apply.branches_preparing",
                    count=len(branches),
                    school=school_label(school_key),
                ))
            group_ok = ensure_ogretmenler_group()
            for label in branches:
                uname = branch_to_username(label)
                if not uname:
                    continue
                if user_exists(uname):
                    skipped_branches.append(uname)
                    if progress:
                        progress(f"  ≈ {uname}")
                elif create_user(uname, full_name=label):
                    created_branches.append(uname)
                    if progress:
                        progress(f"  + {uname}")
                else:
                    if progress:
                        progress(t(
                            "m01.apply.branch_create_failed", user=uname,
                        ))
                    continue
                _ensure_home(uname)
                if group_ok and not _in_group(uname, OGRETMENLER_GROUP):
                    r = run_cmd(["usermod", "-a", "-G", OGRETMENLER_GROUP, uname])
                    if r.ok:
                        grouped_branches.append(uname)
                    else:
                        log.warning("'%s' ogretmenler grubuna eklenemedi: %s",
                                    uname, r.stderr.strip())

        if _branch_delete:
            from .m03_otp_secrets import kill_user_processes
            if progress:
                progress(t("m01.apply.branches_deleting", count=len(_branch_delete)))
            for uname in _branch_delete:
                kill_user_processes(uname)
                r = run_cmd(["deluser", "--remove-home", uname])
                if r.ok:
                    deleted_branches.append(uname)
                    if progress:
                        progress(f"  ✗ {uname}")
                else:
                    failed_branch_deletes.append(uname)
                    log.warning("Branş hesabı silinemedi '%s': %s",
                                uname, r.stderr.strip())
                    if progress:
                        progress(t("m01.apply.branch_delete_failed", user=uname))
        deleted_branch_secrets = _remove_otp_secrets(deleted_branches) if deleted_branches else []

        if created_branches or deleted_branches:
            # Giriş ekranındaki kullanıcı listesi yeni hesap kümesini görsün.
            try:
                from .m03_otp_secrets import GREETER_SCRIPT_PATH, run_greeter_script_once
                if GREETER_SCRIPT_PATH.exists():
                    run_greeter_script_once()
            except Exception as exc:
                log.debug("Greeter tazelenemedi: %s", exc)

        details_lines = []
        if removed_users:
            details_lines.append(t("m01.apply.removed_common", users=", ".join(removed_users)))

        failed_users = []
        for user, success in results.items():
            details_lines.append(t("m01.apply.password_line", user=user,
                                    state=t("m01.apply.password_set") if success else t("m01.apply.password_not_set")))
            if not success:
                failed_users.append(user)

        if keyrings_moved:
            details_lines.append("")
            details_lines.append(t("m01.apply.keyrings_moved_title"))
            for username, names in keyrings_moved.items():
                details_lines.append(f"  - {username}: {', '.join(names)}")
            details_lines.append(t("m01.apply.keyrings_moved_why"))
            details_lines.append(t("m01.apply.keyrings_moved_next"))
            details_lines.append(t("m01.apply.keyrings_backup", path=keyring_backup_root))

        # Başarısız olan kullanıcılar için bilgi
        if failed_users:
            details_lines.append("")
            details_lines.append(t("m01.apply.failure_info"))

        # Yedek hesap özeti — detay satırlarına
        if created_reserve or skipped_reserve:
            details_lines.append("")
            details_lines.append(t("m01.apply.reserve_title"))
            if created_reserve:
                details_lines.append(t("m01.apply.reserve_created",
                                       count=len(created_reserve),
                                       users=", ".join(created_reserve)))
            if skipped_reserve:
                details_lines.append(t("m01.apply.reserve_skipped",
                                       count=len(skipped_reserve),
                                       users=", ".join(skipped_reserve)))
            details_lines.append(t("m01.apply.reserve_pin_note"))

        if purged_reserve:
            details_lines.append("")
            details_lines.append(t(
                "m01.apply.reserve_purge_title", count=len(purged_reserve),
            ))
            details_lines.append(t(
                "m01.apply.reserve_purge_users",
                users=", ".join(purged_reserve),
            ))
            if purged_reserve_secrets:
                details_lines.append(t(
                    "m01.apply.branches_secrets_removed",
                    count=len(purged_reserve_secrets),
                ))

        if created_branches or skipped_branches:
            details_lines.append("")
            details_lines.append(t("m01.apply.branches_title"))
            if created_branches:
                details_lines.append(t(
                    "m01.apply.branches_created",
                    count=len(created_branches),
                    users=", ".join(created_branches),
                ))
            if skipped_branches:
                details_lines.append(t(
                    "m01.apply.branches_skipped",
                    count=len(skipped_branches),
                    users=", ".join(skipped_branches),
                ))
            if grouped_branches:
                details_lines.append(t(
                    "m01.apply.branches_grouped",
                    count=len(grouped_branches),
                    users=", ".join(grouped_branches),
                ))
            details_lines.append(t("m01.apply.branches_pin_note"))

        if deleted_branches or failed_branch_deletes:
            details_lines.append("")
            if deleted_branches:
                details_lines.append(t(
                    "m01.apply.branches_deleted",
                    count=len(deleted_branches),
                    users=", ".join(deleted_branches),
                ))
            if deleted_branch_secrets:
                details_lines.append(t(
                    "m01.apply.branches_secrets_removed",
                    count=len(deleted_branch_secrets),
                ))
            if failed_branch_deletes:
                details_lines.append(t(
                    "m01.apply.branches_delete_failed",
                    users=", ".join(failed_branch_deletes),
                ))

        overall = (
            (all(results.values()) if results else False)
            or bool(removed_users)
            or bool(created_reserve)
            or bool(skipped_reserve)
            or bool(created_branches)
            or bool(skipped_branches)
            or bool(deleted_branches)
            or bool(purged_reserve)
        )

        summary_parts = []
        if removed_users:
            summary_parts.append(t("m01.apply.summary_removed", count=len(removed_users)))

        password_parts = []
        for user, success in results.items():
            if success:
                password_parts.append(user)
        if password_parts:
            summary_parts.append(t("m01.apply.summary_passwords", users="/".join(password_parts)))

        if created_reserve:
            summary_parts.append(t("m01.apply.summary_reserve", count=len(created_reserve)))

        if created_branches:
            summary_parts.append(t(
                "m01.apply.summary_branches", count=len(created_branches),
            ))

        if deleted_branches:
            summary_parts.append(t(
                "m01.apply.summary_deleted_branches", count=len(deleted_branches),
            ))

        if purged_reserve:
            summary_parts.append(t(
                "m01.apply.summary_purged_reserve",
                count=len(purged_reserve),
            ))

        keyring_count = sum(len(names) for names in keyrings_moved.values())
        if keyring_count:
            summary_parts.append(t("m01.apply.summary_keyrings", count=keyring_count))

        return ApplyResult(
            success=overall,
            summary="; ".join(summary_parts) + "." if overall and summary_parts
                    else t("m01.apply.partial_failure"),
            details="\n".join(details_lines),
            data={
                "removed_users": removed_users,
                "keyrings_moved": keyrings_moved,
                "created_reserve": created_reserve,
                # Özet raporu için (gizli değer içermez): hangi hesabın
                # parolası gerçekten atandı / atanamadı, hangi yedek hesap
                # zaten vardı, öğretmen parolası hesap yokluğundan mı
                # uygulanmadı.
                "passwords_set": [u for u, ok in results.items() if ok],
                "passwords_failed": [u for u, ok in results.items() if not ok],
                "skipped_reserve": skipped_reserve,
                "purged_reserve": purged_reserve,
                "reserve_requested": reserve,
                "created_branches": created_branches,
                "skipped_branches": skipped_branches,
                "grouped_branches": grouped_branches,
                "deleted_branches": deleted_branches,
                "teacher_skipped_no_account": bool(teacher_pw) and "ogretmen" not in results,
            },
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        state = self.state_dir
        backup = state / "shadow"
        if not backup.exists():
            return ApplyResult(False, t("m01.undo.no_backup"))

        restored_users = []
        removed_users = data.get("removed_users", [])
        deleted_reserve: list[str] = []

        # Bu apply'da yeni açılan yedek hesapları önce sil. Shadow'u
        # sonra yükleyeceğimiz için useradd'in bıraktığı '!' shadow
        # satırları da doğal olarak kaybolur; /etc/passwd ve ev
        # dizinleri de deluser ile temizlenir.
        for username in data.get("created_reserve") or []:
            if user_exists(username):
                r = run_cmd(["deluser", "--remove-home", username])
                if r.ok:
                    deleted_reserve.append(username)
                else:
                    log.warning(
                        "Yedek hesap silinemedi '%s': %s",
                        username, r.stderr.strip(),
                    )

        # Bu apply'da yeni açılan branş hesaplarını da geri al. Skipped
        # (zaten mevcut) hesaplara dokunulmaz — belki eskiden buradaymış
        # ya da başka bir yolla açılmış.
        deleted_branches: list[str] = []
        for username in data.get("created_branches") or []:
            if user_exists(username):
                r = run_cmd(["deluser", "--remove-home", username])
                if r.ok:
                    deleted_branches.append(username)
                else:
                    log.warning(
                        "Branş hesabı silinemedi '%s': %s",
                        username, r.stderr.strip(),
                    )

        # Silinen kullanıcıları geri yükle
        for username in removed_users:
            if restore_user(username, state):
                restored_users.append(username)

        try:
            restore_file(backup, SHADOW)
        except OSError as exc:
            log.error("Shadow geri yükleme hatası: %s", exc)
            return ApplyResult(False, t("m01.undo.restore_failed", error=exc))

        # Parolalar eski hâline döndüğü için kenara alınan anahtarlıklar
        # yeniden açılabilir durumda; yerlerine koyuyoruz. Shadow geri
        # yüklenemediyse buraya hiç gelinmez — eski parolayla şifreli bir
        # anahtarlığı yeni parolanın yanına bırakmak sorunu geri getirirdi.
        keyring_backup_root = state / KEYRING_BACKUP_SUBDIR
        restored_keyrings: dict[str, list[str]] = {}
        for username in data.get("keyrings_moved") or {}:
            names = restore_quarantined_keyrings(username, keyring_backup_root)
            if names:
                restored_keyrings[username] = names

        summary_parts = [t("m01.undo.shadow_restored")]
        if deleted_reserve:
            summary_parts.append(t("m01.undo.reserve_deleted",
                                   count=len(deleted_reserve),
                                   users=", ".join(deleted_reserve)))
        if deleted_branches:
            summary_parts.append(t("m01.undo.branches_deleted",
                                   count=len(deleted_branches),
                                   users=", ".join(deleted_branches)))
        if restored_users:
            summary_parts.append(t("m01.undo.users_restored",
                                   count=len(restored_users),
                                   users=", ".join(restored_users)))
        keyring_count = sum(len(names) for names in restored_keyrings.values())
        if keyring_count:
            summary_parts.append(t("m01.undo.keyrings_restored", count=keyring_count))
        return ApplyResult(True, "; ".join(summary_parts) + ".")

    # -----------------------------------------------------------------
    # Sistem Kullanıcı Yönetimi Fonksiyonları
    # -----------------------------------------------------------------

    def can_remove_system_users(self) -> bool:
        """Sistem kullanıcıları silme düğmesinin aktif olup olmayacağını belirler."""
        user_status = get_removable_user_status()
        return any(user_status.values())

    def remove_system_users_action(self, params: dict | None = None) -> ApplyResult:
        """Sistem kullanıcılarını (ogrenci, ogretmen) siler."""
        user_status = get_removable_user_status()
        existing_users = [user for user, exists in user_status.items() if exists]

        if not existing_users:
            return ApplyResult(
                False,
                t("m01.action.no_system_users"),
                details=t("m01.action.no_system_users_details")
            )

        state = self.ensure_state_dir()
        removed_users = []

        for username in existing_users:
            if remove_user_with_backup(username, state):
                removed_users.append(username)

        if removed_users:
            return ApplyResult(
                True,
                t("m01.action.system_users_removed", count=len(removed_users),
                  users=", ".join(removed_users)),
                details=t("m01.action.backed_up_removed"),
                data={"removed_users": removed_users}
            )
        else:
            return ApplyResult(
                False,
                t("m01.action.none_removed"),
                details=t("m01.action.see_log")
            )

    def remove_student_user_action(self, params: dict | None = None) -> ApplyResult:
        """Öğrenci kullanıcısını (ogrenci) siler."""
        if not user_exists("ogrenci"):
            return ApplyResult(
                False,
                t("m01.action.student_not_found"),
                details=t("m01.action.student_maybe_deleted")
            )

        state = self.ensure_state_dir()

        if remove_user_with_backup("ogrenci", state):
            return ApplyResult(
                True,
                t("m01.action.student_removed"),
                details=t("m01.action.student_backed_up"),
                data={"removed_users": ["ogrenci"]}
            )
        else:
            return ApplyResult(
                False,
                t("m01.action.student_remove_failed"),
                details=t("m01.action.see_log")
            )

    def launch_users_admin_gui_action(self, params: dict | None = None) -> ApplyResult:
        """Cinnamon 'Kullanıcılar ve Gruplar' uygulamasını kullanıcının X oturumunda açar."""
        binary = Path("/usr/bin/cinnamon-settings-users")
        if not binary.exists():
            return ApplyResult(
                False,
                t("m01.action.users_admin_missing"),
                details=t("m01.action.users_admin_missing_details", binary=binary),
            )

        user = invoking_username()
        try:
            subprocess.Popen(
                ["sudo", "-u", user, "env",
                 "DISPLAY=:0",
                 f"XAUTHORITY=/home/{user}/.Xauthority",
                 str(binary)],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return ApplyResult(
                False,
                t("m01.action.users_admin_failed"),
                details=str(exc),
            )

        return ApplyResult(
            True,
            t("m01.action.users_admin_opened", user=user),
            details=t("m01.action.users_admin_opened_details"),
        )
