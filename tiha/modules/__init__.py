"""TiHA işlem modülleri — sihirbazdaki sıralı adımlar."""

from __future__ import annotations

from ..core.module import Module

# Aşağıdaki sıralama doğrudan sihirbaz akışını belirler.
# Yeni modül eklerken buraya da tanıtın.
from .m01_initial_passwords import InitialPasswordsModule
from .m02_boot_password_wipe import BootPasswordWipeModule
from .m03_otp_secrets import OTPSecretsModule
from .m04_ssh_server import SSHServerModule
from .m05_samba_share import SambaShareModule
from .m06_remote_syslog import RemoteSyslogModule
from .m07_hostname import HostnameModule
from .m08_system_update import SystemUpdateModule
from .m09_image_sanitize import ImageSanitizeModule


def all_modules() -> list[Module]:
    """Sihirbaza eklenecek tüm modüllerin sıralı örneklerini döndürür."""
    return [
        InitialPasswordsModule(),
        BootPasswordWipeModule(),
        OTPSecretsModule(),
        SSHServerModule(),
        SambaShareModule(),
        RemoteSyslogModule(),
        HostnameModule(),
        SystemUpdateModule(),
        ImageSanitizeModule(),
    ]
