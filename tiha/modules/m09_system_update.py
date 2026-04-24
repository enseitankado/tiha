"""Modül 9 — Sistem güncellemesi.

**Ne yapar?**
``apt-get update``, ``apt-get full-upgrade -y``, ``apt-get autoremove -y``
ve ``apt-get clean`` komutlarını sırayla çalıştırır. Çıktı ekrana **canlı
olarak** akar; kullanıcı asılmadığından emin olur.

**Neden gerekir?**
İmaj alındıktan sonra sahaya dağıtılacak tahtaların en güncel yama
seviyesinde çıkmaları güvenlik ve kararlılık için tercih edilir.

**Geri al.** Paket yükseltmeleri otomatik olarak geri alınamaz; modül
``undo_supported = False`` ile işaretlenmiştir.
"""

from __future__ import annotations

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback
from ..core.utils import run_cmd_stream

log = get_logger(__name__)


class SystemUpdateModule(Module):
    id = "m09_system_update"
    title = "Sistem güncellemesi (apt)"
    apply_hint = (
        "apt update + full-upgrade + temizlik çalışır (uzun sürer)."
    )
    rationale = (
        "Tahtadaki paketleri en güncel sürüme çıkarır. Güvenlik yamaları ve "
        "kararlılık düzeltmeleri için imaj öncesi tavsiye edilir. Çıktı "
        "ekranda canlı olarak akar; güncelleme uzun sürebilir."
    )
    undo_supported = False
    streams_output = True

    def preview(self) -> str:
        return "apt update → full-upgrade → autoremove → clean. Uzun sürebilir."

    def apply(self, params=None, progress: ProgressCallback | None = None) -> ApplyResult:
        env = {"DEBIAN_FRONTEND": "noninteractive"}
        steps = [
            ("apt update", ["apt-get", "update"]),
            ("apt full-upgrade", ["apt-get", "full-upgrade", "-y"]),
            ("apt autoremove", ["apt-get", "autoremove", "-y"]),
            ("apt clean", ["apt-get", "clean"]),
        ]
        failed: list[str] = []
        for label, cmd in steps:
            if progress:
                progress(f"\n==== {label} ====")
            log.info("%s çalıştırılıyor…", label)
            result = run_cmd_stream(cmd, progress=progress, env=env, timeout=3600)
            if not result.ok:
                failed.append(label)
                if progress:
                    progress(f"[HATA] {label} başarısız (çıkış kodu {result.returncode})")
                log.error("%s başarısız", label)

        if failed:
            return ApplyResult(
                False,
                "Bazı güncelleme adımları başarısız oldu.",
                details="Başarısız olanlar: " + ", ".join(failed),
            )
        return ApplyResult(True, "Sistem güncel.")
