"""TiHA'nın disk üzerinde kullandığı sabit dizin ve dosya yolları.

Bütün yollar tek yerde tanımlıdır; modüller buradan içe aktararak kullanır.
Böylece yeri değiştirmek gerekirse tek bir noktayı düzenlemek yeterli olur.
"""

from __future__ import annotations

from pathlib import Path

# Ana kök: sistem genelinde TiHA'nın yazdığı yerler
VAR_ROOT = Path("/var/lib/tiha")
LOG_ROOT = Path("/var/log/tiha")
ETC_ROOT = Path("/etc/tiha")

# Alt dizinler
STATE_DIR = VAR_ROOT / "state"          # Modül başına yedek ve durum klasörleri
JOURNAL_FILE = VAR_ROOT / "journal.json"  # Yapılan işlemlerin özet defteri
LOG_FILE = LOG_ROOT / "tiha.log"         # Ana uygulama log dosyası

# Sistem tarafı entegrasyonlar
BOOT_WIPE_SERVICE = Path("/etc/systemd/system/tiha-boot-password-wipe.service")
BOOT_WIPE_SCRIPT = Path("/usr/local/sbin/tiha-boot-password-wipe.sh")
OTP_SECRETS_FILE = Path("/etc/otp-secrets.json")  # eta-otp-lock ile bire bir uyumlu
RSYSLOG_CONF = Path("/etc/rsyslog.d/90-tiha-remote.conf")
SAMBA_SHARE_CONF = Path("/etc/samba/smb.conf.d/tiha-root-share.conf")
SAMBA_SMB_CONF = Path("/etc/samba/smb.conf")

# Yardımcı: dizinleri garanti altına al
def ensure_runtime_dirs() -> None:
    """Çalışma anında TiHA'nın ihtiyaç duyduğu dizinleri kök yetkisiyle oluşturur.

    Bu çağrının yalnızca root (veya pkexec ile yükselmiş) süreç içinde
    çağrılması beklenir. Hata durumunda istisna yayılır.
    """
    for directory in (VAR_ROOT, LOG_ROOT, ETC_ROOT, STATE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
