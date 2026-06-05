"""Küçük yardımcılar: komut çalıştırma (bloklu / akışlı), dosya yedekleme,
rastgele parola üretme.

``run_cmd``        — Komutu senkron çalıştırır, tüm çıktıyı toplu döner.
``run_cmd_stream`` — Komutu çalıştırır, her çıktı satırını ``progress`` ile
                     canlı iletir (UI'ın akış olarak göstermesi için).
"""

from __future__ import annotations

import os
import secrets
import shutil
import string
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .logger import get_logger

log = get_logger(__name__)


@dataclass
class CmdResult:
    """Komut çalıştırmanın sonucu. İstisna fırlatmaz, elle kontrol edilir."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_cmd(
    cmd: list[str],
    *,
    check: bool = False,
    input_data: str | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> CmdResult:
    """Blok-eden süreç çalıştırıcı — çıktıyı toplu döner."""
    cmd_str = " ".join(cmd)
    log.debug("=== KOMUT BAŞLADI ===")
    log.debug("Komut: %s", cmd_str)
    if input_data:
        # Parola içerebileceği için sadece uzunluğunu log'la
        log.debug("Stdin verisi: %d karakter", len(input_data))
    if env:
        log.debug("Ek ortam değişkenleri: %s", list(env.keys()))

    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    start_time = os.times()
    try:
        proc = subprocess.run(
            cmd,
            input=input_data,
            capture_output=True,
            text=True,
            env=merged_env,
            timeout=timeout,
            check=False,
        )
        end_time = os.times()
        duration = end_time.elapsed - start_time.elapsed

        result = CmdResult(proc.returncode, proc.stdout or "", proc.stderr or "")

        # Detaylı sonuç loglama
        log.debug("=== KOMUT SONUCU ===")
        log.debug("Komut: %s", cmd_str)
        log.debug("Süre: %.2f saniye", duration)
        log.debug("Çıktı kodu: %d", proc.returncode)

        if result.stdout.strip():
            log.debug("STDOUT (%d satır):", len(result.stdout.splitlines()))
            for i, line in enumerate(result.stdout.splitlines(), 1):
                log.debug("stdout[%d]: %s", i, line)

        if result.stderr.strip():
            log.debug("STDERR (%d satır):", len(result.stderr.splitlines()))
            for i, line in enumerate(result.stderr.splitlines(), 1):
                log.debug("stderr[%d]: %s", i, line)

        if not result.ok:
            log.warning("Komut başarısız - %s (kod:%d)", cmd_str, proc.returncode)
            if result.stderr.strip():
                log.error("Hata detayı: %s", result.stderr.strip())

        log.debug("=== KOMUT BİTTİ ===")

        if check and not result.ok:
            raise subprocess.CalledProcessError(
                proc.returncode, cmd, output=result.stdout, stderr=result.stderr
            )
        return result

    except subprocess.TimeoutExpired as exc:
        log.error("KOMUT ZAMAN AŞIMI: %s (timeout: %s)", cmd_str, timeout)
        result = CmdResult(-1, "", f"Timeout after {timeout}s")
        if check:
            raise exc
        return result
    except Exception as exc:
        log.error("KOMUT İSTİSNASI: %s - %s", cmd_str, exc)
        result = CmdResult(-1, "", str(exc))
        if check:
            raise exc
        return result


def run_cmd_stream(
    cmd: list[str],
    progress: Callable[[str], None] | None = None,
    *,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> CmdResult:
    """Komutu çalıştırır ve her stdout satırını ``progress``'a iletir.

    ``stderr`` de ``stdout``'a birleştirilir; böylece apt gibi araçların
    uyarı satırları da kullanıcıya gösterilir. Arayüz, ``progress`` geri-
    çağrısını thread-güvenli şekilde (GLib.idle_add) saracaktır.
    """
    cmd_str = " ".join(cmd)
    log.debug("=== AKIŞLI KOMUT BAŞLADI ===")
    log.debug("Komut: %s", cmd_str)
    if env:
        log.debug("Ek ortam değişkenleri: %s", list(env.keys()))

    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    start_time = os.times()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=merged_env,
        )
        assert proc.stdout is not None
        collected: list[str] = []
        line_count = 0

        for raw in proc.stdout:
            line = raw.rstrip()
            line_count += 1
            collected.append(line)
            log.debug("stream[%d]: %s", line_count, line)

            if progress:
                try:
                    progress(line)
                except Exception as exc:  # UI hatası komutu kesmesin
                    log.warning("progress geri çağrısında hata: %s", exc)

        try:
            rc = proc.wait(timeout=timeout)
            end_time = os.times()
            duration = end_time.elapsed - start_time.elapsed

            log.debug("=== AKIŞLI KOMUT SONUCU ===")
            log.debug("Komut: %s", cmd_str)
            log.debug("Süre: %.2f saniye", duration)
            log.debug("Çıktı kodu: %d", rc)
            log.debug("Toplam satır: %d", line_count)

            if rc != 0:
                log.warning("Akışlı komut başarısız - %s (kod:%d)", cmd_str, rc)

        except subprocess.TimeoutExpired:
            log.error("AKIŞLI KOMUT ZAMAN AŞIMI: %s (timeout: %s)", cmd_str, timeout)
            proc.kill()
            rc = -1
            collected.append("[ZAMAN AŞIMI]")

        log.debug("=== AKIŞLI KOMUT BİTTİ ===")
        return CmdResult(rc, "\n".join(collected), "")

    except Exception as exc:
        log.error("AKIŞLI KOMUT İSTİSNASI: %s - %s", cmd_str, exc)
        return CmdResult(-1, str(exc), str(exc))


def backup_file(src: Path, backup_dir: Path) -> Path | None:
    """Bir dosyanın yedeğini alır; yoksa ``None`` döner."""
    if not src.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / src.name
    shutil.copy2(src, dest)
    return dest


def restore_file(backup: Path, dest: Path) -> None:
    """``backup_file`` ile alınan bir yedeği yerine koyar."""
    shutil.copy2(backup, dest)


def random_password(length: int = 32) -> str:
    """Kriptografik olarak güvenli rastgele parola üretir."""
    alphabet = string.ascii_letters + string.digits + "!@#%^&*-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def is_root() -> bool:
    return os.geteuid() == 0


def user_exists(username: str) -> bool:
    import pwd
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False


def _find_active_graphical_session() -> dict[str, str] | None:
    """loginctl ile aktif grafik oturumun ortam değişkenlerini döner.

    Bulunan ilk State=active + Type=(x11|wayland|mir) oturum kullanılır.
    USER/HOME/DISPLAY/XAUTHORITY/XDG_RUNTIME_DIR/DBUS_SESSION_BUS_ADDRESS
    içeren bir dict döner; bulamazsa None.
    """
    import pwd

    r = run_cmd(["loginctl", "list-sessions", "--no-legend"], timeout=5)
    if not r.ok:
        return None

    for line in r.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        sid = parts[0]
        det = run_cmd(
            ["loginctl", "show-session", sid,
             "-p", "Type", "-p", "State", "-p", "User",
             "-p", "Name", "-p", "Display"],
            timeout=5,
        )
        if not det.ok:
            continue
        props: dict[str, str] = {}
        for ln in det.stdout.splitlines():
            if "=" in ln:
                k, _, v = ln.partition("=")
                props[k] = v
        if props.get("State") != "active":
            continue
        if props.get("Type") not in ("x11", "wayland", "mir"):
            continue
        try:
            uid = int(props.get("User", "0"))
            pw = pwd.getpwuid(uid)
        except (ValueError, KeyError):
            continue
        if uid < 1000:
            continue

        runtime_dir = f"/run/user/{uid}"
        display = props.get("Display") or ":0"
        xauth = ""
        for cand in (
            f"{pw.pw_dir}/.Xauthority",
            f"{runtime_dir}/gdm/Xauthority",
            f"/var/run/lightdm/{pw.pw_name}/xauthority",
            f"/run/lightdm/{pw.pw_name}/xauthority",
        ):
            if os.path.exists(cand):
                xauth = cand
                break

        return {
            "USER": pw.pw_name,
            "HOME": pw.pw_dir,
            "DISPLAY": display,
            "XAUTHORITY": xauth,
            "XDG_RUNTIME_DIR": runtime_dir,
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime_dir}/bus",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        }
    return None


def screen_blank_seconds() -> int | None:
    """Aktif grafik oturumda ekranın güç tasarrufuna geçeceği idle eşiği (sn).

    Şu kaynakların en küçük non-zero değerini döner:

    * X11 DPMS Standby/Suspend/Off timeout (``xset q``)
    * X11 Screen Saver timeout
    * Cinnamon ``org.cinnamon.settings-daemon.plugins.power``
      ``sleep-display-ac`` / ``sleep-display-battery``
    * GNOME ``org.gnome.settings-daemon.plugins.power``
      ``sleep-display-ac`` / ``sleep-display-battery``

    Hepsi 0 / yok ise ``None`` döner — sistem ekran enerjisini kesmiyor
    demektir. Aktif grafik oturum bulunamazsa da ``None`` döner; çağıran
    bunu "bilgi yok, kısıt uygulama" diye yorumlamalıdır.
    """
    import re

    env = _find_active_graphical_session()
    if not env:
        return None

    sudo_env = (
        ["sudo", "-u", env["USER"], "env"]
        + [f"{k}={v}" for k, v in env.items()]
    )
    values: list[int] = []

    r = run_cmd(sudo_env + ["xset", "q"], timeout=5)
    if r.ok:
        for m in re.finditer(r"\b(Standby|Suspend|Off):\s*(\d+)", r.stdout):
            v = int(m.group(2))
            if v > 0:
                values.append(v)
        m = re.search(r"Screen Saver:[^\n]*\n\s*timeout:\s*(\d+)", r.stdout)
        if m:
            v = int(m.group(1))
            if v > 0:
                values.append(v)

    # Sadece yüklü schema'ları sorgula — yüklü olmayanlara get çağırmak
    # warning log gürültüsü üretir ve gereksizdir.
    schemas_r = run_cmd(sudo_env + ["gsettings", "list-schemas"], timeout=5)
    loaded = set(schemas_r.stdout.split()) if schemas_r.ok else set()
    for schema in (
        "org.cinnamon.settings-daemon.plugins.power",
        "org.gnome.settings-daemon.plugins.power",
    ):
        if schema not in loaded:
            continue
        for key in ("sleep-display-ac", "sleep-display-battery"):
            r = run_cmd(sudo_env + ["gsettings", "get", schema, key], timeout=5)
            if not r.ok:
                continue
            s = r.stdout.strip().replace("uint32 ", "")
            try:
                v = int(s)
            except ValueError:
                continue
            if v > 0:
                values.append(v)

    return min(values) if values else None
