# m17 — Başarım

Bu belge, TiHA'nın `m17_performance` adımı (sihirbazda **"Başarım"**) için
mekanizmayı, ölçümleri ve gerçek tahtada deneme adımlarını anlatır.

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

## 3. Fare imleci ve klavye

### Dokunmatik kullanıldıktan sonra kaybolan imleç

Belirti: etapadmin oturumunda fare imleci görünür; oturum kapatılıp
ogretmen hesabıyla girilince fare hareket eder ve tıklar ama imleç
görünmez. Xorg ayarı (modesetting/SWcursor) ve mod değişiminde tazeleme
servisi bunu düzeltmedi.

Nedenler, olasılık sırasıyla:

1. **Muffin dokunmadan sonra imleci gizliyor.** Cinnamon'un pencere
   yöneticisi (mutter 3.36 çatalı, muffin 5.6) son girdi dokunmatikten
   gelince imleci `XFixesHideCursor` ile gizler, ancak kendi yüzeylerinde
   (panel, pencere çerçevesi, masaüstü kökü) dokunmatik olmayan bir işaretçi
   hareketi görünce geri açar. X11'de uygulama pencerelerinin üstündeki fare
   hareketi muffin'e ulaşmaz; imleç görünmez kalır. Gizleme X sunucusunda
   yapıldığı için donanımsal ya da yazılımsal imleç ayrımı sonucu
   değiştirmez. Öğretmenler tahtaya dokunduğu, etapadmin denemesi ise
   genelde yalnız fareyle yapıldığı için fark hesaplar arasında gibi görünür.
2. **Oturum açılışındaki mod değişimi.** Kullanıcının
   `~/.config/cinnamon-monitors.xml` dosyası (eta-resolution ya da hafif
   mod yazar) giriş ekranınkinden farklı bir mod taşıyorsa muffin onu
   autostart'tan önce uygular; tazeleme servisi o `MonitorsChanged`
   sinyalini kaçırır.

Çareler:

- **"Dokunmatik kullanıldıktan sonra da fare imlecini göster"**:
  `/usr/share/cinnamon/extensions/tiha-imlec@tiha/` altına küçük bir
  Cinnamon eklentisi kurulur. Eklenti
  `Meta.CursorTracker.get_for_display(global.display)` nesnesinin
  `visibility-changed` sinyalini dinler, imleç gizlenince
  `set_pointer_visible(true)` çağırır. Eklentiyi her oturum açılışında
  `/etc/xdg/autostart/tr.org.tiha.imlec-eklenti.desktop` →
  `/usr/local/bin/tiha-imlec-eklenti.py` kullanıcının
  `org.cinnamon enabled-extensions` listesine ekler (gschema override yalnız
  varsayılanı değiştirirdi, anahtarı kaydetmiş hesaplar kapsanmazdı).
  Kaldırılınca hesapların listesindeki kayıt da silinir. Geliştirme VM'inde
  denendi: eklenti yüklendi, `set_pointer_visible(false)` anında `true`'ya
  döndü.
- **Tazeleme servisi** artık oturum açılışından ~4 sn sonra da imleci bir
  kez tazeler (2. neden).

Gerçek tahtada teşhis (ogretmen oturumunda, imleç görünmezken):

```bash
E='gdbus call --session --dest org.Cinnamon --object-path /org/Cinnamon --method org.Cinnamon.Eval'
$E 'String(imports.gi.Meta.CursorTracker.get_for_display(global.display).get_pointer_visible())'
$E 'imports.gi.Meta.CursorTracker.get_for_display(global.display).set_pointer_visible(true)'
```

İlki `"false"` döner ve ikincisi imleci geri getirirse neden 1'dir. Fareyi
alt panelin üstünde oynatmak imleci geri getiriyorsa yine neden 1'dir.
Getirmiyorsa `gsettings get org.cinnamon.desktop.interface cursor-size`,
`cat ~/.config/cinnamon-monitors.xml` ve
`grep -iE 'SWcursor|Loading.*_drv' /var/log/Xorg.0.log` çıktılarına bakın.

### Giriş ekranında NumLock

Pardus giriş ekranı (`pardus-lightdm-greeter`, `[keyboard] numlock-on`
varsayılanı açık) açılırken bir kez `numlockx on` çalıştırır. Ekran
açıldıktan sonra takılan ya da o anda henüz tanınmamış klavyede NumLock
kapalı kalır.

`/etc/lightdm/lightdm.conf.d/99-tiha-numlock.conf` LightDM'e
`greeter-setup-script=/usr/local/sbin/tiha-giris-numlock` ekler. Betik giriş
ekranı her açıldığında root olarak çalışır, NumLock'u açar ve kendini arka
plana alır. Giriş ekranı süreci (lightdm kullanıcısının `greeter`
içeren süreci) yaşadıkça ilk 20 sn NumLock'u açık tutar, sonra yalnız
`/proc/bus/input/devices`'ta LED'li yeni bir klavye belirince yeniden açar.
Giriş ekranı kapanınca çıkar.

Güncellemelere karşı önlemler:

- Betik `numlockx` paketine dayanmaz; NumLock'u libX11'in
  `XkbLockModifiers` çağrısıyla (ctypes) açar. `numlockx` kaldırılsa da
  çalışır.
- LightDM ayarı `99-` önekiyle yazılır; `/etc/lightdm/lightdm.conf.d`
  içindeki diğer dosyalardan sonra okunur.
