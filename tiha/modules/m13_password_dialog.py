"""Modül 13 — EBA QR ilk-giriş parola tanımlama diyalogunu devre dışı bırak.

Pardus ETAP'ta EBA QR kodu ile bir kullanıcı tahtaya ilk kez giriş
yaptığında, ``eta-password-changer`` paketi şu zinciri kurar:

1. ``eta-qr-login`` (paket ``eta-qr-login``, ``ebaqr.service`` üzerinden)
   yeni hesap oluştururken ``/var/lib/eta/expire-uid/<UID>`` sentinel
   dosyasını yazar.
2. LightDM oturumu açar, Cinnamon başlar.
3. ``/etc/xdg/autostart/tr.org.eta.password-changer.desktop`` XDG
   autostart girdisi ``eta-password-changer``'ı tetikler.
4. ``eta-password-changer`` sentinel'i görür → GTK MainWindow ile parola
   tanımlama diyalogu açılır.

Sınıf ortamında bu diyalog karşısında öğretmen klavyeden parolasını
öğrenciler önünde yazmak durumunda kalabilir; bu da parolanın ifşa
olmasına yol açabilir. Bu adım otomatik açılan diyalogu kapatır.

Kullanıcılar diledikleri zaman Sistem Ayarları → Kullanıcı Hesapları
üzerinden veya doğrudan ``eta-password-changer`` komutunu çalıştırarak
parolalarını tanımlayabilir; sadece her ilk girişte zorla açılan diyalog
devre dışı kalır. ``eta-qr-login`` ve ``eta-password-changer`` paketleri
kaldırılmaz; sadece XDG autostart girdisine ``Hidden=true`` eklenir
(XDG-spec ile uyumlu: "girdi başlatılırken atlanır").

Geri al. Apply zamanında yedeklenen orijinal autostart dosyası yerine
yazılır.
"""

from __future__ import annotations

from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.utils import backup_file, restore_file

log = get_logger(__name__)

AUTOSTART_FILE = Path("/etc/xdg/autostart/tr.org.eta.password-changer.desktop")


def _is_hidden(text: str) -> bool:
    """Desktop dosyasında [Desktop Entry] bölümünde Hidden=true var mı?"""
    in_entry = False
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("[") and s.endswith("]"):
            in_entry = (s == "[Desktop Entry]")
            continue
        if not in_entry or "=" not in s:
            continue
        key, _, val = s.partition("=")
        if key.strip().lower() == "hidden" and val.strip().lower() == "true":
            return True
    return False


def _set_hidden(text: str) -> str:
    """[Desktop Entry] bölümüne Hidden=true ekler veya mevcut satırı günceller.

    Diğer satırların sırası ve dosya sonu newline davranışı korunur.
    """
    out: list[str] = []
    in_entry = False
    hidden_written = False

    lines = text.splitlines(keepends=False)
    for raw in lines:
        s = raw.strip()
        if s.startswith("[") and s.endswith("]"):
            # Yeni bölüme geçerken, hâlâ [Desktop Entry] içindeysek Hidden ekle.
            if in_entry and not hidden_written:
                out.append("Hidden=true")
                hidden_written = True
            in_entry = (s == "[Desktop Entry]")
            out.append(raw)
            continue

        if in_entry and "=" in s and s.partition("=")[0].strip().lower() == "hidden":
            out.append("Hidden=true")
            hidden_written = True
            continue

        out.append(raw)

    if in_entry and not hidden_written:
        out.append("Hidden=true")

    result = "\n".join(out)
    if text.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


