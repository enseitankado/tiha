# TiHA makine arayüzü (yönetim araçları için)

Bu belge, TiHA'yı bir yönetim sunucusundan (ör. [Ankamon](https://github.com/enseitankado/ankamon))
SSH ile uzaktan kullanmak isteyen programlar içindir. TiHA'nın formlarını ve düğmelerini yeniden
kodlamadan kendi arayüzünüzde göstermeniz ve seçili tahtalarda uygulamanız için gerekenleri anlatır.

Temel fikir: **form tanımı TiHA'dan gelir.** Yönetim aracı adımları, ayarları, seçenekleri ve
düğmeleri `tiha --sema` çıktısından okuyup genel bir form çizer; işlemleri tahtada `tiha --json …`
ile çalıştırıp satır satır olayları okur. TiHA'ya yeni bir adım, ayar ya da düğme eklendiğinde
yönetim aracında kod değişmez.

Protokol sürümü: **1** (`protocol` alanı). Geriye uyumsuz bir değişiklikte artar; yeni alan eklemek
sürümü artırmaz, okuyan taraf tanımadığı alanları yok saymalıdır.

## Tahtada çalıştırma

TiHA tahtaya kurulmaz. İki yol var:

```sh
# 1) Her seferinde GitHub'dan en güncel sürüm (tahtanın internete çıkması gerekir)
ssh root@TAHTA 'curl -fsSL https://raw.githubusercontent.com/enseitankado/tiha/main/bootstrap.sh | bash -s -- --json -l'

# 2) Yönetim sunucusunun tahtaya koyduğu kopya (internetsiz / filtreli ağ)
#    /opt/tiha altında: tiha/, data/, isteğe bağlı eta-otp-cli/ ve eta-112/
ssh root@TAHTA 'TIHA_LOCAL_DIR=/opt/tiha bash /opt/tiha/bootstrap.sh --json -l'
```

- Doğrudan **root** oturumu kabul edilir; `sudo` ve parola sorulmaz. Sihirbaz (seçeneksiz çalıştırma)
  yalnız etapadmin'den açılır.
- `bootstrap.sh`'ın kendi durum satırları **stderr**'e gider; stdout yalnız TiHA'nın çıktısıdır.
- Yerel kopyada `eta-otp-cli/otp-cli.py` ya da `eta-112/eta-112.py` varsa bunlar indirilmez.
- `tiha --sema`, `-h`, `-V`, `-l` yetki istemez; diğer her şey root ister.

## Form tanımı: `tiha --sema`

Tek bir JSON nesnesi yazar (`--json` ile birlikte verilirse tek satırlık bir `schema` olayı).
Tahtaya bakmaz; aynı TiHA sürümünün çıktısı her tahtada aynıdır, bu yüzden sürüm başına bir kez
alınıp önbelleğe konabilir.

```json
{
  "type": "schema",
  "protocol": 1,
  "tiha_version": "0.1.98",
  "presentation": { … },
  "catalogs": { "meb_branslar": { … } },
  "steps": [
    {
      "number": 5, "slug": "merkezi-log-sunucusu", "id": "m06_remote_syslog",
      "title": "Merkezi log sunucusu", "menu_title": "Merkezi log sunucusu",
      "rationale": "…", "apply_hint": "…",
      "undo_supported": true, "experimental": false,
      "scope": "both", "remote_warning": "", "undo_remote_warning": "",
      "fields": [ { "key": "syslog_host", "type": "text", "label": "…", "help": "…",
                    "required": true, "secret": false, "board_dependent": true } ],
      "actions": [ { "name": "log-sunucusunu-test-et", "key": "test_log_server",
                     "label": "Log Sunucusunu Test Et", "help": "…", "destructive": false,
                     "board_dependent": false } ]
    }
  ]
}
```

### Adım (`steps[]`)

