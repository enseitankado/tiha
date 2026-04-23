# TiHA — Tahta İmaj Hazırlık Aracı

Sınıf etkileşimli tahtanızı (Pardus ETAP kurulu 65" dokunmatik ekran) **imaj alınmaya hazır** hâle getiren modern bir sihirbaz uygulaması.

İmajlı yayılan sistemlerde sık karşılaşılan parola gizliliği, çoklu OTP, tekil kimlik ve çakışan isim gibi sorunları tek bir sihirbazdan adım adım çözer. Her adımda ne yaptığını ve **neden yaptığını** açıklar, onayınızı alır ve gerektiğinde işlemi **geri alır**.

## Kimin için?

- BT koordinatörü öğretmenler
- Okul sistem sorumluları
- Tahta dağıtımı yapan teknik personel

Komut satırı bilgisi gerekmez. Tüm işlemler dokunmatik ekran uyumlu bir pencereden yapılır.

## Nasıl çalıştırılır? (Kurulum yok!)

1. Tahtada **Etap Yönetici** (`etapadmin`) hesabıyla oturum açın.
2. Uygulamalar menüsünden **Terminal**'i açın.
3. Aşağıdaki **tek satırı** kopyalayıp terminale yapıştırın, Enter'a basın:

```bash
curl -fsSL https://tiha.dev/r | bash
```

> **Not:** TiHA sisteme kalıcı olarak **kurulmaz**. Bu komut programı geçici bir klasöre indirir, bir kez çalıştırır ve siz kapatınca geçici dosyaları siler. Bir sonraki kullanımda aynı komutla en güncel sürüm çalıştırılır.

Sihirbaz açılırken bir defa `etapadmin` parolanızı isteyebilir.

## Nasıl kullanılır?

1. Sihirbaz açıldığında, **karşılama ekranında** tahtanızın donanım sürümü (Faz 1 / Faz 2 / Faz 3), markası, işletim sistemi sürümü ve çekirdeği özet olarak görünür.
2. Ardından **Donanım Ön Kontrol** adımı, tahtanın imaj alınmaya uygun olup olmadığını (ör. eta-register'ın çalışabilmesi için gerekli SMBIOS bilgileri) doğrular.
3. Her adım için:
   - Adımın **ne yaptığı** ve **neden gerekli olduğu** size açıklanır
   - **Uygula** ile onaylarsınız
   - Uzun süren işlemlerde (sistem güncellemesi gibi) ilerleme ekranda canlı olarak akar
   - Sonuç anlık olarak ekranda gösterilir
   - İsterseniz **Geri Al** ile o adımın yaptıklarını iptal edersiniz
4. Son ekranda tüm adımların özeti listelenir; herhangi birini oradan da geri alabilirsiniz.

## Ne yapar?

İmaj almadan **önce** şu adımları sırasıyla uygular:

0. **Donanım ön kontrol** — Hedef tahtanın SMBIOS `product_uuid` değerinin geçerli olup olmadığını, tahtanın sanal makine olarak algılanıp algılanmadığını, MAC ve machine-id gibi tekil kimliklerin durumunu raporlar. Hatalı olduğunda uyarı verir.
1. **Başlangıç parolaları** — `ogrenci`, `ogretmen` gibi standart kullanıcı hesaplarının parolalarını rastgele atayıp kilitler; `root` ve `etapadmin` için sizin belirlediğiniz güçlü parolaları tanımlar.
2. **Her açılışta parola temizliği** — Klonlanan tahtada sistem her açıldığında, `etapadmin` dışındaki kullanıcıların parolalarını yeniden rastgele atayan bir sistem servisi kurar. Böylece ekrana parola yazılarak ele geçirilmiş hesaplar bir sonraki açılışta geçersiz olur.
3. **OTP (Tek Kullanımlık Parola) hazırlığı** — Öğretmen listenizden TOTP güvenlik anahtarları (secret) üretir ve `/etc/otp-secrets.json` dosyasına kaydeder. İsteğe bağlı yedek hesaplar da oluşturulur; liste kopyalanabilir şekilde ekranda sunulur.
4. **Uzak erişim (SSH)** — SSH sunucusunu kurar ve teknik destek için `root` uzak bağlantısına izin verir.
5. **Dosya paylaşımı (Samba)** — Samba sunucusunu kurar ve dosya alışverişi için seçtiğiniz kullanıcıya (varsayılan `root`) tüm sisteme erişim verecek bir paylaşım oluşturur.
6. **Merkezi log** — Sistem kayıtlarını ağdaki merkezi log sunucusuna ileten yapılandırmayı kurar; böylece tüm tahtaların logları tek bir yerden izlenebilir.
7. **Zaman senkronizasyonu** — Saat dilimini ayarlar ve NTP sunucu(ları)nı tanımlar. Yanlış saat sertifika, OTP ve Kerberos doğrulamalarını bozduğundan kritik bir adımdır.
8. **Benzersiz bilgisayar adı (hostname)** — Aynı imajdan çıkan tahtaların ağda aynı isimle çakışmaması için otomatik benzersiz ad üretme stratejisi kurulur (MAC adresinden türetme).
9. **Sistem güncellemesi** — `apt update`, `apt upgrade` ve ilgili temizlikleri çalıştırır. Uzun sürebilir; ilerleme canlı akar.
10. **İmaj için sanitizasyon** — İmajlanan tahtanın her yerde temiz çalışması için gereken hijyeni yapar: `machine-id` sıfırlama, SSH host anahtarları silme, log temizleme, Lider-Ahenk/eta-register önbelleklerini silme, geçici dosyaları temizleme gibi. **Bu adım son adımdır.**

## Güvenlik ve şeffaflık

- Her adım **tam günlüklenir** (`/var/log/tiha/`).
- Her adımın öncesi yedeklenir, bu sayede **geri alma** mümkündür (bazı adımlarda geri alma fiziksel olarak anlamlı değildir; bunlar açıkça belirtilir).
- Parolalar ve OTP anahtarları yalnızca yerel disktedir, ağa gönderilmez.
- Uygulama yetkili (`etapadmin`) kullanıcı denetiminden geçer.

## Destek

- Hata bildirimi ve soru için: [Issues](https://github.com/ozgurkoca/tiha/issues)
- Proje sayfası: [github.com/ozgurkoca/tiha](https://github.com/ozgurkoca/tiha)

## Lisans

GPL-3.0 — Ayrıntı için `LICENSE` dosyasına bakınız.
