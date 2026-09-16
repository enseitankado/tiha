"""Modül başına kullanıcıdan alınacak parametre şemaları.

Alan tipleri: ``text``, ``password``, ``number``, ``textarea``, ``select``.
"""

from __future__ import annotations

PARAMS_SCHEMA: dict[str, list[dict]] = {
    "m01_initial_passwords": [
        {
            "key": "root_password",
            "label": "Yeni root parolası (isteğe bağlı)",
            "type": "password",
            "show_toggle": True,
            "strength_below": True,
            "required": False,
            "help": "Teknik ekibin tahtaya yönetici olarak bağlanacağı parola. Boş bırakılabilir.",
        },
        {
            "key": "admin_password",
            "label": "Yeni etapadmin parolası (isteğe bağlı)",
            "type": "password",
            "show_toggle": True,
            "strength_below": True,
            "required": False,
            "help": "Tahtada yerel yönetim işleri için kullanılacak parola. Boş bırakılabilir.",
        },
        {
            "key": "teacher_password",
            "label": "Öğretmen parolası (isteğe bağlı)",
            "type": "password",
            "show_toggle": True,
            "strength_below": True,
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
            "label_from": "label_purge_all_secrets",
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
            "label_from": "label_remove_extra_users",
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
            "default": "tcp",
            "options": ["tcp", "udp"],
        },
        {
            "key": "log_profile",
            "label": "Log profili",
            "type": "select",
            "required": False,
            "default": "Bakım (önerilen)",
            "options": [
                "Bakım (önerilen)",
                "Kapsamlı",
                "Yalnız güvenlik",
            ],
            "help": (
                "Merkezi sunucuya iletilecek olayların kapsamını seçin. "
                "Bakım: donanım uyarıları (kern.warning), servis "
                "hataları/notice (daemon.notice), kimlik doğrulama "
                "(auth/authpriv), TiHA + Ahenk servisleri (local0-7). "
                "Öğretmen davranışı ve tarayıcı içeriği iletilmez. "
                "Kapsamlı: her mesaj gönderilir; ayıklama için, kalıcı "
                "olarak açık tutmayın. Yalnız güvenlik: minimum trafik, "
                "sadece kimlik ve kritik hatalar."
            ),
        },
        {
            "key": "install_smart_monitoring",
            "label": "Disk sağlığı + sıcaklık izleme paketlerini kur",
            "type": "bool",
            "required": False,
            "default": "True",
            "help": (
                "İşaretlenirse smartmontools (SMART disk izleme) ve "
                "lm-sensors (sıcaklık okuma) paketleri kurulur; smartd "
                "servisi etkinleştirilir ve sensors-detect otomatik "
                "çalıştırılır. Bu araçlar 'daemon' facility'sine log "
                "yazar; Bakım veya Kapsamlı profil seçilmişse "
                "otomatik olarak merkezi sunucuya iletilir. Böylece "
                "disk arızası ve aşırı ısınma erkenden görünür."
            ),
        },
        {
            "key": "install_node_exporter",
            "label": "Metrik izleme",
            "type": "bool",
            "required": False,
            "default": "False",
            "help": (
                "İşaretlenirse prometheus-node-exporter paketi kurulup "
                "9100 numaralı porta bağlanır. Merkezi Prometheus "
                "sunucusu buradan CPU, RAM, disk, sıcaklık, ağ ve boot "
                "metriklerini toplar; Grafana panosunda onlarca tahtanın "
                "sağlığı tek ekrandan izlenir."
            ),
        },
        {
            "key": "node_exporter_listen",
            "label": "Metrik dinleme adresi",
            "type": "text",
            "required": False,
            "default": ":9100",
            "enable_when_field": "install_node_exporter",
            "help": (
                "Varsayılan ':9100' — tüm ağ arayüzlerinde dinler; "
                "Prometheus scrape'i yapar. Güvenlik sıkı istenirse "
                "'127.0.0.1:9100' yazın (sadece localhost dinler; "
                "Prometheus ile arada SSH tünel gerekir)."
            ),
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
    "m15_wake_on_lan": [
        {
            "key": "enable_wol_listen",
            "label": "Ağ kartını magic packet dinleme moduna al",
            "type": "bool",
            "required": False,
            "default": "False",
            "help": (
                "İşaretlenirse imaja bir systemd servisi gömülür. Her "
                "boot'ta bu servis birincil ağ kartını Wake-on-LAN "
                "dinleme moduna alır (ethtool wol g). Bilgisayar "
                "kapatıldığında kart magic packet dinlemede kalır; "
                "merkez bilgisayardan 'wakeonlan <MAC>' komutuyla tahta "
                "uzaktan açılabilir. Ayar kalıcı olmadığı için her "
                "boot'ta yeniden yazılır. İşaretli değilse hiçbir "
                "servis kurulmaz ve tahta uzaktan uyandırılamaz."
            ),
        },
    ],
    "m16_grub_protection": [
        {
            "key": "enable_grub_lock",
            "label": "GRUB koruması etkin",
            "type": "bool",
            "required": False,
            "default": "False",
            "help": (
                "İşaretlenirse GRUB önyükleme menüsünde 'e' (düzenle) "
                "kipine girildiğinde ya da GRUB shell'ine ('c' tuşu) "
                "düşüldüğünde aşağıdaki 'GRUB yönetici parolası' "
                "alanına yazılan parola sorulur. Recovery girdisi de "
                "menüden kaldırılır. Boot akışının kendisi bu "
                "parolayı sormaz; yalnız menüye elle müdahale eden "
                "kişi görür.\n\n"
                "Neden gerekli? GRUB varsayılan olarak fiziksel "
                "klavye erişimi olan herkese kernel komut satırını "
                "düzenleme hakkı verir. Buraya 'init=/bin/bash' "
                "yazılırsa sistem doğrudan root shell açar; oradan "
                "da parola değiştirmek, diski okumak, tahtayı kalıcı "
                "olarak ele geçirmek mümkündür. Bu kutucuk o vektörü "
                "tek bir hash ile tüm klonlarda kapatır.\n\n"
                "Nasıl çalışır? Adım, aşağıya yazdığınız parolanın "
                "PBKDF2-SHA512 hash'ini /etc/grub.d/01_tiha_grub_password "
                "içine yazar; /etc/grub.d/10_linux'ta menü girdilerine "
                "--unrestricted bayrağı ekler (menü seçilirken parola "
                "sorulmasın diye); /etc/default/grub'da "
                "GRUB_DISABLE_RECOVERY=\"true\" yapar; update-grub "
                "çalıştırır. Hash imaja gömüldüğü için aynı parola tüm "
                "klonlarda geçerli olur; düz parola sistemde tutulmaz."
            ),
        },
        {
            "key": "grub_password",
            "label": "GRUB yönetici parolası",
            "type": "password",
            "show_toggle": True,
            "required": False,
            "default": "",
            "enable_when_field": "enable_grub_lock",
            "help": (
                "GRUB menü kipine ('e' tuşu) ya da GRUB shell'ine "
                "('c' tuşu) girmeye çalışan kullanıcıdan istenecek "
                "parola. Bu adımda girdiğiniz metnin PBKDF2-SHA512 "
                "hash'i /etc/grub.d/01_tiha_grub_password içine "
                "yazılır ve klon imajına gömülür; düz parola "
                "sistemde tutulmaz. Kutucuk işaretsizken bu alan "
                "pasiftir. Aynı parolayı bütün klonlar kullanır — "
                "operatörün hatırlaması gereken tek bir parola olur."
            ),
        },
    ],
}


def get(module_id: str) -> list[dict]:
    return PARAMS_SCHEMA.get(module_id, [])
