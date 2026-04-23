"""Küçük yardımcılar: komut çalıştırma, dosya yedekleme, rastgele parola üretme."""

from __future__ import annotations

import os
import secrets
import shutil
import string
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .logger import get_logger

log = get_logger(__name__)


@dataclass
class CmdResult:
    """``run_cmd`` çıktısı. Hatayı yükseltmeden geri döndürür."""

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
    """Bir alt süreç çalıştırır ve çıktıyı yakalar.

    :param cmd: Çalıştırılacak komut (liste hâlinde).
    :param check: ``True`` ise hata kodu 0 değilse istisna fırlatılır.
    :param input_data: Varsa standart girişe yazılacak metin.
    :param env: Özel çevre değişkenleri (verilmezse mevcut ortam kopyalanır).
    :param timeout: Saniye cinsinden zaman aşımı.
    """
    log.debug("Komut: %s", " ".join(cmd))
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    proc = subprocess.run(
        cmd,
        input=input_data,
        capture_output=True,
        text=True,
        env=merged_env,
        timeout=timeout,
        check=False,
    )
    result = CmdResult(proc.returncode, proc.stdout or "", proc.stderr or "")
    if not result.ok:
        log.debug("Komut çıktı kodu=%s stderr=%s", proc.returncode, result.stderr.strip())
    if check and not result.ok:
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, output=result.stdout, stderr=result.stderr
        )
    return result


def backup_file(src: Path, backup_dir: Path) -> Path | None:
    """Bir dosyanın yedeğini alır.

    Kaynak yoksa ``None`` döner. Yedek dizini yoksa oluşturulur.
    """
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
    """Kriptografik olarak güvenli rastgele parola üretir.

    Harf + rakam + birkaç güvenli noktalama karakterinden oluşur. Aksan/özel
    karakter içermez, böylece her klavye düzeninde sorunsuz işler.
    """
    alphabet = string.ascii_letters + string.digits + "!@#%^&*-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def is_root() -> bool:
    """Süreç kök yetkisiyle mi çalışıyor?"""
    return os.geteuid() == 0


def user_exists(username: str) -> bool:
    """``/etc/passwd`` içinde bu kullanıcı var mı?"""
    import pwd
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False
