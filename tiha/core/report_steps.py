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

from pathlib import Path

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
# m04 — SSH sunucusu
# ---------------------------------------------------------------------------


def narrate_m04(ctx: StepContext, rep: StepReport) -> None:
    before = ctx.data.get("was_installed_before")
    if before is False:
        rep.done.append(
            "Tahtaya SSH sunucusunu kurdunuz ve root kullanıcısının ağ üzerinden "
            "parolayla oturum açmasına izin verdiniz."
        )
    elif before is True:
        rep.done.append(
            "Tahtada zaten kurulu olan SSH sunucusunda root kullanıcısının ağ "
            "üzerinden parolayla oturum açmasına izin verdiniz."
        )
    else:
        rep.done.append("SSH sunucusunu etkinleştirip root girişine izin verdiniz.")
    rep.tests.append(
        "Yönetim bilgisayarınızdan `ssh root@<klon-ip>` ile klona bağlanın; "
        "beklediğiniz root parolasının geçtiğini doğrulayın."
    )
    rep.tests.append(
        "Klonda `systemctl is-active ssh` çıktısının active olduğunu ve "
        "`sudo sshd -T | grep -Ei 'permitrootlogin|passwordauthentication'` "
        "çıktısında ikisinin de yes olduğunu doğrulayın (adım, servisin gerçekten "
        "ayağa kalktığını denetlemiyor)."
    )
    rep.tests.append(
        "İki farklı klonda `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` "
        "parmak izlerinin FARKLI olduğunu doğrulayın."
    )
    rep.tests.append(
        "Öğrenci ya da misafir ağından klonun SSH portuna erişilemediğini "
        "doğrulayın."
    )
    rep.notes.append(
        "Root parolası ve parolayla SSH girişi bütün klonlarda aynı olacak; "
        "parola sızarsa bütün tahtalar uzaktan yönetici erişimine açılır. "
        "Erişimi güvenlik duvarı ya da VLAN ile yönetim bilgisayarlarına "
        "sınırlayın."
    )


# ---------------------------------------------------------------------------
# m05 — Samba dosya paylaşımı
# ---------------------------------------------------------------------------


def narrate_m05(ctx: StepContext, rep: StepReport) -> None:
    d = ctx.data
    user = str(d.get("samba_user") or ctx.text("samba_user") or "").strip()
    who = f"'{user}' kullanıcısı" if user else "belirlediğiniz kullanıcı"
    lead = (
        "Tahtaya Samba'yı kurdunuz ve"
        if d.get("was_installed_before") is False
        else "Samba ile"
    )
    rep.done.append(
        f"{lead} tahtanın tüm diskini (kök '/') ağda \\\\<tahta-ip>\\root "
        f"adıyla, {who} ve parolasıyla tam yazma yetkisiyle paylaştınız."
    )
    if user and user != "root":
        rep.done.append(
            f"Paylaşıma '{user}' ile bağlanılsa da dosyalar root yetkisiyle yazılır."
        )
    rep.tests.append(
        "Bir Windows bilgisayarda Dosya Gezgini'ne \\\\<klon-ip>\\root yazıp "
        + (f"'{user}' " if user else "")
        + "kullanıcı adı ve parolayla bağlanın; bir dosya oluşturup silerek "
        "yazma yetkisini doğrulayın."
    )
    rep.tests.append(
        "Klonda `systemctl is-active smbd` çıktısının active olduğunu doğrulayın "
        "(adım servisin ayağa kalktığını denetlemiyor)."
    )
    rep.tests.append(
        "Birkaç klon aynı anda ağdayken Windows'un Ağ görünümünde her tahtanın "
        "kendi adıyla göründüğünü, ad çakışması olmadığını doğrulayın."
    )
    rep.notes.append(
        "Samba parolası bütün klonlarda aynı ve paylaşım diskin tamamına root "
        "yetkisiyle yazabiliyor; parola sızarsa bütün tahtalar etkilenir. "
        "Paylaşıma erişimi yönetim ağıyla sınırlayın."
    )


# ---------------------------------------------------------------------------
# m06 — Merkezi log iletimi
# ---------------------------------------------------------------------------

_PROFILE_TEXT = {
    "bakim": "kimlik doğrulama, donanım uyarıları, servis bildirimleri ve "
             "TiHA/Ahenk kayıtları iletilir",
    "kapsamli": "bütün kayıtlar iletilir",
    "guvenlik": "kimlik doğrulama ve kritik hatalar iletilir",
}


def _profile_key(label: str) -> str:
    low = label.lower()
    if "kapsaml" in low:
        return "kapsamli"
    if "güvenlik" in low or "guvenlik" in low:
        return "guvenlik"
    return "bakim"


