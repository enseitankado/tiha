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

from ..core.i18n import t
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
    title = t("m13.title")
    sidebar_title = t("m13.sidebar_title")
    rationale_inline = True
    apply_hint = t("m13.apply_hint")
    rationale = t("m13.rationale")
    undo_supported = True

    def preview(self) -> str:
        if not AUTOSTART_FILE.is_file():
            return t("m13.preview.target_missing", path=AUTOSTART_FILE)

        try:
            text = AUTOSTART_FILE.read_text(encoding="utf-8")
        except OSError as exc:
            return t("m13.preview.read_error", error=exc)

        already_hidden = _is_hidden(text)
        backup_path = self.state_dir / AUTOSTART_FILE.name
        backup_exists = backup_path.exists()

        status = (
            t("m13.preview.status_disabled") if already_hidden
            else t("m13.preview.status_enabled")
        )
        backup = (
            t("m13.preview.backup_present", path=backup_path)
            if backup_exists else t("m13.preview.backup_absent")
        )
        lines = [
            t("m13.preview.target", path=AUTOSTART_FILE),
            t("m13.preview.status", status=status),
            t("m13.preview.backup", backup=backup),
            "",
            t("m13.preview.body"),
        ]
        return "\n".join(lines)

    def apply(
        self,
        params: dict | None = None,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        if not AUTOSTART_FILE.is_file():
            return ApplyResult(False, t("m13.apply.not_found", path=AUTOSTART_FILE))

        try:
            text = AUTOSTART_FILE.read_text(encoding="utf-8")
        except OSError as exc:
            return ApplyResult(False, t("m13.apply.read_error", error=exc))

        if _is_hidden(text):
            return ApplyResult(
                True,
                t("m13.apply.already_hidden"),
                data={"was_already_hidden": True},
            )

        backup_dir = self.ensure_state_dir()
        backup_path = backup_dir / AUTOSTART_FILE.name
        if not backup_path.exists():
            if progress:
                progress(t("m13.apply.backing_up", path=backup_path))
            try:
                backup_file(AUTOSTART_FILE, backup_dir)
            except OSError as exc:
                return ApplyResult(False, t("m13.apply.backup_failed", error=exc))

        new_text = _set_hidden(text)
        try:
            AUTOSTART_FILE.write_text(new_text, encoding="utf-8")
        except OSError as exc:
            return ApplyResult(False, t("m13.apply.write_failed", error=exc))

        if progress:
            progress(t("m13.apply.hidden_added"))

        return ApplyResult(
            True,
            t("m13.apply.done"),
            details=t(
                "m13.apply.done_details",
                backup=backup_path, target=AUTOSTART_FILE,
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
            return ApplyResult(True, t("m13.undo.nothing"))

        backup_path = self.state_dir / AUTOSTART_FILE.name
        if not backup_path.exists():
            return ApplyResult(False, t("m13.undo.backup_missing", path=backup_path))

        try:
            restore_file(backup_path, AUTOSTART_FILE)
        except OSError as exc:
            return ApplyResult(False, t("m13.undo.restore_failed", error=exc))

        return ApplyResult(True, t("m13.undo.done"))
