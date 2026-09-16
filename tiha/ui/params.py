"""Modül başına kullanıcıdan alınacak parametre şemaları.

Alan tipleri: ``text``, ``password``, ``number``, ``textarea``, ``select``,
``bool``, ``spin``, ``button``, ``file`` ve değer taşımayan bölüm başlığı
``heading``. ``bool`` alanı ``enables`` listesi taşıyabilir: kutu
işaretsizken listedeki alanlar pasifleşir.
"""

from __future__ import annotations

PARAMS_SCHEMA: dict[str, list[dict]] = {
    "m01_initial_passwords": [
        {
            "key": "root_password",
            "label": "Yeni root parolası (isteğe bağlı)",
            "type": "password",
            "show_toggle": True,
            "required": False,
            "help": "Teknik ekibin tahtaya yönetici olarak bağlanacağı parola. Boş bırakılabilir.",
        },
        {
            "key": "admin_password",
            "label": "Yeni etapadmin parolası (isteğe bağlı)",
            "type": "password",
            "show_toggle": True,
            "required": False,
            "help": "Tahtada yerel yönetim işleri için kullanılacak parola. Boş bırakılabilir.",
        },
        {
            "key": "teacher_password",
            "label": "Öğretmen parolası (isteğe bağlı)",
            "type": "password",
            "show_toggle": True,
            "required": False,
            "help": "Öğretmen hesabı varsa, bu hesap için parola belirleyebilirsiniz. Boş bırakılabilir.",
        },
        {
            "key": "remove_student",
            "label": "Öğrenci Hesabını Sil",
            "type": "button",
            "action": "remove_student_user_action",
            "style": "destructive",
            "help": "Öğrenci hesabı güvenlik riski oluşturur. Bu buton ile güvenli şekilde silebilirsiniz.",
        },
    ],
    "m03_otp_secrets": [
        {
            "key": "teacher_names",
            "label": "Öğretmen ad soyad listesi (her satıra bir kişi)",
            "type": "textarea",
            "required": False,
            "placeholder": (
                "AYŞE YILMAZ\n"
                "MEHMET DEMİR\n"
                "FATMA ÖZTÜRK\n"
                "AHMET KARA"
            ),
            "help": (
                "İsimleri BÜYÜK HARFLERLE girin, her satıra bir kişi. "
                "Ad ve soyadların öğretmenin MEBBIS'te kayıtlı olduğu "
                "biçimle birebir aynı yazılması önemlidir; harf atlanması "
                "veya küçük bir yazım farkı sonrasında öğretmenin PIN "
                "koduyla oturum açamamasına neden olur. Alan boş "
                "bırakılabilir; yalnızca yedek hesap üretmek de mümkündür. "
                "Örnek satırlar tıklayıp yazmaya başladığınızda silinir."
            ),
        },
        {
            "key": "include_etapadmin",
            "label": "Sistem yöneticisi (etapadmin) için de PIN üret",
            "type": "bool",
            "required": False,
            "default": "True",
            "help": (
                "İşaretlenirse etapadmin için de bir OTP anahtarı üretilir. "
                "Yönetici parolasını paylaşmak yerine birine sadece o anlık "
                "6 haneli PIN'i vererek geçici yetki devredilebilir; parola "
                "güvende kalır. etapadmin hesabı yine her giriş yolu (parola, "
                "USB, QR, PIN) için kullanılabilir."
            ),
        },
        {
            "key": "include_ogretmen",
            "label": "Ortak öğretmen hesabı (ogretmen) için de PIN üret",
            "type": "bool",
            "required": False,
            "default": "True",
            "help": (
                "İşaretlenirse ortak ogretmen hesabı için de bir OTP "
                "anahtarı üretilir. Sınıfta parola paylaşmadan geçici "
                "giriş için kullanışlıdır; hesabın kendi parolası varsa "
                "onunla giriş yine mümkündür."
            ),
        },
        {
            "key": "reserve_count",
            "label": "Yedek hesap sayısı",
            "type": "spin",
            "required": False,
            "default": "0",
            # Sistemde ogretmen01 … ogretmenNN varsa kutu NN ile dolu
            # gelsin; yönetici farkında olmadan yeni hesap açmasın.
            "default_from": "suggested_reserve_count",
            "min": 0,
            "max": 999,
            "step": 1,
            "help": (
                "Sonradan okula atanacak öğretmenler için ogretmen01, "
                "ogretmen02 … biçiminde boş hesaplar hazırlar. Bu adımda "
                "yerel makine üzerinde her yedek hesap için ev dizini "
                "açılır (useradd ile), hesap EBA QR / eta-usb-login ile "
                "aynı standart cihaz gruplarına (ses, USB, kamera, yazıcı "
                "vb.) eklenir ve ogretmenler grubuna üye yapılır; "
                "böylece imaj alındığında bu hesaplar tüm klon tahtalara "
                "birlikte gider. Parola kilitli tutulur — hesaplar yalnız "
                "OTP/QR ile açılır."
            ),
        },
        {
            "key": "add_teachers_to_group",
            "label": "Öğretmen hesaplarını ogretmenler grubuna ekle",
            "type": "bool",
            "required": False,
            "default": "True",
            "help": (
                "Bu adımın yönettiği öğretmen hesaplarını (listedekiler ve "
                "yedek hesaplar) 'ogretmenler' grubuna üye yapar. Ayrıca "
                "imaja /etc/passwd'i izleyen küçük bir sistem servisi "
                "gömülür: EBA QR ile bir öğretmen tahtaya ilk kez oturum "
                "açtığında oluşan yeni yerel hesap da otomatik olarak gruba "
                "dahil edilir. Grup üyeliği, aşağıdaki '@ogretmenler' ortak "
                "PIN'inin çalışması için ön koşuldur."
            ),
        },
        {
            "key": "make_group_pin",
            "label": "Ogretmenler grubu için ortak PIN oluştur",
            "type": "bool",
            "required": False,
            "default": "False",
            # Ortak grup-PIN ile ortak 'ogretmen' hesabının PIN'i aynı
            # ihtiyaca iki ayrı yerden cevap veriyor; ikisini birlikte
            # üretmek gereksiz bir ikinci ortak sır demek. Grup-PIN
            # seçilince hesap PIN'inin işareti kalkar.
            "deselects": ["include_ogretmen"],
            "help": (
                "İşaretlenirse ogretmenler grubuna özel bir '@ogretmenler' "
                "PIN anahtarı üretilir (eta-otp-lock @grup mekanizması). "
                "Bu ortak PIN, gruba üye tüm hesaplara giriş için "
                "kullanılabilir; PIN kâğıdında ayrı bir 'ORTAK PIN' kartı "
                "olarak çıkar. Zaten bir ortak PIN varsa korunur, "
                "yenilenmez. Tek tek öğretmen PIN'lerinden daha zayıf bir "
                "izdir (herkes aynı kodu kullanır), bu yüzden varsayılan "
                "olarak kapalıdır."
            ),
        },
        {
            "key": "purge_all_secrets",
            "label": "Tüm PIN Anahtarlarını Sil",
            "type": "button",
            "action": "purge_all_secrets_action",
            "style": "destructive",
            "visible_when": "can_purge_secrets",
            "confirm": {
                "title": "Tüm PIN anahtarları silinsin mi?",
                "message": (
                    "/etc/otp-secrets.json içindeki BÜTÜN PIN anahtarları "
                    "silinecek ve bu anahtarları taşıyan yazdırılabilir "
                    "kâğıtlar da kaldırılacak.\n\n"
                    "Dağıtılmış anahtarlar geçersiz olur: telefonlardaki "
                    "kayıtlarla artık tahtaya giriş yapılamaz.\n\n"
                    "Silme öncesi bir yedek alınır. Devam edilsin mi?"
                ),
            },
            "help": (
                "Kaynak imajı temiz bir sayfadan hazırlamak için (ör. test "
                "amaçlı üretilmiş anahtarları imaja taşımamak) tüm PIN "
                "anahtarlarını siler. Onay ister; silme öncesi dosyanın "
                "yedeği modülün durum dizinine alınır."
            ),
        },
        {
            "key": "remove_extra_users",
            "label": "Fazladan Hesapları Sil",
            "type": "button",
            "action": "remove_extra_users_action",
            "style": "destructive",
            "visible_when": "can_remove_extra_users",
            "confirm": {
                "title": "Fazladan hesaplar silinsin mi?",
                "message": (
                    "Varsayılan hesaplar (etapadmin, ogrenci, ogretmen) "
                    "dışındaki tüm kullanıcılar, ev dizinleriyle birlikte "
                    "silinecek.\n\n"
                    "Ayrıca karşılığı kalmayan PIN kayıtları "
                    "/etc/otp-secrets.json'dan temizlenecek — hesabı "
                    "olmayan bir anahtar kullanılamaz ama imaja okunabilir "
                    "bir sır olarak giderdi. Grup anahtarları (@...) ve "
                    "varsayılan hesapların kayıtları korunur.\n\n"
                    "Silme öncesi PIN dosyasının yedeği alınır. Devam "
                    "edilsin mi?"
                ),
            },
            "help": (
                "Etap Pardus'daki varsayılan kullanıcılar (etapadmin, "
                "ogrenci, ogretmen) dışındaki tüm fazladan kullanıcıları "
                "siler ve karşılığı kalmayan PIN kayıtlarını temizler. "
                "Fazladan hesap olmasa bile, geride kalmış yetim PIN "
                "kayıtları varsa bu düğme onları temizlemek için görünür. "
                "Bu işlem onay gerektirir."
            ),
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
        {
            "key": "test_log_server",
            "label": "Log Sunucusunu Test Et",
            "type": "button",
            "action": "test_log_server_action",
            "help": (
                "Yukarıda yazılan host/port/protokole erişilebilir mi diye "
                "kontrol eder; TCP ise gerçek bağlantı kurar, UDP ise "
                "örnek bir RFC3164 mesajı gönderir."
            ),
        },
    ],
    "m07_time_sync": [
        {
            "key": "ntp_servers",
            "label": "NTP sunucuları (boşlukla ayırın)",
            "type": "text",
            "required": False,
            "default": "0.tr.pool.ntp.org 1.tr.pool.ntp.org",
            "help": (
                "MEB iç NTP adresini biliyorsanız buraya yazın "
                "(ör. time.meb.gov.tr veya okul sunucu IP'si). Varsayılan: "
                "Türkiye NTP havuzu."
            ),
        },
        {
            "key": "test_ntp_servers",
            "label": "NTP Sunucularını Test Et",
            "type": "button",
            "action": "test_ntp_servers_action",
            "help": "Yukarıda yazılan NTP sunucularının çevrimiçi ve işlevsel olup olmadığını kontrol eder.",
        },
        {
            "key": "ntp_fallback",
            "label": "Yedek NTP sunucuları",
            "type": "text",
            "required": False,
            "default": "time.cloudflare.com pool.ntp.org",
        },
        {
            "key": "timezone",
            "label": "Saat dilimi",
            "type": "text",
            "required": False,
            "default": "Europe/Istanbul",
        },
    ],
    "m08_hostname": [
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
    "m11_power_management": [
        {
            "key": "auto_enabled",
            "label": "Sabit saat kapatma",
            "type": "bool",
            "required": False,
            "default": "False",
            "help": "Belirlenen saatte otomatik kapatma yapar.",
        },
        {
            "key": "auto_hour",
            "label": "Kapatma saati",
            "type": "spin",
            "required": False,
            "default": "22",
            "min": 0,
            "max": 23,
            "step": 1,
            "help": "Otomatik kapatma yapılacak saat (24 saat formatında).",
        },
        {
            "key": "auto_minute",
            "label": "Kapatma dakikası",
            "type": "spin",
            "required": False,
            "default": "0",
            "min": 0,
            "max": 59,
            "step": 1,
            "help": "Otomatik kapatma yapılacak dakika.",
        },
        {
            "key": "idle_enabled",
            "label": "Idle tabanlı kapatma",
            "type": "bool",
            "required": False,
            "default": "True",
            "help": "Tahta boşta kalırsa otomatik kapatma yapar.",
        },
        {
            "key": "idle_minute",
            "label": "Idle süresi (dakika)",
            "type": "spin",
            "required": False,
            "default": "15",
            "min": 1,
            "max": 180,
            "step": 1,
            "help": "Tahta bu süre boşta kalırsa kapatılır. Minimum 1 dakika.",
        },
    ],
    "m17_performance": [
        {"label": "Oturum kalıntıları", "type": "heading"},
        {
            "key": "kill_user_processes",
            "label": "Eski oturum kalıntılarını temizle",
            "type": "bool",
            "default": "True",
            "help": (
                "Öğretmen oturumunu kapattığında o oturumdan kalan bütün "
                "süreçler (kapatılmadan bırakılan Firefox/Chrome ve alt "
                "süreçleri dahil) sonlandırılır. Tahta yeniden başlatılınca "
                "etkin olur. Aynı kullanıcının SSH gibi başka açık "
                "oturumlarına dokunulmaz; root hariç tutulur."
            ),
        },
        {
            "key": "terminate_lingering",
            "label": "Şu an asılı kalmış oturumları da kapat",
            "type": "bool",
            "default": "True",
            "help": (
                "Kapanmış ama süreçleri hâlâ çalışan (closing) öğretmen "
                "oturumları hemen sonlandırılır. Açık (aktif) oturumlara "
                "dokunulmaz. Asılı oturumlar yukarıdaki önizlemede listelenir."
            ),
        },
        {"label": "ETA Hafif Mod (eta-light-mode)", "type": "heading"},
        {
            "key": "light_mode_enabled",
            "label": "Hafif modu tüm kullanıcılara uygula",
            "type": "bool",
            "default": "False",
            "enables": [
                "lm_effects", "lm_compositor", "lm_thumbnails",
                "lm_directory_counts", "lm_app_monitoring",
                "lm_low_resolution", "lm_low_refresh_rate",
            ],
            "help": (
                "Paket yoksa kurulur. Seçilen ayarlar her kullanıcıya oturum "
                "açılışında uygulanır; kullanıcı kendi oturumunda değiştirse "
                "de bir sonraki girişte yeniden uygulanır. Kişisel ayarlar "
                "geri al ile otomatik eski hâline dönmez."
            ),
        },
        {
            "key": "lm_effects",
            "label": "Pencere ve menü animasyonlarını kapat",
            "type": "bool",
            "default": "True",
            "help": "Menü, pencere ve diyaloglar daha çabuk açılır hissi verir.",
        },
        {
            "key": "lm_compositor",
            "label": "Tam ekran pencereleri doğrudan çiz",
            "type": "bool",
            "default": "True",
            "help": "Tam ekran video ve sunumlarda yükü azaltır; ekran yırtılması görülebilir.",
        },
        {
            "key": "lm_thumbnails",
            "label": "Resim ve video önizlemelerini kapat",
            "type": "bool",
            "default": "True",
            "help": "Çok dosyalı USB/klasör açılışındaki yoğun işlemci ve disk yükünü kaldırır.",
        },
        {
            "key": "lm_directory_counts",
            "label": "Klasör öğesi sayımını kapat",
            "type": "bool",
            "default": "True",
        },
        {
            "key": "lm_app_monitoring",
            "label": "Uygulama kullanım izlemesini kapat",
            "type": "bool",
            "default": "True",
        },
        {
            "key": "lm_low_resolution",
            "label": "Çözünürlüğü 1600x900'e düşür",
            "type": "bool",
            "default": "False",
            "help": (
                "Grafik yükünü ~%30 azaltır ama yazı ve kalem çizgisi "
                "bulanıklaşır. Büyüyen arayüzü dengelemek için yazı boyutu "
                "ve dosya ikonları da küçültülür. Her kullanıcının ilk "
                "girişinde ekran kısa süre kararabilir."
            ),
        },
        {
            "key": "lm_low_refresh_rate",
            "label": "Yenileme hızını 50 Hz'e düşür",
            "type": "bool",
            "default": "False",
            "help": "Hareketli içerikte yükü ~%17 azaltır; kalem gecikmesi ~3 ms artar.",
        },
    ],
    "m14_bios_password": [
        {
            "key": "supervisor_password",
            "label": "BIOS yönetici parolası",
            # Düz metin — kullanıcının ne yazdığını görmesi gerekir
            # (BIOS yalnız BÜYÜK A-Z 0-9 kabul eder; 'I' yasak — '1' ile
            # karışıyor. UI input mask ile zorlanır, apply'da yeniden
            # doğrulanır). Donanım desteklenmiyorsa alan gizlenir.
            "type": "text",
            "required": False,
            "placeholder": "ABC23X",
            "visible_when": "is_hardware_supported_cached",
            "help": (
                "Yalnızca BÜYÜK harf (A-Z, I hariç) ve rakam (0-9). "
                "Uzunluk modele göre 4-12 karakter. Kutu boş gelir — "
                "donanımdaki mevcut parolayı görmek için aşağıdaki "
                "düğmeye basın. BOŞ bırakırsanız hem klon servisi hem "
                "“şimdi uygula” düğmesi parolayı TEMİZLER (BIOS "
                "koruması fiilen kalkar)."
            ),
        },
        {
            "key": "protection_mode",
            "label": "Yönetici parolası ne zaman sorulsun",
            "type": "select",
            "required": False,
            "default": "Yalnız BIOS ayarlarına girilirken (setup)",
            "options": [
                "Yalnız BIOS ayarlarına girilirken (setup)",
                "Her açılışta (always)",
            ],
            "visible_when": "is_hardware_supported_cached",
            "help": (
                "Bu seçim hem klona gömülen servise hem de "
                "“Bu makinenin BIOS parolasını ayarla” düğmesine "
                "uygulanır. Parola kutusu boşsa (clear yolu) bu seçim "
                "yok sayılır — parola olmadan BIOS koruması zaten "
                "etkili değildir."
            ),
        },
        {
            "key": "read_current",
            "label": "Mevcut yönetici parolasını oku",
            "type": "button",
            "action": "read_current_supervisor_action",
            "visible_when": "is_hardware_supported_cached",
            "help": (
                "Tıklayınca eta-112 indirilir (gerekirse), donanım "
                "sorgulanır; mevcut parola ve koruma modu yukarıdaki "
                "alanlara yazılır. İlerleme alt kısımda görünür."
            ),
        },
        {
            "key": "set_local",
            "label": "Bu makinenin BIOS parolasını ayarla",
            "type": "button",
            "action": "set_local_supervisor_action",
            "style": "destructive",
            "visible_when": "is_hardware_supported_cached",
            "help": (
                "DİKKAT: Klon servisi KURMAZ; doğrudan bu makinenin "
                "BIOS flash'ına yazar. Parola kutusu boşsa parolayı "
                "TEMİZLER, doluysa girilen parolayı ve koruma modunu "
                "yazar. Geri alma yoktur; eta-112 “brick riski” uyarısı "
                "burada da geçerli. Değişikliğin tam etkili olması için "
                "işlem sonrası makineyi yeniden başlatın."
            ),
        },
    ],
}


def get(module_id: str) -> list[dict]:
    return PARAMS_SCHEMA.get(module_id, [])