def narrate_m06(ctx: StepContext, rep: StepReport) -> None:
    d = ctx.data
    host = ctx.text("syslog_host")
    port = ctx.num("syslog_port", 514)
    proto = (ctx.text("syslog_proto") or "").lower()
    profile_label = ctx.text("log_profile")
    if not host:
        m = _re.search(r"iletimi (\S+?):(\d+)/(\w+) için kuruldu \((.+?)\)", ctx.summary)
        if m:
            host, port, proto, profile_label = m.group(1), int(m.group(2)), m.group(3).lower(), m.group(4)
    profile = _profile_key(profile_label or "")
    if host:
        rep.done.append(
            f"Tahtanın sistem günlüklerini {host}:{port} adresindeki merkezi log "
            f"sunucusuna {proto.upper() or 'ağ'} ile iletecek şekilde ayarladınız "
            f"(profil: {profile_label or 'Bakım'}; {_PROFILE_TEXT[profile]})."
        )
    else:
        rep.done.append("Tahtanın sistem günlüklerini merkezi log sunucusuna yönlendirdiniz.")
    if proto == "tcp":
        rep.done.append(
            "Sunucuya ulaşılamazsa kayıtlar tahtada en fazla 2 GB'a kadar "
            "biriktirilip bağlantı gelince gönderilecek."
        )
    elif proto == "udp":
        rep.notes.append(
            "UDP seçtiniz: teslim onayı olmadığı için sunucu kapalıyken gönderilen "
            "kayıtlar kaybolabilir. Kayıpsız iletim için TCP önerilir."
        )
    smart = d.get("install_smart_monitoring", ctx.flag("install_smart_monitoring"))
    if smart:
        rep.done.append(
            "Disk sağlığı (SMART) ve sıcaklık izleme paketlerinin kurulmasını "
            "istediniz."
        )
    exporter = d.get("install_node_exporter", ctx.flag("install_node_exporter"))
    if exporter:
        listen = ctx.text("node_exporter_listen")
        rep.done.append(
            "Metrik izleme ajanının (node_exporter) "
            + (f"{listen} adresinde " if listen else "")
            + "dinlemesini istediniz."
        )
        rep.notes.append(
            "node_exporter metrikleri kimlik doğrulamasız sunar; erişimi yönetim "
            "ağıyla sınırlayın."
        )
    if profile == "kapsamli":
        rep.notes.append(
            "Kapsamlı profil bütün kayıtları gönderir; ayıklama içindir. Bütün "
            "klonlarda kalıcı açık kalırsa ağ ve disk yükü oluşturur."
        )

    rep.tests.append(
        "Klonda `logger -p auth.notice \"tiha-klon-test\"` çalıştırın; kaydın "
        "log sunucusuna ulaştığını ve orada klonun KENDİ bilgisayar adıyla "
        "(kaynak tahtanın ya da ortak imaj adının değil) göründüğünü doğrulayın."
    )
    rep.tests.append(
        "İki klonu aynı anda açıp log sunucusunda iki ayrı bilgisayar adı ve IP "
        "gördüğünüzü doğrulayın; bunu ilk açılışta ve bir yeniden başlatmadan "
        "sonra ayrı ayrı yapın."
    )
    rep.tests.append(
        "Klonun ilk açılışında `sudo ls -la /var/lib/rsyslog/` ile bekleyen bir "
        "kuyruk dosyası olmadığını doğrulayın; varsa kaynak tahtanın eski "
        "kayıtları her klondan yeniden gönderiliyor demektir."
    )
    if proto == "tcp":
        rep.tests.append(
            "Log sunucusunu ya da ağı kısa süre kesin; bağlantı gelince aradaki "
            "kayıtların sunucuya ulaştığını doğrulayın."
        )
    if smart:
        rep.tests.append(
            "Klonda `systemctl is-active smartd` ve `sensors` çalıştırın; farklı "
            "tahta modellerinde sıcaklığın okunabildiğini doğrulayın."
        )
    if exporter:
        rep.tests.append(
            "İzleme sunucusundan klonun metrik adresine (ör. "
            "http://<klon-ip>:9100/metrics) erişebildiğinizi doğrulayın."
        )


# ---------------------------------------------------------------------------
# m07 — Zaman senkronizasyonu
# ---------------------------------------------------------------------------


def narrate_m07(ctx: StepContext, rep: StepReport) -> None:
    ntp = ctx.text("ntp_servers")
    fallback = ctx.text("ntp_fallback")
    tz = ctx.text("timezone")
    if not tz:
        m = _re.search(r"saat dilimi: (.+?)\)", ctx.summary)
        tz = m.group(1) if m else ""
    tz_part = f" Saat dilimi: {tz}." if tz else ""
    if ntp and fallback:
        rep.done.append(
            f"Tahtanın saatini {ntp} NTP sunucularıyla eşitleyecek şekilde "
            f"ayarladınız; bunlara ulaşılamazsa {fallback} kullanılacak.{tz_part}"
        )
    elif ntp:
        rep.done.append(
            f"Tahtanın saatini {ntp} NTP sunucularıyla eşitleyecek şekilde "
            f"ayarladınız; yedek sunucu tanımlamadınız.{tz_part}"
        )
    elif fallback:
        rep.done.append(
            "Birincil NTP sunucusu tanımlamadınız; tahta saatini yalnız yedek "
            f"sunucularla ({fallback}) eşitleyecek.{tz_part}"
        )
    else:
        rep.done.append(
            "Zaman eşitlemesini (NTP) etkinleştirdiniz"
            + (f" ve saat dilimini {tz} olarak ayarladınız." if tz else ".")
        )
    if "pool.ntp.org" in f"{ntp} {fallback}":
        rep.notes.append(
            "İnternet NTP havuzunu kullandınız; okul ağı UDP 123 çıkışını "
            "engelliyorsa saat eşitlenmez. MEB iç NTP adresini biliyorsanız onu "
            "tercih edin."
        )
    rep.tests.append(
        "Klonu kurulacağı OKUL AĞINDA açıp `timedatectl` çalıştırın; "
        "\"System clock synchronized: yes\""
        + (f" ve \"Time zone: {tz}\"" if tz else "")
        + " görülmeli. Hazırlık ağıyla okul ağı farklı olabilir."
    )
    rep.tests.append(
        "Tahtayı birkaç saat kapalı tutup açın; saatin açılıştan kısa süre sonra "
        "doğru değere geldiğini doğrulayın."
    )
    rep.notes.append(
        "Geçersiz bir saat dilimi sessizce yok sayılır; yukarıdaki testte saat "
        "dilimini mutlaka kontrol edin."
    )


# ---------------------------------------------------------------------------
# m08 — Dinamik hostname
# ---------------------------------------------------------------------------

_HOSTNAME_OK = _re.compile(r"^[a-z0-9-]+$")


