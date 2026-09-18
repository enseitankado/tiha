"""Özet raporunun adım anlatıcıları ve adımlar arası denetimler.

Her anlatıcı ``(ctx, rep)`` alır: ``ctx`` o adımın maskelenmiş
parametrelerini, modülün ``ApplyResult.data``'sını ve düğme eylemlerini
taşır; anlatıcı ``rep.done`` (yaptıklarınız), ``rep.tests`` (klonda
deneyin) ve ``rep.notes`` (dikkat) listelerini doldurur.

Yazım kuralları:
* "Yaptıklarınız" maddeleri ikinci çoğul şahıs, geçmiş zaman:
  "…ayarladınız", "…oluşturdunuz".
* Klon test maddeleri emir kipinde ve somut: ne yapılacak, neyin
  görülmesi gerektiği.
* Parola, PIN anahtarı gibi gizli değerler asla yazılmaz; yalnız
  ayarlandıkları söylenir.
* Parametre kaydı olmayan eski günce kayıtlarında (rapor özelliğinden
  önce uygulanmış adımlar) anlatıcı ``data``'ya ve modülün özet satırına
  düşer; bilmediği bir şeyi uydurmaz.
"""

from __future__ import annotations

from .report import StepContext, StepReport

# ---------------------------------------------------------------------------
# Ortak yardımcılar
# ---------------------------------------------------------------------------


def _join(items: list[str]) -> str:
    """["a", "b", "c"] → "a, b ve c"."""
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " ve " + items[-1]


def narrate_failed(ctx: StepContext, rep: StepReport) -> None:
    rep.done.append(
        "Bu adım başarısız oldu"
        + (f": {ctx.summary.rstrip('.')}." if ctx.summary else ".")
    )
    rep.notes.append(
        "Başarısız adım imaja yarım bir değişiklik bırakmış olabilir. İmajı "
        "almadan önce adımı düzeltip yeniden uygulayın ya da geri alın."
    )


# ---------------------------------------------------------------------------
# m09 — Sistem güncellemesi
# ---------------------------------------------------------------------------


def narrate_m09(ctx: StepContext, rep: StepReport) -> None:
    rep.done.append(
        "Sistem güncellemesini çalıştırdınız: depo yapılandırması denetlendi, "
        "paketler en güncel sürüme yükseltildi ve gereksiz paketler temizlendi."
    )
    rep.tests.append(
        "Güncelleme yeni çekirdek ve sürücüler getirmiş olabilir. Klonda "
        "ekranın, dokunmatiğin, kalemin, sesin ve ağın (kablolu ve kablosuz) "
        "çalıştığını doğrulayın."
    )
    rep.tests.append(
        "EBA QR girişini ve sık kullanılan ETAP uygulamalarını klonda açıp "
        "deneyin."
    )
    rep.tests.append(
        "Terminalde `sudo apt-get update` komutunun hatasız bittiğini ve "
        "/etc/apt/sources.list dosyasındaki depo satırlarının beklediğiniz "
        "gibi olduğunu doğrulayın. Bu adım bozuk depo dosyasını yeniden "
        "yazabilir; kurum içi özel depo satırlarınız varsa silinmiş olabilir."
    )
    rep.notes.append(
        "Paket yükseltmeleri geri alınamaz: bir güncellemeden kaynaklanan "
        "sorun imajla birlikte bütün klonlara gider."
    )


# ---------------------------------------------------------------------------
# m01 — Kullanıcı parolaları / yerel hesaplar
# ---------------------------------------------------------------------------

import re as _re  # noqa: E402

_RESERVE_RE = _re.compile(r"^ogretmen\.?\d+$")


def _m01_passwords(ctx: StepContext) -> tuple[list[str], list[str]]:
    """(atananlar, atanamayanlar). Eski kayıtlarda özet metninden çıkarılır."""
    d = ctx.data
    if "passwords_set" in d:
        return list(d.get("passwords_set") or []), list(d.get("passwords_failed") or [])
    m = _re.search(r"([a-z/]+) parolaları atandı", ctx.summary)
    ok = m.group(1).split("/") if m else []
    failed = [
        u for u, key in (("root", "root_password"), ("etapadmin", "admin_password"))
        if ctx.secret_set(key) and u not in ok
    ]
    return ok, failed