class PasswordDialogModule(Module):
    id = "m13_password_dialog"
    title = "EBA QR parola diyalogu"
    sidebar_title = "QR Parola Diyaloğu"
    apply_hint = (
        "EBA QR ile ilk girişte otomatik açılan parola tanımlama "
        "diyalogu devre dışı bırakılır."
    )
    rationale = (
        "Bir kullanıcı tahtaya EBA QR kodu ile ilk kez giriş yaptığında, "
        "sistem otomatik olarak parola tanımlama penceresini açar. Sınıf "
        "ortamında öğretmen, parolasını öğrencilerin önünde klavyeden "
        "yazmak durumunda kalabilir ve bu da parolanın istemeden ifşa "
        "olmasına yol açabilir.\n\n"
        "Bu adım, ilk girişte otomatik açılan diyalogu kapatır. "
        "Kullanıcının parolası olmamış olur; tahtaya yine yalnızca EBA QR "
        "ile giriş yapılır. Bir öğretmen kendi parolasını koymak isterse "
        "her zaman Sistem Ayarları → Kullanıcı Hesapları üzerinden uygun "
        "bir ortamda (örneğin teneffüste, sınıf boşken) tanımlayabilir."
    )
    undo_supported = True

    def preview(self) -> str:
        if not AUTOSTART_FILE.is_file():
            return (
                f"⚠ Hedef bulunamadı: {AUTOSTART_FILE}\n\n"
                "eta-password-changer paketi kurulu değil görünüyor. "
                "Bu adım uygulanamaz."
            )

        try:
            text = AUTOSTART_FILE.read_text(encoding="utf-8")
        except OSError as exc:
            return f"⚠ Autostart dosyası okunamadı: {exc}"

        already_hidden = _is_hidden(text)
        backup_path = self.state_dir / AUTOSTART_FILE.name
        backup_exists = backup_path.exists()

        lines = [
            f"Hedef     : {AUTOSTART_FILE}",
            f"Durum     : {'⛔ devre dışı (Hidden=true)' if already_hidden else '🔔 etkin — diyalog açılıyor'}",
            f"Yedek     : {'var (' + str(backup_path) + ')' if backup_exists else 'yok'}",
            "",
            "Bu adım uygulandığında:",
            "  • Orijinal autostart dosyası modül dizinine yedeklenir (yoksa)",
            "  • Dosyaya 'Hidden=true' eklenerek otomatik tetikleme kapatılır",
            "  • eta-password-changer / eta-qr-login paketleri kaldırılmaz",
            "  • Kullanıcı dilerse parolasını Sistem Ayarları'ndan",
            "    (veya doğrudan 'eta-password-changer' ile) tanımlayabilir",
            "",
            "Geri al: yedek dosya orijinal yerine yazılır.",
        ]
        return "\n".join(lines)

    def apply(
        self,
        params: dict | None = None,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        if not AUTOSTART_FILE.is_file():
            return ApplyResult(
                False,
                f"{AUTOSTART_FILE} bulunamadı; "
                "eta-password-changer paketi yüklü değil.",
            )

        try:
            text = AUTOSTART_FILE.read_text(encoding="utf-8")
        except OSError as exc:
            return ApplyResult(False, f"Autostart dosyası okunamadı: {exc}")

        if _is_hidden(text):
            return ApplyResult(
                True,
                "Diyalog zaten devre dışıydı; değişiklik yapılmadı.",
                data={"was_already_hidden": True},
            )

        backup_dir = self.ensure_state_dir()
        backup_path = backup_dir / AUTOSTART_FILE.name
        if not backup_path.exists():
            if progress:
                progress(f"Orijinal yedekleniyor → {backup_path}")
            try:
                backup_file(AUTOSTART_FILE, backup_dir)
            except OSError as exc:
                return ApplyResult(False, f"Yedek alınamadı: {exc}")

        new_text = _set_hidden(text)
        try:
            AUTOSTART_FILE.write_text(new_text, encoding="utf-8")
        except OSError as exc:
            return ApplyResult(False, f"Autostart dosyası yazılamadı: {exc}")

        if progress:
            progress("✓ Hidden=true eklendi — otomatik diyalog devre dışı.")

        return ApplyResult(
            True,
            "EBA QR ilk-giriş parola diyalogu devre dışı bırakıldı.",
            details=(
                f"Yedek: {backup_path}\n"
                f"Hedef: {AUTOSTART_FILE} (Hidden=true)\n"
                "Kullanıcı parolasını Sistem Ayarları → Kullanıcı "
                "Hesapları üzerinden istediği zaman tanımlayabilir."
            ),
            data={"was_already_hidden": False},
        )

    def undo(
        self,
        data: dict,
        params: dict | None = None,
    ) -> ApplyResult:
        data = data or {}
        if data.get("was_already_hidden"):
            return ApplyResult(
                True,
                "Apply zamanı diyalog zaten devre dışıydı; "
                "geri alacak değişiklik yok.",
            )

        backup_path = self.state_dir / AUTOSTART_FILE.name
        if not backup_path.exists():
            return ApplyResult(
                False,
                f"Yedek dosya bulunamadı: {backup_path}",
            )

        try:
            restore_file(backup_path, AUTOSTART_FILE)
        except OSError as exc:
            return ApplyResult(False, f"Geri yükleme başarısız: {exc}")

        return ApplyResult(
            True,
            "Orijinal autostart geri yüklendi; otomatik diyalog yeniden aktif.",
        )
