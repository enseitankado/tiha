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

from ..modules.m10_image_sanitize import SENSITIVE_STATE
from .i18n import t
from .paths import STATE_DIR
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
    return ", ".join(items[:-1]) + t("core.report.join_and") + items[-1]


def narrate_failed(ctx: StepContext, rep: StepReport) -> None:
    rep.done.append(
        t("core.report.failed_done_reason", reason=ctx.summary.rstrip("."))
        if ctx.summary else t("core.report.failed_done")
    )
    rep.notes.append(t("core.report.failed_note"))


# ---------------------------------------------------------------------------
# m09 — Sistem güncellemesi
# ---------------------------------------------------------------------------


def narrate_m09(ctx: StepContext, rep: StepReport) -> None:
    rep.done.append(t("m09.report.done"))
    rep.tests.append(t("m09.report.test_hardware"))
    rep.tests.append(t("m09.report.test_eba"))
    rep.tests.append(t("m09.report.test_apt"))
    rep.notes.append(t("m09.report.note_irreversible"))


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
            t("m01.report.admins_set_many", users=_join(admins)) if len(admins) > 1
            else t("m01.report.admins_set_one", users=_join(admins))
        )
    if "ogretmen" in ok:
        rep.done.append(t("m01.report.ogretmen_set"))
    for user in failed:
        rep.notes.append(t("m01.report.failed_user", user=user))
    if d.get("teacher_skipped_no_account"):
        rep.notes.append(t("m01.report.teacher_no_account"))
    for user, files in (d.get("keyrings_moved") or {}).items():
        if files:
            rep.done.append(t("m01.report.keyrings_moved", user=user))

    created = list(d.get("created_reserve") or [])
    skipped = list(d.get("skipped_reserve") or [])
    if created:
        span = created[0] + (" – " + created[-1] if len(created) > 1 else "")
        rep.done.append(t("m01.report.reserve_created", count=len(created), span=span))
    elif skipped:
        rep.done.append(t("m01.report.reserve_kept", count=len(skipped)))
    removed = list(d.get("removed_users") or [])
    if removed:
        rep.done.append(
            t("m01.report.removed_many", users=_join(removed)) if len(removed) > 1
            else t("m01.report.removed_one", users=_join(removed))
        )
    student_removed = bool(ctx.action("remove_student_user_action"))
    if student_removed:
        rep.done.append(t("m01.report.student_removed"))

    # Klonda deneyin
    if "etapadmin" in ok:
        rep.tests.append(t("m01.report.test_etapadmin"))
    if "root" in ok:
        rep.tests.append(t("m01.report.test_root"))
    if "ogretmen" in ok:
        rep.tests.append(t("m01.report.test_ogretmen"))
    if created or skipped:
        rep.tests.append(t("m01.report.test_reserve"))
    if student_removed or "ogrenci" in removed:
        rep.tests.append(t("m01.report.test_student"))
    if admins:
        rep.notes.append(
            t("m01.report.note_admins_many", users=_join(admins)) if len(admins) > 1
            else t("m01.report.note_admins_one", users=_join(admins))
        )


# ---------------------------------------------------------------------------
# m02 — Her açılışta parola temizliği
# ---------------------------------------------------------------------------


