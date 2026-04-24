# TiHA — Tahta İmaj Hazırlık Aracı

> Pardus ETAP kurulu sınıf etkileşimli tahtalarını, **imaj yöntemiyle kopyalanıp** yüzlerce tahtaya kolayca dağıtılabilecek biçimde hazırlayan sihirbaz uygulaması.

![Durum](https://img.shields.io/badge/durum-alfa-orange) ![Platform](https://img.shields.io/badge/platform-Pardus%20ETAP%2023-blue) ![Lisans](https://img.shields.io/badge/lisans-GPL--3.0-green) ![Dil](https://img.shields.io/badge/dil-Türkçe-red)

---

## TiHA nedir?

Bir sınıfta veya bir okulun tüm tahtalarında aynı hazırlıkları tek tek yapmak çok yorucu. TiHA, **bir tahtaya** bir kez hazırlık yaptırıp, o tahtanın imajını diğer tahtalara uygulamanızı kolaylaştırır.

Pardus ETAP 23 kurulu tahtada **tek bir komutla** çalışır — **bilgisayara yüklenmez**, geçici olarak açılıp kapanır. Görsel bir sihirbaz her adımın **ne yaptığını** ve **neden yaptığını** size açıklar, onayınızı alır, sonucu gösterir ve gerektiğinde **geri alır**.

## Hangi sorunu çözer?

Sınıfta öğretmen, tahtada **EBA-QR ile ilk kez oturum açarken** sistem kendisinden bir yerel parola belirlemesini ister. Öğretmen bu parolayı **65 inç dokunmatik ekranda parmağıyla yazmak zorundadır**. Arkadaki sıralarda oturan öğrenciler ekrandaki tuşları rahatlıkla gördüğü için **parolayı ezberler**. Sonraki derslerde öğretmenin hesabıyla tahtayı açıp yetkisiz işlemler yapabilirler.

**TiHA bu sorunu kökten çözer.** İmaj uygulandıktan sonra ekrandan parola yazarak giriş yapmak **artık mümkün değildir**. Öğretmen yalnızca şu üç yoldan biriyle oturum açabilir:

| Yol | Açıklama |
|-----|----------|
| 🔳 **EBA-QR** | Telefondaki EBA uygulamasından ekrandaki kare kodu okutarak |
| 🔢 **OTP (PIN)** | Google Authenticator gibi bir uygulamadan üretilen 6 haneli kod |
| 🗝️ **USB anahtar** | Öğretmene özel hazırlanmış kişisel USB bellek |

## Nasıl çalışır?

Aşağıdaki akış, TiHA'nın yerini ve iş akışını özetler:

```mermaid
flowchart TD
    A([Bir tahtaya Pardus ETAP kurulur])
    B[Etapadmin terminaline tek komut yapıştırılır]
    C[Sihirbaz açılır]
    D[Her adım: açıklama → onay → uygula → sonuç → gerekirse geri al]
    E[Tahta yeniden başlatılır - test]
    F[Clonezilla / dd ile imaj alınır]
    G([İmaj diğer onlarca tahtaya uygulanır])
    H[Her tahta ilk açılışta kendi benzersiz kimliğini üretir:<br/>hostname, SSH anahtarları, machine-id]
    I([Sınıfta kullanıma hazır tahtalar])

    A --> B --> C --> D --> E --> F --> G --> H --> I

    classDef green fill:#d4edda,stroke:#28a745,color:#155724
    classDef blue fill:#cce5ff,stroke:#0d6efd,color:#084298
    class A,I green
    class G,H blue
```

İmaj uygulandıktan sonra her tahtaya açılan öğretmen şu üç yoldan biriyle girer — **yerel parolayla giriş yoktur**:

```mermaid
flowchart LR
    T((Tahta<br/>açıldı))
    T --> A[🔳 EBA-QR okut]
    T --> B[🔢 OTP PIN gir]
    T --> C[🗝️ USB anahtar tak]
    A --> S((Oturum<br/>açıldı))
    B --> S
    C --> S
    X[❌ Ekrandan parola]:::forbidden -.-> T

    classDef forbidden stroke:#dc3545,color:#dc3545,stroke-dasharray: 5 5,fill:#f8d7da
```

## TiHA ne değildir?

- ❌ **İmaj alma aracı değildir.** İmajı siz Clonezilla, dd veya benzer bir araçla alırsınız. TiHA yalnızca imaj alınmadan **önceki** hazırlığı yapar.
- ❌ **Pardus ETAP dağıtım medyası değildir.** İşletim sistemini siz temiz kurulum olarak kurarsınız.
- ❌ **Uzaktan yönetim aracı değildir.** Lider-Ahenk bu işe bakar; TiHA Ahenk kurulumuna karışmaz.
- ❌ **OTP doğrulayıcı değildir.** Standart `eta-otp-lock` + PAM akışı kullanılır; TiHA yalnızca anahtarları toplu üretip yerlerine yazar.
- ❌ **Sisteme kurulmaz.** Çalışır, iş biter, iz bırakmadan silinir.

## Neler yapar?

Sihirbaz bu adımları sırasıyla uygular. Her biri isteğe bağlıdır; ancak ana senaryo için hepsinin uygulanması tavsiye edilir.

| # | Adım | Kısa açıklama |
|---|------|----------------|
| 1  | **Donanım ön kontrol**          | Tahta imajlanmaya uygun mu? SMBIOS, MAC, `machine-id` kontrolü. |
| 2  | **Başlangıç parolaları**         | `root` ve `etapadmin` parolalarını ayarlar; genel hesapları rastgele parolayla kilitler. |
| 3  | **Her açılışta parola temizliği**| Her boot'ta genel hesapların parolalarını otomatik olarak rastgele değere çeviren sistem servisini kurar. |
| 4  | **Öğretmen OTP anahtarları**     | Öğretmen listenizden TOTP anahtarları üretir, her hesap için `otpauth://` URL'si sunar. |
| 5  | **SSH sunucusu**                 | Teknik bakım için uzaktan SSH erişimini kurar. |
| 6  | **Samba dosya paylaşımı**        | Tahtaya SMB ile uzaktan dosya erişimi sağlar. |
| 7  | **Merkezi log iletimi**          | Tahtanın günlüklerini ağdaki merkezi log sunucusuna iletir. |
| 8  | **Zaman senkronizasyonu (NTP)**  | Saat dilimi ve NTP sunucusunu ayarlar. OTP'nin çalışması için şarttır. |
| 9  | **Benzersiz hostname stratejisi**| Her klonun ilk açılışta kendine özgü bir hostname almasını sağlar. |
| 10 | **Sistem güncellemesi (apt)**    | `apt update` + `full-upgrade` + temizlik. |
| 11 | **İmaj için sanitizasyon**       | Son adım: tekil kimlikleri (SSH anahtarları, `machine-id`, NetworkManager vb.) temizler. |

## Nasıl çalıştırılır?

1. Tahtada **Etap Yönetici** (`etapadmin`) hesabıyla oturum açın.
2. Uygulamalar menüsünden **Terminal**'i açın.
3. Aşağıdaki komutu **kopyalayıp** terminale **yapıştırın** ve **Enter**'a basın:

```bash
curl -fsSL https://raw.githubusercontent.com/enseitankado/tiha/main/bootstrap.sh | bash
```

İlk çalıştırmada tek seferliğine etapadmin parolanızı sorabilir. Sonra sihirbaz penceresi açılır — gerisi tamamen görsel ve adım adımdır.

## Proje yapısı

```
tiha/
├── README.md
├── LICENSE
├── bootstrap.sh                 Tek komutla çalıştıran başlatıcı
├── pyproject.toml
├── data/styles.css
└── tiha/                        Python paketi
    ├── app.py                   Uygulama girişi
    ├── core/                    Altyapı (günce, günlük, yetki, yardımcılar)
    ├── modules/                 11 sihirbaz adımı (m00–m10)
    └── ui/                      GTK3 arayüzü
```

## Katkı ve destek

- 🐛 Hata bildirimi ve öneri: [GitHub Issues](https://github.com/enseitankado/tiha/issues)
- 💬 Soru ve tartışma: [GitHub Discussions](https://github.com/enseitankado/tiha/discussions)
- Pull request'ler hoş karşılanır; ayrıntılar için [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Lisans

GPL-3.0 — ayrıntı için [`LICENSE`](LICENSE) dosyasına bakınız.

Copyright © 2026 Özgür Koca
