"""Modül başına kullanıcıdan alınacak parametre şemaları.

Alan tipleri: ``text``, ``password``, ``number``, ``textarea``, ``select``,
``bool``, ``spin``, ``button``, ``file``, değeri modülden gelen ve
kullanıcının değiştiremediği ``readonly`` (değerini ``default_from``
sağlar) ve değer taşımayan bölüm başlığı ``heading``. ``bool`` alanı
``enables`` listesi taşıyabilir: kutu işaretsizken listedeki alanlar
pasifleşir. Ek olarak ``deselects`` (bu kutu işaretlenince
listedeki kutuların işareti kaldırılır) ve simetriği ``selects``
(bu kutu işaretlenince listedeki ön-koşul kutuları da otomatik
işaretlenir) bayrakları da desteklenir.
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
            "key": "reserve_count",
            "label": "Yedek hesap sayısı",
            "type": "spin",
            "required": False,
            "default": "0",
            # Sistemde ogretmen01 … ogretmenNN varsa kutu NN ile dolu
            # gelsin; adım yeniden uygulandığında yönetici farkında
            # olmadan yeni hesap açmaz.
            "default_from": "suggested_reserve_count",
            "min": 0,
            "max": 999,
            "step": 1,
            "help": (
                "Sonradan okula atanacak öğretmenler için ogretmen1, "
                "ogretmen2 … biçiminde boş hesaplar hazırlar. Her yedek "
                "hesap için ev dizini açılır (useradd), hesap EBA QR / "
                "eta-usb-login ile aynı standart cihaz gruplarına (ses, "
                "USB, kamera, yazıcı vb.) eklenir ve parola kilitli "
                "tutulur. Eski kurulumlardan kalma ogretmen01 / "
                "ogretmen.1 biçimindeki hesaplar da mevcut sayılır — "
                "üzerine yazılmaz. İmaj alındığında bu hesaplar tüm "
                "klon tahtalara birlikte gider. PIN anahtarları için "
                "\"Öğretmen PIN anahtarları\" adımı gerekir; o adım bu "
                "hesapları da otomatik olarak PIN üretim listesine "
                "ekler."
            ),
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
            # PAM grup-PIN'i yalnız gruba üye kullanıcılara kabul ediyor;
            # bu bayrak işaretlenince "Öğretmen hesaplarını ogretmenler
            # grubuna ekle" kutusu da otomatik işaretlenir, kullanıcı bağı
            # UI'dan görür.
            "selects": ["add_teachers_to_group"],
            "help": (
                "İşaretlenirse ogretmenler grubuna özel bir '@ogretmenler' "
                "PIN anahtarı üretilir (eta-otp-lock @grup mekanizması). "
                "Bu ortak PIN, gruba üye tüm hesaplara giriş için "
                "kullanılabilir; PIN kâğıdında ayrı bir 'ORTAK PIN' kartı "
                "olarak çıkar. Zaten bir ortak PIN varsa korunur, "
                "yenilenmez. Tek tek öğretmen PIN'lerinden daha zayıf bir "
                "izdir (herkes aynı kodu kullanır), bu yüzden varsayılan "
                "olarak kapalıdır.\n\n"
                "ÖN KOŞUL: PAM grup PIN'ini yalnız 'ogretmenler' grubunun "
                "ÜYESİ olan hesaplara kabul eder. Bu kutu işaretlenince "
                "'Öğretmen hesaplarını ogretmenler grubuna ekle' kutusu "
                "da otomatik olarak işaretlenir — aksi halde üretilen "
                "ortak PIN hiçbir hesapta çalışmayacağı için. Manuel "
                "olarak geri kaldırmayın; iki bayrak birlikte çalışır."
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
            "default_from": "auto_shutdown_active",
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
            "default_from": "idle_shutdown_active",
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
        {
            "key": "countdown_seconds",
            "label": "Geri sayım süresi (saniye)",
            "type": "spin",
            "required": False,
            "default": "120",
            # Yüklü service.py'de zaten bir COUNTDOWN_SECONDS varsa
            # kutu o değerle açılsın — kullanıcı farkında olmadan
            # eski süreyi yeniden yazmaz.
            "default_from": "suggested_countdown_seconds",
            "min": 30,
            "max": 600,
            "step": 10,
            "help": (
                "Sabit saat veya idle tabanlı kapatma tetiklendiğinde "
                "\"Kapanıyor…\" penceresinin ekranda kalma süresi. "
                "Bu süre boyunca kullanıcı 10 dakika erteleyebilir ya da "
                "pencerenin sağ üst X'ine basıp idle sayacını "
                "sıfırlayabilir. Alt sınır 30 saniye (kullanıcı "
                "pencereyi görüp tepki verebilsin), üst sınır 600 "
                "saniye (10 dakika)."
            ),
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
                "oturumlarına dokunulmaz; root hariç tutulur. Bu ayar "
                "system-wide logind yapılandırmasıdır; klon tahtaya sonradan "
                "eklenen hesaplar dahil, root dışındaki tüm kullanıcılara "
                "otomatik uygulanır."
            ),
        },
        {"label": "ETA Hafif Mod (eta-light-mode)", "type": "heading"},
        {
            "key": "light_mode_enabled",
            "label": "Hafif modu tüm kullanıcılara uygula",
            "type": "bool",
            "default": "False",
            "default_from": "light_mode_active",
            "enables": [
                "lm_effects", "lm_compositor", "lm_thumbnails",
                "lm_directory_counts", "lm_app_monitoring",
                "lm_low_resolution", "lm_low_refresh_rate",
            ],
            "help": (
                "Paket yoksa kurulur. Seçilen ayarlar system-wide olarak "
                "/etc/eta-light-mode/settings.json'a ve /etc/xdg/autostart'a "
                "yazılır; her kullanıcıya (klon tahtaya sonradan eklenen "
                "hesaplar dahil) oturum açılışında uygulanır. Kullanıcı "
                "kendi oturumunda değiştirse de bir sonraki girişte yeniden "
                "uygulanır. Kutu sistemin o anki durumunu gösterir: "
                "işaretliyken kaldırıp uygularsanız hafif mod sistemden "
                "kaldırılır ve daha önce giriş yapmış hesapların masaüstü "
                "ayarları da geri alınır. Paket kaldırılmaz."
            ),
        },
        {
            "key": "lm_effects",
            "label": "Pencere ve menü animasyonlarını kapat",
            "type": "bool",
            "default": "True",
            "default_from": "lm_effects_active",
            "help": "Menü, pencere ve diyaloglar daha çabuk açılır hissi verir.",
        },
        {
            "key": "lm_compositor",
            "label": "Tam ekran pencereleri doğrudan çiz",
            "type": "bool",
            "default": "True",
            "default_from": "lm_compositor_active",
            "help": "Tam ekran video ve sunumlarda yükü azaltır; ekran yırtılması görülebilir.",
        },
        {
            "key": "lm_thumbnails",
            "label": "Resim ve video önizlemelerini kapat",
            "type": "bool",
            "default": "True",
            "default_from": "lm_thumbnails_active",
            "help": "Çok dosyalı USB/klasör açılışındaki yoğun işlemci ve disk yükünü kaldırır.",
        },
        {
            "key": "lm_directory_counts",
            "label": "Klasör öğesi sayımını kapat",
            "type": "bool",
            "default": "True",
            "default_from": "lm_directory_counts_active",
        },
        {
            "key": "lm_app_monitoring",
            "label": "Uygulama kullanım izlemesini kapat",
            "type": "bool",
            "default": "True",
            "default_from": "lm_app_monitoring_active",
        },
        {
            "key": "lm_low_resolution",
            "label": "Çözünürlüğü 1600x900'e düşür",
            "type": "bool",
            "default": "False",
            "default_from": "lm_low_resolution_active",
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
            "default_from": "lm_low_refresh_rate_active",
            "help": "Hareketli içerikte yükü ~%17 azaltır; kalem gecikmesi ~3 ms artar.",
        },
        {"label": "Fare imleci (ekran modu değişimi)", "type": "heading"},
        {
            "key": "cursor_xorg_fix",
            "label": "İmleç kaybolmasını önle (Xorg)",
            "type": "select",
            "required": False,
            "default": "Kapalı",
            "options": [
                "Kapalı",
                "modesetting sürücüsüne geç",
                "modesetting + yazılımsal imleç (SWcursor)",
            ],
            "help": (
                "Tahtada çözünürlük ya da tazeleme frekansı değiştirilip "
                "uygulandığında fare imleci görünmez oluyor; tıklama ve odak "
                "çalışmaya devam ediyor, fare çıkarılıp takılınca düzeliyor. "
                "Nedeni, mod değişiminde donanımsal imleç düzleminin yeniden "
                "kurulurken imleç görüntüsünü geri yüklememesi.\n\n"
                "'modesetting sürücüsüne geç': /etc/X11/xorg.conf.d/"
                "20-tiha-imlec.conf yazılır; ETAP'ta kurulu gelen eski intel "
                "sürücüsü yerine çekirdeğin modesetting sürücüsü kullanılır. "
                "Başarım bedeli yoktur; önce bunu deneyin.\n\n"
                "'+ yazılımsal imleç': aynı dosyaya Option \"SWcursor\" \"on\" "
                "eklenir; donanımsal imleç tamamen kapanır, kaybolacak düzlem "
                "kalmaz. Kesin çözümdür ama imleci her karede sistem çizer; "
                "zayıf tahtalarda hızlı fare hareketinde hafif gecikme "
                "görülebilir.\n\n"
                "Değişiklik oturum (LightDM) yeniden başlayınca geçerli olur."
            ),
        },
        {
            "key": "cursor_refresh_service",
            "label": "Mod değişiminde imleci tazele (servis)",
            "type": "bool",
            "default": "False",
            "help": (
                "Yukarıdaki Xorg düzeltmesine alternatif, hafif yol: her "
                "kullanıcının oturumunda küçük bir servis çalışır, Muffin'in "
                "MonitorsChanged sinyalini dinler ve mod değişiminden ~1 sn "
                "sonra cursor-size değerini bir artırıp geri alarak imleci "
                "yeniden çizdirir. Donanımsal imleç korunur, başarım bedeli "
                "yoktur; imleç yalnız bir an kaybolur. İkisini birlikte de "
                "seçebilirsiniz."
            ),
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
            # Kutucuk adıma girildiğinde sistemin gerçek durumunu
            # göstersin: GRUB zaten korumalıysa işaretli açılır.
            "default_from": "lockdown_active",
            "help": (
                "Kutucuk, adıma girildiğinde sistemin o anki durumunu "
                "gösterir; GRUB zaten korumalıysa işaretli gelir. "
                "İŞARETİ KALDIRIP UYGULARSANIZ koruma kaldırılır: "
                "01_tiha_grub_password silinir, 10_linux'teki TiHA "
                "yamaları sökülür, update-grub çalıştırılır; menü, "
                "kurtarma girdisi dahil, parolasız hâline döner. Koruma "
                "etkinken parola alanını boş bırakıp uygularsanız mevcut "
                "parola korunur.\n\n"
                "İşaretlenirse GRUB önyükleme menüsünde 'e' (düzenle) "
                "kipine girildiğinde ya da GRUB shell'ine ('c' tuşu) "
                "düşüldüğünde GRUB önce KULLANICI ADI sorar — aşağıdaki "
                "salt okunur 'GRUB kullanıcı adı' kutusundaki değer — "
                "ardından 'GRUB yönetici parolası' alanına yazılan "
                "parolayı ister. Kurtarma (recovery) girdisi menüde "
                "kalır; onu ve 'Gelişmiş seçenekler' alt menüsünü açmak "
                "da aynı kullanıcı adı ve parolayı ister (kurtarma kipi "
                "parolasız root kabuğu verdiği için). Boot akışının "
                "kendisi bu parolayı sormaz; yalnız menüye elle müdahale "
                "eden kişi görür.\n\n"
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
                "sorulmasın diye), kurtarma girdilerini bu bayraktan muaf "
                "tutar ve alt menü girdilerinin açılış varsayılanı olarak "
                "kaydedilmesini engeller (gözetimsiz açılış parola "
                "ekranında beklemesin); update-grub çalıştırıp üretilen "
                "menüyü denetler. TiHA'nın eski sürümü kurtarma girdisini "
                "GRUB_DISABLE_RECOVERY ile kapatıyordu; bu adım onu geri "
                "açar. Hash imaja gömüldüğü için aynı parola tüm "
                "klonlarda geçerli olur; düz parola sistemde tutulmaz."
            ),
        },
        {
            "key": "grub_username",
            "label": "GRUB kullanıcı adı",
            "type": "readonly",
            "required": False,
            # Tek kaynak modüldeki SUPERUSER sabiti.
            "default_from": "superuser_name",
            "help": (
                "GRUB, parolayı sormadan önce bir kullanıcı adı ister "
                "('Enter username:'). Açılış ekranında bu kutudaki adı "
                "yazacaksınız. Değer adımın kendisi tarafından belirlenir, "
                "değiştirilemez; GRUB'ın kendi kullanıcı listesinde tanımlı "
                "bir addır, sistemdeki etapadmin hesabıyla ve onun "
                "parolasıyla ilgisi yoktur."
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
                "parola. GRUB ekranında önce 'Enter username:' çıkar; "
                "oraya yukarıdaki 'GRUB kullanıcı adı' kutusundaki ad "
                "yazılır, sonra bu parola girilir. "
                "Bu adımda girdiğiniz metnin PBKDF2-SHA512 "
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