def narrate_m02(ctx: StepContext, rep: StepReport) -> None:
    rep.done.append(t("m02.report.done"))
    rep.tests.append(t("m02.report.test_etapadmin"))
    rep.tests.append(t("m02.report.test_ogretmen"))
    rep.tests.append(t("m02.report.test_pin"))
    rep.tests.append(t("m02.report.test_journal"))
    rep.notes.append(t("m02.report.note"))


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
            rep.done.append(t("m03.report.teachers_tool", count=len(teachers)))
        else:
            rep.done.append(t("m03.report.teachers_internal", count=len(teachers)))
            rep.notes.append(t("m03.report.note_internal"))
    if reserves:
        span = reserves[0] + (f" – {reserves[-1]}" if len(reserves) > 1 else "")
        rep.done.append(t("m03.report.reserves", count=len(reserves), span=span))
    for user, label in (("etapadmin", t("m03.report.label_etapadmin")),
                        ("ogretmen", t("m03.report.label_ogretmen"))):
        if user in created:
            rep.done.append(t("m03.report.key_created", label=label))
        elif user in preserved:
            rep.done.append(t("m03.report.key_preserved", label=label))
    if new_keys:
        rep.done.append(t("m03.report.new_keys", count=len(new_keys)))
    if preserved:
        rep.done.append(t("m03.report.preserved", count=len(preserved)))

    group_new = "@ogretmenler" in created
    if group_new:
        rep.done.append(t("m03.report.group_new"))
    elif ctx.flag("make_group_pin"):
        rep.done.append(t("m03.report.group_kept"))
    if grouped:
        rep.done.append(t("m03.report.grouped", count=len(grouped)))
    if d.get("ungrouped_users"):
        rep.done.append(t("m03.report.ungrouped"))
    if d.get("auto_group_service_installed"):
        rep.done.append(t("m03.report.auto_group"))
    total = d.get("total_users")
    if d.get("greeter_cache_applied"):
        rep.done.append(t("m03.report.greeter_applied", total=total))
    elif isinstance(total, int) and total >= 50:
        rep.notes.append(t("m03.report.note_greeter_missing", total=total))
    changed = list(d.get("changed_users") or [])
    if changed:
        rep.notes.append(t(
            "m03.report.note_changed", count=len(changed), users=_join(changed),
        ))
    if ctx.applied:
        rep.done.append(t("m03.report.paper"))

    # Düğme eylemleri
    applied_at = ctx.entry.timestamp if ctx.entry else ""
    for a in ctx.action("purge_all_secrets_action"):
        n = len(a.data.get("purged_users") or [])
        rep.done.append(
            t("m03.report.purged_count", count=n) if n else t("m03.report.purged")
        )
        if applied_at and a.timestamp > applied_at:
            rep.notes.append(t("m03.report.note_purged_after"))
    for a in ctx.action("remove_extra_users_action"):
        rep.done.append(t("m03.report.extra_removed"))

    # Klonda deneyin
    if not ctx.applied:
        return
    rep.tests.append(t("m03.report.test_time"))
    rep.tests.append(t("m03.report.test_qr"))
    if teachers and used_tool:
        rep.tests.append(t("m03.report.test_teacher"))
    if reserves:
        rep.tests.append(t("m03.report.test_reserve", user=reserves[0]))
    if "etapadmin" in created or "etapadmin" in preserved:
        rep.tests.append(t("m03.report.test_etapadmin"))
    if "ogretmen" in created or "ogretmen" in preserved:
        rep.tests.append(t("m03.report.test_ogretmen"))
    if group_new or ctx.flag("make_group_pin"):
        rep.tests.append(t("m03.report.test_group"))
    if d.get("auto_group_service_installed"):
        rep.tests.append(t("m03.report.test_auto_group"))
    if d.get("greeter_cache_applied"):
        rep.tests.append(t("m03.report.test_greeter"))
    rep.notes.append(t("m03.report.note_copied"))
    if group_new or ctx.flag("make_group_pin"):
        rep.notes.append(t("m03.report.note_group_weak"))


# ---------------------------------------------------------------------------
# m13 — EBA QR parola diyaloğu
# ---------------------------------------------------------------------------


def narrate_m13(ctx: StepContext, rep: StepReport) -> None:
    if ctx.data.get("was_already_hidden"):
        rep.done.append(t("m13.report.already_hidden"))
    else:
        rep.done.append(t("m13.report.hidden"))
    rep.tests.append(t("m13.report.test_first_login"))
    rep.tests.append(t("m13.report.test_second_login"))
    rep.notes.append(t("m13.report.note"))


# ---------------------------------------------------------------------------
# m04 — SSH sunucusu
# ---------------------------------------------------------------------------