def narrate_m08(ctx: StepContext, rep: StepReport) -> None:
    template = ctx.text("template")
    prefix = ctx.text("prefix")
    if not (template and prefix):
        m = _re.search(r"Hostname '(.+?)' olarak ayarlandı; her açılışta '(.+?)-XXXXXX'", ctx.summary)
        if m:
            template, prefix = template or m.group(1), prefix or m.group(2)
    if template and prefix:
        rep.done.append(
            f"İmaj için tahtanın bilgisayar adını geçici olarak '{template}' "
            "yaptınız. İmajdan çıkan her tahta açılışta kablolu ağ kartının MAC "
            f"adresinden kendi adını üretecek: '{prefix}-XXXXXX' (XXXXXX = MAC'in "
            "son 6 hanesi)."
        )
    else:
        rep.done.append(
            "Her tahtanın açılışta MAC adresinden kendine özgü bir bilgisayar adı "
            "üretmesini sağlayan servisi kurdunuz."
        )
    prev = str(ctx.data.get("previous_hostname") or "")
    if prev and template and prev != template:
        rep.done.append(f"Tahtanın önceki adı '{prev}' idi.")
    shown = f"'{prefix}-'" if prefix else "önekle"
    rep.tests.append(
        f"Klonu açıp `hostnamectl` çalıştırın: ad {shown} ile başlamalı ve son 6 "
        "hanesi kablolu ağ kartının MAC adresinin (`ip link`) son 6 hanesi "
        "olmalı. Ad hâlâ imaj adındaysa servis çalışmamıştır."
    )
    rep.tests.append(
        "Klonu ikinci kez yeniden başlatın; adın DEĞİŞMEDİĞİNİ doğrulayın. Her "
        "açılışta değişiyorsa tahtada kablolu kart bulunamamıştır."
    )
    rep.tests.append(
        "`time sudo true` komutunun anında döndüğünü doğrulayın (10 saniye "
        "sürüyorsa /etc/hosts güncellenmemiştir)."
    )
    rep.tests.append(
        "Klonun ilk açılışında oturum açıp birkaç uygulama başlatın; ad oturum "
        "açıldıktan sonra değişirse yeni pencereler açılamayabilir."
    )
    rep.tests.append(
        "İki klonun farklı ad aldığını ve bu adın DHCP/DNS'te, Lider'de ve "
        "(kuruluysa) log sunucusunda göründüğünü doğrulayın."
    )
    if prefix and (len(prefix) > 8 or not _HOSTNAME_OK.match(prefix)):
        rep.notes.append(
            f"Önek '{prefix}' önerilen biçimde değil (küçük harf, rakam ve '-', "
            "en fazla 8 karakter). Uzun önekler Windows ağ adında (15 karakter "
            "sınırı) kırpılır ve tahtalar aynı adla görünebilir."
        )


# ---------------------------------------------------------------------------
# m11 — Otomatik kapanma
# ---------------------------------------------------------------------------


def _duration(seconds: int) -> str:
    if seconds and seconds % 60 == 0:
        return f"{seconds // 60} dakika"
    return f"{seconds} saniye"


def narrate_m11(ctx: StepContext, rep: StepReport) -> None:
    if not ctx.has_params:
        rep.done.append(
            (ctx.summary.rstrip(".") or "Otomatik kapanma sistemini kurdunuz") + "."
        )
        rep.tests.append(
            "Klonda kapanma ayarlarının (saat ve boşta süresi) beklediğiniz gibi "
            "çalıştığını doğrulayın; bu adımın seçenekleri kayıtlı değil."
        )
        return
    auto = ctx.flag("auto_enabled")
    idle = ctx.flag("idle_enabled")
    hh = ctx.num("auto_hour", 22) or 0
    mm = ctx.num("auto_minute", 0) or 0
    idle_min = ctx.num("idle_minute", 15) or 15
    cs = ctx.num("countdown_seconds", 120) or 120
    at = f"{hh:02d}:{mm:02d}"
    warn = (
        f"Kapanmadan önce ekranda {_duration(cs)} süren bir uyarı penceresi "
        "çıkacak; kullanıcı kapanmayı 10 dakika erteleyebilecek."
    )
    if auto and idle:
        rep.done.append(
            f"Tahtanın her gün {at}'de ve {idle_min} dakika boşta kaldığında "
            f"kapanmasını ayarladınız. {warn}"
        )
    elif auto:
        rep.done.append(
            f"Tahtanın her gün {at}'de kapanmasını ayarladınız (boşta kalınca "
            f"kapanma kapalı). {warn}"
        )
    elif idle:
        rep.done.append(
            f"Tahtanın {idle_min} dakika kullanılmadığında kapanmasını "
            f"ayarladınız (sabit saatte kapanma kapalı). {warn}"
        )
    else:
        rep.done.append(
            "Otomatik kapanma altyapısını kurdunuz ama iki kapanma modunu da "
            "kapalı bıraktınız; bu imajdan çıkan tahtalar kendiliğinden "
            "kapanmayacak."
        )
    if idle:
        rep.tests.append(
            f"Klonda oturum açıp dokunmadan bırakın; yaklaşık {idle_min + 1} "
            f"dakika sonra uyarı penceresinin çıktığını ve geri sayımın "
            f"{_duration(cs)} ile başladığını doğrulayın."
        )
        rep.tests.append(
            "\"10 dakika ertele\" düğmesiyle pencerenin kapandığını ve 10 dakika "
            "boyunca yeniden açılmadığını, sonra sayacın bitince tahtanın "
            "kapandığını doğrulayın."
        )
        rep.tests.append(
            "Aynı denemeyi OTURUM AÇMADAN, giriş ekranında yapın; pencere orada "
            "da çıkmalı. Ekran kararmışsa pencerenin ekranı uyandırdığını görün."
        )
    if auto:
        rep.tests.append(
            f"Klonun saatinin doğru olduğundan emin olun; {at}'den {_duration(cs)} "
            f"önce pencerenin açıldığını ve {at}'de tahtanın kapandığını "
            f"doğrulayın. Tahtayı {at}'den ÖNCE açmış olmanız gerekir; bu saatten "
            "sonra açılan tahta o gün sabit saatte kapanmaz."
        )
        rep.notes.append(
            "Sabit saatteki kapanma ertelenirse o günün sabit saat kapanması "
            "iptal olur; tahta yalnız boşta kalma ile kapanabilir."
        )
        if cs < 60:
            rep.notes.append(
                "Geri sayımı 60 saniyenin altında seçtiniz; sabit saatteki "
                "kapanma bazı günler kaçırılabilir."
            )
    if auto or idle:
        rep.tests.append(
            "Klonda `systemctl is-active eta-shutdown` çıktısının active olduğunu "
            "doğrulayın."
        )