def narrate_m01(ctx: StepContext, rep: StepReport) -> None:
    d = ctx.data
    ok, failed = _m01_passwords(ctx)
    admins = [u for u in ("root", "etapadmin") if u in ok]
    if admins:
        rep.done.append(
            f"{_join(admins)} "
            + ("parolalarını" if len(admins) > 1 else "parolasını")
            + " ayarladınız."
        )
    if "ogretmen" in ok:
        rep.done.append(
            "Ortak öğretmen hesabının (ogretmen) parolasını ayarlayıp hesabın "
            "kilidini açtınız."
        )
    for user in failed:
        rep.notes.append(
            f"{user} parolasını ayarlamaya çalıştınız ama işlem başarısız oldu; "
            "bu hesabın parolası değişmedi."
        )
    if d.get("teacher_skipped_no_account"):
        rep.notes.append(
            "Öğretmen parolası girdiniz ama tahtada ortak ogretmen hesabı "
            "olmadığı için parola hiçbir hesaba uygulanmadı."
        )
    for user, files in (d.get("keyrings_moved") or {}).items():
        if files:
            rep.done.append(
                f"{user} hesabının eski parolayla şifreli kalan anahtarlık "
                "dosyalarını kenara aldınız; ilk girişte yeni parolayla "
                "yenisi oluşacak."
            )

    created = list(d.get("created_reserve") or [])
    skipped = list(d.get("skipped_reserve") or [])
    if created:
        rep.done.append(
            f"{len(created)} yedek öğretmen hesabı oluşturdunuz "
            f"({created[0]}{' – ' + created[-1] if len(created) > 1 else ''}). "
            "Hesaplar parolasız (kilitli) açıldı; bu hesaplara PIN anahtarı "
            "adımında üretilen kodlarla girilir."
        )
    elif skipped:
        rep.done.append(
            f"Yedek hesap sayısını {len(skipped)} olarak bıraktınız; bu "
            "hesaplar zaten vardı, yeni hesap açılmadı."
        )
    removed = list(d.get("removed_users") or [])
    if removed:
        rep.done.append(
            f"{_join(removed)} ortak "
            + ("hesaplarını" if len(removed) > 1 else "hesabını")
            + " ev dizinleriyle birlikte sildiniz."
        )
    student_removed = bool(ctx.action("remove_student_user_action"))
    if student_removed:
        rep.done.append("Öğrenci (ogrenci) hesabını ev diziniyle birlikte sildiniz.")

    # Klonda deneyin
    if "etapadmin" in ok:
        rep.tests.append(
            "Klonu yeniden başlatıp giriş ekranında etapadmin ile yeni parolayla "
            "oturum açın; \"giriş anahtarlığınızın parolası uyuşmuyor\" uyarısı "
            "çıkmamalı."
        )
    if "root" in ok:
        rep.tests.append("Klonda bir terminalde `su -` ile yeni root parolasını deneyin.")
    if "ogretmen" in ok:
        rep.tests.append("Ortak öğretmen hesabına (ogretmen) yeni parolayla girin.")
    if created or skipped:
        rep.tests.append(
            "Giriş ekranında yedek öğretmen hesaplarının göründüğünü doğrulayın. "
            "Terminalde `id ogretmen1` çıktısında audio, video, plugdev gibi "
            "cihaz gruplarının bulunduğunu kontrol edin."
        )
    if student_removed or "ogrenci" in removed:
        rep.tests.append(
            "Giriş ekranında öğrenci hesabının artık görünmediğini doğrulayın."
        )
    if admins:
        rep.notes.append(
            f"{_join(admins)} "
            + ("parolaları" if len(admins) > 1 else "parolası")
            + " bütün klonlarda aynı olacak ve geri okunamaz. Parolayı güvenli "
            "bir yerde saklayın; unutulursa her tahtada ayrı ayrı erişim sorunu "
            "yaşanır."
        )


# ---------------------------------------------------------------------------
# m02 — Her açılışta parola temizliği
# ---------------------------------------------------------------------------


