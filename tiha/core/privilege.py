"""Yetki denetimi.

TiHA, işlemlerin büyük çoğunluğunu kök yetkisiyle yapar. Uygulama
``pkexec`` sarmalayıcısı ile (GUI polkit prompt'u) ya da ``sudo``
altında başlatılır. Bu modül "doğru ortamda mıyım" sorusunu yanıtlar.
"""

from __future__ import annotations

import os
import pwd

from .utils import is_root

# Sistemin beklediği yönetici kullanıcı adı. eta-register de aynı kontrolü yapar.
ADMIN_USER = "etapadmin"


def effective_username() -> str:
    """Etkin kullanıcı adı (uid'den çözülmüş)."""
    return pwd.getpwuid(os.geteuid()).pw_name


def invoking_username() -> str:
    """Pkexec/sudo ile yükseltilmeden önceki asıl kullanıcı adı.

    ``pkexec``:: ``PKEXEC_UID`` çevre değişkenini doldurur.
    ``sudo``::    ``SUDO_USER`` çevre değişkenini doldurur.
    Hiçbiri yoksa mevcut gerçek kullanıcı döner.
    """
    if "PKEXEC_UID" in os.environ:
        try:
            return pwd.getpwuid(int(os.environ["PKEXEC_UID"])).pw_name
        except (KeyError, ValueError):
            pass
    if "SUDO_USER" in os.environ:
        return os.environ["SUDO_USER"]
    return pwd.getpwuid(os.getuid()).pw_name


def is_admin_user() -> bool:
    """Çağıran kullanıcı yönetici (:data:`ADMIN_USER`) mı?"""
    return invoking_username() == ADMIN_USER


def require_root_and_admin() -> tuple[bool, str]:
    """Kök yetki + yönetici kullanıcı koşulunu birlikte denetler.

    :return: ``(ok, aciklama)`` çifti. ``ok`` False ise ``aciklama`` kullanıcıya
        gösterilebilecek Türkçe bir ileti içerir.
    """
    if not is_root():
        return False, (
            "TiHA yönetici (kök) yetkisi gerektirir. Uygulamayı terminalden "
            "`sudo tiha` ile ya da menüdeki kısayoldan (parola ister) başlatın."
        )
    user = invoking_username()
    if user != ADMIN_USER:
        return False, (
            f"TiHA yalnızca '{ADMIN_USER}' kullanıcısı tarafından kullanılabilir. "
            f"Şu anda '{user}' hesabıyla çalışıyorsunuz."
        )
    return True, ""