# ---------------------------------------------------------------------------
# m15 — Uzaktan uyandırma (Wake-on-LAN)
# ---------------------------------------------------------------------------

_WOL_SERVICE = "/etc/systemd/system/tiha-wake-on-lan.service"


def narrate_m15(ctx: StepContext, rep: StepReport) -> None:
    rep.done.append(
        "İmajdan çıkan tahtaların ağ kartını her açılışta uzaktan uyandırma "
        "(Wake-on-LAN) paketini dinleyecek moda alan servisi kurdunuz; kapalı "
        "tahtalar merkezden `wakeonlan <MAC>` komutuyla açılabilecek."
    )
    if ctx.data.get("was_ethtool_installed") is False:
        rep.done.append("Bunun için gereken ethtool paketini de kurdunuz.")
    rep.tests.append(
        "Klonun BIOS ayarlarında 'Wake on LAN' ve 'Power On by PCI-E' açık, "
        "'ErP' ve 'Deep Sleep' KAPALI olmalı. BIOS ayarları imajla taşınmaz; "
        "her tahtada ayrıca yapılmalı."
    )
    rep.tests.append(
        "Klonda `sudo ethtool <arayüz>` çıktısında \"Wake-on: g\" görün ve "
        "klonun MAC adresini not edin (her klonun MAC'i farklıdır)."
    )
    rep.tests.append(
        "Klonu normal yoldan kapatın; AYNI ağ bölümündeki (VLAN) başka bir "
        "bilgisayardan `wakeonlan <klon-MAC>` gönderip tahtanın açıldığını "
        "doğrulayın."
    )
    rep.notes.append(
        "Merkezden uyandırma için bütün klonların MAC adreslerini toplamanız "
        "gerekir; TiHA bu listeyi tutmaz."
    )


def narrate_m15_failed(ctx: StepContext, rep: StepReport) -> None:
    if "atlandı" not in ctx.summary:
        narrate_failed(ctx, rep)
        return
    # Kutu işaretsiz uygulandı: hata değil, bilinçli atlama.
    rep.failed = False
    rep.skipped = True
    import os.path
    if os.path.exists(_WOL_SERVICE):
        rep.done.append(
            "Bu oturumda uzaktan uyandırma adımını atladınız; ama tahtada daha "
            "önce kurulmuş uzaktan uyandırma servisi hâlâ etkin ve imaja girecek."
        )
        rep.tests.append(
            "Klonda uzaktan uyandırmanın (Wake-on-LAN) çalıştığını doğrulayın; "
            "servis imajda kurulu."
        )
    else:
        rep.done.append(
            "Uzaktan uyandırmayı açmadınız; tahtalar merkezden uyandırılamayacak."
        )


# ---------------------------------------------------------------------------
# m12 — Otomatik Ahenk kaydı
# ---------------------------------------------------------------------------


def narrate_m12(ctx: StepContext, rep: StepReport) -> None:
    d = ctx.data
    mac = d.get("imaged_mac")
    rep.done.append(
        "İmajdan çıkan her tahtanın ilk açılışta kendini kopya olarak tanıyıp "
        "kaynak tahtanın Lider kimliğini silmesini ve Lider'e kendi kimliğiyle "
        "yeniden abone olmasını sağlayan mekanizmayı kurdunuz"
        + (f"; kaynak tahtanın MAC adresi ({mac}) imza olarak kaydedildi." if mac else ".")
    )
    if d.get("was_installed_before") is False:
        rep.done.append("Tahtada bulunmayan ahenk paketini de kurdunuz.")
    rep.done.append(
        "Kaynak tahtanın kendi Ahenk kimliğine dokunulmadı; imaj alınana kadar "
        "Lider'e bağlı çalışmaya devam eder."
    )
    rep.tests.append(
        "Klonu ilk kez açmadan önce kablolu ağa bağlayın; klonun "
        "api-etap.eba.gov.tr adresine erişebildiğinden emin olun."
    )
    rep.tests.append(
        "Açılıştan sonra `sudo journalctl -t tiha-clone-reclaim` çıktısında "
        "\"klon\" ve ardından \"KAYITLI\" ya da \"KAYITSIZ\" satırını görün. "
        "\"API'ye ulaşılamadı\" yazıyorsa ağı düzeltip yeniden başlatın."
    )
    rep.tests.append(
        "Lider konsolunda klonun kaynak tahtadan AYRI bir kayıt olarak, kendi "
        "MAC adresiyle göründüğünü; klona gönderilen bir test komutunun kaynak "
        "tahtaya düşmediğini doğrulayın."
    )
    rep.tests.append(
        "Envanterde kayıtlı olmayan bir klonda etapadmin ile oturum açınca "
        "eta-register kayıt ekranının açıldığını, kayıttan sonra tahtanın "
        "Lider'de göründüğünü doğrulayın."
    )
    rep.tests.append(
        "İki klonu aynı anda açıp Lider'de iki ayrı kayıt oluştuğunu doğrulayın."
    )
    rep.notes.append(
        "Klonun ilk açılışında ağ ya da EBA servisi yoksa ahenk o açılış boyunca "
        "kaynak tahtanın kimliğiyle Lider'e bağlanır ve komutlar yanlış tahtaya "
        "gidebilir. Klonları ilk kez ağ hazırken açın."
    )


# ---------------------------------------------------------------------------
# m14 — BIOS yönetici parolası
# ---------------------------------------------------------------------------