| Alan | Anlamı |
|---|---|
| `number` | Sihirbazın sol menüsündeki numara. Adım eklenince değişebilir; kalıcı kimlik olarak `id` kullanın. |
| `slug` | Komut satırındaki kısa ad. Değişse bile eski adlar çalışmaya devam eder. |
| `id` | Kalıcı adım kimliği; komut satırı da kabul eder (`-u m06_remote_syslog`). |
| `undo_supported` | `-g` ile geri alınabilir mi. |
| `scope` | `both`: kurulu tahtada da anlamlı. `image`: **yalnız imajı alınacak kaynak tahtada** çalıştırılmalı; yönetim aracı kurulu tahtalarda göstermemeli ya da ayrıca onay istemeli. |
| `remote_warning` | Doluysa uygulamadan önce kullanıcıya gösterilmesi gereken uyarı (ör. SSH anahtarlarını yeniler). |
| `undo_remote_warning` | Geri almanın uzaktan erişimi etkilediği durum (ör. SSH sunucusunu kaldırır). |
| `shared_secrets` | Adım tahtada rastgele gizli değer üretiyorsa (PIN anahtarları) `{field, source_file}`; yoksa `null`. Bkz. "Ortak gizli değerler". |
| `view` | Sayfanın görünüşü: `rationale_mode` (`inline`: açıklama sayfada; `dialog`: başlığın yanında "?" düğmesi açar), `doc_url`/`doc_label` (açıklamanın altındaki belge bağlantısı), `output_window` (Uygula'da canlı çıktı penceresi açılır), `preview_tabular` ("Mevcut durum" hizalı tablo; satır kaydırılmaz), `auto_apply` (sayfaya gelince kendiliğinden uygulanır, Uygula düğmesi yok). |

### Ayar (`fields[]`)

| Alan | Anlamı |
|---|---|
| `key` | Ayarın adı; `-d key=değer` ve `--degerler` bununla verilir. |
| `type` | `bool`, `text`, `password`, `number`, `spin`, `select`, `textarea`, `branch_accounts`, `file`; görsel öğeler `heading` (yalnız başlık: `label`) ve `readonly` (değiştirilemez bilgi). |
| `label`, `help`, `help_more` | Etiket ve yardım. `help_more` katlanır ayrıntıdır. Metinlerde `[[ad]]` TiHA'nın bağlantı işaretidir (ör. `[[ankamon]]`); düz metin olarak göstermek yeterli. |
| `default` | Şemadaki varsayılan. Tahtanın gerçek değeri `-a --json` ile alınır. |
| `options` | `select` için geçerli değerler (değer = görünen metin). |
| `min`, `max`, `step` | `spin` sınırları. |
| `placeholder`, `hint` | Boş kutuda gösterilecek örnek; kısa ipucu. |
| `required` | Boş geçilemez. |
| `secret` | Parola ya da gizli değer: maskeli kutu, kayıtlarda gösterilmez; tahtadan değeri **okunmaz** (`-a` yalnız `is_set` verir). |
| `enable_when_field` | Bu ayar, adı verilen onay kutusu işaretliyken etkindir. |
| `enable_when_any` | Listedeki onay kutularından en az biri işaretliyken etkindir. |
| `enables` | Bu onay kutusu listedeki ayarları etkinleştirir. |
| `locked` | Kullanıcı değiştiremez (bilgi amaçlı). |
| `allowed_chars` | İzinli karakterler (düzenli ifade karakter sınıfı içeriği, ör. `A-Za-hj-z0-9`). |
| `remote_warning`, `remote_warning_when` | Bu ayar `remote_warning_when` değerini alırsa (`*`: boş olmayan herhangi bir değer) uyarı gösterilmeli. Ör. `enable_ssh=False` SSH'yi kapatır; `root_password` verilirse root parolası değişir. |
| `board_dependent` | Değeri, görünürlüğü ya da etiketi tahtadan tahtaya değişir; ayrıntı `-a --json`'dadır. |
| `machine_only` | Sihirbazda çizilmez; yalnız yönetim aracının kullandığı alan (ör. ortak PIN anahtarları). Formda gösterilmemeli. |
| `view` | Alanın sihirbazdaki görünüşü (aşağıda). |
| `catalog`, `value_format` | Branş seçici (`branch_accounts`): seçenekler `catalogs.meb_branslar`'da; değer `{school_type, branches, unselected}` JSON metnidir. |

`view` alanları:

| Alan | Anlamı |
|---|---|
| `widget` | `checkbox`, `entry`, `password_entry`, `entry_numeric`, `spin_button`, `dropdown`, `multiline_text`, `section_heading`, `readonly_entry`, `branch_picker` (okul türü + branş listesi, elle branş eklenebilir), `file_chooser`. |
| `width_chars` | Kısa değerli kutu: sola yaslı, bu kadar karakter genişliğinde (0: içeriğe göre). |
| `fill_row` | Kutu, kontrol sütununun tamamını kaplar. |
| `height_px`, `monospace` | Çok satırlı kutunun yüksekliği; eş aralıklı yazı. |
| `show_toggle` | Parola kutusunda göz simgesiyle göster/gizle. |
| `strength_meter` | Parola kutusunun altında yazarken güç göstergesi (`presentation.password_strength`). |
| `help_mode` | `auto`: açıklama `presentation.form.help_bubble_over_lines` satırı aşarsa alanın yanındaki "?" balonunda, aşmazsa alanın altında; `bubble`: her zaman balonda; `folded`: alanın altında kapalı bir katlayıcıda (`presentation.form.help_folded_label`). |
| `help_more_label` | `help_more` katlayıcısının başlığı. |
| `style` | `destructive`: kırmızı/tehlikeli işlem düğmesi. |

### Düğme (`actions[]`)

| Alan | Anlamı |
|---|---|
| `name` | `-x ADIM name` ile çalıştırılır. |
| `destructive` | Geri dönüşü olmayan bir işlem. |
| `confirm` | `{title, message}`: çalıştırmadan önce sorulacak soru; tahtada `-e` olmadan çalışmaz. |
| `board_dependent` | Bazı tahtalarda kullanılamaz ya da etiketi değişir (`-a --json`). |
| `output_window` | Çıktı penceresinin sihirbazdaki boyutu `[genişlik, yükseklik]` (yoksa `presentation.output_window.default_size`). |
| `output_window_buttons` | Çıktı penceresinin altındaki ek düğmeler; `desktop_only: true` olan tahtanın ekranında program açar, uzaktan gösterilmemeli. |

### Görünüş (`presentation`)

Yönetim aracının formu sihirbaza görsel olarak benzer çizebilmesi için sayfa düzeni, ölçüler,
arayüz metinleri ve renkler. Değerler sihirbazın kendi kullandığı kaynaktan (`tiha/ui/layout.py`,
`data/styles.css`) üretilir; sihirbazda değişen bir ölçü ya da renk buraya kendiliğinden yansır.

| Alan | Anlamı |
|---|---|
| `page_order` | Sayfadaki bölümlerin yukarıdan aşağı sırası: başlık, deneysel adım şeridi, tahtaya özgü not, açıklama, "Mevcut durum", form, sonuç, alt düğme çubuğu. |
| `page` | Sayfa kenar boşluğu ve bölümler arası boşluk (piksel). |
| `heading` | Açıklama kaç cümleyi aşınca "?" penceresine taşınır; düğmenin metni ve ipucu. |
| `experimental_banner` | `experimental: true` adımların başlık altı uyarı şeridi metni. |
| `notice` | `-a`'daki `notice.kind` → stil (`warning`, `info`). |
| `preview` | "Mevcut durum" katlayıcısı: başlığı, kapalı başlar, eş aralıklı, en fazla yükseklik. |
| `form` | İki sütunlu ızgara (ad, kontrol): ad sütunu genişliği (karakter), aralıklar, açıklama balonu kuralı, tür başına varsayılan kutu genişlikleri, açıklama metnindeki işaretler (`help_markers`: `[[ankamon]]` bağlantı, `[[dikkat]]…[[/dikkat]]` kırmızı uyarı). |
| `previous_apply` | "Daha önce uygulandı" şeridinin metni (`{when}` `when_format` ile biçimlenir, `{summary}`) ve geri al düğmesi. |
| `sidebar` | Sol menüdeki adım işaretleri: `-l --json`'daki `status.state` → `mark` (✓, ⚠ ya da boş), stil rolü ve ipucu (`{summary}`). |
| `action_bar` | Alt çubuktaki düğmeler (Geri, Uygula, İleri) ve Uygula'nın yanındaki ipucu (`{hint}` = adımın `apply_hint`'i). |
| `password_strength` | Güç göstergesinin metni, 0–4 puan etiketleri ve renkleri. Puanı TiHA hesaplar; makine arayüzü yalnız görünüşü verir. |
| `output_window` | Canlı çıktı penceresi: varsayılan boyut, eş aralıklı, "çalışıyor" metni. |
| `styles` | Rol → CSS özellikleri: `heading`, `rationale` (açıklama/yardım yazısı), `form_section`, `banner_info`, `banner_warning`, `apply_hint`, `result_ok`, `result_fail`, `preview`, `preview_box`, `help_button`, `help_dot`, `step_active`, `step_done`, `step_failed`, `sidebar`. Değerler web CSS'idir; sistem teması renkleri `var(--theme-fg-color)`, `var(--theme-selected-bg-color)` gibi değişkenlerle gelir, okuyan taraf kendi temasından tanımlar. |

