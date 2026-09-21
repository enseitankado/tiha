# Özet raporu — "Bu imajda neler yaptınız?"

Özet adımının başında, bu tahtada TiHA ile yapılan her şeyi anlatan ve
imajı yaymadan önce **en az bir klon tahtada** neyin denenmesi gerektiğini
listeleyen bir rapor bulunur. Amaç: gözden kaçan tek bir ayrıntı, imajın
yazıldığı tahta sayısı kadar ayrı ayrı düzeltme demektir; rapor kullanıcıyı
bu ayrıntıları klonda yakalamaya yöneltir.

Kod: `tiha/core/report.py` (çatı), `tiha/core/report_steps.py` (adım
anlatıcıları ve adımlar arası denetimler), `tiha/core/report_log.py`
(kayıt katmanı), `tiha/ui/pages.py` → `SummaryPage._render_report`.

## Raporun yapısı

1. **Giriş paragrafı** — kaç adımın uygulandığı (başarısız ve atlanan
   adımlar ayrı sayılır) ve neden klonda test gerektiği.
2. **Yaptıklarınız** — sihirbaz sırasıyla, adım başına ikinci çoğul şahıs,
   geçmiş zaman maddeler ("…ayarladınız", "…oluşturdunuz") ve o adıma özel
   uyarılar (⚠).
3. **Dikkat** — adımlar arası sıra, ilişki ve eksik adım
   uyarıları (aşağıdaki tablo).
4. **Klon tahtada deneyin** — adım başına somut, emir kipinde denetim
   maddeleri (☐) ve her imaj için geçerli genel denetimler.
5. **Kapanış** — "Bu imajı yaymadan önce kapsamlı bir testten geçirmeyi
   unutmayın."

Rapor panoya kopyalanabilir ya da metin dosyasına kaydedilebilir.

## Veri kaynakları

| Kaynak | İçerik |
|---|---|
| `journal.json` | Her adımın son kaydı: durum, özet, modülün `data`'sı |
| `journal.json` → `data._rapor_params` | Uygulanan form parametreleri; parola alanlarında yalnız `***` (dolu) ya da `""` (boş) |
| `actions.json` | Form içi düğme eylemleri (hesap silme, BIOS parolası ayarlama, PIN silme…); özet metnindeki gizli değerler temizlenir |
| Canlı sistem | `/etc/machine-id`, `/var/lib/tiha/first-boot-sshkeys.done`, `/etc/ahenk/ahenk.conf`, `/etc/systemd/system/tiha-wake-on-lan.service`, `/var/lib/tiha/state` altındaki hassas yedekler |

Parametreler `JournalEntry`'ye yeni alan olarak değil `data` içinde
ayrılmış bir anahtarda tutulur: günce `JournalEntry(**e)` ile yüklendiği
için yeni bir alan eski sürümlerin günceyi okumasını bozardı. Rapor
özelliğinden önce yazılmış kayıtlarda parametre yoktur; anlatıcılar o
zaman modülün `data`'sına ve özet satırına düşer, bilmediklerini uydurmaz.

Raporda, günce dosyasında ve eylem kaydında hiçbir parola geçmez.

## Adım başına ayırt edilen durumlar

| Adım | Ayırt edilen birleşimler |
|---|---|
| Sistem güncellemesi | uygulandı / başarısız |
| Kullanıcı parolaları | hangi parolalar atandı ya da atanamadı (root, etapadmin, ogretmen), öğretmen parolası hesap yokluğundan uygulanmadı, anahtarlıklar kenara alındı, yedek hesap açıldı ya da zaten vardı, ortak hesap silindi, öğrenci hesabı düğmeyle silindi |
| Her açılışta parola temizliği | uygulandı |
| Öğretmen PIN anahtarları | eta-otp-cli ya da dahili yol, öğretmen listesi, yedek hesaplar, etapadmin/ogretmen için üretildi ya da korundu, yeni/korunan anahtar sayısı, grup PIN'i oluşturuldu ya da korundu, gruba eklenenler, otomatik grup servisi, giriş ekranı önbelleği (≥50 kullanıcı), değişen anahtar, PIN'lerin üretimden sonra silinmesi, fazladan hesap silme, ortak hesabın gruptan çıkarılması |
| EBA QR parola diyaloğu | kapatıldı / zaten kapalıydı |
| SSH sunucusu | paket kuruldu / zaten kuruluydu |
| Dosya sunucusu | paket kuruldu ya da vardı, kullanıcı root ya da başka |
| Merkezi log | sunucu, port, TCP/UDP, profil (bakım/kapsamlı/güvenlik), SMART, node_exporter |
| Zaman eşitlemesi | birincil ve yedek sunucu birleşimleri, saat dilimi, internet havuzu uyarısı |
| Dinamik hostname | şablon ve önek, önceki ad, geçersiz ya da uzun önek |
| Otomatik kapanma | sabit saat × boşta kapanma (4 birleşim), geri sayım süresi, 60 sn altı geri sayım |
| Uzaktan uyandırma | kuruldu, ethtool kuruldu, atlandı (kutu işaretsiz) ama önceki servis duruyor / hiç yok |
| Başarım | oturum kalıntısı temizliği, hafif mod ayarları tek tek, hafif mod kaldırıldı, imleç Xorg düzeltmesi, imleç tazeleme servisi |
| Otomatik Ahenk Kaydı | ahenk kuruldu ya da vardı, imzalanan MAC |
| BIOS parolası | temizleme, yalnız ayarlara girişte, her açılışta, Faz 1 modeli, model adı, kaynak tahtanın BIOS'unun düğmeyle doğrudan değiştirilmesi |
| GRUB koruması | kuruldu (kurtarma girdisi menüde ve parolalı), eski sürümün kapattığı kurtarma geri açıldı, kurtarma yöneticinin ayarıyla kapalı, kayıtlı açılış varsayılanı sıfırlandı, kaldırıldı, zaten etkindi (parola korundu), etkinleştirilmedi |
| İmaj öncesi temizlik | uygulandı, boşaltılan alan, hassas TiHA yedeklerinin silinmesi, imaj damgasının yalnız root'a açık olması |