def narrate_m14(ctx: StepContext, rep: StepReport) -> None:
    d = ctx.data
    if ctx.applied:
        model = str(d.get("model") or "")
        faz1 = "Faz 1" in model
        prot = d.get("protection")
        if d.get("clear_mode"):
            rep.done.append(
                "Klon tahtaların ilk açılışında BIOS yönetici parolasını "
                "TEMİZLEYECEK bir servisi imaja yerleştirdiniz; klonlarda BIOS "
                "parola koruması olmayacak."
            )
        else:
            n = d.get("pw_len")
            when = (
                "her açılışta" if prot == "always"
                else "yalnızca BIOS ayarlarına girilirken"
            )
            rep.done.append(
                "Klon tahtaların ilk açılışında BIOS yönetici parolasını "
                + (f"{n} karakterlik " if n else "")
                + f"parolanıza ayarlayacak tek seferlik bir servisi imaja "
                f"yerleştirdiniz; parola {when} sorulacak."
            )
            if faz1:
                rep.done.append(
                    "Faz 1 modeli: "
                    + ("yönetici ve kullanıcı parolasına aynı değer atanacak."
                       if prot == "always" else "yalnız yönetici parolası atanacak.")
                )
        if model:
            rep.done.append(f"Servis {model} modeli için hazırlandı.")
        rep.done.append("Bu tahtanın (kaynak) BIOS'una bu adımda dokunulmadı.")

        rep.tests.append(
            "Klonu ilk kez açıp oturum açın: `sudo journalctl -t "
            "tiha-first-boot-bios -b` çıktısında \"BIOS yönetici parolası işlemi "
            "başarılı\" satırını ve `/usr/local/sbin/tiha-first-boot-bios.py` "
            "dosyasının artık OLMADIĞINI doğrulayın."
        )
        if d.get("clear_mode"):
            rep.tests.append(
                "Klonu yeniden başlatıp BIOS'a girin; parola sorulmamalı."
            )
        else:
            rep.tests.append(
                "Klonu yeniden başlatıp BIOS'a girin; parola sorulmalı ve "
                "yazdığınız parola kabul edilmeli. Gömülen parola normalleştirilmiş "
                "hâlidir (küçük harfler büyütülür, 'I' ve harf/rakam dışı "
                "karakterler atılır)."
                + (" Her açılışta işletim sisteminden önce parola sorulmalı."
                   if prot == "always" else " Normal açılış parola sormamalı.")
            )
            rep.tests.append(
                "Klonu bir kez daha yeniden başlatıp BIOS parolasının hâlâ yerinde "
                "olduğunu doğrulayın."
            )
        rep.tests.append(
            "Kaynak tahtayı yeniden başlattığınızda onun BIOS parolasının "
            "DEĞİŞMEDİĞİNİ doğrulayın."
        )
        rep.notes.append(
            "Klonun ilk açılışında BIOS flash belleğine yazılır; bu işlem her "
            "tahtada tekrar eder. Önce tek bir klonda, sonra filodaki her tahta "
            "modelinden bir klonda deneyin."
        )
        if not d.get("clear_mode"):
            rep.notes.append(
                "Parola, kaynak tahtada ve imajda düz metin bir betikte duruyor "
                "(/usr/local/sbin/tiha-first-boot-bios.py); klonda yalnız işlem "
                "başarılı olunca silinir. İmaj dosyalarını buna göre koruyun."
            )
    for a in ctx.action("set_local_supervisor_action"):
        clear = a.data.get("clear_mode")
        rep.done.append(
            "Bu tahtanın (kaynak) BIOS "
            + ("yönetici parolasını doğrudan temizlediniz."
               if clear else "yönetici parolasını doğrudan ayarladınız.")
        )
        rep.notes.append(
            "Kaynak tahtanın BIOS'unda yaptığınız değişiklik imajla taşınmaz; "
            "yalnız bu tahtayı etkiler."
        )


# ---------------------------------------------------------------------------
# m16 — GRUB koruması
# ---------------------------------------------------------------------------


def narrate_m16(ctx: StepContext, rep: StepReport) -> None:
    d = ctx.data
    if d.get("removed"):
        rep.done.append(
            "GRUB korumasını kaldırdınız; menü düzenleme ve komut satırı artık "
            "parolasız, kurtarma (recovery) girdisi yeniden menüde."
        )
        rep.tests.append(
            "Klonun açılış menüsünde 'e' tuşuyla düzenleme ekranının parola "
            "sormadan açıldığını doğrulayın."
        )
        return
    if "linux_backup" in d:
        rep.done.append(
            "GRUB açılış menüsünü korumaya aldınız: menü düzenleme ('e') ve GRUB "
            "komut satırı ('c') artık 'etapadmin' GRUB kullanıcı adı ve bu adımda "
            "belirlediğiniz GRUB parolasıyla açılıyor. Kurtarma (recovery) "
            "girdisini menüden kaldırdınız; normal açılış parola sormuyor."
        )
    elif "zaten etkin" in ctx.summary:
        rep.done.append(
            "GRUB koruması zaten etkindi; mevcut GRUB parolasını değiştirmeden "
            "korudunuz."
        )
    elif "zaten yok" in ctx.summary or (ctx.has_params and not ctx.flag("enable_grub_lock")):
        rep.done.append(
            "GRUB korumasını etkinleştirmediniz; klonlarda açılış menüsü parola "
            "korumasız olacak."
        )
        rep.notes.append(
            "Tahtaya klavyeyle erişen biri açılış menüsünde 'e' ile tahtayı "
            "doğrudan yönetici kabuğuna düşürebilir."
        )
        return
    else:
        rep.done.append((ctx.summary.rstrip(".") or "GRUB korumasını ayarladınız") + ".")
    rep.tests.append(
        "Klonun açılış menüsünde bir girdinin üzerindeyken 'e' tuşuna basın: "
        "kullanıcı adı olarak etapadmin, ardından GRUB parolası sorulmalı; "
        "yanlış parolayla düzenleme ekranı açılmamalı. Menü görünmüyorsa "
        "açılışta Shift ya da Esc tuşunu basılı tutun."
    )
    rep.tests.append(
        "'c' tuşuyla GRUB komut satırında da aynı iki sorunun geldiğini doğrulayın."
    )
    rep.tests.append(
        "Varsayılan girdiyle ve zaman aşımıyla açılışın HİÇ parola sormadan "
        "ilerlediğini, menüde kurtarma (recovery) girdisi olmadığını doğrulayın."
    )
    rep.tests.append(
        "Parolayı fiziksel bir USB klavyeyle deneyin: GRUB'da dokunmatik ve "
        "ekran klavyesi yoktur, klavye düzeni ABD'dir."
    )
    rep.tests.append(
        "\"Gelişmiş seçenekler\" alt menüsünün de parola istediğini doğrulayın; "
        "eski çekirdekle açmak gerekirse GRUB parolası gerekir."
    )
    rep.notes.append(
        "GRUB parolası sistemdeki etapadmin parolası değildir ve hiçbir yerden "
        "geri okunamaz; bütün klonlarda aynıdır. Türkçe karakter (ç, ğ, ı, ö, "
        "ş, ü) içeren bir parola GRUB'ın ABD klavye düzeninde yazılamayabilir."
    )


