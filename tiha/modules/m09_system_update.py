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
from ..core.utils import run_cmd, run_cmd_stream

log = get_logger(__name__)


def pending_update_count() -> int:
    """Yükseltilebilir paket sayısı. Negatif → bilinmiyor (apt erişilemedi).

    apt'ın mevcut önbelleğini kullanır; ``apt-get update`` çalıştırmaz.
    Hızlıdır (genellikle <1 sn).
    """
    result = run_cmd(
        ["apt-get", "-s", "-q", "full-upgrade"],
        check=False,
        timeout=60,
    )
    if not result.ok:
        return -1
    return sum(1 for line in result.stdout.splitlines() if line.startswith("Inst "))


class SystemUpdateModule(Module):
    id = "m09_system_update"
    title = "Sistem güncellemesi (apt)"
    sidebar_title = "Sistem güncellemesi"
    apply_hint = (
        "apt update + full-upgrade + temizlik çalışır (uzun sürer)."
    )
    rationale = (
        "Tahtadaki paketleri en güncel sürüme çıkarır. Güvenlik yamaları ve "
        "kararlılık düzeltmeleri için imaj öncesi tavsiye edilir. Çıktı "
        "ekranda canlı olarak akar; güncelleme uzun sürebilir.\n\n"
        "Bekleyen yükseltme yoksa bu adım atlanabilir; alttaki “İleri” "
        "düğmesi ya da soldaki listeden bir sonraki adıma geçebilirsiniz."
    )
    undo_supported = False
    streams_output = True

    def pending_update_count(self) -> int:
        return pending_update_count()

    def preview(self) -> str:
        count = pending_update_count()
        if count < 0:
            return (
                "Bekleyen yükseltme sayısı tespit edilemedi (apt erişilemedi).\n"
                "Yine de Uygula çalıştırılabilir: apt update → full-upgrade → "
                "autoremove → clean."
            )
        if count == 0:
            return (
                "Bekleyen yükseltme yok. Sistem güncel görünüyor; bu adımı "
                "uygulamadan bir sonrakine geçebilirsiniz."
            )
        return (
            f"{count} paket için yükseltme bekleniyor.\n"
            "Uygula çalıştırıldığında: apt update → full-upgrade → autoremove "
            "→ clean. Uzun sürebilir."
        )

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
