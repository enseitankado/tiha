# m17 — Başarım (Deneysel)

Bu belge, TiHA'nın `m17_performance` adımı (sihirbazda **"Başarım
(Deneysel)"**) için mekanizmayı, ölçümleri ve gerçek tahtada deneme
adımlarını anlatır. Adımdaki iki mekanizma da **gerçek tahta donanımında
henüz doğrulanmadı**.

## 1. Eski oturum kalıntılarını temizle

### Sorun

Pardus ETAP 23 (Debian 12, systemd 252) `systemd-logind`'i derleme
varsayılanıyla `KillUserProcesses=no` çalıştırır. Öğretmen oturumunu
kapattığında LightDM oturumu biter ama oturumun cgroup'unda
(`session-N.scope`) kalan süreçler öldürülmez:

- kapatılmadan bırakılan Firefox/Chrome ve bütün içerik süreçleri,
- kullanıcının `user@UID.service` servisleri (pipewire, gvfs, portal…) —
  oturum kapanamadığı için kullanıcı da "çıkmış" sayılmaz.

Oturum `loginctl list-sessions` çıktısında `closing` durumunda asılı
kalır. Tahta gün içinde yeniden başlatılmazsa her öğretmen bu yükün
üstüne ekler.

### Ölçüm (2026-09-16, geliştirme VM'i)

Aynı 4 sekme (eba.gov.tr, youtube.com, tr.wikipedia.org, trthaber.com),
headless, dokunulmadan beklerken:

| Tarayıcı | Süreç | Bellek (PSS) | CPU (20 sn ort.) |
|---|--:|--:|--:|
| Firefox ESR 140 | 15 | 913 MB | %149* |
| Chrome 148 | 16 | 592 MB | %15 |

\* VM'de yazılımsal çizim (llvmpipe/SWGL) kullanıldığı için şişkin olabilir.
Pencereli gerçek kullanımda bellek bundan az değil, çoğunlukla fazladır.
Bildirilen "oturum başına ~500 MB" bu ölçümle uyumlu.

### Mekanizma

`/etc/systemd/logind.conf.d/90-tiha-oturum-kalintilari.conf`:

```ini
[Login]
KillUserProcesses=yes
KillExcludeUsers=root
```

- Oturum kapanınca logind, **o oturumun** scope'unu durdurur: SIGTERM,
  gerekirse SIGKILL. cgroup tabanlı olduğu için çift fork ile ebeveynden
  kopan süreçler de yakalanır.
- Kullanıcının başka canlı oturumu yoksa `user@UID.service` de
  `UserStopDelaySec` (10 sn) sonra durur.
- Aynı kullanıcının SSH gibi **başka açık oturumlarına dokunulmaz**.
- systemd 252'de logind yapılandırmayı yeniden okuyamaz (`CanReload=no`).
  Çalışan grafik oturumun altında logind'i yeniden başlatmak riskli
  olduğundan ayar **yeniden başlatınca** etkin olur.
- "Şu an asılı kalmış oturumları da kapat" seçeneği `closing` durumundaki
  (UID ≥ 1000) oturumlara `loginctl terminate-session` uygular; aktif
  oturumlara dokunmaz.
- Önizleme, asılı oturumları ve tuttukları anonim belleği
  (`memory.stat` → `anon`, önbellek hariç) listeler.

## 2. ETA Hafif Mod

Pardus'un `eta-light-mode` paketi (0.2.3) kurulur. Seçilen ayarlar paketin
kendi yardımcısıyla (`/usr/share/eta/eta-light-mode/src/Action.py enable`)
`/etc/eta-light-mode/settings.json`'a yazılır ve autostart girdisi
`/etc/xdg/autostart/tr.org.eta.light-mode-autostart.desktop` olarak
kopyalanır. Her kullanıcı oturum açtığında `eta-light-mode --apply-config`
çalışır.

| Form alanı | settings.json anahtarı | Etkisi |
|---|---|---|
| Animasyonlar | `effects` | `org.cinnamon desktop-effects-workspace=false` — Cinnamon 5.6'da genel animasyon anahtarı (pencere, menü, diyalog) |
| Tam ekran doğrudan çizim | `compositor` | `org.cinnamon.muffin unredirect-fullscreen-windows=true` |
| Önizlemeler | `thumbnails` | `org.nemo.preferences show-image-thumbnails=never` |
| Klasör sayımı | `directory-item-counts` | `org.nemo.preferences show-directory-item-counts=never` |
| Uygulama izleme | `app-monitoring` | `org.cinnamon enable-app-monitoring=false` |
| 1600x900 | `low-resolution` + `text-scaling` + `file-icon-size` | Çözünürlük 1600x900; yazı 9.5 punto, dosya ikonları `small` (büyüyen arayüzün telafisi) |
| 50 Hz | `low-refresh-rate` | Yenileme hızı 50 Hz |