# ---------------------------------------------------------------------------
# m10 — İmaj için sanitize
# ---------------------------------------------------------------------------


def narrate_m10(ctx: StepContext, rep: StepReport) -> None:
    m = _re.search(r"~(.+?) alan boşaltıldı", ctx.summary)
    freed = m.group(1) if m else ""
    rep.done.append(
        "İmajı klonlamaya hazırlamak için kimlik temizliği yaptınız: makine "
        "kimliğini (machine-id) sıfırladınız, SSH anahtarlarını sildiniz (her "
        "klon ilk açılışta kendi anahtarını üretecek), kayıtlı ağ bağlantılarını "
        "ve Wi-Fi parolalarını temizlediniz."
    )
    rep.done.append(
        "Günlükleri, APT önbelleğini ve paket listelerini, kabuk geçmişlerini, "
        "kullanıcı önbelleklerini, tarayıcı gezinti verilerini (yer imleri "
        "korunarak), GNOME anahtarlıklarını ve geçici dosyaları sildiniz"
        + (f"; yaklaşık {freed} disk alanı boşalttınız." if freed and freed != "ölçülemedi" else ".")
    )
    rep.done.append(
        "İmaja /etc/tiha-image-info.json damgasını yazdınız; sahada bu dosyadan "
        "imajın sürümü ve uygulanan adımlar görülebilir."
    )
    rep.tests.append(
        "İki farklı klonda `cat /etc/machine-id` ve `ssh-keygen -lf "
        "/etc/ssh/ssh_host_ed25519_key.pub` çıktılarının FARKLI olduğunu "
        "doğrulayın."
    )
    rep.tests.append(
        "Klonda `ls /etc/ssh/ssh_host_*` ile SSH anahtarlarının üretildiğini ve "
        "(SSH kuruluysa) `systemctl is-active ssh` çıktısının active olduğunu "
        "doğrulayın."
    )
    rep.tests.append(
        "Kablolu ağın klonda kendiliğinden bağlandığını doğrulayın; Wi-Fi "
        "kullanılacaksa bağlantıyı yeniden tanımlamanız gerekir (Wi-Fi "
        "parolaları imajdan silindi)."
    )
    rep.tests.append(
        "etapadmin ve bir öğretmen hesabıyla girişte \"anahtarlık parolası "
        "uyuşmuyor\" uyarısı çıkmadığını, Firefox ve Chrome'un açıldığını "
        "doğrulayın."
    )
    rep.tests.append(
        "Klonda `sudo apt update` komutunun çalıştığını ve `cat "
        "/etc/tiha-image-info.json` çıktısının beklediğiniz sürümü gösterdiğini "
        "doğrulayın."
    )
    rep.notes.append(
        "Temizlikten sonra kaynak tahtayı işletim sistemiyle YENİDEN AÇMAYIN: "
        "kapatın ve imajı canlı USB'den (Clonezilla vb.) alın. Açarsanız makine "
        "kimliği ve SSH anahtarları kaynak tahtada yeniden üretilir ve bütün "
        "klonlara aynen gider."
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
    "m04_ssh_server": narrate_m04,
    "m05_samba_share": narrate_m05,
    "m06_remote_syslog": narrate_m06,
    "m07_time_sync": narrate_m07,
    "m08_hostname": narrate_m08,
    "m11_power_management": narrate_m11,
    "m15_wake_on_lan": narrate_m15,
    "m17_performance": narrate_m17,
    "m12_ahenk_reset": narrate_m12,
    "m14_bios_password": narrate_m14,
    "m16_grub_protection": narrate_m16,
    "m10_image_sanitize": narrate_m10,
}

# Başarısız kaydı olağan "başarısız" anlatımından farklı yorumlanması
# gereken adımlar (ör. m15'te kutu işaretsizse kayıt "failed" düşüyor ama
# bu bir hata değil, bilinçli atlama).
FAILED_NARRATORS = {
    "m15_wake_on_lan": narrate_m15_failed,
}


# ---------------------------------------------------------------------------
# Adımlar arası uyarılar ve genel testler
# ---------------------------------------------------------------------------


# Canlı denetimlerin okuduğu yollar (testlerde sandbox'a yönlendirilir).
MACHINE_ID = Path("/etc/machine-id")
SSH_SENTINEL = Path("/var/lib/tiha/first-boot-sshkeys.done")
AHENK_CONF = Path("/etc/ahenk/ahenk.conf")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _really_failed(ctx: StepContext) -> bool:
    """m15'te kutu işaretsiz uygulanınca kayıt 'failed' düşer ama hata değildir."""
    if not ctx.failed:
        return False
    return not (ctx.module_id == "m15_wake_on_lan" and "atlandı" in ctx.summary)


def _grub_on(ctx: StepContext | None) -> bool:
    if ctx is None or not ctx.applied:
        return False
    return "linux_backup" in ctx.data or "zaten etkin" in ctx.summary


def _bios_on(ctx: StepContext | None) -> bool:
    return bool(ctx and ctx.applied and not ctx.data.get("clear_mode"))