def narrate_m04(ctx: StepContext, rep: StepReport) -> None:
    before = ctx.data.get("was_installed_before")
    if before is False:
        rep.done.append(t("m04.report.done_installed"))
    elif before is True:
        rep.done.append(t("m04.report.done_existing"))
    else:
        rep.done.append(t("m04.report.done_generic"))
    rep.tests.append(t("m04.report.test_ssh"))
    rep.tests.append(t("m04.report.test_active"))
    rep.tests.append(t("m04.report.test_fingerprint"))
    rep.tests.append(t("m04.report.test_network"))
    rep.notes.append(t("m04.report.note"))


# ---------------------------------------------------------------------------
# m05 — Samba dosya paylaşımı
# ---------------------------------------------------------------------------


def narrate_m05(ctx: StepContext, rep: StepReport) -> None:
    d = ctx.data
    user = str(d.get("samba_user") or ctx.text("samba_user") or "").strip()
    who = t("m05.report.who_user", user=user) if user else t("m05.report.who_generic")
    lead = (
        t("m05.report.lead_installed")
        if d.get("was_installed_before") is False
        else t("m05.report.lead_existing")
    )
    rep.done.append(t("m05.report.done", lead=lead, who=who))
    if user and user != "root":
        rep.done.append(t("m05.report.user_note", user=user))
    rep.tests.append(t(
        "m05.report.test_windows",
        user_part=t("m05.report.user_part", user=user) if user else "",
    ))
    rep.tests.append(t("m05.report.test_active"))
    rep.tests.append(t("m05.report.test_names"))
    rep.notes.append(t("m05.report.note"))


# ---------------------------------------------------------------------------
# m06 — Merkezi log iletimi
# ---------------------------------------------------------------------------

