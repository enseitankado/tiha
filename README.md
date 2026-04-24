# TiHA — Tahta İmaj Hazırlık Aracı

> Pardus ETAP kurulu sınıf etkileşimli tahtalarını, **imaj yöntemiyle kopyalanıp** yüzlerce tahtaya dağıtılmaya hazır hâle getiren modern sihirbaz uygulaması.

![Durum](https://img.shields.io/badge/durum-alfa-orange) ![Platform](https://img.shields.io/badge/platform-Pardus%20ETAP%2023-blue) ![Lisans](https://img.shields.io/badge/lisans-GPL--3.0-green) ![Dil](https://img.shields.io/badge/dil-Türkçe-red)

---

## İçindekiler

- [TiHA nedir?](#tiha-nedir)
- [Hangi sorunu çözer?](#hangi-sorunu-çözer)
- [TiHA ne **değildir**?](#tiha-ne-değildir)
- [Neler yapar? (11 adım)](#neler-yapar-11-adım)
- [Nasıl çalıştırılır?](#nasıl-çalıştırılır)
- [Kullanım akışı](#kullanım-akışı)
- [Güvenlik ve geri alma](#güvenlik-ve-geri-alma)
- [Proje yapısı](#proje-yapısı)
- [Gereksinimler](#gereksinimler)
- [Katkı ve destek](#katkı-ve-destek)
- [Lisans](#lisans)

---

## TiHA nedir?

TiHA, Pardus ETAP 23 kurulu bir etkileşimli tahtada **etapadmin** kullanıcısı tarafından tek bir komutla çalıştırılabilen; tahtayı okullarda **imaj (disk kopyası) yöntemiyle seri şekilde dağıtmaya hazırlayan** bir hazırlık sihirbazıdır.

Kurulumu **yoktur** — sadece çalıştırılır. 11 adımdan oluşan görsel bir sihirbazla her işin ne yaptığı ve neden yapıldığı açıklanır, onayınız alınır, işlem uygulanır, sonuç paylaşılır ve istediğinizde geri alınabilir.

## Hangi sorunu çözer?

Sınıf ortamında öğretmen 65 inç dokunmatik tahtada **EBA-QR ile ilk oturum açışta** sistem tarafından kendisine bir **yerel parola tanımlaması** istenir. Öğretmen bu parolayı ekranda parmağıyla yazar; arkadaki sıralarda oturan öğrenciler ekranı rahatlıkla gördüğü için parolayı ezberler ve sonraki derslerde öğretmenin hesabıyla tahtayı açıp yetkisiz işlemler yapabilir.

> **Bu kabul edilemez bir güvenlik zafiyetidir** ve öğretmenden "öğrencisiz bir ortamda parola oluşturmasını" beklemek sürdürülebilir bir çözüm değildir.

**TiHA'nın asıl çözümü:** Parolanın ekrandan girilmesini imkânsız kılar. İmaj uygulandıktan sonra öğretmen hesapları **her sistem açılışında rastgele bir parola** ile otomatik olarak kilitlenir — bilinmeyen ve kullanılamaz hâle gelir. Öğretmen artık yalnızca üç yoldan birini kullanabilir:

| Yol | Açıklama |
| --- | --- |
| 🎯 **EBA-QR** | Telefonundaki EBA uygulamasından kare kodu okutarak (sunucu provizyonlu) |
| 🔢 **OTP** | Google Authenticator benzeri uygulamalarla 6 haneli PIN kodu |
| 🔑 **USB bellek** | Öğretmene özel hazırlanmış kişisel USB anahtar |

Bu ana amacın yanında TiHA; imajdan dağıtılan tahtaların sahada **ağ ve kimlik çakışmaları** yaşamaması için de bir dizi hazırlık yapar (benzersiz hostname, SSH host anahtarları, NetworkManager profilleri, machine-id vs.).

## TiHA ne **değildir**?

- ❌ **İmaj alma aracı değildir.** İmajı siz Clonezilla / dd / SystemRescue / Acronis gibi bir araçla alırsınız. TiHA yalnızca imaj alınmadan önceki hazırlığı yapar.
- ❌ **Pardus ETAP dağıtım medyası değildir.** Pardus ETAP'ı siz temiz kurulum olarak kurarsınız.
- ❌ **Uzaktan yönetim aracı değildir.** Lider-Ahenk bu işe bakar; TiHA Ahenk'in kurulumuna müdahale etmez.
- ❌ **OTP doğrulayıcı değildir.** Standart `eta-otp-lock` + PAM akışı kullanılır; TiHA yalnızca anahtarları toplu üretip `/etc/otp-secrets.json` dosyasına yazar.
- ❌ **Sisteme kurulmaz.** Geçici klasörde çalışır, kapanınca iz bırakmaz.

## Neler yapar? (11 adım)

Sihirbaz şu adımları sırasıyla uygular. Her biri opsiyonel olarak atlanabilir; ancak ana senaryo için hepsinin çalıştırılması tavsiye edilir.

| # | Adım | Kısa açıklama |
|---|------|----------------|
| 1  | **Donanım ön kontrol**          | SMBIOS `product_uuid`, VM tespiti, MAC adresi ve `machine-id` durumunu raporlar. Tahta imajlanmaya uygun mu? |
| 2  | **Başlangıç parolaları**         | `root` ve `etapadmin` parolalarını çift onaylı ve "göster/gizle" düğmeli biçimde alır; genel hesapları rastgele parolayla kilitler. |
| 3  | **Her açılışta parola temizliği**| Her boot'ta `ogretmen`, `ogrenci` ve kişisel öğretmen hesaplarının parolasını rastgele değere çeviren sistem servisi kurar. Ekrandan parola girişini kapatır. |
| 4  | **Öğretmen OTP anahtarları**     | Girdiğiniz öğretmen listesinden TOTP secret'leri üretir; `/etc/otp-secrets.json`'a yazar. Her hesap için `otpauth://` URL'si sunar (Google Authenticator vb. uyumlu). |
| 5  | **SSH sunucusu**                 | `openssh-server` kurar ve root uzak girişine izin verir — teknik bakım için. |
| 6  | **Samba dosya paylaşımı**        | Tahtanın kök `/` dizinini `\\tahta\root` adıyla yetkili bir SMB paylaşımı olarak sunar. |
| 7  | **Merkezi log iletimi**          | Tahtanın tüm sistem günlüklerini ağdaki merkezi bir `rsyslog` sunucusuna gönderir. |
| 8  | **Zaman senkronizasyonu (NTP)**  | `systemd-timesyncd` ile NTP sunucusu ve saat dilimi tanımlar. OTP kodlarının çalışması için kritik. |
| 9  | **Benzersiz hostname stratejisi**| İmaj için şablon hostname atar, ilk açılışta MAC'ten benzersiz isim üreten bir servis kurar. `/etc/hosts`'u da eşitler. |
| 10 | **Sistem güncellemesi (apt)**    | `apt update → full-upgrade → autoremove → clean`. Çıktı canlı olarak akar. |
| 11 | **İmaj için sanitizasyon**       | `machine-id`, SSH host anahtarları, NetworkManager bağlantı profilleri, loglar, kabuk geçmişi, önbellekler — imaj almadan önce temizlenir. Bu son adımdır. |

## Nasıl çalıştırılır?

TiHA'nın kurulumu yoktur. Aşağıdaki tek komut, kaynağı geçici bir klasöre indirip sihirbazı başlatır ve kapatıldığında geçici dosyaları siler.

### 1. Tahtada `etapadmin` olarak oturum açın.

### 2. Uygulamalar menüsünden **Terminal**'i açın.

### 3. Şu tek satırı kopyalayıp Enter'a basın:

```bash
curl -fsSL https://tiha.dev/r | bash
```

> **İpucu:** İlk çalıştırmada sistem `etapadmin` parolanızı bir kez ister (sudo oturumu). Sonraki komutlar bu oturumu kullanır.

### Eksik paketler

Komut, eksikse şu paketleri otomatik olarak kurar:

```
python3-gi  gir1.2-gtk-3.0  python3-pyotp  policykit-1  tar  curl
```

### Yerel / geliştirme modunda

Proje kaynağını klonladıysanız:

```bash
cd tiha
sudo -E PYTHONPATH=. python3 -m tiha
```

ya da bootstrap'ı yerel kaynakla:

```bash
TIHA_LOCAL_DIR=$(pwd) bash bootstrap.sh
```

## Kullanım akışı

1. **Karşılama ekranı** — TiHA'nın amacını, senaryoyu ve tespit ettiği donanımı özetler.
2. **Sol taraf: adım listesi** — Numaralı 11 adım. Mouse ya da dokunmatikle istenen adıma doğrudan atlanabilir.
3. **Sağ taraf: adım sayfası**
   - Başlık ve "neden gerekir" açıklaması
   - "Uyguladığında ne olacak?" önizlemesi
   - Parametre alanları (form: parola/metin/seçim/sayaç)
   - **Uygula** → spinner + "Uygulanıyor…" göstergesi → uzun adımlar için canlı çıktı akışı → yeşil/kırmızı sonuç kartı
   - Başarılıysa **Bu adımı geri al** düğmesi
4. **Alt: navigasyon çubuğu** — Geri / Uygula / İleri. Aksiyon çubuğu hep ekran altında sabittir.
5. **Özet ekranı** — Uygulanmış her adım kart hâlinde listelenir; oradan da tek tıkla geri alınabilir.

## Güvenlik ve geri alma

- **Tam günlükleme:** `/var/log/tiha/tiha.log` dosyasına yazılır.
- **Yedek defteri:** Her uygulanmış adım, parametreleri ve önceki durum özetiyle `/var/lib/tiha/journal.json`'a kaydedilir.
- **Tam restore:**
  - SSH ve Samba: Eğer TiHA paketi kurduysa, geri alırken `apt-get purge` ile kaldırır.
  - Hostname: Önceki hostname ve `/etc/hosts` yedeğe alınır, geri alınca aynen yüklenir.
  - Parolalar: `/etc/shadow` yedeği geri yüklenir.
  - OTP: Önceki `/etc/otp-secrets.json` geri yüklenir.
- **Onaylı silme:** 3. adımın geri alması, standart dışı hesap tespit ederse kullanıcıya **silme onayı** soran bir diyalog açar.
- **Şeffaflık:** Tüm kararlar, yedeklemeler ve hatalar kullanıcıya ekranda gösterilir.
- **Gizlilik:** Parolalar ve OTP anahtarları yalnızca yerel disktedir, ağa gönderilmez.

## Proje yapısı

```
tiha/
├── README.md                    Bu dosya
├── LICENSE                      GPL-3.0 lisansı
├── bootstrap.sh                 curl|bash ile çalışan başlatıcı
├── pyproject.toml               Python paket tanımı
├── requirements.txt             Çalışma zamanı bağımlılıkları
├── data/
│   └── styles.css               Etap temasıyla uyumlu CSS
├── docs/
│   └── images/                  Ekran görüntüleri (ileride)
└── tiha/                        Python paketi
    ├── __main__.py              `python -m tiha` giriş noktası
    ├── app.py                   Uygulama başlatıcı, yetki denetimi
    ├── core/                    Ortak altyapı
    │   ├── board.py             Donanım ve dağıtım tespiti
    │   ├── logger.py            Dosya + ekran günlükleyici
    │   ├── module.py            Tüm sihirbaz modüllerinin tabanı
    │   ├── paths.py             Sistem yol sabitleri
    │   ├── privilege.py         root / etapadmin denetimi
    │   ├── undo.py              Günce (journal) ve geri alma defteri
    │   └── utils.py             Komut çalıştırma, yedekleme, rastgele parola
    ├── modules/                 Sihirbaz adımları (m00–m10)
    │   ├── m00_precheck.py
    │   ├── m01_initial_passwords.py
    │   ├── m02_boot_password_wipe.py
    │   ├── m03_otp_secrets.py
    │   ├── m04_ssh_server.py
    │   ├── m05_samba_share.py
    │   ├── m06_remote_syslog.py
    │   ├── m07_time_sync.py
    │   ├── m08_hostname.py
    │   ├── m09_system_update.py
    │   └── m10_image_sanitize.py
    └── ui/                      GTK3 arayüzü
        ├── main_window.py       Ana pencere, kenar çubuğu, navigasyon
        ├── pages.py             Karşılama, Modül, Özet sayfaları
        └── params.py            Modül başına form şemaları
```

### Yeni modül nasıl eklenir?

1. `tiha/modules/` altına `mNN_adı.py` oluşturun.
2. `Module`'dan türeyin, `id`, `title`, `rationale` doldurun.
3. `apply()` ve (destekliyorsa) `undo()` yazın.
4. `tiha/modules/__init__.py` içine ekleyin ve sıralamada doğru yere koyun.
5. Form alanı gerekiyorsa `tiha/ui/params.py` içine şemasını yazın.
6. Test: `PYTHONPATH=. python3 -c "from tiha.modules import all_modules; print(all_modules())"`

## Gereksinimler

- **Platform:** Pardus ETAP GNU/Linux 23 (Debian 12 bookworm tabanlı)
- **Kullanıcı:** `etapadmin` (sudo yetkili)
- **Python:** 3.11+
- **GTK:** 3.24+
- **Ek paketler:** `python3-pyotp`, `python3-gi`, `gir1.2-gtk-3.0`, `policykit-1`

Bu bağımlılıklar `bootstrap.sh` içinde eksikse otomatik kurulur.

## Katkı ve destek

- 🐛 Hata bildirimi: [GitHub Issues](https://github.com/enseitankado/tiha/issues)
- 💬 Soru/tartışma: [GitHub Discussions](https://github.com/enseitankado/tiha/discussions)
- 🤝 Pull request'ler hoş karşılanır — tercihen önce bir issue açıp yaklaşımı konuşalım.

**Yazım kuralları:** Kullanıcıya görünen metinler Türkçe, kod kimlikleri İngilizce (PEP-8), kod içi yorumlar Türkçe ve anlamlı. Modül başlıkları tutarlı ve `neden gerekli` açıklaması birinci sınıf vatandaş.

## Lisans

GPL-3.0 — ayrıntı için [`LICENSE`](LICENSE) dosyasına bakınız.

Copyright © 2026 Özgür Koca