Kurallar:

- JSON'a yalnızca seçilen anahtarlar `true` yazılır. Paket `false`'u
  "dokunma" değil "koda gömülü normal değere (1920x1080@60, Ubuntu 11)
  döndür" diye yorumlar.
- Çalışan oturuma canlı uygulanmaz (m14 postmortem'indeki canlı
  Cinnamon/Nemo müdahalesi dersleri); etki sonraki oturum açılışında
  görülür.
- JSON anahtarları paketin resmî arayüzü değildir. Denenen sürüm dışında
  uyarı verilir; kurulu `Settings.py`'de olmayan anahtarlar atlanır.
- Ayarlar her girişte yeniden uygulanır; öğretmen kendi oturumunda
  değiştirse de bir sonraki girişte geri gelir.

## Geri al

İlk uygulamadan önceki durum `/var/lib/tiha/state/m17_performance/
original.json`'a ve dosya yedeklerine kaydedilir; sonraki uygulamalar bu
kaydı değiştirmez. Geri alma yalnızca TiHA'nın dokunduğu parçaları o
duruma döndürür, paketi TiHA kurduysa `apt-get purge` eder. logind
değişikliği yine yeniden başlatınca etkin olur. Hafif mod bir kez
uygulanmış kullanıcıların kişisel masaüstü ayarları geri dönmez.

## Gerçek tahtada deneme

Dalı ana dala birleştirmeden çalıştırmak için (etapadmin terminalinde):

```bash
curl -fsSL https://raw.githubusercontent.com/enseitankado/tiha/main/bootstrap.sh \
  | TIHA_TARBALL=https://codeload.github.com/enseitankado/tiha/tar.gz/refs/heads/feat/basarim-deneysel bash
```

### A. Oturum kalıntıları

1. **Önce (adım uygulanmadan):** Öğretmen hesabıyla girin, Firefox ve
   Chrome'da birkaç sekme açın, tarayıcıları kapatmadan oturumu kapatın.
   etapadmin ile girip:

   ```bash
   loginctl list-sessions
   loginctl show-session <N> -p Name -p State          # State=closing beklenir
   grep -E '^anon ' /sys/fs/cgroup/user.slice/user-<UID>.slice/session-<N>.scope/memory.stat
   ps -u <ogretmen> --no-headers | wc -l
   ```

   TiHA'da Başarım sayfasının önizlemesi de aynı oturumu ve belleğini
   göstermelidir.
2. Adımı "Eski oturum kalıntılarını temizle" + "Şu an asılı kalmış
   oturumları da kapat" ile uygulayın; asılı oturumun kapandığını ve
   `ps -u <ogretmen>` çıktısının boşaldığını görün.
3. Tahtayı yeniden başlatın ve doğrulayın:

   ```bash
   busctl get-property org.freedesktop.login1 /org/freedesktop/login1 \
     org.freedesktop.login1.Manager KillUserProcesses   # b true
   ```

4. **Sonra:** 1. maddeyi tekrarlayın. Oturum kapandıktan birkaç saniye sonra
   `closing` oturum kalmamalı; ~10 sn içinde `ps -u <ogretmen>` boş olmalı.
5. Yan etki kontrolü: aynı öğretmen hemen yeniden girebiliyor mu; EBA QR,
   USB ve PIN ile giriş; ses (pipewire) sonraki girişte çalışıyor mu;
   kullanıcı değiştirme ve ekran kilidi normal mi.

### B. ETA Hafif Mod

1. Önce yalnız varsayılan (önerilen) ayarlarla uygulayın, öğretmen
   hesabıyla oturum açın:

   ```bash
   gsettings get org.cinnamon desktop-effects-workspace        # false
   gsettings get org.cinnamon.muffin unredirect-fullscreen-windows  # true
   gsettings get org.nemo.preferences show-image-thumbnails    # 'never'
   ```

2. Çözünürlük/Hz seçeneklerini ayrıca deneyin: ilk girişte ekranın kaç kez
   karardığı, yazı ve kalem çizgisi netliği, **dokunmatik hizası** (OTD ve
   Optical panellerde köşelere dokunarak), `xrandr | grep '\*'`.
3. Geri alın, yeni bir öğretmen hesabıyla girip ayarların uygulanmadığını
   doğrulayın.