Diğer alanlardaki `style` adları (`banner_warning`, `rationale` …) `styles` içindeki rollere işaret eder.

## Olaylar: `--json`

`-l`, `-V`, `-a`, `-o`, `-u`, `-x`, `-g`, `-b`, `-t` seçenekleriyle `--json` verilirse stdout'a her
satıra bir JSON nesnesi yazılır (JSON Lines). `type` alanı olayın türüdür. Adımların ya da alt
süreçlerin kendiliğinden bastığı her şey stderr'e yönlendirilir; stdout'ta JSON dışında satır olmaz.

Her çalıştırma **`end`** olayıyla biter. `end` gelmeden bağlantı kapandıysa çalışma yarıda kesilmiştir
(SSH koptu, süreç öldürüldü).

| `type` | Ne zaman | Alanlar |
|---|---|---|
| `error` | Kullanım, tanımsız adım/ayar, yetki, okunamayan dosya | `message` |
| `steps` | `-l` | `tiha_version`, `protocol`, `status_available`, `steps[]` (`number`, `slug`, `id`, `title`, `menu_title`, `scope`, `undo_supported`, root ile `status`) |
| `version` | `-V` | `tiha_version`, `protocol` |
| `params` | `-a ADIM` | `step` (o adımın `--sema` tanımı), `fields[]`, `actions[]` (tahtadaki durum; aşağıda) |
| `actions` | `-x ADIM` (eylem verilmeden) | `step`, `actions[]` (tanım + `available`) |
| `preview` | `-o ADIM` | `step`, `text` |
| `start` | `-u`, `-x`, `-g`, `-t` başlarken | `command` (`run`, `action`, `undo`, `batch`), `step` / `steps`, `action` |
| `step_start` | `-t`: her adım başlarken | `step`, `index`, `total` |
| `progress` | İşlem sürerken, satır satır | `text` |
| `confirm_required` | Onay gereken işlem `-e` olmadan istendi (çıkış 13) | `title`, `message` |
| `prompt_answered` | `-g`: adım geri almadan önce soru soruyor; `-e` varsa "evet", yoksa güvenli "hayır" seçildi | `title`, `message`, `answer` |
| `result` | İşlem bitti | `step`, `success`, `not_applicable`, `summary`, `details`, `warning`, `copyable`, `secrets_withheld`, `data` (gizliler temizlenmiş), `exit_code` |
| `batch_result` | `-t` bitti | `ok`, `failed`, `unknown` |
| `preset` | `-b` | `path`, `steps[]` (`step`, `params` — gizliler temizlenmiş), `unknown` |
| `end` | Her zaman, en son | `exit_code` |