def narrate_m02(ctx: StepContext, rep: StepReport) -> None:
    rep.done.append(
        "Her açılışta etapadmin dışındaki tüm yerel hesapların (ortak "
        "öğretmen/öğrenci, yedek ve kişisel öğretmen hesapları) parolasını "
        "rastgele bir değere çeviren açılış servisini kurdunuz. Bu hesaplara "
        "artık yalnız EBA QR, PIN ya da USB bellek ile girilebilir."
    )
    rep.tests.append(
        "Klonu yeniden başlatın; etapadmin ile parolayla girebildiğinizi "
        "doğrulayın (bu hesaba dokunulmaz)."
    )
    rep.tests.append(
        "Ortak öğretmen hesabına bilinen parolasıyla girmeyi deneyin; giriş "
        "reddedilmeli."
    )
    rep.tests.append(
        "Bir öğretmen ve bir yedek hesaba PIN ile (ya da USB ile) girin; "
        "tahtayı yeniden başlattıktan sonra da girilebildiğini doğrulayın."
    )
    rep.tests.append(
        "Terminalde `journalctl -t tiha-boot-wipe -b` çıktısında HATA satırı "
        "olmadığını doğrulayın."
    )
    rep.notes.append(
        "Servis bütün klonlarda her açılışta çalışır. PIN ya da USB ile giriş "
        "kurulu ve çalışır değilse öğretmenler hiçbir tahtaya giremez; "
        "yalnız etapadmin kalır."
    )


# ---------------------------------------------------------------------------
# m03 — Öğretmen PIN anahtarları
# ---------------------------------------------------------------------------


