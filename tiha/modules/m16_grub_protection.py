"""Modül 16 — GRUB koruması.

Ne yapar?
GRUB önyükleme menüsünde ``e`` (düzenle) kipine girildiğinde ya da GRUB
shell'ine (``c`` tuşu) düşüldüğünde, adımda kullanıcıdan alınan
"GRUB yönetici parolası" sorulur. Kurtarma (recovery) girdisi menüde
kalır ama onu açmak da aynı kullanıcı adı ve parolayı ister. Boot akışı
bu parolayı sormaz; yalnız menüye elle müdahale eden görür.

Neden gerekir?
GRUB varsayılan olarak yerel klavye erişimi olan herkese kernel komut
satırını düzenleme hakkı verir. Buraya ``init=/bin/bash`` eklenirse
sistem doğrudan bir root shell açar; oradan da parola değiştirmek,
diski okumak, tahtayı kalıcı olarak ele geçirmek mümkündür. Bu adım
imaja tek bir PBKDF2-SHA512 hash gömerek tüm klonlarda o vektörü
kapatır.

Nasıl çalışır?
- Kullanıcının form alanına yazdığı parolanın PBKDF2-SHA512 hash'i
  ``/etc/grub.d/01_tiha_grub_password`` içine yazılır; superuser
  ``etapadmin`` tanımlanır. GRUB açılışta bu dosyayı okuduğu için ``e``
  kipi ve GRUB shell önce kullanıcı adını, sonra bu parolayı sorar.
  Ad formda salt okunur bir alanda gösterilir; GRUB superuser'ı
  sistemdeki ``etapadmin`` hesabından bağımsızdır, yalnız operatörün
  tanıdık bir ad yazması için aynı seçilmiştir.
- ``/etc/grub.d/10_linux`` içindeki ``CLASS="..."`` satırına
  ``--unrestricted`` bayrağı eklenir. Böylece menü girdisi seçilirken
  parola sorulmaz; sadece ``e`` düzenlemesi ve GRUB shell parolalıdır.
- Aynı dosyadaki ``linux_entry`` fonksiyonuna küçük bir blok eklenir:
  kurtarma girdileri ``--unrestricted`` almaz, yani kurtarma kipi (tek
  kullanıcı modu, root kabuğu) de GRUB kullanıcı adı ve parolası ister.
  "Gelişmiş seçenekler" alt menüsü zaten ``--unrestricted`` taşımadığı
  için parolalıdır. Alt menüdeki girdiler açılış varsayılanı olarak
  kaydedilmez: ETAP'ta ``GRUB_DEFAULT=saved`` olduğu için, kurtarmayla
  bir kez açılan tahta sonraki gözetimsiz açılışta parola ekranında
  beklerdi.
- ``/etc/default/grub``'a dokunulmaz. TiHA'nın eski sürümleri kurtarma
  girdisini ``GRUB_DISABLE_RECOVERY="true"`` ile kapatıyordu; bu adım o
  değişikliği geri alır.
- ``update-grub`` çalıştırılır ve üretilen menüde kurtarma girdilerinin
  gerçekten parolalı olduğu doğrulanır.

Aynı hash tüm klonlara aynen taşınır; operatör tek bir parolayı
hatırlar. Düz parola sistemde tutulmaz — yalnız hash gömülür.

Geri al
``01_tiha_grub_password`` silinir, ``10_linux``'teki iki yama metin
olarak sökülür (yedekten geri yüklemek, arada gelmiş bir GRUB paketi
güncellemesini de geri alırdı), ``update-grub`` yeniden çalıştırılır.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.utils import run_cmd

log = get_logger(__name__)

GRUB_LOCKDOWN_INCLUDE = Path("/etc/grub.d/01_tiha_grub_password")
GRUB_LINUX_SCRIPT = Path("/etc/grub.d/10_linux")
GRUB_DEFAULTS = Path("/etc/default/grub")
# Üretilmiş menü. Koruma başka bir araçla kurulmuş olabileceği için
# durumu yalnız kendi include dosyamıza bakarak değil buradan da
# okuruz (bkz. lockdown_active).
GRUB_GENERATED_CFG = Path("/boot/grub/grub.cfg")

# GRUB superuser adı. Sistemdeki etapadmin hesabıyla ilgisi yoktur
# (GRUB kendi kullanıcı listesini tutar); operatör açılış ekranında
# tanıdık bir ad yazsın diye aynı seçildi. Formda salt okunur alanda
# gösterilir — bkz. params.py "grub_username".
SUPERUSER = "etapadmin"
PBKDF2_ITERATIONS = 10000

# Debian 12 (Pardus ETAP 23) /etc/grub.d/10_linux'ün 34. satırında
# birebir bulunan sabit dize. Yamayı yalnız bu dize yakalanabildiğinde
# uygularız — GRUB paketi güncellenip biçim değiştiyse kullanıcıyı
# uyarır, sessiz kalmayız.
CLASS_NEEDLE = 'CLASS="--class gnu-linux --class gnu --class os"'
CLASS_REPLACE = 'CLASS="--class gnu-linux --class gnu --class os --unrestricted"'

# linux_entry fonksiyonunun girdi türünü okuduğu satır; kurtarma bloğu
# bunun hemen ardına eklenir. Satır bulunmazsa (GRUB paketi değişmiş)
# hiçbir değişiklik yapılmaz.
ENTRY_ANCHOR = '  type="$3"\n'
GUARD_BEGIN = "  # >>> TiHA m16: kurtarma girdisi GRUB parolasıyla korunur\n"
GUARD_END = "  # <<< TiHA m16\n"
# CLASS'taki --unrestricted bütün girdileri parolasız açar; kurtarma girdisi
# bundan muaf tutulur. Alt menüdeki girdiler (gelişmiş seçenekler ve
# kurtarma) açılış varsayılanı olarak kaydedilmez: GRUB_DEFAULT=saved iken
# bir kez kullanılan kurtarma girdisi sonraki gözetimsiz açılışta parola
# ekranında beklerdi. save_default_entry değişkeni çağrıldığı anda okur.
RECOVERY_GUARD = (
    GUARD_BEGIN
    + '  : "${tiha_class_base:=$CLASS}"\n'
    + '  : "${tiha_savedefault_base=${GRUB_SAVEDEFAULT-}}"\n'
    + '  if [ "x$type" = xrecovery ]; then\n'
    + '    CLASS="$(printf \'%s\' "$tiha_class_base" | sed \'s/ --unrestricted//\')"\n'
    + '  else\n'
    + '    CLASS="$tiha_class_base"\n'
    + '  fi\n'
    + '  if [ "x$type" = xsimple ]; then\n'
    + '    GRUB_SAVEDEFAULT="$tiha_savedefault_base"\n'
    + '  else\n'
    + '    GRUB_SAVEDEFAULT=false\n'
    + '  fi\n'
    + GUARD_END
)

GRUB_ENV = Path("/boot/grub/grubenv")
RECOVERY_OFF = 'GRUB_DISABLE_RECOVERY="true"'


def _pbkdf2_hash(password: str) -> str:
    """GRUB için ``grub.pbkdf2.sha512.<iter>.<salt>.<hash>`` biçiminde
    bir hash üretir. Yerel Python hashlib ile — ``grub-mkpasswd-pbkdf2``
    aracına ihtiyaç yok, hiçbir alt-süreç açılmaz, parola argv'ye
    düşmez."""
    salt = os.urandom(64)
    dk = hashlib.pbkdf2_hmac(
        "sha512", password.encode("utf-8"), salt,
        PBKDF2_ITERATIONS, dklen=64,
    )
    return (
        f"grub.pbkdf2.sha512.{PBKDF2_ITERATIONS}."
        f"{salt.hex().upper()}.{dk.hex().upper()}"
    )


def _include_content(pw_hash: str) -> str:
    """GRUB tarafından okunacak superuser + password bloğu."""
    return (
        "#!/bin/sh\n"
        "# TiHA m16 — GRUB düzenleme kilidi\n"
        "set -e\n"
        "cat << 'EOF'\n"
        f'set superusers="{SUPERUSER}"\n'
        f"password_pbkdf2 {SUPERUSER} {pw_hash}\n"
        "EOF\n"
    )


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _patch_linux(text: str) -> str:
    """10_linux'e iki yamayı (yoksa) uygular. Çağıran, iğne ve çapanın
    bulunduğunu önceden doğrulamış olmalı."""
    if CLASS_REPLACE not in text:
        text = text.replace(CLASS_NEEDLE, CLASS_REPLACE, 1)
    if GUARD_BEGIN not in text:
        text = text.replace(ENTRY_ANCHOR, ENTRY_ANCHOR + RECOVERY_GUARD, 1)
    return text


def _unpatch_linux(text: str) -> str:
    """İki yamayı metin olarak söker (eski sürümlerin tek yaması dahil)."""
    text = text.replace(CLASS_REPLACE, CLASS_NEEDLE, 1)
    start = text.find(GUARD_BEGIN)
    if start != -1:
        end = text.find(GUARD_END, start)
        if end != -1:
            text = text[:start] + text[end + len(GUARD_END):]
    return text


def _recovery_disabled(text: str) -> bool:
    return any(line.strip() == RECOVERY_OFF for line in text.splitlines())


def _enable_recovery(text: str) -> str:
    """Etkin ``GRUB_DISABLE_RECOVERY="true"`` satırını Debian varsayılanı
    olan yorum hâline çevirir."""
    return "\n".join(
        "#" + line.lstrip() if line.strip() == RECOVERY_OFF else line
        for line in text.splitlines()
    ) + "\n"


def _menu_audit(cfg: str) -> dict:
    """Üretilmiş grub.cfg'de kurtarma ve normal girdilerin kısıtını sayar."""
    rec_total = rec_open = normal_open = 0
    for line in cfg.splitlines():
        s = line.strip()
        if not s.startswith("menuentry "):
            continue
        is_rec = "recovery" in s
        unrestricted = "--unrestricted" in s
        if is_rec:
            rec_total += 1
            rec_open += unrestricted
        else:
            normal_open += unrestricted
    return {"recovery": rec_total, "recovery_open": rec_open,
            "normal_open": normal_open}