`step` nesnesi her yerde `{number, slug, id, title, menu_title}` biçimindedir.

### Gizli değerler olaylara girmez

Olaylar yönetim aracının görev kaydına (veritabanına) yazılacağı varsayılır. Bu yüzden:

- Verilen parolalar (`password` türü ya da adı parola/anahtar çağrıştıran alanlar) `summary`,
  `details`, `warning`, `copyable` ve `progress` metinlerinden silinir; yerine `***` yazılır.
- `data` içindeki gizli anahtarlar `***` olur (ör. BIOS'tan okunan yönetici parolası).
- Adımın metin raporu üretilen gizli değerleri içeriyorsa (PIN anahtarları) `details` ve
  `copyable` boş gelir, `secrets_withheld: true` olur. Anahtarların kendisi yalnız dosya kanalıyla
  alınır (bkz. "Ortak gizli değerler").

### `-l --json`: bütün adımların bu tahtadaki durumu

root ile çalıştırılırsa her adıma `status` eklenir; tek çağrıda sol menünün işaretleri çıkar:

```json
{"type": "steps", "status_available": true, "steps": [
  {"number": 3, "slug": "pin-anahtarlari", "id": "m03_otp_secrets", …,
   "status": {"state": "applied", "summary": "3 PIN anahtarı üretildi …", "when": "2026-11-08T19:31:07+00:00"}},
  {"number": 4, "slug": "ssh-samba", …, "status": {"state": "none"}}]}
```

`state`: `none` (hiç uygulanmadı), `applied` (uygulandı, menüde ✓), `off` (kutular boş bırakılıp
uygulandı, özellik kapalı; işaret yok), `failed` (son deneme başarısız, ⚠), `skipped` (bu tahtada
uygulanamaz), `undone` (geri alındı). Sihirbazın kendi menüsü de aynı kuralı kullanır. root değilse
günlük okunamaz: `status_available: false`, `status` alanı gelmez.

### `-a ADIM --json`: tahtadaki durum

```json
{"type": "params", "step": {…},
 "fields": [{"key": "syslog_host", "value": "10.0.0.5"},
            {"key": "root_password", "value": null, "is_set": false},
            {"key": "supervisor_password", "value": null, "is_set": false, "available": false}],
 "actions": [{"name": "log-sunucusunu-test-et", "available": true},
             {"name": "fazladan-hesaplari-sil", "available": true, "label": "Fazladan Hesapları Sil (3)"}]}
```

- `value`: sihirbazın o tahtada formu açarken göstereceği değer (tahtanın gerçek durumu).
  Gizli alanlarda `null`; yerine `is_set` verilir.
- `available`: alan ya da düğme o tahtada görünür mü (ör. BIOS desteklenmiyorsa `false`).
  Görünmeyen düğme `-x` ile de çalışmaz (çıkış 15).
- `enabled_also`: alan, bağlı olduğu kutu işaretli olmasa da etkin (tahtaya özgü durum).
- `label`: düğmenin o tahtadaki etiketi şemadakinden farklıysa.
- `notice`: sayfanın üstündeki tahtaya özgü vurgulu not `{kind, text}` (ör. SSH adımında "root
  parolası tanımlı değil"); yoksa alan gelmez.
- `previous_apply`: adım bu tahtada daha önce uygulandıysa `{when, summary}` ("Daha önce uygulandı"
  şeridi); `when` ISO 8601 zaman damgasıdır.

Çok tahtada aynı formu gösterirken her tahtanın `-a --json` çıktısı toplanır; farklı değerler
"tahtalar arasında farklı" diye gösterilebilir.

## Değer vermek

- `-d key=değer` (birden çok kez). Kabukta çok satır için `\n` yazılır.
- `--degerler DOSYA` ya da `--degerler -` (standart girdi): `{"key": değer, …}` JSON nesnesi. Değerler
  metin, sayı ya da `true`/`false` olabilir. **Parolalar için bunu kullanın**: komut satırına yazılan
  değer tahtada `ps` çıktısında ve kabuk geçmişinde görünür, standart girdi görünmez.
- İkisi birlikte verilirse önce dosya, sonra `-d` uygulanır.
- **Yazılmayan ayarlar tahtanın şu anki değerinde kalır.** Yönetim aracı yalnız kullanıcının
  değiştirdiği ayarları göndermelidir; böylece her tahta kendi ayarlarını korur.
- Onay kutusu: `evet/hayır`, `true/false`, `1/0`. Seçim listesi: `options`'tan biri. Geçersiz değer
  hiçbir şey yapılmadan 12 ile reddedilir (`error` olayı nedeni söyler).
- Onay isteyen işlemler (hesap silme, PIN anahtarlarını silme…) `-e` olmadan çalışmaz:
  `confirm_required` olayı gelir, çıkış 13. Kullanıcı onaylarsa aynı komut `-e` ile yeniden çalıştırılır.

```sh
echo '{"root_password": "Gizli.Parola1"}' | ssh root@TAHTA 'TIHA_LOCAL_DIR=/opt/tiha bash /opt/tiha/bootstrap.sh --json -u 2 --degerler -'
```

## Ortak gizli değerler (PIN anahtarları)

PIN anahtarları her tahtada rastgele üretilir. İmaj akışında sorun yoktur: anahtarlar kaynak tahtada
bir kez üretilir, imajla bütün klonlara aynı gider. Bağımsız kurulmuş tahtalara ayrı ayrı uygulanırsa
her tahta kendi anahtarını üretir; bir öğretmene tahta sayısı kadar kart gerekir.

Adımın `shared_secrets` alanı bunun için yol gösterir
(`{"field": "shared_pin_secrets", "source_file": "/etc/otp-secrets.json"}`):

1. Adımı **bir** tahtada normal uygulayın (`-u 3`).
2. O tahtanın `source_file` dosyasını SFTP ile alın (root'a açık, `{"kullanıcı": "BASE32", …}`).
   Olay akışından değil dosyadan alın: anahtarlar görev kaydına girmesin.
3. Aynı ayarlarla diğer tahtalara uygularken dosyanın içeriğini `field` ile, standart girdiden verin:

```sh
python3 - <<'PY' | ssh root@TAHTA 'TIHA_LOCAL_DIR=/opt/tiha bash /opt/tiha/bootstrap.sh --json -u 3 --degerler -'
import json
print(json.dumps({"teacher_names": "AYŞE YILMAZ\nMEHMET DEMİR",
                  "shared_pin_secrets": open("otp-secrets-tahta1.json").read()}))
PY
```

O tahtada anahtar alacak her hesap ortak değeri alır (hesabın anahtarı farklıysa değiştirilir ve
sonuçta uyarı gelir). Ortak listede olup bu tahtada anahtar almayacak hesaplar atlanır; ortak listede
olmayan, bu tahtaya özgü hesaplar (ör. yalnız burada açılmış bir branş hesabı) kendi anahtarını
alır. `result.data`'da yalnız hesap adlarıyla bildirilir: `shared_synced`, `shared_not_here`,
`board_only_keys`. Bozuk bir liste (JSON değil, Base32 olmayan anahtar) hiçbir şeye dokunulmadan
reddedilir.

Yazdırılabilir PIN kâğıdının tahtadaki yolu `result.data.paper_file`'dadır (yalnız root okur; SFTP ile
alınır). Ortak anahtarla uygulamada öğretmenlerin kâğıdı ilk tahtanınkidir; `board_only_keys` dolu
tahtaların ek kartları kendi kâğıtlarındadır.

## Çıkış kodları

| Kod | Anlamı |
|---|---|
| 0 | Başarılı |
| 1 | Adım ya da eylem başarısız (`result.summary` nedeni söyler) |
| 2 | Yetki yok |
| 3 | Çalışma dizini oluşturulamadı |
| 10 | Ön ayar dosyası okunamadı |
| 11 | Tanımsız adım |
| 12 | Geçersiz ayar, değer ya da eylem adı |
| 13 | Onay gerekiyor (`-e` verilmedi) |
| 14 | Geri alınacak uygulama yok |
| 15 | Eylem bu tahtada kullanılamaz |
| 16 | Bu tahtada başka bir TiHA işlemi sürüyor (sihirbaz ya da başka bir uzak iş); bekleyip yeniden deneyin |
| 64 | Hatalı kullanım |

## Aynı anda tek işlem

Uygulama, geri alma ve düğme eylemleri `/run/lock/tiha.lock` üzerinde kilit tutar. Biri tahtada
sihirbazda "Uygula"ya basmışken gelen uzak iş beklemeden 16 ile döner (ve tersi); `result.summary`
kilidi kimin tuttuğunu söyler. Sihirbazın yalnız açık durması kilit tutmaz.

## Uzaktan uygularken dikkat

- `scope: "image"` olan adımı (İmaj öncesi temizlik) kurulu bir tahtada çalıştırmayın: makine
  kimliğini ve SSH sunucu anahtarlarını yeniler; yönetim aracının o tahta için kayıtlı SSH anahtarı
  uyuşmaz.
- `remote_warning` taşıyan ayarlar bağlantıyı koparabilir: SSH'yi kapatmak, root parolasını
  değiştirmek. Root parolasıyla bağlanan bir yönetim aracı, TiHA'nın atadığı yeni parolayı kendi
  kaydına da yazmalıdır.
- PIN anahtarları her tahtada ayrı üretilir.

## Uyumluluk

`python3 tools/cli_check.py` bu sözleşmenin TiHA tarafını her sürümden önce denetler: form
tanımının her adım için üretilebildiğini, düğme ve kısa adların çözüldüğünü, yeni alan türlerinin
tanındığını.
