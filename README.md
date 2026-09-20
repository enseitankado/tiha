# TiHA — Tahta İmaj Hazırlık Aracı

> Bir sınıf tahtasını baştan sona hazırlayıp imajını alın, o imajı okuldaki bütün tahtalara dağıtın. TiHA bu hazırlığın sıkıcı ve kolay unutulan kısımlarını sizin yerinize, doğru sırayla yapar.

![Durum](https://img.shields.io/badge/durum-alfa-orange) ![Platform](https://img.shields.io/badge/platform-Pardus%20ETAP%2023-blue) ![Lisans](https://img.shields.io/badge/lisans-GPL--3.0-green) ![Dil](https://img.shields.io/badge/dil-Türkçe-red)

---

## TiHA nedir?

Okuldaki tahtaları tek tek elden geçirmek yerine, bir tahtayı örnek olarak hazırlarsınız: parolalar, öğretmen PIN'leri, ağ ayarları, güvenlik… Sonra o tahtanın imajını alıp diğerlerine yazarsınız. Sorun şurada: imajdan çıkan tahtaların hepsi birbirinin tıpatıp kopyasıdır. Aynı ada, aynı kimliğe ve aynı sırlara sahip oldukları için merkezi yönetim onları ayırt edemez, kayıtlar karışır.

TiHA hem bu hazırlığı tek bir pencerede toparlar hem de klonların birbirine karışmasını önler. Her tahta ilk açılışında kendi adını ve kimliğini alır.

Sihirbaz her adımda ne yapacağını ve neden yaptığını anlatır, onayınızı alır, sonucu gösterir. Adımların çoğu **geri alınabilir**. Tahtaya kurulmaz; çalıştırdığınızda açılır, kapattığınızda gider.

## Nasıl çalıştırılır?

1. Tahtada **Etap Yönetici** (`etapadmin`) hesabıyla oturum açın.
2. Uygulamalar menüsünden **Terminal**'i açın.
3. Aşağıdaki komutu **kopyalayıp** terminale **yapıştırın** ve **Enter**'a basın:

```bash
curl -fsSL https://raw.githubusercontent.com/enseitankado/tiha/main/bootstrap.sh | bash
```

İlk çalıştırmada bir kez etapadmin parolanızı sorabilir. Sonrası tamamen görseldir.

## Baştan bilmeniz gerekenler

- **Adımlar isteğe bağlıdır.** Soldaki listeden istediğinize geçebilir, istemediğinizi atlayabilirsiniz. Yalnız sıraya sadık kalmak işinizi kolaylaştırır.
- **"İmaj öncesi temizlik" en sona bırakılır.** O adım geri alınamaz ve tahtayı "imaj alınmaya hazır" hâle getirir. Sonrasında başka bir adım uygularsanız temizliği tekrarlamanız gerekir.
- **Temizlikten sonra tahtayı açmayın.** Kapatın ve imajı canlı USB'den alın. Açarsanız tahta kendine yeni kimlikler üretir ve bunlar bütün klonlara kopyalanır.
- **Parolalar bütün klonlara gider.** Burada belirlediğiniz root, etapadmin, Samba ve BIOS parolaları imajdaki her tahtada aynıdır. Güçlü seçin ve kimlerle paylaştığınıza dikkat edin.
- **En az bir klonda deneyin.** Gözden kaçan bir ayrıntıyı, imajı yazdığınız tahta sayısı kadar ayrı ayrı düzeltmek zorunda kalırsınız. Son adımdaki Özet sayfası tam olarak bunun için bir kontrol listesi hazırlar.
- **Öğretmen tahtaya ilk kez EBA QR ile girer.** Öğretmenin tahtadaki kişisel hesabı ancak ilk QR girişinde oluşur; PIN ve USB ile giriş ondan sonra çalışır. İnternet yoksa ya da QR çalışmıyorsa öğretmenin elinde yalnız ortak hesap kalır — bu yüzden ortak hesaba bir PIN tanımlamak ya da parolasını paylaşmak gerekir.

## Adımlar

| # | Adım | Ne işe yarar | Dikkat |
|---|------|--------------|--------|
| 1 | **Sistem güncellemesi** | Tahtadaki paketleri imaj alınmadan önce günceller. Depo ayarlarında bozukluk varsa önce onu onarır. | İnternet gerekir ve uzun sürebilir. Güncelleme sonrası bir şey ters giderse bu, imajdaki bütün tahtalara gider; güncellemeden sonra tahtayı bir süre kullanıp denemekte fayda var. |
| 2 | **Yerel hesaplar** | root, etapadmin ve ortak öğretmen hesabının parolasını siz belirlersiniz. Öğrenci hesabını silebilir, ileride okula gelecek öğretmenler için `ogretmen1`, `ogretmen2` gibi hazır yedek hesaplar açabilirsiniz. | Parola değişince o hesabın kayıtlı parolaları (kablosuz ağ, tarayıcı) sıfırlanır; TiHA bunu kendisi hallederek giriş ekranında takılmanızı önler. Yedek hesaplar parolasız açılır, yalnız PIN ya da QR ile girilir — PIN üretmek için 4. adım gerekir. Boş bıraktığınız parola alanına dokunulmaz. |
| 3 | **Otomatik parola temizliği** | Tahta her açıldığında etapadmin dışındaki hesapların parolasını geçersiz kılar. Sınıfta tahtaya yazılan bir parolanın kalıcı olmasını engeller. | Bu adımdan sonra o hesaplara **parolayla giriş yapılamaz**; yalnız EBA QR, PIN ya da USB ile girilir. Öğretmenlerin PIN'i hazır değilse tahtaya giremezler; önce 4. adımı tamamlayın. etapadmin'e dokunulmaz, yönetici erişiminiz her zaman durur. |
| 4 | **Toplu pin anahtarı** | Öğretmenlerin tahtaya girerken kullanacağı 6 haneli PIN kodlarını üretir ve imaja gömer. Her öğretmene yazdırılabilir bir kâğıt (QR kodlu) hazırlar. | Kâğıtlar ve anahtarlar gizlidir; öğretmenlere özelden teslim edin. Daha önce üretilmiş anahtarlara dokunulmaz, yani adımı yeniden uygulamak öğretmenlerin telefonundaki kaydı bozmaz. "Ortak PIN" seçeneği gruptaki herkese aynı kodu verir; tek tek PIN'lerden daha zayıftır. Ortak PIN işaretlenince hesapların gruba eklenmesi de zorunlu olduğu için otomatik açılır. |
| 5 | **QR Parola Diyaloğu** | EBA QR ile ilk girişte açılan "parola belirleyin" penceresini kapatır. Öğretmen sınıfta, öğrencilerin gözü önünde parola yazmak zorunda kalmaz. | Öğretmen yine isterse Sistem Ayarları'ndan parola tanımlayabilir. 3. adımı da uyguladıysanız o parola bir sonraki açılışta zaten geçersiz olur. |
| 6 | **SSH Sunucusu** | Tahtaya ağ üzerinden terminalle bağlanıp uzaktan bakım yapmanızı sağlar. | root parolasıyla giriş açılır; parolayı 2. adımda belirlemeniz gerekir, yoksa kimse bağlanamaz. Aynı parola bütün tahtalarda geçerli olacağı için erişimi okul ağıyla sınırlamak iyi olur. Yalnız tahtaların bulunduğu ağdan erişilir (aşağıdaki ağ bölümüne bakın). |
| 7 | **Dosya sunucusu** | Tahtanın diskine Windows veya Linux'tan dosya gezginiyle erişmenizi sağlar. | Tahtanın tamamı paylaşıma açılır ve yazma yetkisi verilir; paylaşım parolasını dikkatli belirleyin ve erişimi yönetim bilgisayarlarıyla sınırlayın. Bu da yalnız tahta ağından çalışır. |
| 8 | **Merkezi log sunucusu** | Tahtaların kayıtlarını okuldaki merkezi sunucuya gönderir. Sunucu kapalıyken kayıtlar tahtada birikir, sunucu dönünce gönderilir. | Hangi kayıtların gideceğini profil ile seçersiniz; "Kapsamlı" profil her şeyi gönderir, kalıcı açık bırakmayın. **10. adımı da uygulayın**, yoksa sunucuda hangi kaydın hangi tahtadan geldiği anlaşılmaz. İsterseniz disk sağlığı ve sıcaklık takibi de kurulur. |
| 9 | **Zaman senkronizasyonu (NTP)** | Tahtanın saatini doğru tutar. | PIN kodları saate bağlıdır: saat kayarsa öğretmenler PIN'le giremez. Okul ağı dışarıya kapalıysa kendi NTP sunucunuzun adresini yazın, sunucuları test düğmesiyle deneyin. |
| 10 | **Dinamik hostname** | Her tahtanın ağda kendine özgü bir adı olmasını sağlar. Ad, tahtanın ağ kartından üretilir. | İmaj alınırken ortak bir ad kullanılır, tahtalar açıldıkça kendi adlarını alır. Log, envanter ve uzaktan bağlantıda tahtaları ayırt etmenin tek yolu budur. |
| 11 | **Otomatik kapanma** | Unutulan tahtayı belirlediğiniz saatte ya da boşta kaldığında kapatır. Kapatmadan önce ekranda geri sayım gösterir. | Geri sayım süresini siz ayarlarsınız; kullanıcı 10 dakika erteleyebilir. Ekranın kararma süresi, boşta kapatma süresinden kısaysa uyarı ekranı kararmış ekranın arkasında kalabilir — adım bunu size söyler. Sayfanın başında tahtanın mevcut güç ayarları da listelenir. |
| 12 | **Uzaktan uyandırma** | Kapalı tahtayı ağ üzerinden açabilmenizi sağlar. Sabah bütün sınıfları merkezden açabilirsiniz. | Yalnız tahta tarafını hazırlar. Her tahtanın BIOS'unda Wake on LAN açık, ErP ve Deep Sleep kapalı olmalıdır; BIOS ayarları imajla taşınmaz, tek tek yapılır. Ethernet kablosu takılı olmalıdır. |
| 13 | **Başarım (Deneysel)** | Kapatılan oturumdan arta kalan programların bellekte kalmasını önler; isterseniz arayüzü hafifleten ETA Hafif Mod'u bütün kullanıcılara uygular. | *Gerçek tahta donanımında henüz doğrulanmadı.* Hafif mod renkleri, pencere boyutlarını ve görüntü kalitesini değiştirebilir: kalem çizgisi bulanıklaşabilir, bazı programların arayüzü beklenenden farklı görünebilir. Klonlamadan önce örnek tahtada sınıfta kullanılan yazılımlarla deneyin. Değişiklik bir sonraki açılışta etkili olur. [Ayrıntı](docs/m17-basarim-deneysel.md) |
| 14 | **Otomatik Ahenk Kaydı** | Klon tahtaların merkezi yönetime (Lider) kendi kimlikleriyle kaydolmasını sağlar. | Bu adım olmadan bütün klonlar aynı kimlikle görünür, komutlar yanlış tahtaya gider. Tahtanın merkezi envanterde kayıtlı olması ön koşuldur: kayıtlıysa klon ilk açılışta kendiliğinden abone olur, kayıtlı değilse klonda etapadmin ile oturum açıp kayıt uygulamasını çalıştırmanız gerekir. [Ayrıntı](docs/m12-clone-reclaim.md) |
| 15 | **BIOS parolası** | Klonların BIOS'una ilk açılışta yönetici parolası koyar. Böylece kimse USB'den başka sistem açamaz. | Yalnız desteklenen tahta modellerinde çalışır; desteklenmeyen donanımda adım "uygulanamaz" diye işaretlenir, hata sayılmaz. Parolayı unutursanız BIOS'a giremezsiniz. Parola alanını boş bırakıp uygularsanız koruma kalkar. |
| 16 | **GRUB koruması** | Açılış menüsünün kurcalanmasını engeller. Menüyü düzenlemeye ya da kurtarma kipine girmeye çalışan kişiden kullanıcı adı ve parola istenir. | Bu koruma olmadan tahtanın başına oturan biri açılış menüsünden yönetici hakkı alabilir. Normal açılışta parola sorulmaz. Kullanıcı adı `etapadmin`'dir ama parolası sistemdeki etapadmin parolası değildir, burada belirlediğinizdir; unutursanız kurtarma kipine giremezsiniz. Kutunun işaretini kaldırıp uygularsanız koruma kalkar. |
| 17 | **İmaj öncesi temizlik** | Tahtayı imaj alınmaya hazırlar: tahtaya özgü kimlikleri sıfırlar, kullanım izlerini ve gereksiz dosyaları siler, yer açar (genelde 500 MB – 1 GB). | **Geri alınamaz.** Kablosuz ağ parolaları, tarayıcı geçmişi ve çerezleri, çöp kutusu, kayıtlı parola anahtarlıkları ve TiHA'nın yedekleri silinir; yer imleri kalır. Bu yüzden 2. ve 4. adımlar bu noktadan sonra geri alınamaz. En son uygulayın, sonra tahtayı **açmadan** kapatıp imajı alın. |

## Özet sayfası ve rapor

Son adımdan sonra Özet sayfası, imaja tam olarak neyin girdiğini anlatan bir rapor hazırlar. Rapor üç bölümden oluşur: **Yapılanlar**, adımlar arası **Dikkat** uyarıları (ör. temizlikten sonra değişiklik yaptıysanız sizi uyarır) ve klon tahtada tek tek denemeniz gereken maddelerden oluşan **Kontrol et** listesi.

Raporu panoya kopyalayabilir ya da yazdırmaya uygun bir dosya olarak kaydedebilirsiniz. Kaydedilen dosyayı tarayıcıda açıp yazdırırsanız kontrol maddeleri kutucuklu çıkar; klon tahtayı denerken elinizde işaretleyebileceğiniz bir liste olur. Aynı sayfadan, geri alınabilir adımları tek tek geri alabilirsiniz. [Raporun ayrıntısı](docs/ozet-raporu.md)

## Sihirbazdan kareler

<table>
<tr>
<td width="50%"><a href="docs/images/00-hosgeldiniz.png"><img src="docs/images/00-hosgeldiniz.png" alt="Hoş geldiniz"></a><br><sub><b>Hoş geldiniz</b> — adımların listesi ve akışın özeti</sub></td>
<td width="50%"><a href="docs/images/01-sistem-guncellemesi.png"><img src="docs/images/01-sistem-guncellemesi.png" alt="Sistem güncellemesi"></a><br><sub><b>1. Sistem güncellemesi</b></sub></td>
</tr>
<tr>
<td><a href="docs/images/02-yerel-hesaplar.png"><img src="docs/images/02-yerel-hesaplar.png" alt="Yerel hesaplar"></a><br><sub><b>2. Yerel hesaplar</b> — parolalar ve yedek öğretmen hesapları</sub></td>
<td><a href="docs/images/03-otomatik-parola-temizligi.png"><img src="docs/images/03-otomatik-parola-temizligi.png" alt="Otomatik parola temizliği"></a><br><sub><b>3. Otomatik parola temizliği</b></sub></td>
</tr>
<tr>
<td><a href="docs/images/04-toplu-pin-anahtari.png"><img src="docs/images/04-toplu-pin-anahtari.png" alt="Toplu pin anahtarı"></a><br><sub><b>4. Toplu pin anahtarı</b> — öğretmen PIN'leri ve yazdırılabilir kâğıt</sub></td>
<td><a href="docs/images/05-eba-qr-parola-diyalogu.png"><img src="docs/images/05-eba-qr-parola-diyalogu.png" alt="QR Parola Diyaloğu"></a><br><sub><b>5. QR Parola Diyaloğu</b> — sınıfta parola yazma zorunluluğunu kaldırır</sub></td>
</tr>
<tr>
<td><a href="docs/images/06-ssh-sunucusu.png"><img src="docs/images/06-ssh-sunucusu.png" alt="SSH sunucusu"></a><br><sub><b>6. SSH Sunucusu</b> — uzaktan bakım</sub></td>
<td><a href="docs/images/07-samba-dosya-paylasimi.png"><img src="docs/images/07-samba-dosya-paylasimi.png" alt="Dosya sunucusu"></a><br><sub><b>7. Dosya sunucusu</b> — ağ üzerinden dosya alıp verme</sub></td>
</tr>
<tr>
<td><a href="docs/images/08-merkezi-log-sunucusu.png"><img src="docs/images/08-merkezi-log-sunucusu.png" alt="Merkezi log"></a><br><sub><b>8. Merkezi log sunucusu</b> — kayıtlar kaybolmadan toplanır</sub></td>
<td><a href="docs/images/09-zaman-senkronizasyonu.png"><img src="docs/images/09-zaman-senkronizasyonu.png" alt="NTP"></a><br><sub><b>9. Zaman senkronizasyonu</b> — test düğmesi dahil</sub></td>
</tr>
<tr>
<td><a href="docs/images/10-dinamik-hostname.png"><img src="docs/images/10-dinamik-hostname.png" alt="Hostname"></a><br><sub><b>10. Dinamik hostname</b> — her tahtaya kendi adı</sub></td>
<td><a href="docs/images/11-otomatik-kapanma.png"><img src="docs/images/11-otomatik-kapanma.png" alt="Otomatik kapanma"></a><br><sub><b>11. Otomatik kapanma</b> — geri sayım ve erteleme</sub></td>
</tr>
<tr>
<td><a href="docs/images/12-otomatik-ahenk-kaydi.png"><img src="docs/images/12-otomatik-ahenk-kaydi.png" alt="Otomatik Ahenk Kaydı"></a><br><sub><b>14. Otomatik Ahenk Kaydı</b> — klonlar kendi kimliğiyle kaydolur</sub></td>
<td><a href="docs/images/13-bios-yonetici-parolasi.png"><img src="docs/images/13-bios-yonetici-parolasi.png" alt="BIOS parolası"></a><br><sub><b>15. BIOS parolası</b> — klonda ilk açılışta ayarlanır</sub></td>
</tr>
<tr>
<td><a href="docs/images/14-imaj-icin-sanitize.png"><img src="docs/images/14-imaj-icin-sanitize.png" alt="İmaj öncesi temizlik"></a><br><sub><b>17. İmaj öncesi temizlik</b> — kimlikleri ve izleri siler</sub></td>
<td><a href="docs/images/15-ozet.png"><img src="docs/images/15-ozet.png" alt="Özet"></a><br><sub><b>Özet</b> — imaj raporu ve geri alma</sub></td>
</tr>
<tr>
<td colspan="2" align="center" width="100%"><a href="docs/images/bonus-greeter-countdown.png"><img src="docs/images/bonus-greeter-countdown.png" alt="Greeter ekranında geri sayım" width="55%"></a><br><sub><b>Otomatik kapanma uyarısı giriş ekranında</b><br>Kimse oturum açmamışken bile geri sayım görünür; "10 dakika ertele" ile vazgeçilebilir.</sub></td>
</tr>
</table>

## Ağ: neyin nereden çalıştığı

Okul ağı genellikle ikiye ayrılır: öğretmenler odasındaki idari bilgisayarlar ve tahtaların bağlı olduğu ağ. **SSH (6), Dosya sunucusu (7) ve Merkezi log (8)** yalnızca **tahtaların bulunduğu ağdan** çalışır.

```mermaid
graph TB
    subgraph "🌐 FATİH İnternet"
        I[Internet]
    end

    subgraph "🏫 Okul Ağı"
        R[🌐 Ana Router]

        subgraph "💻 İdari Ağ (Öğretmenler Odası)"
            direction TB
            A1[💻 Müdür PC]
            A2[💻 Sekreter PC]
            A3[💻 Öğretmen PC]
        end

        subgraph "📱 Tahta ve AP Ağı (10.x.x.x)"
            direction TB
            T1[📺 Sınıf 1 Tahta]
            T2[📺 Sınıf 2 Tahta]
            T3[📺 Sınıf N Tahta]
            AP1[📡 Access Point 1]
            AP2[📡 Access Point 2]
            LS[🖥️ Log Sunucusu]
        end
    end

    I ---|🌐| R
    R ---|🔗| A1
    R ---|🔗| A2
    R ---|🔗| A3
    R ---|🔗| T1
    R ---|🔗| T2
    R ---|🔗| T3
    R ---|🔗| AP1
    R ---|🔗| AP2
    R ---|🔗| LS

    T1 -.->|🔧 SSH Erişimi| LS
    T2 -.->|📁 Dosya Paylaşımı| LS
    T3 -.->|📋 Log İletimi| LS

    classDef admin fill:#e3f2fd
    classDef tahta fill:#f3e5f5
    classDef internet fill:#f1f8e9
    classDef router fill:#fff3e0
    classDef logserver fill:#fce4ec

    class A1,A2,A3 admin
    class T1,T2,T3,AP1,AP2 tahta
    class I internet
    class R router
    class LS logserver
```

**İdari ağdaki bilgisayarlardan bu üç özelliğe erişilemez.** Teknik destek için dizüstü bilgisayarınızla tahta ağına bağlanmanız, log sunucusunu da tahta ağına koymanız gerekir.

## Ekrandaki yazıları değiştirmek

Sihirbazda gördüğünüz bütün yazılar tek bir dosyada toplanmıştır: `tiha/locale/tr.toml`. Bir açıklamayı okulunuza göre değiştirmek isterseniz dosyayı açıp yazıyı düzenlemeniz yeterli; dosyanın başında nelere dikkat edileceği yazıyor. Aynı dosyanın çevrilmiş bir kopyasıyla sihirbaz başka bir dilde de çalıştırılabilir.

## Katkı ve destek

- Hata bildirimi ve öneri: [GitHub Issues](https://github.com/enseitankado/tiha/issues)
- Soru ve tartışma: [GitHub Discussions](https://github.com/enseitankado/tiha/discussions)
- Pull request'ler hoş karşılanır; ayrıntılar için [`CONTRIBUTING.md`](CONTRIBUTING.md).

Teknik ayrıntılar ve tasarım kararları `docs/` klasöründedir.

## Teşekkür

Öğretmen PIN'leri adımı [enseitankado/eta-otp-cli](https://github.com/enseitankado/eta-otp-cli) aracını kullanır; BIOS parolası adımı [enseitankado/eta-112](https://github.com/enseitankado/eta-112) ile çalışır. TiHA'nın dayandığı bütün projeler, sihirbazın sol alt köşesindeki **Emeği Geçenler** penceresinde lisanslarıyla birlikte listelenir.

## Lisans

GPL-3.0 — ayrıntı için [`LICENSE`](LICENSE) dosyasına bakınız.

Copyright © 2026 Özgür Koca
