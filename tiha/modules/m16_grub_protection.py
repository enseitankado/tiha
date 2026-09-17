"""Modül 16 — GRUB koruması.

Ne yapar?
GRUB önyükleme menüsünde ``e`` (düzenle) kipine girildiğinde ya da GRUB
shell'ine (``c`` tuşu) düşüldüğünde, adımda kullanıcıdan alınan
"GRUB yönetici parolası" sorulur. Ek olarak recovery girdisi menüden
kaldırılır. Boot akışı bu parolayı sormaz; yalnız menüye elle müdahale
eden görür.

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
- ``/etc/default/grub`` içinde ``GRUB_DISABLE_RECOVERY="true"``
  yapılır; recovery girdisi menüden çıkarılır (recovery girdisi tek
  kullanıcı moduna düşüp root shell veriyordu — kapatılır).
- ``update-grub`` çalıştırılır.

Aynı hash tüm klonlara aynen taşınır; operatör tek bir parolayı
hatırlar. Düz parola sistemde tutulmaz — yalnız hash gömülür.

Geri al
``01_tiha_grub_password`` silinir, ``10_linux`` ve
``/etc/default/grub`` yedeklerinden geri yüklenir, ``update-grub``
yeniden çalıştırılır.
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


def _set_recovery_line(text: str) -> str:
    """``/etc/default/grub``'da GRUB_DISABLE_RECOVERY satırını
    ``"true"`` olarak ayarlar; yoksa sonuna ekler; yorumdaysa açar."""
    out: list[str] = []
    seen = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("GRUB_DISABLE_RECOVERY") or \
           stripped.startswith("#GRUB_DISABLE_RECOVERY") or \
           stripped.startswith("# GRUB_DISABLE_RECOVERY"):
            out.append('GRUB_DISABLE_RECOVERY="true"')
            seen = True
        else:
            out.append(line)
    if not seen:
        out.append('GRUB_DISABLE_RECOVERY="true"')
    return "\n".join(out) + "\n"


class GrubProtectionModule(Module):
    id = "m16_grub_protection"
    title = "GRUB koruması"
    sidebar_title = "GRUB koruması"
    streams_output = True
    apply_hint = (
        f"İşaretliyken: `e` düzenleme kipi ve GRUB shell, kullanıcı adı "
        f"`{SUPERUSER}` + buraya yazdığınız parolanın arkasına alınır, "
        f"recovery girdisi kaldırılır. İşaretsizken: varsa mevcut koruma "
        f"kaldırılır, GRUB eski hâline döner."
    )
    rationale = (
        "GRUB açılış menüsünde ``e`` tuşu kernel komut satırının "
        "düzenlenmesine izin verir. Yerel klavye erişimi olan biri "
        "buraya ``init=/bin/bash`` yazarak tahtayı doğrudan root "
        "shell'e düşürebilir; root parolasını sıfırlamak, diski "
        "okumak, kalıcı arka kapı bırakmak buradan mümkündür. m02 "
        "(Açılışta parola temizliği) etkin olsa dahi o oturum "
        "içindeki hasar sınırsızdır. Aynı vektör recovery girdisi "
        "üzerinden de açıktır.\n\n"
        "Bu adım aşağıdaki 'GRUB yönetici parolası' alanına "
        "yazdığınız parolayı PBKDF2-SHA512 hash'i olarak GRUB'a "
        "tanımlar (kullanıcı adı: ``etapadmin``), menü girdilerini "
        "``--unrestricted`` işaretler ve recovery girdisini kapatır. "
        "Boot akışı bu parolayı sormaz; yalnız kullanıcı menüde "
        "``e`` (düzenle) kipine girdiğinde ya da GRUB shell'ine "
        "(``c`` tuşu) düştüğünde adımda tanımladığınız bu parola "
        "sorulur. Aynı hash bütün klonlara aynen taşınır; operatör "
        "tek bir parolayı hatırlar.\n\n"
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
        already_patched = "--unrestricted" in linux_txt
        needle_present = CLASS_NEEDLE in linux_txt
        defaults_txt = _read_text(GRUB_DEFAULTS)
        recovery_disabled = any(
            line.strip().startswith('GRUB_DISABLE_RECOVERY="true"')
            for line in defaults_txt.splitlines()
        )

        lines: list[str] = []
        lines.append(
            f"01_tiha_grub_password  : "
            f"{'zaten var, yeniden yazılacak' if include_exists else 'yazılacak'}"
        )
        lines.append(
            f"10_linux yaması        : "
            f"{'zaten uygulanmış' if already_patched else 'uygulanacak (--unrestricted eklenir)'}"
        )
        if not already_patched and not needle_present:
            lines.append(
                "  UYARI: Beklenen CLASS satırı 10_linux içinde bulunamadı."
            )
            lines.append(
                "  GRUB paketi güncellenmiş olabilir; yama uygulanamaz."
            )
        lines.append(
            f"Recovery girdisi       : "
            f"{'zaten kapalı' if recovery_disabled else 'kapatılacak'}"
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
        lines.append("  - Recovery girdisi       → menüde yer almaz")
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
        # yalnız adımdan geçiyordur, mevcut parolayı bozmayalım.
        if not password and self.lockdown_active():
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
        if not password:
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
        if len(password) < 8:
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
        already_patched = "--unrestricted" in linux_txt
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

        emit("Parola hash'i hesaplanıyor (PBKDF2-SHA512)…")
        pw_hash = _pbkdf2_hash(password)

        emit("10_linux ve /etc/default/grub yedekleniyor…")
        try:
            shutil.copy2(GRUB_LINUX_SCRIPT, linux_backup)
            shutil.copy2(GRUB_DEFAULTS, defaults_backup)
        except OSError as exc:
            return ApplyResult(False, f"Yedek alınamadı: {exc}")

        emit(f"{GRUB_LOCKDOWN_INCLUDE} yazılıyor…")
        try:
            GRUB_LOCKDOWN_INCLUDE.write_text(
                _include_content(pw_hash), encoding="utf-8",
            )
            GRUB_LOCKDOWN_INCLUDE.chmod(0o755)
        except OSError as exc:
            return ApplyResult(False, f"Include yazılamadı: {exc}")

        if not already_patched:
            emit("/etc/grub.d/10_linux dosyasına --unrestricted ekleniyor…")
            try:
                GRUB_LINUX_SCRIPT.write_text(
                    linux_txt.replace(CLASS_NEEDLE, CLASS_REPLACE, 1),
                    encoding="utf-8",
                )
            except OSError as exc:
                return ApplyResult(False, f"10_linux güncellenemedi: {exc}")

        emit("/etc/default/grub içinde GRUB_DISABLE_RECOVERY=true yapılıyor…")
        try:
            GRUB_DEFAULTS.write_text(
                _set_recovery_line(_read_text(GRUB_DEFAULTS)),
                encoding="utf-8",
            )
        except OSError as exc:
            return ApplyResult(False, f"/etc/default/grub güncellenemedi: {exc}")

        emit("update-grub çalıştırılıyor…")
        r = run_cmd(["update-grub"])
        if not r.ok:
            return ApplyResult(
                False,
                "update-grub başarısız oldu.",
                details=(r.stderr or r.stdout).strip(),
            )
        emit("update-grub tamamlandı.")

        return ApplyResult(
            True,
            f"GRUB koruması etkinleştirildi — kullanıcı adı: {SUPERUSER}",
            details=(
                f"Kullanıcı adı    : {SUPERUSER}\n"
                f"Include          : {GRUB_LOCKDOWN_INCLUDE}\n"
                f"10_linux yedeği  : {linux_backup}\n"
                f"grub yedeği      : {defaults_backup}\n\n"
                "Bir sonraki açılıştan itibaren `e` düzenleme kipi ve\n"
                "GRUB komut satırı önce 'Enter username:' sorar —\n"
                f"buraya {SUPERUSER} yazılır — ardından formda\n"
                "girdiğiniz parolayı ister. Boot akışının kendisi bu\n"
                "parolayı sormaz. Aynı hash bu tahtadan alınacak tüm\n"
                "klonlarda geçerlidir. Düz parola sistemde tutulmaz."
            ),
            data={
                "linux_backup": str(linux_backup),
                "defaults_backup": str(defaults_backup),
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

        if linux_backup.exists():
            say("10_linux yedekten geri yükleniyor…")
            try:
                shutil.copy2(linux_backup, GRUB_LINUX_SCRIPT)
            except OSError as exc:
                log.warning("10_linux geri yüklenemedi: %s", exc)
        else:
            # Yedek yoksa yamayı elle sök.
            say("10_linux yedeği yok — --unrestricted yaması sökülüyor…")
            try:
                txt = _read_text(GRUB_LINUX_SCRIPT)
                if CLASS_REPLACE in txt:
                    GRUB_LINUX_SCRIPT.write_text(
                        txt.replace(CLASS_REPLACE, CLASS_NEEDLE, 1),
                        encoding="utf-8",
                    )
            except OSError as exc:
                log.warning("10_linux geri döndürülemedi: %s", exc)

        if defaults_backup.exists():
            say("/etc/default/grub yedekten geri yükleniyor…")
            try:
                shutil.copy2(defaults_backup, GRUB_DEFAULTS)
            except OSError as exc:
                log.warning("grub varsayılanları geri yüklenemedi: %s", exc)
        else:
            # Yedek yoksa recovery satırını Debian varsayılanına (yorumlu)
            # çeviririz; özgün değeri bilemeyiz, en yakın tahmin budur.
            say("/etc/default/grub yedeği yok — recovery satırı yoruma alınıyor…")
            try:
                txt = _read_text(GRUB_DEFAULTS)
                if 'GRUB_DISABLE_RECOVERY="true"' in txt:
                    GRUB_DEFAULTS.write_text(
                        txt.replace(
                            'GRUB_DISABLE_RECOVERY="true"',
                            '#GRUB_DISABLE_RECOVERY="true"',
                        ),
                        encoding="utf-8",
                    )
            except OSError as exc:
                log.warning("recovery satırı geri alınamadı: %s", exc)

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
            "GRUB koruması kaldırıldı; menü ve recovery girdisi eski hâline döndü.",
        )
