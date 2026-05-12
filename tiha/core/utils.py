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
