"""Modül başına kullanıcıdan alınacak parametre şemaları.

Şema ile arayüz dağıtıcısı (``module_page.build_params_form``) uyum
içindedir. Her alan şu yapıyı taklit eder:

``{"key": "root_password", "label": "Root parolası", "type": "password",
   "required": True, "default": "", "help": "En az 12 karakter."}``

Alan tipleri: ``text``, ``password``, ``password_confirm``, ``number``,
``textarea``, ``select``.
"""

from __future__ import annotations

# Modül id → form şeması
PARAMS_SCHEMA: dict[str, list[dict]] = {
    "m01_initial_passwords": [
        {
            "key": "root_password",
            "label": "Yeni root parolası",
            "type": "password",
            "required": True,
            "help": "Teknik ekibin tahtaya yönetici olarak bağlanacağı parola.",
        },
        {
            "key": "admin_password",
            "label": "Yeni etapadmin parolası",
            "type": "password",
            "required": True,
            "help": "Tahtada yerel yönetim işleri için kullanılacak parola.",
        },
    ],
    "m03_otp_secrets": [
        {
            "key": "teacher_names",
            "label": "Öğretmen ad soyad listesi (her satıra bir kişi)",
            "type": "textarea",
            "required": False,
            "default": "",
            "help": "Boş bırakılabilir. Yalnızca yedek hesap üretmek de mümkündür.",
        },
        {
            "key": "reserve_count",
            "label": "Yedek hesap sayısı",
            "type": "number",
            "required": False,
            "default": "0",
            "help": "Sonradan okula atanacak öğretmenler için ogretmen01, ogretmen02 şeklinde boş hesap.",
        },
    ],
    "m05_samba_share": [
        {
            "key": "samba_user",
            "label": "Samba kullanıcı adı",
            "type": "text",
            "required": True,
            "default": "root",
        },
        {
            "key": "samba_password",
            "label": "Samba parolası",
            "type": "password",
            "required": True,
        },
    ],
    "m06_remote_syslog": [
        {
            "key": "syslog_host",
            "label": "Merkezi log sunucusu (IP veya isim)",
            "type": "text",
            "required": True,
        },
        {
            "key": "syslog_port",
            "label": "Port",
            "type": "number",
            "required": False,
            "default": "514",
        },
        {
            "key": "syslog_proto",
            "label": "Protokol",
            "type": "select",
            "required": False,
            "default": "udp",
            "options": ["udp", "tcp"],
        },
    ],
    "m07_hostname": [
        {
            "key": "template",
            "label": "İmaj şablon hostname",
            "type": "text",
            "required": False,
            "default": "etap-image",
            "help": "İmaj alınırken tahta bu isimle kalır; klon ilk açılışta kendi ismini üretir.",
        },
        {
            "key": "prefix",
            "label": "Yeni hostname öneki",
            "type": "text",
            "required": False,
            "default": "etap",
            "help": "Klonda hostname şu biçimde olur: <önek>-<MAC'in son 6 hanesi>",
        },
    ],
}


def get(module_id: str) -> list[dict]:
    return PARAMS_SCHEMA.get(module_id, [])