def narrate_m03(ctx: StepContext, rep: StepReport) -> None:
    d = ctx.data
    passed = [str(n) for n in d.get("passed_names") or []]
    created = [str(n) for n in d.get("created_users") or []]
    preserved = [str(n) for n in d.get("preserved_users") or []]
    grouped = [str(n) for n in d.get("grouped_users") or []]
    used_tool = d.get("used_tool", True)

    reserves = [n for n in passed if _RESERVE_RE.match(n)]
    teachers = [
        n for n in passed
        if not _RESERVE_RE.match(n) and n not in ("etapadmin", "ogretmen")
    ]
    new_keys = [n for n in created if not n.startswith("@")]

    if teachers:
        if used_tool:
            rep.done.append(
                f"Listeye girdiğiniz {len(teachers)} öğretmen için PIN anahtarı "
                "hazırladınız. Bu öğretmenlerin tahtadaki kişisel hesabı ilk "
                "EBA QR girişlerinde oluşacak; PIN ile giriş ancak bundan sonra "
                "çalışır."
            )
        else:
            rep.done.append(
                f"Listeye girdiğiniz {len(teachers)} öğretmen için hem yerel "
                "hesap açtınız hem PIN anahtarı ürettiniz (TiHA'nın dahili "
                "yolu; kullanıcı adları 'ad.soyad' biçiminde)."
            )
            rep.notes.append(
                "PIN aracı (eta-otp-cli) kullanılamadı, dahili yol devreye girdi. "
                "Bu yolun açtığı kullanıcı adları EBA QR'ın açacağı adlardan "
                "farklı olabilir; klonda mutlaka deneyin."
            )
    if reserves:
        span = reserves[0] + (f" – {reserves[-1]}" if len(reserves) > 1 else "")
        rep.done.append(
            f"Tahtadaki {len(reserves)} yedek öğretmen hesabını ({span}) PIN "
            "listesine eklediniz."
        )
    for user, label in (("etapadmin", "Sistem yöneticisi (etapadmin)"),
                        ("ogretmen", "Ortak öğretmen hesabı (ogretmen)")):
        if user in created:
            rep.done.append(f"{label} için de PIN anahtarı ürettiniz.")
        elif user in preserved:
            rep.done.append(f"{label} için mevcut PIN anahtarını korudunuz.")
    if new_keys:
        rep.done.append(
            f"Toplam {len(new_keys)} yeni PIN anahtarı üretildi ve imaja girecek."
        )
    if preserved:
        rep.done.append(
            f"{len(preserved)} hesabın mevcut PIN anahtarına dokunulmadı; "
            "öğretmenlerin telefonlarındaki kayıtlar geçerli kalıyor."
        )

    group_new = "@ogretmenler" in created
    if group_new:
        rep.done.append(
            "ogretmenler grubu için ortak PIN anahtarı oluşturdunuz; bu kod "
            "gruba üye kişisel ve yedek öğretmen hesaplarında geçerli."
        )
    elif ctx.flag("make_group_pin"):
        rep.done.append(
            "ogretmenler grubu için ortak PIN'in açık kalmasını seçtiniz; "
            "mevcut ortak anahtar korundu."
        )
    if grouped:
        rep.done.append(f"{len(grouped)} hesabı ogretmenler grubuna eklediniz.")
    if d.get("auto_group_service_installed"):
        rep.done.append(
            "EBA QR ile sonradan açılacak öğretmen hesaplarını ogretmenler "
            "grubuna kendiliğinden ekleyen servisi etkinleştirdiniz."
        )
    total = d.get("total_users")
    if d.get("greeter_cache_applied"):
        rep.done.append(
            f"Tahtada {total} kullanıcı olduğu için giriş ekranı önbelleği "
            "servisini kurdunuz."
        )
    elif isinstance(total, int) and total >= 50:
        rep.notes.append(
            f"Kullanıcı sayısı {total} ama giriş ekranı önbelleği kurulamadı; "
            "bazı hesaplar giriş ekranında görünmeyebilir."
        )
    changed = list(d.get("changed_users") or [])
    if changed:
        rep.notes.append(
            f"{len(changed)} hesabın PIN anahtarı DEĞİŞTİ ({_join(changed)}). "
            "Bu öğretmenlerin telefonundaki eski kayıt artık çalışmaz; yeni "
            "PIN kâğıdını onlara yeniden teslim edin."
        )
    if ctx.applied:
        rep.done.append(
            "Tüm anahtarları QR kodlarıyla içeren yazdırılabilir PIN kâğıdı "
            "üretildi; öğretmenlere yalnızca özelden teslim edin."
        )

    # Düğme eylemleri
    applied_at = ctx.entry.timestamp if ctx.entry else ""
    for a in ctx.action("purge_all_secrets_action"):
        n = len(a.data.get("purged_users") or [])
        rep.done.append(
            "Tüm PIN anahtarlarını sildiniz" + (f" ({n} anahtar)." if n else ".")
        )
        if applied_at and a.timestamp > applied_at:
            rep.notes.append(
                "PIN anahtarlarını, anahtar üretiminden SONRA sildiniz. Yukarıda "
                "üretildiği yazan anahtarlar artık tahtada yok; imajı almadan "
                "önce PIN adımını yeniden uygulayın."
            )
    for a in ctx.action("remove_extra_users_action"):
        rep.done.append(
            "Fazladan hesapları (yedek ve kişisel öğretmen hesapları) ev "
            "dizinleriyle birlikte sildiniz."
        )

    # Klonda deneyin
    if not ctx.applied:
        return
    rep.tests.append(
        "Klonun tarih, saat ve saat diliminin doğru olduğunu doğrulayın. PIN "
        "kodları saate bağlıdır; saat birkaç dakika bile kaymışsa bütün PIN "
        "girişleri reddedilir."
    )
    rep.tests.append(
        "PIN kâğıdındaki bir QR kodu telefondaki doğrulayıcı uygulamaya okutun; "
        "klonu yeniden başlatıp o hesaba telefonun gösterdiği 6 haneli kodla "
        "girin."
    )
    if teachers and used_tool:
        rep.tests.append(
            "Listedeki bir öğretmenle klonda önce EBA QR ile giriş yapın, sonra "
            "oturumu kapatıp aynı hesaba PIN ile girin. Ad soyad MEBBİS'teki "
            "yazımdan farklı girildiyse o öğretmenin PIN'i hiçbir tahtada "
            "çalışmaz."
        )
    if reserves:
        rep.tests.append(f"Bir yedek hesaba (ör. {reserves[0]}) PIN ile girin.")
    if "etapadmin" in created or "etapadmin" in preserved:
        rep.tests.append(
            "etapadmin'e hem PIN ile hem de parolayla girilebildiğini doğrulayın."
        )
    if "ogretmen" in created or "ogretmen" in preserved:
        rep.tests.append("Ortak öğretmen hesabına (ogretmen) PIN ile girin.")
    if group_new or ctx.flag("make_group_pin"):
        rep.tests.append(
            "Kâğıttaki ORTAK PIN kartını okutup bir yedek ya da kişisel öğretmen "
            "hesabına ortak kodla girin."
        )
    if d.get("auto_group_service_installed"):
        rep.tests.append(
            "Klonda EBA QR ile yeni bir öğretmen girişi yaptıktan sonra bu "
            "hesabın ogretmenler grubuna eklendiğini doğrulayın (terminalde "
            "`id <kullanıcı>`)."
        )
    if d.get("greeter_cache_applied"):
        rep.tests.append(
            "Yeniden başlatmadan sonra giriş ekranında bütün hesapların (yedekler "
            "dahil) listelendiğini doğrulayın."
        )
    rep.notes.append(
        "PIN anahtarları imajla birlikte bütün klonlara aynen kopyalanır; bu "
        "bilinçli bir tasarım. Tek bir tahtadan ya da kâğıttan sızan anahtar "
        "bütün tahtaları etkiler."
    )
    if group_new or ctx.flag("make_group_pin"):
        rep.notes.append(
            "Ortak PIN, kişisel PIN'lerden daha zayıf bir önlemdir: gruptaki "
            "herkes aynı kodu kullanır."
        )