_PROFILE_TEXT = {
    "bakim": lambda: t("m06.report.profile_bakim"),
    "kapsamli": lambda: t("m06.report.profile_kapsamli"),
    "guvenlik": lambda: t("m06.report.profile_guvenlik"),
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
        rep.done.append(t(
            "m06.report.done",
            host=host, port=port,
            proto=proto.upper() or t("m06.report.proto_fallback"),
            profile=profile_label or t("m06.report.profile_fallback"),
            profile_text=_PROFILE_TEXT[profile](),
        ))
    else:
        rep.done.append(t("m06.report.done_generic"))
    if proto == "tcp":
        rep.done.append(t("m06.report.tcp_buffer"))
    elif proto == "udp":
        rep.notes.append(t("m06.report.note_udp"))
    smart = d.get("install_smart_monitoring", ctx.flag("install_smart_monitoring"))
    if smart:
        rep.done.append(t("m06.report.smart"))
    exporter = d.get("install_node_exporter", ctx.flag("install_node_exporter"))
    if exporter:
        listen = ctx.text("node_exporter_listen")
        rep.done.append(
            t("m06.report.exporter_listen", listen=listen) if listen
            else t("m06.report.exporter")
        )
        rep.notes.append(t("m06.report.note_exporter"))
    if profile == "kapsamli":
        rep.notes.append(t("m06.report.note_kapsamli"))

    rep.tests.append(t("m06.report.test_logger"))
    rep.tests.append(t("m06.report.test_two_clones"))
    rep.tests.append(t("m06.report.test_queue"))
    if proto == "tcp":
        rep.tests.append(t("m06.report.test_tcp"))
    if smart:
        rep.tests.append(t("m06.report.test_smart"))
    if exporter:
        rep.tests.append(t("m06.report.test_exporter"))


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
    tz_part = t("m07.report.tz_part", tz=tz) if tz else ""
    if ntp and fallback:
        rep.done.append(t("m07.report.ntp_both", ntp=ntp, fallback=fallback, tz_part=tz_part))
    elif ntp:
        rep.done.append(t("m07.report.ntp_only", ntp=ntp, tz_part=tz_part))
    elif fallback:
        rep.done.append(t("m07.report.fallback_only", fallback=fallback, tz_part=tz_part))
    else:
        rep.done.append(
            t("m07.report.enabled_tz", tz=tz) if tz else t("m07.report.enabled")
        )
    if "pool.ntp.org" in f"{ntp} {fallback}":
        rep.notes.append(t("m07.report.note_pool"))
    rep.tests.append(t(
        "m07.report.test_timedatectl",
        tz_check=t("m07.report.tz_check", tz=tz) if tz else "",
    ))
    rep.tests.append(t("m07.report.test_off_hours"))
    rep.notes.append(t("m07.report.note_tz"))


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
        rep.done.append(t("m08.report.done_template", template=template, prefix=prefix))
    else:
        rep.done.append(t("m08.report.done_generic"))
    prev = str(ctx.data.get("previous_hostname") or "")
    if prev and template and prev != template:
        rep.done.append(t("m08.report.prev", name=prev))
    shown = t("m08.report.shown_prefix", prefix=prefix) if prefix else t("m08.report.shown_generic")
    rep.tests.append(t("m08.report.test_hostnamectl", shown=shown))
    rep.tests.append(t("m08.report.test_reboot"))
    rep.tests.append(t("m08.report.test_sudo"))
    rep.tests.append(t("m08.report.test_session"))
    rep.tests.append(t("m08.report.test_two"))
    if prefix and (len(prefix) > 8 or not _HOSTNAME_OK.match(prefix)):
        rep.notes.append(t("m08.report.note_prefix", prefix=prefix))


# ---------------------------------------------------------------------------
# m11 — Otomatik kapanma
# ---------------------------------------------------------------------------


def _duration(seconds: int) -> str:
    if seconds and seconds % 60 == 0:
        return t("m11.report.duration_min", count=seconds // 60)
    return t("m11.report.duration_sec", count=seconds)


def narrate_m11(ctx: StepContext, rep: StepReport) -> None:
    if not ctx.has_params:
        rep.done.append(
            (ctx.summary.rstrip(".") or t("m11.report.no_params_default")) + "."
        )
        rep.tests.append(t("m11.report.test_no_params"))
        return
    auto = ctx.flag("auto_enabled")
    idle = ctx.flag("idle_enabled")
    hh = ctx.num("auto_hour", 22) or 0
    mm = ctx.num("auto_minute", 0) or 0
    idle_min = ctx.num("idle_minute", 15) or 15
    cs = ctx.num("countdown_seconds", 120) or 120
    at = f"{hh:02d}:{mm:02d}"
    warn = t("m11.report.warn", duration=_duration(cs))
    if auto and idle:
        rep.done.append(t("m11.report.both", at=at, idle=idle_min, warn=warn))
    elif auto:
        rep.done.append(t("m11.report.auto_only", at=at, warn=warn))
    elif idle:
        rep.done.append(t("m11.report.idle_only", idle=idle_min, warn=warn))
    else:
        rep.done.append(t("m11.report.none"))
    if idle:
        rep.tests.append(t(
            "m11.report.test_idle", minutes=idle_min + 1, duration=_duration(cs),
        ))
        rep.tests.append(t("m11.report.test_postpone"))
        rep.tests.append(t("m11.report.test_greeter"))
    if auto:
        rep.tests.append(t("m11.report.test_auto", at=at, duration=_duration(cs)))
        rep.notes.append(t("m11.report.note_postpone"))
        if cs < 60:
            rep.notes.append(t("m11.report.note_short"))
    if auto or idle:
        rep.tests.append(t("m11.report.test_service"))


# ---------------------------------------------------------------------------
# m15 — Uzaktan uyandırma (Wake-on-LAN)
# ---------------------------------------------------------------------------

_WOL_SERVICE = "/etc/systemd/system/tiha-wake-on-lan.service"


def narrate_m15(ctx: StepContext, rep: StepReport) -> None:
    if ctx.data.get("wol_removed"):
        rep.done.append(t("m15.report.removed"))
        return
    rep.done.append(t("m15.report.done"))
    if ctx.data.get("was_ethtool_installed") is False:
        rep.done.append(t("m15.report.ethtool"))
    rep.tests.append(t("m15.report.test_bios"))
    rep.tests.append(t("m15.report.test_ethtool"))
    rep.tests.append(t("m15.report.test_wake"))
    rep.notes.append(t("m15.report.note_macs"))


def narrate_m15_failed(ctx: StepContext, rep: StepReport) -> None:
    if "atlandı" not in ctx.summary:
        narrate_failed(ctx, rep)
        return
    # Kutu işaretsiz uygulandı: hata değil, bilinçli atlama.
    rep.failed = False
    rep.skipped = True
    import os.path
    if os.path.exists(_WOL_SERVICE):
        rep.done.append(t("m15.report.skipped_active"))
        rep.tests.append(t("m15.report.test_skipped_active"))
    else:
        rep.done.append(t("m15.report.skipped"))


# ---------------------------------------------------------------------------
# m12 — Otomatik Ahenk kaydı
# ---------------------------------------------------------------------------


def narrate_m12(ctx: StepContext, rep: StepReport) -> None:
    d = ctx.data
    mac = d.get("imaged_mac")
    rep.done.append(
        t("m12.report.done_mac", mac=mac) if mac else t("m12.report.done")
    )
    if d.get("was_installed_before") is False:
        rep.done.append(t("m12.report.ahenk_installed"))
    rep.done.append(t("m12.report.source_untouched"))
    rep.tests.append(t("m12.report.test_network"))
    rep.tests.append(t("m12.report.test_journal"))
    rep.tests.append(t("m12.report.test_lider"))
    rep.tests.append(t("m12.report.test_register"))
    rep.tests.append(t("m12.report.test_two"))
    rep.notes.append(t("m12.report.note_first_boot"))


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
            rep.done.append(t("m14.report.clear_done"))
        else:
            n = d.get("pw_len")
            when = (
                t("m14.report.when_always") if prot == "always"
                else t("m14.report.when_setup")
            )
            rep.done.append(
                t("m14.report.set_done_len", length=n, when=when) if n
                else t("m14.report.set_done", when=when)
            )
            if faz1:
                rep.done.append(
                    t("m14.report.faz1_both") if prot == "always"
                    else t("m14.report.faz1_admin")
                )
        if model:
            rep.done.append(t("m14.report.model", model=model))
        rep.done.append(t("m14.report.source_untouched"))

        rep.tests.append(t("m14.report.test_journal"))
        if d.get("clear_mode"):
            rep.tests.append(t("m14.report.test_clear"))
        else:
            rep.tests.append(t(
                "m14.report.test_set",
                suffix=t("m14.report.test_set_always") if prot == "always"
                else t("m14.report.test_set_setup"),
            ))
            rep.tests.append(t("m14.report.test_persist"))
        rep.tests.append(t("m14.report.test_source"))
        rep.notes.append(t("m14.report.note_flash"))
        if not d.get("clear_mode"):
            rep.notes.append(t("m14.report.note_plaintext"))
    for a in ctx.action("set_local_supervisor_action"):
        clear = a.data.get("clear_mode")
        rep.done.append(
            t("m14.report.local_cleared") if clear else t("m14.report.local_set")
        )
        rep.notes.append(t("m14.report.note_local"))


# ---------------------------------------------------------------------------
# m16 — GRUB koruması
# ---------------------------------------------------------------------------


def narrate_m16(ctx: StepContext, rep: StepReport) -> None:
    d = ctx.data
    if d.get("removed"):
        rep.done.append(t("m16.report.removed"))
        rep.tests.append(t("m16.report.test_removed"))
        return
    if "linux_backup" in d:
        rep.done.append(t("m16.report.protected"))
        if d.get("recovery_restored"):
            rep.done.append(t("m16.report.recovery_restored"))
        if d.get("recovery_protected") and d.get("recovery_entries") == 0:
            rep.notes.append(t("m16.report.note_recovery_disabled"))
        if d.get("saved_entry_reset"):
            rep.done.append(t("m16.report.saved_entry_reset"))
    elif "zaten etkin" in ctx.summary:
        rep.done.append(t("m16.report.already"))
    elif "zaten yok" in ctx.summary or (ctx.has_params and not ctx.flag("enable_grub_lock")):
        rep.done.append(t("m16.report.not_enabled"))
        rep.notes.append(t("m16.report.note_not_enabled"))
        return
    else:
        rep.done.append((ctx.summary.rstrip(".") or t("m16.report.fallback_default")) + ".")
    rep.tests.append(t("m16.report.test_edit"))
    rep.tests.append(t("m16.report.test_console"))
    rep.tests.append(t("m16.report.test_default_boot"))
    rep.tests.append(t("m16.report.test_recovery"))
    rep.tests.append(t("m16.report.test_after_recovery"))
    rep.tests.append(t("m16.report.test_keyboard"))
    rep.tests.append(t("m16.report.test_advanced"))
    rep.notes.append(t("m16.report.note"))


# ---------------------------------------------------------------------------
# m10 — İmaj için sanitize
# ---------------------------------------------------------------------------


def narrate_m10(ctx: StepContext, rep: StepReport) -> None:
    m = _re.search(r"~(.+?) alan boşaltıldı", ctx.summary)
    freed = m.group(1) if m else ""
    rep.done.append(t("m10.report.done_identity"))
    rep.done.append(
        t("m10.report.done_cleanup_freed", freed=freed)
        if freed and freed != "ölçülemedi" else t("m10.report.done_cleanup")
    )
    rep.done.append(t("m10.report.done_stamp"))
    rep.done.append(t("m10.report.done_sensitive"))
    rep.tests.append(t("m10.report.test_identity"))
    rep.tests.append(t("m10.report.test_ssh"))
    rep.tests.append(t("m10.report.test_network"))
    rep.tests.append(t("m10.report.test_login"))
    rep.tests.append(t("m10.report.test_apt"))
    rep.notes.append(t("m10.report.note"))


# ---------------------------------------------------------------------------
# m17 — Başarım (Deneysel)
# ---------------------------------------------------------------------------

# eta-light-mode ayar anahtarı → metin (çağrıldığında katalogdan okunur)
_LIGHT_LABELS = {
    "effects": lambda: t("m17.report.light.effects"),
    "compositor": lambda: t("m17.report.light.compositor"),
    "thumbnails": lambda: t("m17.report.light.thumbnails"),
    "directory-item-counts": lambda: t("m17.report.light.directory_item_counts"),
    "app-monitoring": lambda: t("m17.report.light.app_monitoring"),
    "low-resolution": lambda: t("m17.report.light.low_resolution"),
    "text-scaling": lambda: t("m17.report.light.text_scaling"),
    "file-icon-size": lambda: t("m17.report.light.file_icon_size"),
    "low-refresh-rate": lambda: t("m17.report.light.low_refresh_rate"),
}


def narrate_m17(ctx: StepContext, rep: StepReport) -> None:
    d = ctx.data
    if d.get("session_cleanup_removed"):
        rep.done.append(t("m17.report.session_removed"))
    if d.get("session_cleanup"):
        rep.done.append(t("m17.report.session_done"))
        rep.tests.append(t("m17.report.test_session"))
        rep.tests.append(t("m17.report.test_ssh"))

    keys = [k for k in d.get("light_mode_keys") or [] if isinstance(k, str)]
    if keys:
        labels = [_LIGHT_LABELS[k]() if k in _LIGHT_LABELS else k for k in keys]
        rep.done.append(t("m17.report.light_done", labels=_join(labels)))
        rep.tests.append(t("m17.report.test_light_login"))
        if "low-resolution" in keys:
            rep.tests.append(t("m17.report.test_low_res"))
        if "low-refresh-rate" in keys:
            rep.tests.append(t("m17.report.test_50hz"))
        if {"low-resolution", "low-refresh-rate"} & set(keys):
            rep.tests.append(t("m17.report.test_cursor_mode"))
    if d.get("light_mode_removed"):
        rep.done.append(t("m17.report.light_removed"))
        rep.tests.append(t("m17.report.test_light_removed"))

    xorg = d.get("cursor_xorg_fix")
    if xorg:
        rep.done.append(t("m17.report.xorg_done", choice=str(xorg).lower()))
        rep.tests.append(t("m17.report.test_xorg"))
        rep.notes.append(t("m17.report.note_xorg"))
    if d.get("cursor_refresh_service"):
        rep.done.append(t("m17.report.cursor_refresh_done"))
        rep.tests.append(t("m17.report.test_cursor_refresh"))
    if d.get("cursor_service_removed"):
        rep.done.append(t("m17.report.cursor_service_removed"))


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
TIHA_STATE_DIR = STATE_DIR

_SENSITIVE_LABELS = {
    ("m01_initial_passwords", "shadow"): lambda: t("core.report.sensitive.shadow"),
    ("m01_initial_passwords", "keyrings"): lambda: t("core.report.sensitive.keyrings"),
    ("m03_otp_secrets", "otp-secrets.json"): lambda: t("core.report.sensitive.otp_backup"),
    ("m03_otp_secrets", "ogretmen-pin-kagitlari-*.html"):
        lambda: t("core.report.sensitive.pin_papers"),
}


def _sensitive_leftovers() -> list[str]:
    """Sanitize'ın sildiği hassas TiHA yedeklerinden diskte duranlar."""
    found = []
    for key in SENSITIVE_STATE:
        module_dir, pattern = key
        root = TIHA_STATE_DIR / module_dir
        try:
            if root.is_dir() and any(root.glob(pattern)):
                label = _SENSITIVE_LABELS.get(key)
                found.append(label() if label else f"{module_dir}/{pattern}")
        except OSError:
            continue
    return found


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
        return t("core.report.quoted", title=titles.get(mid, mid))

    get = contexts.get
    m01, m03 = get("m01_initial_passwords"), get("m03_otp_secrets")
    m11, m14 = get("m11_power_management"), get("m14_bios_password")
    m15, m16 = get("m15_wake_on_lan"), get("m16_grub_protection")

    # --- Başarısız adımlar --------------------------------------------------
    failed = [mid for mid, c in contexts.items() if _really_failed(c)]
    if failed:
        w.append(t("core.report.warn.failed", steps=_join([q(m) for m in failed])))

    # --- İmaj temizliği (sanitize): varlık, sıra ve sonrası -------------------
    sanitize = "m10_image_sanitize"
    others = applied - {sanitize}
    if sanitize not in applied:
        if others:
            w.append(t("core.report.warn.sanitize_missing", step=q(sanitize)))
    else:
        t10 = ts(sanitize)
        later = {titles.get(mid, mid) for mid in others if ts(mid) > t10}
        later |= {a.title for c in contexts.values() for a in c.actions if a.timestamp > t10}
        if later:
            w.append(t("core.report.warn.after_sanitize", steps=_join(sorted(later))))
        if _read(MACHINE_ID):
            w.append(t("core.report.warn.machine_id"))
        if SSH_SENTINEL.exists():
            w.append(t("core.report.warn.ssh_sentinel", path=SSH_SENTINEL))

    # --- Lider / Ahenk --------------------------------------------------------
    if "m12_ahenk_reset" not in applied and AHENK_CONF.exists():
        w.append(t("core.report.warn.ahenk", step=q("m12_ahenk_reset")))

    # --- Benzersiz ad ---------------------------------------------------------
    by_name = [m for m in ("m04_ssh_server", "m05_samba_share", "m06_remote_syslog") if m in applied]
    if by_name and "m08_hostname" not in applied:
        w.append(t(
            "core.report.warn.hostname",
            hostname=q("m08_hostname"), steps=_join([q(m) for m in by_name]),
        ))

    # --- Parola, PIN ve QR ilişkileri -----------------------------------------
    if "m02_boot_password_wipe" in applied:
        if m01 is not None and m01.applied and "ogretmen" in _m01_passwords(m01)[0]:
            w.append(t("core.report.warn.wipe_ogretmen", wipe=q("m02_boot_password_wipe")))
        if "m03_otp_secrets" not in applied:
            w.append(t(
                "core.report.warn.wipe_no_pin",
                wipe=q("m02_boot_password_wipe"), pin=q("m03_otp_secrets"),
            ))
    if "m13_password_dialog" in applied and "m03_otp_secrets" not in applied:
        w.append(t("core.report.warn.qr_no_pin", qr=q("m13_password_dialog")))
    if (m01 is not None and m03 is not None and m01.applied and m03.applied
            and m01.data.get("created_reserve")
            and ts("m01_initial_passwords") > ts("m03_otp_secrets")):
        w.append(t("core.report.warn.reserve_after_pin", pin=q("m03_otp_secrets")))

    # --- Saat -----------------------------------------------------------------
    need_time = []
    need_pin_time = "m03_otp_secrets" in applied
    if need_pin_time:
        need_time.append(t("core.report.warn.time_pin"))
    if m11 is not None and m11.applied and m11.flag("auto_enabled"):
        need_time.append(t("core.report.warn.time_shutdown"))
    if "m06_remote_syslog" in applied:
        need_time.append(t("core.report.warn.time_logs"))
    if need_time and "m07_time_sync" not in applied:
        w.append(t(
            "core.report.warn.time",
            items=_join(need_time), step=q("m07_time_sync"),
            suffix=t("core.report.warn.time_suffix_pin") if need_pin_time
            else t("core.report.warn.time_suffix"),
        ))

    # --- Uyandırma, kapanma ve BIOS -------------------------------------------
    if m15 is not None and m15.applied and m11 is not None and m11.applied and m11.flag("idle_enabled"):
        idle = m11.num("idle_minute", 15) or 15
        cs = m11.num("countdown_seconds", 120) or 120
        w.append(t("core.report.warn.wol_idle", idle=idle, duration=_duration(cs)))
    if _bios_on(m14) and m15 is not None and m15.applied:
        if m14.data.get("protection") == "always":
            w.append(t("core.report.warn.bios_always_wol"))
        w.append(t("core.report.warn.bios_wol"))
    if _bios_on(m14) and "m12_ahenk_reset" in applied:
        w.append(t(
            "core.report.warn.bios_ahenk",
            bios=q("m14_bios_password"), ahenk=q("m12_ahenk_reset"),
        ))

    # --- Açılış güvenliği bütünlüğü --------------------------------------------
    if _grub_on(m16) and not _bios_on(m14):
        w.append(t("core.report.warn.grub_no_bios"))
    if _bios_on(m14) and not _grub_on(m16):
        w.append(t("core.report.warn.bios_no_grub"))

    # --- TiHA'nın kendi hassas yedekleri diskte mi? (canlı denetim) -------------
    left = _sensitive_leftovers()
    if left:
        w.append(t(
            "core.report.warn.sensitive",
            items=_join(left), step=q(sanitize),
            again=t("core.report.warn.again") if sanitize in applied else "",
        ))
    return w


def general_tests(contexts: dict[str, StepContext]) -> list[str]:
    """Her imaj için geçerli, adımlardan bağımsız klon denetimleri."""
    applied = {mid for mid, c in contexts.items() if c.applied}
    tests = [
        t("core.report.general.first_boot"),
        t("core.report.general.reboot"),
        t("core.report.general.accounts"),
        t("core.report.general.hardware"),
    ]
    identity = {"m04_ssh_server", "m05_samba_share", "m06_remote_syslog",
                "m08_hostname", "m10_image_sanitize", "m12_ahenk_reset"}
    if applied & identity:
        tests.append(t("core.report.general.identity"))
    tests += [
        t("core.report.general.models"),
        t("core.report.general.school"),
        t("core.report.general.fix_source"),
    ]
    return tests
