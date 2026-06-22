# 10. Adım — Orijinal eta-shutdown servisinin bypass edildiği nokta

Bu belge, TiHA'nın 10. adımı `m11_power_management` (sihirbazda
**"Otomatik kapanma"**) ile Pardus ETA paketinin sağladığı
`eta-shutdown` altyapısı üzerinde yapılan **kısmi** değişikliği ve
geri alma kapsamını açıklar.

## Kapsam

TiHA, Pardus ETA paketinin `eta-shutdown` altyapısını **tamamen
kaldırmaz**:

- `systemd` unit'i (`eta-shutdown.service`) — korunur
- `main.py` — korunur
- Yapılandırma dosyası `/etc/pardus/eta-shutdown.conf` — korunur
- "ETA Zamanlı Kapatma" GUI'si — değişmeden çalışmaya devam eder

Bypass edilen tek dosya `/usr/share/eta/eta-shutdown/src/service/service.py`'dir.
TiHA bu dosyayı, ek özellikler içeren genişletilmiş bir sürümle
değiştirir.

## Genişletilmiş servisin getirdikleri

- **2 dakikalık GTK geri sayım penceresi** — "10 dakika ertele" veya
  "Şimdi kapat" düğmesiyle.
- **Aktif grafik oturum tespiti** — `systemd-logind` (`loginctl`)
  üzerinden; kullanıcı login değilse uyarı **LightDM greeter
  ekranında** da gösterilir.
- **Çoklu X11 DISPLAY** (`:0`, `:1`, `:10`, `:11`) üzerinden idle
  algılama düzeltmesi.
- **Ekran-blank uyumluluk uyarısı** — idle eşiği sistemin
  ekran-blank süresinden uzun seçilirse "geri sayım kararmış
  ekranda görünmeyebilir" uyarısı verilir (yumuşak; apply'ı
  engellemez).
- **Geri sayım öncesi ekran uyandırma** — pencere açılırken
  `xset dpms force on` + `xset s reset` ile monitör güç
  tasarrufundan otomatik çıkarılır, böylece ekran kapalıyken
  bile pencere görünür kılınır.

## Yedek ve geri al

- Orijinal `service.py`, aynı dizinde `service.py.tiha-backup`
  adıyla yedeklenir.
- Kullanıcı oturumunda gösterilen geri sayım penceresi
  `/usr/local/sbin/tiha-shutdown-countdown.py` olarak yazılır.
- 10. adımın **"geri al"** işlemi yedeği geri yükler, servisi
  yeniden başlatır ve `tiha-shutdown-countdown.py` dosyasını siler.

<p align="center">
<a href="images/bonus-greeter-countdown.png"><img src="images/bonus-greeter-countdown.png" alt="Greeter ekranında 2 dakikalık geri sayım" width="60%"></a><br>
<sub><i>LightDM giriş ekranında etkin geri sayım — kullanıcı login değilken bile uyarı görünür.</i></sub>
</p>

## İlgili dosyalar

- Modül kodu: [`tiha/modules/m11_power_management.py`](../tiha/modules/m11_power_management.py)
- Orijinal Pardus servisi: `/usr/share/eta/eta-shutdown/src/service/service.py`
- Korunan systemd unit'i: `/lib/systemd/system/eta-shutdown.service`
- Korunan yapılandırma: `/etc/pardus/eta-shutdown.conf`
