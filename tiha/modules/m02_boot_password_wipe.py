"""Modül 2 — Her açılışta parola temizliği.

**Ne yapar?**
Tahta her açıldığında, ``etapadmin`` **dışındaki** tüm gerçek kullanıcıların
parolalarını rastgele yeni bir değere çeviren bir *systemd* servisi kurar.

**Neden gerekir?**
İmajlanmış tahta okula dağıtıldıktan sonra dahi, bir kullanıcı ekrana
parola yazarak oturum açmaya çalışırsa, bu parola sınıftaki öğrenciler
tarafından görülüp öğrenilebilir. Her açılışta parolayı bozarak bu yol
kapatılır — kullanıcılar yalnızca OTP/QR/USB gibi parolasız yollarla
oturum açabilir.

**Geri al.** Servis devre dışı bırakılır, ilgili dosyalar kaldırılır.
Daha önce atanmış rastgele parolalar KENDİLİĞİNDEN eski hâline dönmez;
gerekirse yeni parola manuel atanmalıdır.
"""

from __future__ import annotations

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module
from ..core.paths import BOOT_WIPE_SCRIPT, BOOT_WIPE_SERVICE
from ..core.utils import run_cmd

log = get_logger(__name__)


SCRIPT_CONTENT = """#!/bin/bash
# TiHA — her açılışta genel kullanıcıların parolalarını rastgele atar.
# Öğrencilerin ekrana parola yazarak oturum açmasını engellemek içindir.
# Yalnızca UID 1000-59999 aralığı ve etapadmin hariç.
set -euo pipefail
log() { logger -t tiha-boot-wipe "$*"; }
while IFS=: read -r user _ uid _ _ _ _; do
    if [[ "$uid" -ge 1000 && "$uid" -lt 60000 && "$user" != "etapadmin" ]]; then
        rand=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 40 || true)
        if echo "${user}:${rand}" | chpasswd; then
            log "kullanıcı '$user' parolası rastgele atandı"
        else
            log "HATA: '$user' için chpasswd başarısız"
        fi
    fi
done < /etc/passwd
"""

SERVICE_CONTENT = """[Unit]
Description=TiHA — Açılışta genel kullanıcı parolalarını rastgele ata
After=multi-user.target
ConditionPathExists=!/etc/tiha/boot-wipe.disabled

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/tiha-boot-password-wipe.sh

[Install]
WantedBy=multi-user.target
"""


class BootPasswordWipeModule(Module):
    id = "m02_boot_password_wipe"
    title = "Her açılışta parola temizliği"
    rationale = (
        "Tahta her yeniden başladığında, etapadmin dışındaki tüm hesapların "
        "parolalarını rastgele bir değere çevirecek bir sistem servisi kurar. "
        "Bu sayede ekranda görülen bir parola bir sonraki açılışta geçersiz "
        "hâle gelir ve öğrenciler tarafından öğrenilen parolalar kullanılamaz."
    )

    def preview(self) -> str:
        existing = BOOT_WIPE_SERVICE.exists()
        return (
            "Kurulum zaten mevcut; yeniden uygulamak güncelleme yapar."
            if existing
            else "Script ve systemd servis dosyası kurulacak, servis etkinleştirilecek."
        )

    def apply(self, params: dict | None = None) -> ApplyResult:
        try:
            BOOT_WIPE_SCRIPT.write_text(SCRIPT_CONTENT, encoding="utf-8")
            BOOT_WIPE_SCRIPT.chmod(0o750)
            BOOT_WIPE_SERVICE.write_text(SERVICE_CONTENT, encoding="utf-8")
        except OSError as exc:
            return ApplyResult(False, f"Dosya yazılamadı: {exc}")

        run_cmd(["systemctl", "daemon-reload"])
        enable = run_cmd(["systemctl", "enable", BOOT_WIPE_SERVICE.name])
        if not enable.ok:
            return ApplyResult(False, "Servis etkinleştirilemedi.", details=enable.stderr)

        return ApplyResult(
            True,
            "Açılışta parola temizleme servisi kuruldu ve etkinleştirildi.",
            details=(
                f"Script: {BOOT_WIPE_SCRIPT}\nServis: {BOOT_WIPE_SERVICE}\n"
                "Bir sonraki açılıştan itibaren aktif olacaktır."
            ),
        )

    def undo(self, data: dict) -> ApplyResult:
        run_cmd(["systemctl", "disable", "--now", BOOT_WIPE_SERVICE.name])
        for path in (BOOT_WIPE_SERVICE, BOOT_WIPE_SCRIPT):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("Silinemedi %s: %s", path, exc)
        run_cmd(["systemctl", "daemon-reload"])
        return ApplyResult(True, "Açılış parola temizleme servisi kaldırıldı.")