- Yedek yol: `/etc/pardus/greeter.conf.d/99-tiha-numlock.conf` giriş
  ekranının kendi `[keyboard] numlock-on=true` ayarını açık tutar.
- `/etc/apt/apt.conf.d/99-tiha-numlock` her paket işleminden sonra
  (`DPkg::Post-Invoke`) betiği `--onar` ile çalıştırır: silinmiş ya da
  değişmiş ayar dosyalarını yeniden yazar; `lightdm --show-config`'e göre
  `greeter-setup-script`'i başka bir dosya eziyorsa `journalctl -t
  tiha-numlock` günlüğüne uyarı düşer (`/etc/lightdm/lightdm.conf` en son
  okunur, orada tanımlanan bir betik bizimkini ezer).

## Geri al

İlk uygulamadan önceki durum `/var/lib/tiha/state/m17_performance/
original.json`'a ve dosya yedeklerine kaydedilir; sonraki uygulamalar bu
kaydı değiştirmez. Geri alma yalnızca TiHA'nın dokunduğu parçaları o
duruma döndürür, paketi TiHA kurduysa `apt-get purge` eder. logind
değişikliği yine yeniden başlatınca etkin olur.

### Hafif modun asimetrisi

`eta-light-mode` sistem tarafında yalnız iki dosya tutar. Ayarların
kendisi her oturum açılışında, autostart girdisi aracılığıyla,
kullanıcının **kendi dconf'una** yazılır. Paketin kapatma yolu
(`Action.py disable`) yalnız o iki sistem dosyasını siler; daha önce
giriş yapmış hesapların dconf değerleri olduğu gibi kalır. Yani paketin
kendi geri alması asimetriktir: "hafif mod kapalı ama ekran hâlâ hafif".

**Paketi kaldırmak çözüm değil.** `/etc/xdg/autostart/tr.org.eta.light-mode-autostart.desktop`
ve `/etc/eta-light-mode/settings.json` dpkg'nin dosya listesinde yok
(Action.py çalışma anında yazıyor); paketin `postrm`/`prerm` betiği de
yok. `apt remove` (hatta `purge`) bu iki dosyayı yerinde bırakır,
`/usr/bin/eta-light-mode`'u siler ve geriye her oturumda boşa çalışan bir
autostart girdisi kalır. Üstelik paket ETAP deposundan gelen standart
kurulumun parçası; kaldırmak imajı depodan uzaklaştırır ve bir sonraki
güncellemede geri gelebilir.

**TiHA ne yapıyor.** Hafif mod uygulanmadan hemen önce her hesabın
ilgili dconf anahtarları ve `~/.config/cinnamon-monitors.xml` dosyası
yedeklenir (`original.json` → `user_dconf`, `kullanici/<ad>.monitors.xml`).
Aşağıdaki üç durumda bu iz temizlenir:

| Tetikleyici | Kapsam |
|---|---|
| "Bu adımı geri al" | Sistem dosyaları + bütün hesaplar, bütün ayarlar |
| Hafif mod kutusu kaldırılıp "Uygula" | Sistem dosyaları + bütün hesaplar, bütün ayarlar |
| Tek bir alt kutu kaldırılıp "Uygula" | Yalnız o ayarın dconf yolları |

Kurallar:

- Bir anahtara **yalnız** oradaki değer hafif modun yazdığı değerse
  dokunulur; öğretmenin kendi seçtiği bir değer ezilmez.
- Kayıtta değeri olan hesapta özgün değer geri yazılır; kayıtta değeri
  olmayan hesapta anahtar **silinir**, yani sistem varsayılanına döner.
- Hafif mod uygulandıktan **sonra** açılan hesapların TiHA öncesi bir
  değeri yoktur; onlarda da anahtar silinir.
- Okuma başarısız olursa hesap sessizce atlanmaz, hata olarak bildirilir.
- Paket **kaldırılmaz**.

Yazma, hesabın kendisi olarak (`runuser -u`) ve temizlenmiş bir ortamda
(`env -i`) yapılır; hesabın açık oturumu varsa onun veri yoluna, yoksa
`dbus-run-session` ile açılan geçici veri yoluna yazılır. Ekranda
görülmesi için o hesabın oturumu yeniden açılmalıdır.

Adımın önizlemesindeki "İzi taşıyan hesap" satırı kaç hesabın
etkilendiğini gösterir.

#### Geri alınan dconf anahtarları

| Hafif mod ayarı | dconf yolu | Hafif değer |
|---|---|---|
| effects | `/org/cinnamon/desktop-effects-workspace` | `false` |
| compositor | `/org/cinnamon/muffin/unredirect-fullscreen-windows` | `true` |
| thumbnails | `/org/nemo/preferences/show-image-thumbnails` | `'never'` |
| directory-item-counts | `/org/nemo/preferences/show-directory-item-counts` | `'never'` |
| app-monitoring | `/org/cinnamon/enable-app-monitoring` | `false` |
| text-scaling | `/org/cinnamon/desktop/interface/font-name` | `'Ubuntu Regular 9.5'` |
| text-scaling | `/org/nemo/desktop/font` | `'Ubuntu Regular 9.5'` |
| text-scaling | `/org/cinnamon/desktop/wm/preferences/titlebar-font` | `'Ubuntu Bold 9.5'` |
| file-icon-size | `/org/nemo/icon-view/default-zoom-level` | `'small'` |
| low-resolution / low-refresh-rate | `~/.config/cinnamon-monitors.xml` | 1600x900 ya da 50 Hz |

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