def cross_step_warnings(contexts: dict[str, StepContext], modules: list, journal) -> list[str]:
    """Adımların sırası ve birbirleriyle ilişkisinden doğan uyarılar."""
    w: list[str] = []
    titles = {m.id: m.title for m in modules}
    applied = {mid for mid, c in contexts.items() if c.applied}

    def ts(mid: str) -> str:
        c = contexts.get(mid)
        return c.entry.timestamp if c is not None and c.entry is not None else ""

    def q(mid: str) -> str:
        return f"“{titles.get(mid, mid)}”"

    get = contexts.get
    m01, m03 = get("m01_initial_passwords"), get("m03_otp_secrets")
    m11, m14 = get("m11_power_management"), get("m14_bios_password")
    m15, m16 = get("m15_wake_on_lan"), get("m16_grub_protection")

    # --- Başarısız adımlar --------------------------------------------------
    failed = [mid for mid, c in contexts.items() if _really_failed(c)]
    if failed:
        w.append(
            f"Başarısız kalan adımlar var: {_join([q(m) for m in failed])}. "
            "Başarısız bir adım imaja yarım değişiklik bırakabilir; imajı "
            "almadan önce bu adımları düzeltip yeniden uygulayın ya da geri alın."
        )

    # --- İmaj temizliği (sanitize): varlık, sıra ve sonrası -------------------
    sanitize = "m10_image_sanitize"
    others = applied - {sanitize}
    if sanitize not in applied:
        if others:
            w.append(
                f"{q(sanitize)} adımını henüz çalıştırmadınız. Çalıştırmadan imaj "
                "alırsanız bütün klonlar aynı makine kimliğini ve SSH anahtarını "
                "paylaşır; Wi-Fi parolaları, kabuk geçmişleri ve tarayıcı verileri "
                "de imaja girer. Bu adımı en sona, imajı almadan hemen önce "
                "uygulayın."
            )
    else:
        t10 = ts(sanitize)
        later = {titles.get(mid, mid) for mid in others if ts(mid) > t10}
        later |= {a.title for c in contexts.values() for a in c.actions if a.timestamp > t10}
        if later:
            w.append(
                "İmaj temizliğinden SONRA şu adımlarda değişiklik yaptınız: "
                f"{_join(sorted(later))}. Bu değişikliklerin günlük ve önbellek "
                "izleri imaja girecek; imajı almadan önce temizlik adımını yeniden "
                "çalıştırın."
            )
        if _read(MACHINE_ID):
            w.append(
                "İmaj temizliğinden sonra bu tahta işletim sistemiyle yeniden "
                "açılmış görünüyor: makine kimliği (machine-id) yeniden üretilmiş. "
                "Bu hâliyle alınan imajda bütün klonlar aynı makine kimliğini "
                "paylaşır. Temizlik adımını yeniden çalıştırın ve tahtayı açmadan, "
                "canlı USB'den imaj alın."
            )
        if SSH_SENTINEL.exists():
            w.append(
                "İmaj temizliğinden sonra SSH anahtarları bu tahtada yeniden "
                f"üretilmiş ve 'yapıldı' işareti ({SSH_SENTINEL}) bırakılmış. Bu "
                "dosya imajda kalırsa klonlar kendi SSH anahtarlarını ÜRETMEZ: ya "
                "kaynak tahtanın anahtarını paylaşırlar ya da hiç anahtarları olmaz "
                "ve SSH çalışmaz. İmajı almadan önce bu dosyayı silip "
                f"(`sudo rm {SSH_SENTINEL}`) temizlik adımını yeniden çalıştırın."
            )

    # --- Lider / Ahenk --------------------------------------------------------
    if "m12_ahenk_reset" not in applied and AHENK_CONF.exists():
        w.append(
            f"Tahtada Ahenk kurulu ama {q('m12_ahenk_reset')} adımını "
            "uygulamadınız. Klonların hepsi Lider'e bu tahtanın kimliğiyle bağlanır; "
            "tek bir tahtaymış gibi görünür ve komutlar yanlış tahtaya gidebilir."
        )

    # --- Benzersiz ad ---------------------------------------------------------
    by_name = [m for m in ("m04_ssh_server", "m05_samba_share", "m06_remote_syslog") if m in applied]
    if by_name and "m08_hostname" not in applied:
        w.append(
            f"{q('m08_hostname')} adımını uygulamadınız ama "
            f"{_join([q(m) for m in by_name])} tahtaları ağdaki adlarıyla ayırt "
            "etmenizi gerektirir. Bütün klonlar ağda ve log sunucusunda aynı adla "
            "görünecek."
        )

    # --- Parola, PIN ve QR ilişkileri -----------------------------------------
    if "m02_boot_password_wipe" in applied:
        if m01 is not None and m01.applied and "ogretmen" in _m01_passwords(m01)[0]:
            w.append(
                "Ortak öğretmen hesabına (ogretmen) parola belirlediniz ama "
                f"{q('m02_boot_password_wipe')} adımı da etkin: bu parola klonun ilk "
                "açılışında rastgele bir değerle ezilecek ve işe yaramayacak."
            )
        if "m03_otp_secrets" not in applied:
            w.append(
                f"{q('m02_boot_password_wipe')} etkin ama {q('m03_otp_secrets')} "
                "adımını uygulamadınız: öğretmenler tahtaya yalnız EBA QR ya da USB "
                "bellekle girebilir. QR çalışmadığında hiçbir öğretmen giriş "
                "yapamaz; yalnız etapadmin kalır."
            )
    if "m13_password_dialog" in applied and "m03_otp_secrets" not in applied:
        w.append(
            f"{q('m13_password_dialog')} adımıyla parola penceresini kapattınız ama "
            "PIN anahtarı üretmediniz: öğretmen hesaplarında parola olmayacak, QR "
            "çalışmadığında tahtaya giriş yolu kalmaz."
        )
    if (m01 is not None and m03 is not None and m01.applied and m03.applied
            and m01.data.get("created_reserve")
            and ts("m01_initial_passwords") > ts("m03_otp_secrets")):
        w.append(
            f"Yedek öğretmen hesaplarını {q('m03_otp_secrets')} adımından SONRA "
            "açtınız; bu yeni hesapların PIN anahtarı yok. PIN adımını yeniden "
            "uygulayın (mevcut anahtarlara dokunulmaz)."
        )

    # --- Saat -----------------------------------------------------------------
    need_time = []
    if "m03_otp_secrets" in applied:
        need_time.append("PIN kodları")
    if m11 is not None and m11.applied and m11.flag("auto_enabled"):
        need_time.append("sabit saatte kapanma")
    if "m06_remote_syslog" in applied:
        need_time.append("log kayıtlarının zaman damgaları")
    if need_time and "m07_time_sync" not in applied:
        w.append(
            f"{_join(need_time)} doğru saate bağlı ama {q('m07_time_sync')} "
            "adımını uygulamadınız. Klonların saatinin okul ağında doğru "
            "eşitlendiğinden emin olun"
            + ("; PIN kodları saat 30 saniyeden fazla kayınca reddedilir."
               if "PIN kodları" in need_time else ".")
        )

    # --- Uyandırma, kapanma ve BIOS -------------------------------------------
    if m15 is not None and m15.applied and m11 is not None and m11.applied and m11.flag("idle_enabled"):
        idle = m11.num("idle_minute", 15) or 15
        cs = m11.num("countdown_seconds", 120) or 120
        w.append(
            "Uzaktan uyandırılan bir tahta, kimse kullanmazsa yaklaşık "
            f"{idle} dakika + {_duration(cs)} sonra giriş ekranında kendiliğinden "
            "kapanacak (boşta kapanma giriş ekranında da çalışır). Tahtaları "
            "dersten çok önce uyandıracaksanız boşta kalma süresini buna göre seçin."
        )
    if _bios_on(m14) and m15 is not None and m15.applied:
        if m14.data.get("protection") == "always":
            w.append(
                "BIOS parolasının her açılışta sorulmasını seçtiniz ve uzaktan "
                "uyandırmayı açtınız: uzaktan uyandırılan tahtalar BIOS parola "
                "ekranında bekleyip işletim sistemine hiç geçmeyecek."
            )
        w.append(
            "Uzaktan uyandırma her tahtada BIOS ayarı (Wake on LAN açık, ErP ve "
            "Deep Sleep kapalı) ister ve BIOS'a yönetici parolası koyduğunuz için "
            "bu ayarları yapmak her tahtada o parolayı gerektirecek. BIOS "
            "ayarlarını mümkünse parola ayarlanmadan önce yapın."
        )
    if _bios_on(m14) and "m12_ahenk_reset" in applied:
        w.append(
            f"{q('m14_bios_password')} ve {q('m12_ahenk_reset')} aynı MAC "
            "imzasını kullanıyor. BIOS parolası klonun ilk açılışında "
            "ayarlanamazsa, Ahenk kaydı imzayı güncellediği için sonraki "
            "açılışlarda da ayarlanmayabilir. Klonda BIOS parolasını ilk açılıştan "
            "sonra BIOS'a girerek mutlaka doğrulayın."
        )

    # --- Açılış güvenliği bütünlüğü --------------------------------------------
    if _grub_on(m16) and not _bios_on(m14):
        w.append(
            "GRUB menüsünü korudunuz ama BIOS parolası ayarlamadınız: tahtaya "
            "erişen biri BIOS'tan USB bellekle açıp GRUB korumasını aşabilir."
        )
    if _bios_on(m14) and not _grub_on(m16):
        w.append(
            "BIOS parolası ayarladınız ama GRUB menüsünü korumadınız: açılış "
            "menüsünde 'e' tuşuyla tahta doğrudan yönetici kabuğuna düşürülebilir."
        )

    # --- TiHA'nın kendi kayıtları imaja gidiyor ---------------------------------
    sensitive = []
    if m01 is not None and m01.applied and _m01_passwords(m01)[0]:
        sensitive.append("parola değişikliğinden önceki /etc/shadow yedeği (eski parola özetleri)")
    if m03 is not None and m03.applied:
        sensitive.append("bütün PIN anahtarlarını QR kodlarıyla içeren PIN kâğıtları")
    if sensitive:
        w.append(
            "TiHA'nın kayıt dizini /var/lib/tiha imaj temizliğinde silinmiyor ve "
            f"imajla bütün klonlara gidiyor. Bu dizinde {_join(sensitive)} var. "
            "İmaj dosyalarına erişimi buna göre sınırlayın."
        )
    return w