## Adımlar arası denetimler

| Koşul | Uyarı |
|---|---|
| Başarısız kalan adım var | İmaj almadan önce düzeltin ya da geri alın |
| İmaj öncesi temizlik hiç uygulanmadı | Bütün klonlar aynı makine kimliği ve SSH anahtarını paylaşır |
| İmaj öncesi temizlikten sonra adım ya da düğme eylemi | İzler imaja girer; temizliği yeniden çalıştırın |
| Temizlik uygulandı ama `/etc/machine-id` dolu | Tahta temizlikten sonra yeniden açılmış; klonlar aynı kimliği alır |
| Temizlik uygulandı ama SSH "yapıldı" işareti var | Klonlar SSH anahtarı üretmez |
| Ahenk kurulu ama Otomatik Ahenk Kaydı yok | Klonlar Lider'e aynı kimlikle bağlanır |
| SSH / Samba / log var ama dinamik hostname yok | Klonlar ağda ve log sunucusunda aynı adla görünür |
| Ortak öğretmen parolası + parola temizliği | Parola ilk açılışta ezilir |
| Parola temizliği var, PIN anahtarı yok | QR çalışmazsa hiçbir öğretmen giremez |
| QR parola penceresi kapalı, PIN anahtarı yok | QR çalışmazsa giriş yolu kalmaz |
| Yedek hesaplar PIN adımından sonra açıldı | Yeni hesapların PIN'i yok |
| PIN, sabit saatte kapanma ya da log var; saat eşitleme yok | Saate bağlı işlevler bozulabilir |
| Uzaktan uyandırma + boşta kapanma | Uyandırılan tahta giriş ekranında kendiliğinden kapanır |
| BIOS parolası "her açılışta" + uzaktan uyandırma | Uyandırılan tahta BIOS parola ekranında bekler |
| BIOS parolası + uzaktan uyandırma | BIOS ayarları her tahtada parola gerektirir |
| BIOS parolası + Otomatik Ahenk Kaydı | Ortak MAC imzası; BIOS parolasını klonda doğrulayın |
| GRUB var, BIOS yok / BIOS var, GRUB yok | Açılış güvenliği tek taraflı |
| İmaj öncesi temizliğin sildiği hassas yedekler (shadow yedeği, anahtarlıklar, PIN kâğıtları, anahtar yedeği) diskte duruyor — canlı denetim | İmajla klonlara gidecek; temizliği (yeniden) çalıştırın |

## Genel klon denetimleri

Her raporda: ilk açılışı izleme, birkaç yeniden başlatma ve bir soğuk
açılış, öğretmen ve öğrenci hesaplarıyla giriş, dokunmatik/kalem/ses/ağ,
farklı tahta modelleri, okul ağında gerçek kullanım ve sorunların kaynak
tahtada düzeltilip imajın yeniden alınması. Kimlikle ilgili bir adım
uygulandıysa ayrıca iki klonun aynı anda ağda farklı ad, IP ve Lider
kaydıyla göründüğü denetlenir.

## Örnek: bütün adımlar uygulanmış bir tahta

<details>
<summary>Raporun düz metin çıktısı</summary>