class GrubProtectionModule(Module):
    id = "m16_grub_protection"
    title = "GRUB koruması"
    sidebar_title = "GRUB koruması"
    streams_output = True
    apply_hint = (
        f"İşaretliyken: `e` düzenleme kipi ve GRUB shell, kullanıcı adı "
        f"`{SUPERUSER}` + buraya yazdığınız parolanın arkasına alınır; "
        f"kurtarma girdisi menüde kalır ama o da aynı parolayı ister. "
        f"İşaretsizken: varsa mevcut koruma kaldırılır, GRUB eski hâline döner."
    )
    rationale = (
        "GRUB açılış menüsünde ``e`` tuşu kernel komut satırının "
        "düzenlenmesine izin verir. Yerel klavye erişimi olan biri "
        "buraya ``init=/bin/bash`` yazarak tahtayı doğrudan root "
        "shell'e düşürebilir; root parolasını sıfırlamak, diski "
        "okumak, kalıcı arka kapı bırakmak buradan mümkündür. m02 "
        "(Açılışta parola temizliği) etkin olsa dahi o oturum "
        "içindeki hasar sınırsızdır. Aynı vektör kurtarma (recovery) "
        "girdisi üzerinden de açıktır: kurtarma kipi parola sormadan "
        "root kabuğu verir.\n\n"
        "Bu adım aşağıdaki 'GRUB yönetici parolası' alanına "
        "yazdığınız parolayı PBKDF2-SHA512 hash'i olarak GRUB'a "
        "tanımlar (kullanıcı adı: ``etapadmin``) ve normal menü "
        "girdilerini ``--unrestricted`` işaretler. Kurtarma girdisi "
        "menüde kalır ama bu işareti almaz: onu açmak da aynı kullanıcı "
        "adı ve parolayı ister. 'Gelişmiş seçenekler' alt menüsü de "
        "parolalıdır. Boot akışı bu parolayı sormaz; yalnız kullanıcı "
        "menüde ``e`` (düzenle) kipine girdiğinde, GRUB shell'ine "
        "(``c`` tuşu) düştüğünde ya da kurtarma/gelişmiş seçenekleri "
        "açtığında sorulur. Aynı hash bütün klonlara aynen taşınır; "
        "operatör tek bir parolayı hatırlar.\n\n"
        "Kutucuk adıma girildiğinde sistemin o anki durumunu gösterir: "
        "GRUB zaten korumalıysa işaretli gelir. İşaretini kaldırıp "
        "uygularsanız koruma kaldırılır ve GRUB yedeklerden eski hâline "
        "döndürülür. Koruma etkinken parola alanını boş bırakıp "
        "uygularsanız mevcut parola korunur. Düz parola sistemde "
        "tutulmaz; imaja yalnız hash gömülür."
    )

    def lockdown_active(self) -> bool:
        """GRUB şu an parola korumalı mı? Form kutucuğu bu değerle
        açılır (params.py → ``default_from``), böylece adıma girildiğinde
        kutucuk sistemin gerçek durumunu gösterir.

        Önce TiHA'nın kendi include dosyasına, sonra üretilmiş menüye
        bakarız — koruma başka bir araçla da kurulmuş olabilir."""
        if "set superusers=" in _read_text(GRUB_LOCKDOWN_INCLUDE):
            return True
        return "password_pbkdf2" in _read_text(GRUB_GENERATED_CFG)

    def superuser_name(self) -> str:
        """Formdaki salt okunur "GRUB kullanıcı adı" alanının değeri
        (params.py → ``default_from``). Tek kaynak burasıdır; ad
        değişirse form, önizleme ve GRUB'a yazılan dosya birlikte
        değişir."""
        return SUPERUSER

    def preview(self) -> str:
        include_exists = GRUB_LOCKDOWN_INCLUDE.exists()
        linux_txt = _read_text(GRUB_LINUX_SCRIPT)
        already_patched = CLASS_REPLACE in linux_txt
        needle_present = CLASS_NEEDLE in linux_txt
        guard_present = GUARD_BEGIN in linux_txt
        anchor_ok = linux_txt.count(ENTRY_ANCHOR) == 1
        recovery_disabled = _recovery_disabled(_read_text(GRUB_DEFAULTS))

        lines: list[str] = []
        lines.append(
            f"01_tiha_grub_password  : "
            f"{'zaten var, yeniden yazılacak' if include_exists else 'yazılacak'}"
        )
        lines.append(
            f"10_linux yaması        : "
            f"{'zaten uygulanmış' if already_patched else 'uygulanacak (--unrestricted eklenir)'}"
        )
        if (not already_patched and not needle_present) or (not guard_present and not anchor_ok):
            lines.append(
                "  UYARI: 10_linux içinde beklenen satırlar bulunamadı."
            )
            lines.append(
                "  GRUB paketi güncellenmiş olabilir; yama uygulanamaz."
            )
        lines.append(
            f"Kurtarma girdisi       : "
            f"{'parola korumalı' if guard_present else 'parolayla korunacak'}"
        )
        if recovery_disabled:
            lines.append(
                "  Şu an menüde yok (GRUB_DISABLE_RECOVERY=\"true\"); TiHA'nın eski "
                "sürümü kapatmış olabilir — uygulanınca yeniden açılır."
            )
        lines.append(f"Superuser (kullanıcı adı): {SUPERUSER}")
        lines.append(
            f"Hash algoritması       : PBKDF2-SHA512, "
            f"{PBKDF2_ITERATIONS} iterasyon, 64 bayt salt"
        )
        lines.append("")
        lines.append(
            "GRUB önce 'Enter username:' sorar, sonra parolayı ister:"
        )
        lines.append(f"  - 'e' kipine ('e' tuşu)  → kullanıcı adı: {SUPERUSER}, sonra parola")
        lines.append(f"  - GRUB shell'e ('c' tuşu) → kullanıcı adı: {SUPERUSER}, sonra parola")
        lines.append(f"  - Kurtarma girdisi       → kullanıcı adı: {SUPERUSER}, sonra parola")
        lines.append(f"  - Gelişmiş seçenekler    → kullanıcı adı: {SUPERUSER}, sonra parola")
        lines.append("  - Normal açılış          → parola sormaz")
        lines.append("")
        lines.append("Klonlarda:")
        lines.append("  - Aynı hash tüm klonlara aynen gömülür.")
        lines.append("  - Tek parola bütün filoda geçerli olur.")
        return "\n".join(lines)

    def apply(
        self,
        params: dict | None = None,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        params = params or {}
        enabled = str(
            params.get("enable_grub_lock", "False")
        ).lower() in ("true", "1", "yes", "on")

        def emit(line: str) -> None:
            if progress:
                progress(line)

        # Kutucuk işaretsiz uygulanırsa bu bir "kapat" talebidir: koruma
        # varsa kaldırılır, yoksa yapacak iş yoktur.
        if not enabled:
            if not self.lockdown_active():
                return ApplyResult(
                    True,
                    "GRUB koruması kutucuğu işaretli değil ve koruma zaten "
                    "yok — değişiklik yapılmadı.",
                )
            emit("Kutucuk işaretsiz — mevcut GRUB koruması kaldırılıyor…")
            result = self._remove_protection(emit)
            if result.success:
                result.data = {"removed": True}
            return result

        password = (params.get("grub_password") or "").strip()
        # Koruma zaten etkinken parola kutusu boş bırakıldıysa: operatör
        # yalnız adımdan geçiyordur, mevcut parolayı bozmayalım. Ama koruma
        # TiHA'nın eski sürümüyle kurulduysa (kurtarma girdisi kapalı, yeni
        # yama yok) parolaya dokunmadan yamayı yükseltiriz; yoksa tahtada
        # parolayı yeniden yazmadan kurtarma koruması hiç gelmezdi.
        keep_existing = False
        if not password and self.lockdown_active():
            linux_now = _read_text(GRUB_LINUX_SCRIPT)
            if GRUB_LOCKDOWN_INCLUDE.exists() and (
                GUARD_BEGIN not in linux_now
                or CLASS_REPLACE not in linux_now
                or _recovery_disabled(_read_text(GRUB_DEFAULTS))
            ):
                keep_existing = True
                emit("Koruma etkin, parola korunuyor; eski sürümün yaması "
                     "yükseltiliyor (kurtarma girdisi parolalı)…")
        if not password and self.lockdown_active() and not keep_existing:
            return ApplyResult(
                True,
                "GRUB koruması zaten etkin; parola alanı boş bırakıldığı için "
                "mevcut parola korundu.",
                details=(
                    f"Kullanıcı adı    : {SUPERUSER}\n"
                    "Parolayı değiştirmek isterseniz alana yeni parolayı\n"
                    "yazıp adımı tekrar uygulayın. Korumayı kaldırmak için\n"
                    "kutucuğun işaretini kaldırıp uygulayın."
                ),
            )
        if not password and not keep_existing:
            return ApplyResult(
                False,
                "GRUB yönetici parolası boş bırakılamaz.",
                details=(
                    "Kutucuk işaretli olduğunda 'GRUB yönetici parolası'\n"
                    "alanına en az bir karakter yazılmalıdır. Bu parolanın\n"
                    "PBKDF2-SHA512 hash'i imaja gömülür ve 'e' kipi ile\n"
                    "GRUB shell'ini açmak isteyenden bu parola sorulur."
                ),
            )
        if not keep_existing and len(password) < 8:
            return ApplyResult(
                False,
                "GRUB yönetici parolası çok kısa (en az 8 karakter).",
                details=(
                    "GRUB parolası fiziksel klavye erişimi olan birine\n"
                    "karşı savunmadır; brute-force denemesi hızlı\n"
                    "yapılamasa da 8 karakterin altına düşülmemelidir."
                ),
            )

        state_dir = self.ensure_state_dir()
        linux_backup = state_dir / "10_linux.bak"
        defaults_backup = state_dir / "grub.defaults.bak"

        emit("Beklenen 10_linux satırı doğrulanıyor…")
        linux_txt = _read_text(GRUB_LINUX_SCRIPT)
        if not linux_txt:
            return ApplyResult(
                False,
                f"{GRUB_LINUX_SCRIPT} okunamadı; GRUB kurulumunu kontrol edin.",
            )
        already_patched = CLASS_REPLACE in linux_txt
        guard_present = GUARD_BEGIN in linux_txt
        if not guard_present and linux_txt.count(ENTRY_ANCHOR) != 1:
            return ApplyResult(
                False,
                "10_linux içinde kurtarma girdisini korumak için beklenen satır bulunamadı.",
                details=(
                    "TiHA, linux_entry fonksiyonundaki ``type=\"$3\"`` satırını\n"
                    "arar. GRUB paketi güncellenmiş olabilir. Güvenli tarafta\n"
                    "kalıp hiçbir değişiklik yapmadım."
                ),
            )
        if not already_patched and CLASS_NEEDLE not in linux_txt:
            return ApplyResult(
                False,
                "10_linux içinde beklenen CLASS satırı bulunamadı.",
                details=(
                    "TiHA yalnızca Debian 12 / Pardus ETAP 23'ün varsayılan\n"
                    f"CLASS satırını (``{CLASS_NEEDLE}``) tanır. GRUB paketi\n"
                    "güncellenmiş ya da başka bir araç dosyayı değiştirmiş\n"
                    "olabilir. Güvenli tarafta kalıp hiçbir değişiklik\n"
                    "yapmadım."
                ),
            )

        if not keep_existing:
            emit("Parola hash'i hesaplanıyor (PBKDF2-SHA512)…")
            pw_hash = _pbkdf2_hash(password)

        # Yedekler yalnız TiHA dokunmadan önceki hâli tutar: ilk uygulamada
        # alınır, sonraki uygulamalar üzerine yazmaz (eski sürüm her seferinde
        # alıyordu, ikinci uygulamadan sonra yedek yamalı dosya oluyordu).
        emit("10_linux ve /etc/default/grub yedekleri denetleniyor…")
        try:
            if not already_patched and not guard_present and not (
                linux_backup.exists() and CLASS_REPLACE not in _read_text(linux_backup)
            ):
                shutil.copy2(GRUB_LINUX_SCRIPT, linux_backup)
            if not defaults_backup.exists():
                shutil.copy2(GRUB_DEFAULTS, defaults_backup)
        except OSError as exc:
            return ApplyResult(False, f"Yedek alınamadı: {exc}")

        if not keep_existing:
            emit(f"{GRUB_LOCKDOWN_INCLUDE} yazılıyor…")
            try:
                GRUB_LOCKDOWN_INCLUDE.write_text(
                    _include_content(pw_hash), encoding="utf-8",
                )
                GRUB_LOCKDOWN_INCLUDE.chmod(0o755)
            except OSError as exc:
                return ApplyResult(False, f"Include yazılamadı: {exc}")

        patched = _patch_linux(linux_txt)
        if patched != linux_txt:
            emit("/etc/grub.d/10_linux yamalanıyor (normal girdiler parolasız, "
                 "kurtarma girdisi parolalı)…")
            try:
                GRUB_LINUX_SCRIPT.write_text(patched, encoding="utf-8")
            except OSError as exc:
                return ApplyResult(False, f"10_linux güncellenemedi: {exc}")

        recovery_restored = self._restore_recovery(emit)

        emit("update-grub çalıştırılıyor…")
        r = run_cmd(["update-grub"])
        if not r.ok:
            return ApplyResult(
                False,
                "update-grub başarısız oldu.",
                details=(r.stderr or r.stdout).strip(),
            )
        emit("update-grub tamamlandı.")

        saved_reset = self._reset_saved_submenu_entry(emit)

        audit = _menu_audit(_read_text(GRUB_GENERATED_CFG))
        if audit["recovery_open"]:
            return ApplyResult(
                False,
                "Kurtarma girdisi üretilen menüde hâlâ parolasız görünüyor.",
                details=(
                    f"{audit['recovery_open']} kurtarma girdisinde --unrestricted "
                    f"var ({GRUB_GENERATED_CFG}). 10_linux yaması beklendiği gibi "
                    "çalışmamış olabilir; geri alıp GRUB paketini denetleyin."
                ),
                data={
                    "linux_backup": str(linux_backup),
                    "defaults_backup": str(defaults_backup),
                },
            )
        if audit["recovery"]:
            rec_line = (
                f"Kurtarma girdisi : menüde ({audit['recovery']} adet), "
                f"{SUPERUSER} + parola ister"
            )
        else:
            rec_line = (
                "Kurtarma girdisi : menüde yok (GRUB_DISABLE_RECOVERY sizin\n"
                "                   ayarınızla kapalı); eklenirse parola ister"
            )

        return ApplyResult(
            True,
            (f"GRUB koruması güncellendi — mevcut parola korundu, kurtarma "
             f"girdisi parolalı; kullanıcı adı: {SUPERUSER}")
            if keep_existing else
            f"GRUB koruması etkinleştirildi — kullanıcı adı: {SUPERUSER}",
            details=(
                f"Kullanıcı adı    : {SUPERUSER}\n"
                f"Include          : {GRUB_LOCKDOWN_INCLUDE}\n"
                f"{rec_line}\n"
                + ("Kurtarma girdisi TiHA'nın eski sürümünün kapattığı yerden\n"
                   "yeniden açıldı.\n" if recovery_restored else "")
                + ("Kayıtlı açılış varsayılanı alt menüdeki bir girdiyi\n"
                   "gösteriyordu; sıfırlandı (gözetimsiz açılış parola\n"
                   "ekranında beklemesin).\n" if saved_reset else "")
                + f"10_linux yedeği  : {linux_backup}\n"
                f"grub yedeği      : {defaults_backup}\n\n"
                "Bir sonraki açılıştan itibaren `e` düzenleme kipi, GRUB\n"
                "komut satırı, kurtarma girdisi ve 'Gelişmiş seçenekler'\n"
                "önce 'Enter username:' sorar — buraya "
                f"{SUPERUSER} yazılır —\n"
                "ardından formda girdiğiniz parolayı ister. Normal açılış\n"
                "bu parolayı sormaz. Aynı hash bu tahtadan alınacak tüm\n"
                "klonlarda geçerlidir. Düz parola sistemde tutulmaz."
            ),
            data={
                "linux_backup": str(linux_backup),
                "defaults_backup": str(defaults_backup),
                "recovery_protected": True,
                "recovery_entries": audit["recovery"],
                "recovery_restored": recovery_restored,
                "saved_entry_reset": saved_reset,
            },
        )

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        data = data or {}
        # Bu kayıt zaten bir "kaldırma" işlemiyse geri alınacak koruma yok;
        # yeniden kurmak parola ister.
        if data.get("removed"):
            return ApplyResult(
                True,
                "Bu adım korumayı kaldırmıştı; geri alınacak değişiklik yok. "
                "Yeniden kurmak için kutucuğu işaretleyip parola girin.",
            )
        return self._remove_protection(data=data)

    # ------------------------------------------------------------------
    # Ortak kaldırma yolu — "kutucuk işaretsiz uygulandı" ve "geri al"
    # aynı işi yapar: include silinir, yedekler geri yüklenir (yoksa yama
    # elle sökülür), update-grub çalıştırılır.
    # ------------------------------------------------------------------

    def _remove_protection(
        self,
        emit: ProgressCallback | None = None,
        data: dict | None = None,
    ) -> ApplyResult:
        data = data or {}
        state_dir = self.state_dir
        linux_backup = Path(data.get("linux_backup") or state_dir / "10_linux.bak")
        defaults_backup = Path(
            data.get("defaults_backup") or state_dir / "grub.defaults.bak"
        )

        def say(line: str) -> None:
            if emit:
                emit(line)

        say(f"{GRUB_LOCKDOWN_INCLUDE} siliniyor…")
        try:
            GRUB_LOCKDOWN_INCLUDE.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Include silinemedi: %s", exc)

        # 10_linux yedekten geri yüklenmez: arada GRUB paketi güncellendiyse
        # yedek onu da geri alırdı. Yamalar metin olarak sökülür.
        say("10_linux'teki TiHA yamaları sökülüyor…")
        try:
            txt = _read_text(GRUB_LINUX_SCRIPT)
            clean = _unpatch_linux(txt)
            if clean != txt:
                GRUB_LINUX_SCRIPT.write_text(clean, encoding="utf-8")
        except OSError as exc:
            log.warning("10_linux geri döndürülemedi: %s", exc)

        self._restore_recovery(say, defaults_backup=defaults_backup,
                               linux_backup=linux_backup)

        say("update-grub çalıştırılıyor…")
        r = run_cmd(["update-grub"])
        if not r.ok:
            return ApplyResult(
                False,
                "update-grub başarısız oldu.",
                details=(r.stderr or r.stdout).strip(),
            )
        return ApplyResult(
            True,
            "GRUB koruması kaldırıldı; menü ve kurtarma girdisi eski hâline döndü.",
        )

    # ------------------------------------------------------------------
    # Kurtarma girdisi ve kayıtlı açılış varsayılanı
    # ------------------------------------------------------------------

    def _restore_recovery(
        self,
        emit: ProgressCallback | None = None,
        *,
        defaults_backup: Path | None = None,
        linux_backup: Path | None = None,
    ) -> bool:
        """TiHA'nın eski sürümünün kapattığı kurtarma girdisini geri açar.

        Eski sürüm ``GRUB_DISABLE_RECOVERY="true"`` yazıyordu. Özgün değer
        yedekten okunur; yedek güvenilmezse (eski sürüm her uygulamada
        yamalı dosyadan yedek alıyordu) Debian varsayılanına — kurtarma
        açık — dönülür. Yönetici kurtarmayı TiHA'dan önce kendisi
        kapatmışsa dokunulmaz. Değişiklik yaptıysa True döner.
        """
        state_dir = self.state_dir
        defaults_backup = defaults_backup or state_dir / "grub.defaults.bak"
        linux_backup = linux_backup or state_dir / "10_linux.bak"
        current = _read_text(GRUB_DEFAULTS)
        if not _recovery_disabled(current):
            return False
        backup_reliable = (
            defaults_backup.exists()
            and linux_backup.exists()
            and "--unrestricted" not in _read_text(linux_backup)
        )
        if backup_reliable and _recovery_disabled(_read_text(defaults_backup)):
            return False  # yöneticinin kendi tercihi
        if emit:
            emit("Kurtarma girdisi yeniden açılıyor (GRUB_DISABLE_RECOVERY yoruma alınıyor)…")
        try:
            GRUB_DEFAULTS.write_text(_enable_recovery(current), encoding="utf-8")
        except OSError as exc:
            log.warning("GRUB_DISABLE_RECOVERY geri alınamadı: %s", exc)
            return False
        return True

    def _reset_saved_submenu_entry(self, emit: ProgressCallback | None = None) -> bool:
        """Kayıtlı açılış varsayılanı alt menüyü gösteriyorsa sıfırlar.

        ETAP'ta GRUB_DEFAULT=saved: daha önce gelişmiş seçeneklerden ya da
        kurtarmadan açılmış bir tahtanın kayıtlı varsayılanı parolalı alt
        menüyü gösterir ve gözetimsiz açılış parola ekranında bekler.
        """
        env = _read_text(GRUB_ENV)
        saved = ""
        for line in env.splitlines():
            if line.startswith("saved_entry="):
                saved = line.split("=", 1)[1].strip()
        if not saved or (">" not in saved and "recovery" not in saved
                         and "advanced" not in saved):
            return False
        if emit:
            emit(f"Kayıtlı açılış varsayılanı alt menüyü gösteriyor ({saved}); sıfırlanıyor…")
        r = run_cmd(["grub-editenv", str(GRUB_ENV), "unset", "saved_entry"])
        return r.ok