def general_tests(contexts: dict[str, StepContext]) -> list[str]:
    """Her imaj için geçerli, adımlardan bağımsız klon denetimleri."""
    applied = {mid for mid, c in contexts.items() if c.applied}
    tests = [
        "İmajı en az bir tahtaya yazın ve ilk açılışı başından sonuna izleyin: "
        "hata ekranı, beklenmedik parola sorusu ya da uzun bekleme olmamalı.",
        "Klonu en az iki kez yeniden başlatın ve bir kez tamamen kapatıp "
        "açın; her açılışta aynı sonucu aldığınızı doğrulayın.",
        "Öğretmen ve öğrenci hesaplarının her biriyle oturum açıp kapatın.",
        "Tahtanın dokunmatiği, kalemi, sesi ve ağ bağlantısının klonda "
        "çalıştığını doğrulayın.",
    ]
    identity = {"m04_ssh_server", "m05_samba_share", "m06_remote_syslog",
                "m08_hostname", "m10_image_sanitize", "m12_ahenk_reset"}
    if applied & identity:
        tests.append(
            "En az iki klonu aynı anda aynı ağa bağlayın; bilgisayar adlarının, "
            "IP adreslerinin ve (kullanıyorsanız) Lider kayıtlarının birbirinden "
            "farklı olduğunu doğrulayın."
        )
    tests += [
        "İmaj farklı tahta modellerine (ör. Intel ve AMD işlemcili) "
        "yazılacaksa her modelde en az bir klon deneyin.",
        "Klonu kurulacağı okulun ağında, gerçek bir öğretmen hesabıyla en az "
        "bir ders süresince kullanın.",
        "Testte bulduğunuz her sorunu kaynak tahtada düzeltip imajı yeniden "
        "alın; sorunu klonlarda tek tek düzeltmeye çalışmayın.",
    ]
    return tests
