# TiHA — Tahta İmaj Hazırlık Aracı

Sınıf etkileşimli tahtanızı (Pardus ETAP kurulu 65" dokunmatik ekran) **imaj alınmaya hazır** hâle getiren modern bir sihirbaz uygulaması.

İmajlı yayılan sistemlerde sık karşılaşılan parola gizliliği, çoklu OTP, tekil kimlik ve çakışan isim gibi sorunları tek bir sihirbazdan adım adım çözer. Her adımda ne yaptığını ve **neden yaptığını** açıklar, onayınızı alır ve gerektiğinde işlemi **geri alır**.

## Bu araç kim için?

- BT koordinatörü öğretmenler
- Okul sistem sorumluları
- Tahta dağıtımı yapan teknik personel

Komut satırı bilgisi gerekmez. Tüm işlemler dokunmatik ekran uyumlu bir pencereden yapılır.

## Kurulum

1. Tahtada **Etap Yönetici** (`etapadmin`) hesabıyla oturum açın.
2. Uygulamalar menüsünden **Terminal**'i açın.
3. Aşağıdaki tek satırı kopyalayıp terminale yapıştırın ve Enter'a basın:

```bash
curl -fsSL https://raw.githubusercontent.com/ozgurkoca/tiha/main/install.sh | sudo bash
```

Kurulum bittikten sonra uygulamanız menüye eklenir ve terminalden `tiha` komutuyla da başlatılabilir.

## Nasıl kullanılır?

1. **TiHA Sihirbazı**'nı başlatın.
2. Karşılama ekranında tahtanızın donanım sürümü (Faz 1 / Faz 2 / Faz 3), markası, işletim sistemi sürümü ve çekirdeği özet olarak görünür.
3. **Sonraki** diyerek adımlara başlayın.
4. Her adım için:
   - Adımın **ne yaptığı** ve **neden gerekli olduğu** size açıklanır
   - **Uygula** ile onaylarsınız
   - Sonuç anlık olarak ekranda gösterilir
   - İsterseniz **Geri Al** ile o adımın yaptıklarını iptal edersiniz
5. Son ekranda tüm adımların özeti listelenir, dilerseniz herhangi birini oradan da geri alabilirsiniz.

## Ne yapar?

İmaj almadan **önce** şu adımları sırasıyla uygular:

1. **Başlangıç parolaları** — `ogrenci`, `ogretmen` gibi standart kullanıcı hesaplarının parolalarını rastgele atayıp kilitler; `root` ve `etapadmin` için sizin belirlediğiniz güçlü parolaları tanımlar.
2. **Her açılışta parola temizliği** — Klonlanan tahtada sistem her açıldığında, `etapadmin` dışındaki kullanıcıların parolalarını yeniden rastgele atayan bir sistem servisi kurar. Böylece ekrana parola yazılarak ele geçirilmiş hesaplar bir sonraki açılışta geçersiz olur.
3. **OTP (Tek Kullanımlık Parola) hazırlığı** — Öğretmen listenizden TOTP güvenlik anahtarları (secret) üretir ve `/etc/otp-secrets.json` dosyasına kaydeder. Her öğretmenin kendi anahtarı üretici uygulamasıyla (Google Authenticator, Authy vb.) tahtada oturum açmasına imkân verir. İsterseniz sonradan okula tayin olacak öğretmenler için yedek hesaplar da üretir; liste kopyalanabilir şekilde ekranda sunulur.
4. **Uzak erişim (SSH)** — SSH sunucusunu kurar ve teknik destek için `root` uzak bağlantısına izin verir.
5. **Dosya paylaşımı (Samba)** — Samba sunucusunu kurar ve dosya alışverişi için seçtiğiniz kullanıcıya (varsayılan `root`) tüm sisteme erişim verecek bir paylaşım oluşturur.
6. **Merkezi log** — Sistem kayıtlarını ağdaki merkezi log sunucusuna ileten yapılandırmayı kurar; böylece tüm tahtaların logları tek bir yerden izlenebilir.
7. **Benzersiz bilgisayar adı (hostname)** — Aynı imajdan çıkan tahtaların ağda aynı isimle çakışmaması için otomatik benzersiz ad üretme stratejisi kurulur (MAC adresinden türetme, seri numarası, vb.).
8. **Sistem güncellemesi** — `apt update`, `apt upgrade` ve ilgili temizlikleri çalıştırır.
9. **İmaj için sanitizasyon** — İmajlanan tahtanın her yerde temiz çalışması için gereken hijyeni yapar: `machine-id` sıfırlama, SSH host anahtarları silme, log temizleme, Lider-Ahenk/eta-register önbelleklerini silme, geçici dosyaları temizleme gibi.

## Güvenlik ve şeffaflık

- Her adım **tam günlüklenir** (`/var/log/tiha/`).
- Her adımın öncesi yedeklenir, bu sayede **geri alma** mümkündür.
- Parolalar ve OTP anahtarları yalnızca yerel disktedir, ağa gönderilmez.
- Uygulama yetkili (`etapadmin`) kullanıcı denetiminden geçer.

## Destek

- Hata bildirimi ve soru için: [Issues](https://github.com/ozgurkoca/tiha/issues)
- Proje sayfası: [github.com/ozgurkoca/tiha](https://github.com/ozgurkoca/tiha)

## Lisans

GPL-3.0 — Ayrıntı için `LICENSE` dosyasına bakınız.
