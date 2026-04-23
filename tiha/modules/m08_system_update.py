"""Modül 8 — Sistem güncellemesi.

**Ne yapar?**
``apt update``, ``apt full-upgrade -y``, ``apt autoremove -y`` ve
``apt clean`` çalıştırır. İnternet bağlantısı gerektirir.

**Neden gerekir?**
İmaj alındıktan sonra sahaya dağıtılacak tahtaların en güncel yama
seviyesinde çıkmaları güvenlik ve kararlılık için tercih edilir.

**Geri al.** Paket yükseltmeleri otomatik olarak geri alınamaz. Bu modül
``undo_supported = False`` ile işaretlenmiştir; arayüz buna göre geri al
seçeneğini gizler.
"""

from __future__ import annotations

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.utils import run_cmd

log = get_logger(__name__)


class SystemUpdateModule(Module):
    id = "m08_system_update"
    title = "Sistem güncellemesi (apt)"
    rationale = (
        "Tahtadaki paketlerin en güncel sürüme çıkmasını sağlar. Güvenlik "
        "yamaları ve kararlılık düzeltmeleri için imaj öncesi tavsiye edilir."
    )
    undo_supported = False

    def preview(self) -> str:
        return "apt update → full-upgrade → autoremove → clean çalıştırılacak. Uzun sürebilir."

    def apply(self, params: dict | None = None) -> ApplyResult:
        env = {"DEBIAN_FRONTEND": "noninteractive"}
        steps = [
            ("apt update", ["apt-get", "update"]),
            ("apt full-upgrade", ["apt-get", "full-upgrade", "-y"]),
            ("apt autoremove", ["apt-get", "autoremove", "-y"]),
            ("apt clean", ["apt-get", "clean"]),
        ]
        failed: list[str] = []
        detail_lines = []
        for label, cmd in steps:
            log.info("%s çalıştırılıyor…", label)
            result = run_cmd(cmd, env=env, timeout=3600)
            status = "OK" if result.ok else "HATA"
            detail_lines.append(f"{label}: {status}")
            if not result.ok:
                failed.append(label)
                log.error("%s başarısız: %s", label, result.stderr.strip())

        if failed:
            return ApplyResult(
                False,
                "Bazı güncelleme adımları başarısız oldu.",
                details="\n".join(detail_lines),
            )
        return ApplyResult(True, "Sistem güncel.", details="\n".join(detail_lines))