# ---------------------------------------------------------------------------
# m13 — EBA QR parola diyaloğu
# ---------------------------------------------------------------------------


def narrate_m13(ctx: StepContext, rep: StepReport) -> None:
    if ctx.data.get("was_already_hidden"):
        rep.done.append(
            "EBA QR ilk giriş parola penceresi zaten kapalıydı; bu adımda "
            "değişiklik yapılmadı."
        )
    else:
        rep.done.append(
            "EBA QR ile ilk girişte açılan parola tanımlama penceresini "
            "kapattınız; öğretmenler sınıfta öğrencilerin önünde parola yazmak "
            "zorunda kalmayacak."
        )
    rep.tests.append(
        "Klonda o tahtaya daha önce hiç girmemiş bir öğretmenle EBA QR ile ilk "
        "girişi yapın; masaüstü açıldığında parola tanımlama penceresi "
        "çıkmamalı."
    )
    rep.tests.append(
        "Aynı öğretmenin oturumu kapatıp ikinci kez QR ile (PIN anahtarı varsa "
        "PIN ile de) girebildiğini doğrulayın."
    )
    rep.notes.append(
        "Bu hesaplarda parola olmayacak; QR çalışmadığında giriş için PIN ya "
        "da USB bellek gerekir."
    )


# ---------------------------------------------------------------------------
# m17 — Başarım (Deneysel)
# ---------------------------------------------------------------------------

_LIGHT_LABELS = {
    "effects": "pencere ve menü animasyonları kapatıldı",
    "compositor": "tam ekran pencereler doğrudan çiziliyor",
    "thumbnails": "resim ve video önizlemeleri kapatıldı",
    "directory-item-counts": "klasör öğesi sayımı kapatıldı",
    "app-monitoring": "uygulama kullanım izlemesi kapatıldı",
    "low-resolution": "çözünürlük 1600x900'e düşürüldü",
    "text-scaling": "yazı boyutu küçültüldü",
    "file-icon-size": "dosya ve masaüstü simgeleri küçültüldü",
    "low-refresh-rate": "yenileme hızı 50 Hz'e düşürüldü",
}