```text
Bu tahtada TiHA ile 17 adımı uyguladınız. Aşağıda imaja neyin girdiğini ve her değişikliğin bir klon tahtada nasıl sınanacağını bulacaksınız. Bu imaj çok sayıda tahtaya kopyalanacak. Burada gözden kaçan her ayrıntıyı, imajın yazıldığı tahta sayısı kadar ayrı ayrı düzeltmek zorunda kalırsınız. Bu yüzden imajı yaymadan önce en az bir klon tahtaya yazıp aşağıdaki denetimleri eksiksiz yapın.

YAPTIKLARINIZ
■ Sistem güncellemesi (apt)
  • Sistem güncellemesini çalıştırdınız: depo yapılandırması denetlendi, paketler en güncel sürüme yükseltildi ve gereksiz paketler temizlendi.
  ! Paket yükseltmeleri geri alınamaz: bir güncellemeden kaynaklanan sorun imajla birlikte bütün klonlara gider.
■ Kullanıcı parolaları
  • root ve etapadmin parolalarını ayarladınız.
  • etapadmin hesabının eski parolayla şifreli kalan anahtarlık dosyalarını kenara aldınız; ilk girişte yeni parolayla yenisi oluşacak.
  • 3 yedek öğretmen hesabı oluşturdunuz (ogretmen1 – ogretmen3). Hesaplar parolasız (kilitli) açıldı; bu hesaplara PIN anahtarı adımında üretilen kodlarla girilir.
  • Öğrenci (ogrenci) hesabını ev diziniyle birlikte sildiniz.
  ! root ve etapadmin parolaları bütün klonlarda aynı olacak ve geri okunamaz. Parolayı güvenli bir yerde saklayın; unutulursa her tahtada ayrı ayrı erişim sorunu yaşanır.
■ Her açılışta parola temizliği
  • Her açılışta etapadmin dışındaki tüm yerel hesapların (ortak öğretmen/öğrenci, yedek ve kişisel öğretmen hesapları) parolasını rastgele bir değere çeviren açılış servisini kurdunuz. Bu hesaplara artık yalnız EBA QR, PIN ya da USB bellek ile girilebilir.
  ! Servis bütün klonlarda her açılışta çalışır. PIN ya da USB ile giriş kurulu ve çalışır değilse öğretmenler hiçbir tahtaya giremez; yalnız etapadmin kalır.
■ Öğretmen PIN anahtarları
  • Listeye girdiğiniz 2 öğretmen için PIN anahtarı hazırladınız. Bu öğretmenlerin tahtadaki kişisel hesabı ilk EBA QR girişlerinde oluşacak; PIN ile giriş ancak bundan sonra çalışır.
  • Tahtadaki 3 yedek öğretmen hesabını (ogretmen1 – ogretmen3) PIN listesine eklediniz.
  • Sistem yöneticisi (etapadmin) için de PIN anahtarı ürettiniz.
  • Toplam 6 yeni PIN anahtarı üretildi ve imaja girecek.
  • ogretmenler grubu için ortak PIN anahtarı oluşturdunuz; bu kod gruba üye kişisel ve yedek öğretmen hesaplarında geçerli.
  • 3 hesabı ogretmenler grubuna eklediniz.
  • EBA QR ile sonradan açılacak öğretmen hesaplarını ogretmenler grubuna kendiliğinden ekleyen servisi etkinleştirdiniz.
  • Tüm anahtarları QR kodlarıyla içeren yazdırılabilir PIN kâğıdı üretildi; öğretmenlere yalnızca özelden teslim edin.
  ! PIN anahtarları imajla birlikte bütün klonlara aynen kopyalanır; bu bilinçli bir tasarım. Tek bir tahtadan ya da kâğıttan sızan anahtar bütün tahtaları etkiler.
  ! Ortak PIN, kişisel PIN'lerden daha zayıf bir önlemdir: gruptaki herkes aynı kodu kullanır.
■ EBA QR parola diyalogu
  • EBA QR ile ilk girişte açılan parola tanımlama penceresini kapattınız; öğretmenler sınıfta öğrencilerin önünde parola yazmak zorunda kalmayacak.
  ! Bu hesaplarda parola olmayacak; QR çalışmadığında giriş için PIN ya da USB bellek gerekir.
■ SSH sunucusu (root girişi)
  • Tahtaya SSH sunucusunu kurdunuz ve root kullanıcısının ağ üzerinden parolayla oturum açmasına izin verdiniz.
  ! Root parolası ve parolayla SSH girişi bütün klonlarda aynı olacak; parola sızarsa bütün tahtalar uzaktan yönetici erişimine açılır. Erişimi güvenlik duvarı ya da VLAN ile yönetim bilgisayarlarına sınırlayın.
■ Dosya sunucusu
  • Samba ile tahtanın tüm diskini (kök '/') ağda \\<tahta-ip>\root adıyla, 'root' kullanıcısı ve parolasıyla tam yazma yetkisiyle paylaştınız.
  ! Samba parolası bütün klonlarda aynı ve paylaşım diskin tamamına root yetkisiyle yazabiliyor; parola sızarsa bütün tahtalar etkilenir. Paylaşıma erişimi yönetim ağıyla sınırlayın.
■ Dayanıklı merkezi log iletimi
  • Tahtanın sistem günlüklerini 10.0.0.5:514 adresindeki merkezi log sunucusuna TCP ile iletecek şekilde ayarladınız (profil: Bakım (önerilen); kimlik doğrulama, donanım uyarıları, servis bildirimleri ve TiHA/Ahenk kayıtları iletilir).
  • Sunucuya ulaşılamazsa kayıtlar tahtada en fazla 2 GB'a kadar biriktirilip bağlantı gelince gönderilecek.
  • Disk sağlığı (SMART) ve sıcaklık izleme paketlerinin kurulmasını istediniz.
■ Zaman senkronizasyonu (NTP)
  • Tahtanın saatini 0.tr.pool.ntp.org 1.tr.pool.ntp.org NTP sunucularıyla eşitleyecek şekilde ayarladınız; bunlara ulaşılamazsa time.cloudflare.com pool.ntp.org kullanılacak. Saat dilimi: Europe/Istanbul.
  ! İnternet NTP havuzunu kullandınız; okul ağı UDP 123 çıkışını engelliyorsa saat eşitlenmez. MEB iç NTP adresini biliyorsanız onu tercih edin.
  ! Geçersiz bir saat dilimi sessizce yok sayılır; yukarıdaki testte saat dilimini mutlaka kontrol edin.
■ Dinamik hostname stratejisi
  • İmaj için tahtanın bilgisayar adını geçici olarak 'etap-image' yaptınız. İmajdan çıkan her tahta açılışta kablolu ağ kartının MAC adresinden kendi adını üretecek: 'etap-XXXXXX' (XXXXXX = MAC'in son 6 hanesi).
  • Tahtanın önceki adı 'etap-ab12cd' idi.
■ Otomatik kapanma
  • Tahtanın her gün 22:00'de ve 15 dakika boşta kaldığında kapanmasını ayarladınız. Kapanmadan önce ekranda 2 dakika süren bir uyarı penceresi çıkacak; kullanıcı kapanmayı 10 dakika erteleyebilecek.
  ! Sabit saatteki kapanma ertelenirse o günün sabit saat kapanması iptal olur; tahta yalnız boşta kalma ile kapanabilir.
■ Uzaktan uyandırma (Wake-on-LAN)
  • İmajdan çıkan tahtaların ağ kartını her açılışta uzaktan uyandırma (Wake-on-LAN) paketini dinleyecek moda alan servisi kurdunuz; kapalı tahtalar merkezden `wakeonlan <MAC>` komutuyla açılabilecek.
  • Bunun için gereken ethtool paketini de kurdunuz.
  ! Merkezden uyandırma için bütün klonların MAC adreslerini toplamanız gerekir; TiHA bu listeyi tutmaz.
■ Başarım
  • Öğretmen oturumunu kapattığında arkada asılı kalan süreçlerin (kapatılmadan bırakılan Firefox/Chrome ve alt süreçleri dahil) sonlandırılmasını etkinleştirdiniz. Ayar tahta yeniden başlatılınca devreye girer.
  • Başarımı artırmak için ETA Hafif Mod'u tüm kullanıcılara uyguladınız: pencere ve menü animasyonları kapatıldı, çözünürlük 1600x900'e düşürüldü, yazı boyutu küçültüldü ve dosya ve masaüstü simgeleri küçültüldü. Ayarlar her kullanıcıya oturum açılışında uygulanır; sonradan eklenecek hesaplar dahil.
■ Otomatik Ahenk Kaydı
  • İmajdan çıkan her tahtanın ilk açılışta kendini kopya olarak tanıyıp kaynak tahtanın Lider kimliğini silmesini ve Lider'e kendi kimliğiyle yeniden abone olmasını sağlayan mekanizmayı kurdunuz; kaynak tahtanın MAC adresi (aa:bb:cc:dd:ee:ff) imza olarak kaydedildi.
  • Kaynak tahtanın kendi Ahenk kimliğine dokunulmadı; imaj alınana kadar Lider'e bağlı çalışmaya devam eder.
  ! Klonun ilk açılışında ağ ya da EBA servisi yoksa ahenk o açılış boyunca kaynak tahtanın kimliğiyle Lider'e bağlanır ve komutlar yanlış tahtaya gidebilir. Klonları ilk kez ağ hazırken açın.
■ BIOS yönetici parolası
  • Klon tahtaların ilk açılışında BIOS yönetici parolasını 6 karakterlik parolanıza ayarlayacak tek seferlik bir servisi imaja yerleştirdiniz; parola her açılışta sorulacak.
  • Servis Faz 2 Vestel Gri modeli için hazırlandı.
  • Bu tahtanın (kaynak) BIOS'una bu adımda dokunulmadı.
  ! Klonun ilk açılışında BIOS flash belleğine yazılır; bu işlem her tahtada tekrar eder. Önce tek bir klonda, sonra filodaki her tahta modelinden bir klonda deneyin.
  ! Parola, kaynak tahtada ve imajda düz metin bir betikte duruyor (/usr/local/sbin/tiha-first-boot-bios.py); klonda yalnız işlem başarılı olunca silinir. İmaj dosyalarını buna göre koruyun.
■ GRUB koruması
  • GRUB açılış menüsünü korumaya aldınız: menü düzenleme ('e') ve GRUB komut satırı ('c') artık 'etapadmin' GRUB kullanıcı adı ve bu adımda belirlediğiniz GRUB parolasıyla açılıyor. Kurtarma (recovery) girdisi menüde kalıyor ama onu açmak da aynı kullanıcı adı ve parolayı istiyor; 'Gelişmiş seçenekler' alt menüsü de parolalı. Normal açılış parola sormuyor.
  ! GRUB parolası sistemdeki etapadmin parolası değildir ve hiçbir yerden geri okunamaz; bütün klonlarda aynıdır. Türkçe karakter (ç, ğ, ı, ö, ş, ü) içeren bir parola GRUB'ın ABD klavye düzeninde yazılamayabilir.
■ İmaj öncesi temizlik
  • İmajı klonlamaya hazırlamak için kimlik temizliği yaptınız: makine kimliğini (machine-id) sıfırladınız, SSH anahtarlarını sildiniz (her klon ilk açılışta kendi anahtarını üretecek), kayıtlı ağ bağlantılarını ve Wi-Fi parolalarını temizlediniz.
  • Günlükleri, APT önbelleğini ve paket listelerini, kabuk geçmişlerini, kullanıcı önbelleklerini, tarayıcı gezinti verilerini (yer imleri korunarak), GNOME anahtarlıklarını ve geçici dosyaları sildiniz; yaklaşık 412.3 MB disk alanı boşalttınız.
  • İmaja /etc/tiha-image-info.json damgasını yazdınız; sahada bu dosyadan imajın sürümü ve uygulanan adımlar görülebilir (yalnız root okuyabilir).
  • TiHA'nın imajla klonlara gidecek hassas yedeklerini (parola değişikliği öncesi /etc/shadow yedeği, kenara alınmış anahtarlıklar, PIN kâğıtları ve anahtar yedeği) sildiniz; bu yüzden Kullanıcı parolaları ve PIN adımları artık geri alınamaz.
  ! Temizlikten sonra kaynak tahtayı işletim sistemiyle YENİDEN AÇMAYIN: kapatın ve imajı canlı USB'den (Clonezilla vb.) alın. Açarsanız makine kimliği ve SSH anahtarları kaynak tahtada yeniden üretilir ve bütün klonlara aynen gider.

DİKKAT
  ! Uzaktan uyandırılan bir tahta, kimse kullanmazsa yaklaşık 15 dakika + 2 dakika sonra giriş ekranında kendiliğinden kapanacak (boşta kapanma giriş ekranında da çalışır). Tahtaları dersten çok önce uyandıracaksanız boşta kalma süresini buna göre seçin.
  ! BIOS parolasının her açılışta sorulmasını seçtiniz ve uzaktan uyandırmayı açtınız: uzaktan uyandırılan tahtalar BIOS parola ekranında bekleyip işletim sistemine hiç geçmeyecek.
  ! Uzaktan uyandırma her tahtada BIOS ayarı (Wake on LAN açık, ErP ve Deep Sleep kapalı) ister ve BIOS'a yönetici parolası koyduğunuz için bu ayarları yapmak her tahtada o parolayı gerektirecek. BIOS ayarlarını mümkünse parola ayarlanmadan önce yapın.
  ! “BIOS yönetici parolası” ve “Otomatik Ahenk Kaydı” aynı MAC imzasını kullanıyor. BIOS parolası klonun ilk açılışında ayarlanamazsa, Ahenk kaydı imzayı güncellediği için sonraki açılışlarda da ayarlanmayabilir. Klonda BIOS parolasını ilk açılıştan sonra BIOS'a girerek mutlaka doğrulayın.

KLON TAHTADA DENEYİN
■ Sistem güncellemesi (apt)
  ☐ Güncelleme yeni çekirdek ve sürücüler getirmiş olabilir. Klonda ekranın, dokunmatiğin, kalemin, sesin ve ağın (kablolu ve kablosuz) çalıştığını doğrulayın.
  ☐ EBA QR girişini ve sık kullanılan ETAP uygulamalarını klonda açıp deneyin.
  ☐ Terminalde `sudo apt-get update` komutunun hatasız bittiğini ve /etc/apt/sources.list dosyasındaki depo satırlarının beklediğiniz gibi olduğunu doğrulayın. Bu adım bozuk depo dosyasını yeniden yazabilir; kurum içi özel depo satırlarınız varsa silinmiş olabilir.
■ Kullanıcı parolaları
  ☐ Klonu yeniden başlatıp giriş ekranında etapadmin ile yeni parolayla oturum açın; "giriş anahtarlığınızın parolası uyuşmuyor" uyarısı çıkmamalı.
  ☐ Klonda bir terminalde `su -` ile yeni root parolasını deneyin.
  ☐ Giriş ekranında yedek öğretmen hesaplarının göründüğünü doğrulayın. Terminalde `id ogretmen1` çıktısında audio, video, plugdev gibi cihaz gruplarının bulunduğunu kontrol edin.
  ☐ Giriş ekranında öğrenci hesabının artık görünmediğini doğrulayın.
■ Her açılışta parola temizliği
  ☐ Klonu yeniden başlatın; etapadmin ile parolayla girebildiğinizi doğrulayın (bu hesaba dokunulmaz).
  ☐ Ortak öğretmen hesabına bilinen parolasıyla girmeyi deneyin; giriş reddedilmeli.
  ☐ Bir öğretmen ve bir yedek hesaba PIN ile (ya da USB ile) girin; tahtayı yeniden başlattıktan sonra da girilebildiğini doğrulayın.
  ☐ Terminalde `journalctl -t tiha-boot-wipe -b` çıktısında HATA satırı olmadığını doğrulayın.
■ Öğretmen PIN anahtarları
  ☐ Klonun tarih, saat ve saat diliminin doğru olduğunu doğrulayın. PIN kodları saate bağlıdır; saat birkaç dakika bile kaymışsa bütün PIN girişleri reddedilir.
  ☐ PIN kâğıdındaki bir QR kodu telefondaki doğrulayıcı uygulamaya okutun; klonu yeniden başlatıp o hesaba telefonun gösterdiği 6 haneli kodla girin.
  ☐ Listedeki bir öğretmenle klonda önce EBA QR ile giriş yapın, sonra oturumu kapatıp aynı hesaba PIN ile girin. Ad soyad MEBBİS'teki yazımdan farklı girildiyse o öğretmenin PIN'i hiçbir tahtada çalışmaz.
  ☐ Bir yedek hesaba (ör. ogretmen1) PIN ile girin.
  ☐ etapadmin'e hem PIN ile hem de parolayla girilebildiğini doğrulayın.
  ☐ Kâğıttaki ORTAK PIN kartını okutup bir yedek ya da kişisel öğretmen hesabına ortak kodla girin.
  ☐ Klonda EBA QR ile yeni bir öğretmen girişi yaptıktan sonra bu hesabın ogretmenler grubuna eklendiğini doğrulayın (terminalde `id <kullanıcı>`).
■ EBA QR parola diyalogu
  ☐ Klonda o tahtaya daha önce hiç girmemiş bir öğretmenle EBA QR ile ilk girişi yapın; masaüstü açıldığında parola tanımlama penceresi çıkmamalı.
  ☐ Aynı öğretmenin oturumu kapatıp ikinci kez QR ile (PIN anahtarı varsa PIN ile de) girebildiğini doğrulayın.
■ SSH sunucusu (root girişi)
  ☐ Yönetim bilgisayarınızdan `ssh root@<klon-ip>` ile klona bağlanın; beklediğiniz root parolasının geçtiğini doğrulayın.
  ☐ Klonda `systemctl is-active ssh` çıktısının active olduğunu ve `sudo sshd -T | grep -Ei 'permitrootlogin|passwordauthentication'` çıktısında ikisinin de yes olduğunu doğrulayın (adım, servisin gerçekten ayağa kalktığını denetlemiyor).
  ☐ İki farklı klonda `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` parmak izlerinin FARKLI olduğunu doğrulayın.
  ☐ Öğrenci ya da misafir ağından klonun SSH portuna erişilemediğini doğrulayın.
■ Dosya sunucusu
  ☐ Bir Windows bilgisayarda Dosya Gezgini'ne \\<klon-ip>\root yazıp 'root' kullanıcı adı ve parolayla bağlanın; bir dosya oluşturup silerek yazma yetkisini doğrulayın.
  ☐ Klonda `systemctl is-active smbd` çıktısının active olduğunu doğrulayın (adım servisin ayağa kalktığını denetlemiyor).
  ☐ Birkaç klon aynı anda ağdayken Windows'un Ağ görünümünde her tahtanın kendi adıyla göründüğünü, ad çakışması olmadığını doğrulayın.
■ Dayanıklı merkezi log iletimi
  ☐ Klonda `logger -p auth.notice "tiha-klon-test"` çalıştırın; kaydın log sunucusuna ulaştığını ve orada klonun KENDİ bilgisayar adıyla (kaynak tahtanın ya da ortak imaj adının değil) göründüğünü doğrulayın.
  ☐ İki klonu aynı anda açıp log sunucusunda iki ayrı bilgisayar adı ve IP gördüğünüzü doğrulayın; bunu ilk açılışta ve bir yeniden başlatmadan sonra ayrı ayrı yapın.
  ☐ Klonun ilk açılışında `sudo ls -la /var/lib/rsyslog/` ile bekleyen bir kuyruk dosyası olmadığını doğrulayın; varsa kaynak tahtanın eski kayıtları her klondan yeniden gönderiliyor demektir.
  ☐ Log sunucusunu ya da ağı kısa süre kesin; bağlantı gelince aradaki kayıtların sunucuya ulaştığını doğrulayın.
  ☐ Klonda `systemctl is-active smartd` ve `sensors` çalıştırın; farklı tahta modellerinde sıcaklığın okunabildiğini doğrulayın.
■ Zaman senkronizasyonu (NTP)
  ☐ Klonu kurulacağı OKUL AĞINDA açıp `timedatectl` çalıştırın; "System clock synchronized: yes" ve "Time zone: Europe/Istanbul" görülmeli. Hazırlık ağıyla okul ağı farklı olabilir.
  ☐ Tahtayı birkaç saat kapalı tutup açın; saatin açılıştan kısa süre sonra doğru değere geldiğini doğrulayın.
■ Dinamik hostname stratejisi
  ☐ Klonu açıp `hostnamectl` çalıştırın: ad 'etap-' ile başlamalı ve son 6 hanesi kablolu ağ kartının MAC adresinin (`ip link`) son 6 hanesi olmalı. Ad hâlâ imaj adındaysa servis çalışmamıştır.
  ☐ Klonu ikinci kez yeniden başlatın; adın DEĞİŞMEDİĞİNİ doğrulayın. Her açılışta değişiyorsa tahtada kablolu kart bulunamamıştır.
  ☐ `time sudo true` komutunun anında döndüğünü doğrulayın (10 saniye sürüyorsa /etc/hosts güncellenmemiştir).
  ☐ Klonun ilk açılışında oturum açıp birkaç uygulama başlatın; ad oturum açıldıktan sonra değişirse yeni pencereler açılamayabilir.
  ☐ İki klonun farklı ad aldığını ve bu adın DHCP/DNS'te, Lider'de ve (kuruluysa) log sunucusunda göründüğünü doğrulayın.
■ Otomatik kapanma
  ☐ Klonda oturum açıp dokunmadan bırakın; yaklaşık 16 dakika sonra uyarı penceresinin çıktığını ve geri sayımın 2 dakika ile başladığını doğrulayın.
  ☐ "10 dakika ertele" düğmesiyle pencerenin kapandığını ve 10 dakika boyunca yeniden açılmadığını, sonra sayacın bitince tahtanın kapandığını doğrulayın.
  ☐ Aynı denemeyi OTURUM AÇMADAN, giriş ekranında yapın; pencere orada da çıkmalı. Ekran kararmışsa pencerenin ekranı uyandırdığını görün.
  ☐ Klonun saatinin doğru olduğundan emin olun; 22:00'den 2 dakika önce pencerenin açıldığını ve 22:00'de tahtanın kapandığını doğrulayın. Tahtayı 22:00'den ÖNCE açmış olmanız gerekir; bu saatten sonra açılan tahta o gün sabit saatte kapanmaz.
  ☐ Klonda `systemctl is-active eta-shutdown` çıktısının active olduğunu doğrulayın.
■ Uzaktan uyandırma (Wake-on-LAN)
  ☐ Klonun BIOS ayarlarında 'Wake on LAN' ve 'Power On by PCI-E' açık, 'ErP' ve 'Deep Sleep' KAPALI olmalı. BIOS ayarları imajla taşınmaz; her tahtada ayrıca yapılmalı.
  ☐ Klonda `sudo ethtool <arayüz>` çıktısında "Wake-on: g" görün ve klonun MAC adresini not edin (her klonun MAC'i farklıdır).
  ☐ Klonu normal yoldan kapatın; AYNI ağ bölümündeki (VLAN) başka bir bilgisayardan `wakeonlan <klon-MAC>` gönderip tahtanın açıldığını doğrulayın.
■ Başarım
  ☐ Klonu yeniden başlatın. Bir öğretmen hesabıyla tarayıcıda birkaç sekme açıp tarayıcıyı kapatmadan oturumu kapatın; başka bir hesapla girip Sistem İzleyicisi'nde önceki kullanıcıya ait süreç kalmadığını doğrulayın.
  ☐ Oturum kapatma sonrasında aynı kullanıcının SSH gibi diğer açık oturumlarının kapanmadığını doğrulayın.
  ☐ Klonda öğretmen ve öğrenci hesaplarıyla ayrı ayrı oturum açın; hafif mod ayarlarının ilk girişte uygulandığını doğrulayın.
  ☐ Çözünürlük 1600x900'e düştüğü için tahtaya parmakla ve kalemle dokunup dokunma noktasının imleçle aynı yere düştüğünü (kalibrasyonun kaymadığını), yazıların ve kalem çizgisinin okunaklı olduğunu doğrulayın.
  ☐ USB fare takılıyken oturumu kapatıp başka bir hesaba geçin; fare imlecinin görünür kaldığını doğrulayın (ekran modu değişiminde imleç kaybolabiliyor).
■ Otomatik Ahenk Kaydı
  ☐ Klonu ilk kez açmadan önce kablolu ağa bağlayın; klonun api-etap.eba.gov.tr adresine erişebildiğinden emin olun.
  ☐ Açılıştan sonra `sudo journalctl -t tiha-clone-reclaim` çıktısında "klon" ve ardından "KAYITLI" ya da "KAYITSIZ" satırını görün. "API'ye ulaşılamadı" yazıyorsa ağı düzeltip yeniden başlatın.
  ☐ Lider konsolunda klonun kaynak tahtadan AYRI bir kayıt olarak, kendi MAC adresiyle göründüğünü; klona gönderilen bir test komutunun kaynak tahtaya düşmediğini doğrulayın.
  ☐ Envanterde kayıtlı olmayan bir klonda etapadmin ile oturum açınca eta-register kayıt ekranının açıldığını, kayıttan sonra tahtanın Lider'de göründüğünü doğrulayın.
  ☐ İki klonu aynı anda açıp Lider'de iki ayrı kayıt oluştuğunu doğrulayın.
■ BIOS yönetici parolası
  ☐ Klonu ilk kez açıp oturum açın: `sudo journalctl -t tiha-first-boot-bios -b` çıktısında "BIOS yönetici parolası işlemi başarılı" satırını ve `/usr/local/sbin/tiha-first-boot-bios.py` dosyasının artık OLMADIĞINI doğrulayın.
  ☐ Klonu yeniden başlatıp BIOS'a girin; parola sorulmalı ve yazdığınız parola kabul edilmeli. Gömülen parola normalleştirilmiş hâlidir (küçük harfler büyütülür, 'I' ve harf/rakam dışı karakterler atılır). Her açılışta işletim sisteminden önce parola sorulmalı.
  ☐ Klonu bir kez daha yeniden başlatıp BIOS parolasının hâlâ yerinde olduğunu doğrulayın.
  ☐ Kaynak tahtayı yeniden başlattığınızda onun BIOS parolasının DEĞİŞMEDİĞİNİ doğrulayın.
■ GRUB koruması
  ☐ Klonun açılış menüsünde bir girdinin üzerindeyken 'e' tuşuna basın: kullanıcı adı olarak etapadmin, ardından GRUB parolası sorulmalı; yanlış parolayla düzenleme ekranı açılmamalı. Menü görünmüyorsa açılışta Shift ya da Esc tuşunu basılı tutun.
  ☐ 'c' tuşuyla GRUB komut satırında da aynı iki sorunun geldiğini doğrulayın.
  ☐ Varsayılan girdiyle ve zaman aşımıyla açılışın HİÇ parola sormadan ilerlediğini doğrulayın.
  ☐ "Gelişmiş seçenekler" altındaki kurtarma (recovery mode) girdisini seçin: etapadmin kullanıcı adı ve GRUB parolası sorulmalı, parolayla kurtarma kipine girilebilmeli; yanlış parolayla girilememeli.
  ☐ Kurtarma kipinden çıkıp tahtayı yeniden başlatın: sonraki açılış normal girdiyle ve parola sormadan gerçekleşmeli (alt menü girdileri açılış varsayılanı olarak kaydedilmez).
  ☐ Parolayı fiziksel bir USB klavyeyle deneyin: GRUB'da dokunmatik ve ekran klavyesi yoktur, klavye düzeni ABD'dir.
  ☐ "Gelişmiş seçenekler" alt menüsünün de parola istediğini doğrulayın; eski çekirdekle açmak gerekirse GRUB parolası gerekir.
■ İmaj öncesi temizlik
  ☐ İki farklı klonda `cat /etc/machine-id` ve `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` çıktılarının FARKLI olduğunu doğrulayın.
  ☐ Klonda `ls /etc/ssh/ssh_host_*` ile SSH anahtarlarının üretildiğini ve (SSH kuruluysa) `systemctl is-active ssh` çıktısının active olduğunu doğrulayın.
  ☐ Kablolu ağın klonda kendiliğinden bağlandığını doğrulayın; Wi-Fi kullanılacaksa bağlantıyı yeniden tanımlamanız gerekir (Wi-Fi parolaları imajdan silindi).
  ☐ etapadmin ve bir öğretmen hesabıyla girişte "anahtarlık parolası uyuşmuyor" uyarısı çıkmadığını, Firefox ve Chrome'un açıldığını doğrulayın.
  ☐ Klonda `sudo apt update` komutunun çalıştığını ve `sudo cat /etc/tiha-image-info.json` çıktısının beklediğiniz sürümü gösterdiğini doğrulayın; `ls -l /etc/tiha-image-info.json` yalnız root'a okuma izni (-rw-------) göstermeli.
■ Genel
  ☐ İmajı en az bir tahtaya yazın ve ilk açılışı başından sonuna izleyin: hata ekranı, beklenmedik parola sorusu ya da uzun bekleme olmamalı.
  ☐ Klonu en az iki kez yeniden başlatın ve bir kez tamamen kapatıp açın; her açılışta aynı sonucu aldığınızı doğrulayın.
  ☐ Öğretmen ve öğrenci hesaplarının her biriyle oturum açıp kapatın.
  ☐ Tahtanın dokunmatiği, kalemi, sesi ve ağ bağlantısının klonda çalıştığını doğrulayın.
  ☐ En az iki klonu aynı anda aynı ağa bağlayın; bilgisayar adlarının, IP adreslerinin ve (kullanıyorsanız) Lider kayıtlarının birbirinden farklı olduğunu doğrulayın.
  ☐ İmaj farklı tahta modellerine (ör. Intel ve AMD işlemcili) yazılacaksa her modelde en az bir klon deneyin.
  ☐ Klonu kurulacağı okulun ağında, gerçek bir öğretmen hesabıyla en az bir ders süresince kullanın.
  ☐ Testte bulduğunuz her sorunu kaynak tahtada düzeltip imajı yeniden alın; sorunu klonlarda tek tek düzeltmeye çalışmayın.

Bu imajı yaymadan önce kapsamlı bir testten geçirmeyi unutmayın.
```

</details>