def narrate_m17(ctx: StepContext, rep: StepReport) -> None:
    d = ctx.data
    if d.get("session_cleanup"):
        rep.done.append(
            "Öğretmen oturumunu kapattığında arkada asılı kalan süreçlerin "
            "(kapatılmadan bırakılan Firefox/Chrome ve alt süreçleri dahil) "
            "sonlandırılmasını etkinleştirdiniz. Ayar tahta yeniden "
            "başlatılınca devreye girer."
        )
        rep.tests.append(
            "Klonu yeniden başlatın. Bir öğretmen hesabıyla tarayıcıda birkaç "
            "sekme açıp tarayıcıyı kapatmadan oturumu kapatın; başka bir "
            "hesapla girip Sistem İzleyicisi'nde önceki kullanıcıya ait "
            "süreç kalmadığını doğrulayın."
        )
        rep.tests.append(
            "Oturum kapatma sonrasında aynı kullanıcının SSH gibi diğer açık "
            "oturumlarının kapanmadığını doğrulayın."
        )

    keys = [k for k in d.get("light_mode_keys") or [] if isinstance(k, str)]
    if keys:
        labels = [_LIGHT_LABELS.get(k, k) for k in keys]
        rep.done.append(
            "Başarımı artırmak için ETA Hafif Mod'u tüm kullanıcılara "
            f"uyguladınız: {_join(labels)}. Ayarlar her kullanıcıya oturum "
            "açılışında uygulanır; sonradan eklenecek hesaplar dahil."
        )
        rep.tests.append(
            "Klonda öğretmen ve öğrenci hesaplarıyla ayrı ayrı oturum açın; "
            "hafif mod ayarlarının ilk girişte uygulandığını doğrulayın."
        )
        if "low-resolution" in keys:
            rep.tests.append(
                "Çözünürlük 1600x900'e düştüğü için tahtaya parmakla ve "
                "kalemle dokunup dokunma noktasının imleçle aynı yere "
                "düştüğünü (kalibrasyonun kaymadığını), yazıların ve kalem "
                "çizgisinin okunaklı olduğunu doğrulayın."
            )
        if "low-refresh-rate" in keys:
            rep.tests.append(
                "50 Hz'de video oynatıp kalemle hızlı çizim yapın; titreme "
                "ya da belirgin gecikme olmadığını doğrulayın."
            )
        if {"low-resolution", "low-refresh-rate"} & set(keys):
            rep.tests.append(
                "USB fare takılıyken oturumu kapatıp başka bir hesaba geçin; "
                "fare imlecinin görünür kaldığını doğrulayın (ekran modu "
                "değişiminde imleç kaybolabiliyor)."
            )
    if d.get("light_mode_removed"):
        rep.done.append(
            "ETA Hafif Mod'u kaldırdınız; daha önce giriş yapmış hesapların "
            "masaüstü ayarları da geri alındı (paket yerinde bırakıldı)."
        )
        rep.tests.append(
            "Klonda daha önce giriş yapmış bir hesapla oturum açıp yazı "
            "boyutunun, simgelerin ve çözünürlüğün normale döndüğünü "
            "doğrulayın."
        )

    xorg = d.get("cursor_xorg_fix")
    if xorg:
        rep.done.append(
            "Ekran modu değişiminde kaybolan fare imleci için ekran sürücüsünü "
            f"değiştirdiniz: {str(xorg).lower()}."
        )
        rep.tests.append(
            "Klonu yeniden başlatın; ekranın açıldığını, dokunmatik ve kalemin "
            "çalıştığını doğrulayın. Ardından USB fare takılıyken ekran "
            "ayarlarından çözünürlüğü ya da yenileme hızını değiştirip "
            "imlecin kaybolmadığını doğrulayın."
        )
        rep.notes.append(
            "Ekran sürücüsü değişikliği tahta modeline (Intel/AMD grafik) göre "
            "farklı davranabilir. İmajı yayacağınız her tahta modelinde ayrı "
            "bir klon deneyin."
        )
    if d.get("cursor_refresh_service"):
        rep.done.append(
            "Ekran modu değiştiğinde fare imlecini kendiliğinden tazeleyen "
            "servisi kurdunuz."
        )
        rep.tests.append(
            "Klonda çözünürlüğü değiştirip uygulayın; fare imlecinin bir an "
            "sonra yeniden göründüğünü doğrulayın."
        )


# ---------------------------------------------------------------------------
# Kayıt defteri
# ---------------------------------------------------------------------------

NARRATORS = {
    "m09_system_update": narrate_m09,
    "m01_initial_passwords": narrate_m01,
    "m02_boot_password_wipe": narrate_m02,
    "m03_otp_secrets": narrate_m03,
    "m13_password_dialog": narrate_m13,
    "m17_performance": narrate_m17,
}


# ---------------------------------------------------------------------------
# Adımlar arası uyarılar ve genel testler
# ---------------------------------------------------------------------------


def cross_step_warnings(contexts: dict[str, StepContext], modules: list, journal) -> list[str]:
    return []


def general_tests(contexts: dict[str, StepContext]) -> list[str]:
    tests = [
        "İmajı en az bir tahtaya yazın ve ilk açılışı başından sonuna izleyin: "
        "hata ekranı, beklenmedik parola sorusu ya da uzun bekleme olmamalı.",
        "Klonu en az iki kez yeniden başlatın ve bir kez tamamen kapatıp "
        "açın; her açılışta aynı sonucu aldığınızı doğrulayın.",
        "Öğretmen ve öğrenci hesaplarının her biriyle oturum açıp kapatın.",
        "Tahtanın dokunmatiği, kalemi, sesi ve ağ bağlantısının klonda "
        "çalıştığını doğrulayın.",
        "İmaj farklı tahta modellerine (ör. Intel ve AMD işlemcili) "
        "yazılacaksa her modelde en az bir klon deneyin.",
    ]
    return tests
