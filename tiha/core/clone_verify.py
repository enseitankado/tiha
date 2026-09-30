"""Klon tahtada "Kontrol et" listesinin maddelerini otomatik denetler.

Özet raporunun "Kontrol et" listesi (``Report.test_groups()``) klonda da
aynıdır: günlük imajla gelir. Bu modül listedeki HER maddeyi, hangi metin
anahtarından üretildiğine bakarak tanır ve o maddeye özel bir denetim
çalıştırır. Sonuç dört türdür:

* ``ok``      — makinede denetlendi, beklendiği gibi.
* ``fail``    — makinede denetlendi, beklenen durum yok.
* ``partial`` — makinede denetlenebilen kısmı doğru; kalanı (giriş
  ekranı, BIOS, ikinci klon, telefon…) elle denenmeli.
* ``manual``  — yalnız elle denenebilir.

Denetimler salt okunurdur; sistemde ayar değiştirmez. İstisnalar
``apt-get update`` (paket listelerini tazeler, yapılandırmaya dokunmaz)
ve log sunucusuna gönderilen "tiha-klon-test" deneme kaydıdır.

Maddede elle çalıştırılması istenen komutlar (``systemctl is-active``,
``id``, ``journalctl``, ``sensors``…) denetimde de çalıştırılır; komut
ve kısa çıktısı akış penceresinde maddenin altında görünür.
"""

from __future__ import annotations

import grp
import json
import pwd
import re
import socket
import time
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .i18n import t
from .utils import run_cmd

OK, FAIL, PARTIAL, MANUAL = "ok", "fail", "partial", "manual"


@dataclass
class ItemResult:
    tid: str          # "0.1.66-3.2"
    text: str         # maddenin metni
    key: str          # eşleşen metin anahtarı ("" = tanınmadı)
    status: str
    detail: str = ""


def M(key: str, **kw) -> str:
    return t(f"clone_verify.{key}", **kw)


# --- Madde metni → anahtar ---------------------------------------------------


_LOCALE = Path(__file__).resolve().parents[1] / "locale" / "tr.toml"


@lru_cache(maxsize=1)
def _templates() -> list[tuple[str, re.Pattern]]:
    """Kontrol maddesi üreten bütün şablonlar, metni geri tanıyan desenle."""
    data = tomllib.loads(_LOCALE.read_text(encoding="utf-8"))
    out: list[tuple[str, str]] = []

    def walk(node, prefix):
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                walk(v, path)
            elif isinstance(v, str) and (
                re.search(r"\.report\.test_", path) or path.startswith("core.report.general.")
            ):
                out.append((path, v))

    walk(data, "")
    compiled = []
    for key, tpl in out:
        seen: set[str] = set()

        def repl(m, seen=seen):
            name = m.group(1)
            if name in seen:
                return "(?:.*?)"
            seen.add(name)
            return f"(?P<{name}>.*?)"

        pattern = re.sub(r"\\\{(\w+)\\\}", repl, re.escape(tpl.strip()))
        compiled.append((key, re.compile("^" + pattern + "$", re.S)))
    # Uzun (daha belirgin) şablon önce denensin.
    compiled.sort(key=lambda kp: -len(kp[1].pattern))
    return compiled


def identify(text: str) -> tuple[str, dict]:
    for key, pat in _templates():
        m = pat.match(text.strip())
        if m:
            return key, {k: v for k, v in m.groupdict().items() if v is not None}
    return "", {}


# --- Sistem yardımcıları ------------------------------------------------------


# Bir maddenin denetiminde çalışan komutlar ve kısa çıktıları; verify()
# bunları akış penceresine maddenin altına yazar.
_TRACE: list[str] = []
_TRACE_LINES = 4


def _run(cmd: list[str], timeout: int = 15):
    r = run_cmd(cmd, timeout=timeout, check=False)
    out = [ln.rstrip() for ln in (r.stdout + r.stderr).splitlines() if ln.strip()]
    _TRACE.append(f"$ {' '.join(cmd)}  → {r.returncode}")
    _TRACE.extend(f"  {ln[:160]}" for ln in out[:_TRACE_LINES])
    if len(out) > _TRACE_LINES:
        _TRACE.append(f"  … (+{len(out) - _TRACE_LINES} satır)")
    return r


def _active(unit: str) -> bool:
    return _run(["systemctl", "is-active", unit]).returncode == 0


def _enabled(unit: str) -> bool:
    return _run(["systemctl", "is-enabled", "--quiet", unit]).returncode == 0


def _user(name: str):
    try:
        return pwd.getpwnam(name)
    except KeyError:
        return None


def _groups(name: str) -> set[str]:
    r = _run(["id", "-nG", name])
    return set(r.stdout.split()) if r.ok else set()


def _pw_status(name: str) -> str:
    """passwd -S: P (parola var), L (kilitli), NP (parolasız)."""
    r = _run(["passwd", "-S", name])
    parts = r.stdout.split()
    return parts[1] if r.ok and len(parts) > 1 else "?"


def _personal_accounts() -> list[str]:
    return sorted(
        e.pw_name for e in pwd.getpwall()
        if 1000 <= e.pw_uid < 60000 and e.pw_name not in ("etapadmin", "ogretmen", "ogrenci")
    )


def _reserve_accounts() -> list[str]:
    return [n for n in _personal_accounts() if re.fullmatch(r"ogretmen\.?\d+", n)]


def _secrets() -> set[str]:
    try:
        return set(json.loads(Path("/etc/otp-secrets.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _journal(tag: str, *, boot: bool = True) -> str:
    cmd = ["journalctl", "-t", tag, "--no-pager", "-o", "cat"]
    if boot:
        cmd.append("-b")
    return _run(cmd, timeout=20).stdout


def _wired_iface() -> tuple[str, str] | tuple[None, None]:
    net = Path("/sys/class/net")
    for d in sorted(net.iterdir()) if net.exists() else []:
        if d.name == "lo" or (d / "wireless").exists() or not (d / "device").exists():
            continue
        try:
            return d.name, (d / "address").read_text().strip().lower()
        except OSError:
            continue
    return None, None


def _grub_cfg() -> str:
    try:
        return Path("/boot/grub/grub.cfg").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read(path: str | Path) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        _TRACE.append(f"$ cat {path}  → okunamadı ({exc.strerror})")
        return ""
    _TRACE.append(f"$ cat {path}  → {len(text.splitlines())} satır")
    return text


def _join(items) -> str:
    return ", ".join(items) if items else "-"


# --- Madde denetimleri --------------------------------------------------------
# Her işlev (durum, ayrıntı) döner. params: madde metninden okunan değerler.


def _branches(p):
    user = p.get("user", "").strip()
    if not _user(user):
        return FAIL, M("account_missing", user=user)
    if "ogretmenler" not in _groups(user):
        return FAIL, M("not_in_group", user=user)
    return PARTIAL, M("branch_ok", user=user)


def _branches_deleted(p):
    users = [u.strip() for u in re.split(r"[,\s]+(?:ve\s+)?", p.get("users", "")) if u.strip() and u.strip() != "ve"]
    left = [u for u in users if _user(u)]
    return (FAIL, M("still_exist", users=_join(left))) if left else (OK, M("all_deleted", users=_join(users)))


def _pw_unknown(user: str, st: str):
    return (MANUAL, M("pw_unreadable", user=user)) if st == "?" else None


def _etapadmin_pw(_p):
    st = _pw_status("etapadmin")
    if (u := _pw_unknown("etapadmin", st)):
        return u
    if st != "P":
        return FAIL, M("pw_not_set", user="etapadmin", state=st)
    return PARTIAL, M("pw_set_login_manual", user="etapadmin")


def _ogretmen_pw(_p):
    if not _user("ogretmen"):
        return FAIL, M("account_missing", user="ogretmen")
    st = _pw_status("ogretmen")
    if (u := _pw_unknown("ogretmen", st)):
        return u
    if st != "P":
        return FAIL, M("pw_not_set", user="ogretmen", state=st)
    return PARTIAL, M("pw_set_login_manual", user="ogretmen")


def _reserve(_p):
    res = _reserve_accounts()
    if not res:
        return FAIL, M("no_reserve")
    need = {"audio", "video", "plugdev", "ogretmenler"}
    bad = [u for u in res if not need <= _groups(u)]
    if bad:
        return FAIL, M("reserve_groups_bad", users=_join(bad))
    return OK, M("reserve_ok", count=len(res))


def _reserve_purged(_p):
    res = _reserve_accounts()
    return (FAIL, M("still_exist", users=_join(res))) if res else (OK, M("no_reserve_left"))


def _root_pw(_p):
    st = _pw_status("root")
    if (u := _pw_unknown("root", st)):
        return u
    if st != "P":
        return FAIL, M("pw_not_set", user="root", state=st)
    return PARTIAL, M("pw_set_login_manual", user="root")


def _student_gone(_p):
    return (FAIL, M("still_exist", users="ogrenci")) if _user("ogrenci") else (OK, M("student_gone"))


def _boot_wipe_journal(_p):
    out = _journal("tiha-boot-wipe")
    if not out.strip():
        return FAIL, M("wipe_no_journal")
    errors = [ln for ln in out.splitlines() if "HATA" in ln.upper()]
    if errors:
        return FAIL, M("wipe_errors", count=len(errors), first=errors[0][:120])
    return OK, M("wipe_ran", lines=len(out.splitlines()))


def _boot_wipe_ran(_p):
    if not _enabled("tiha-boot-password-wipe.service"):
        return FAIL, M("unit_disabled", unit="tiha-boot-password-wipe.service")
    if not _journal("tiha-boot-wipe").strip():
        return FAIL, M("wipe_no_journal")
    return PARTIAL, M("wipe_ran_login_manual")


def _pin_accounts(_p):
    s = _secrets()
    if not s:
        return FAIL, M("no_secrets")
    return PARTIAL, M("secrets_count", count=len([k for k in s if not k.startswith("@")]))


def _auto_group(_p):
    unit = "tiha-auto-teacher-group.path"
    if not _enabled(unit):
        return FAIL, M("unit_disabled", unit=unit)
    return PARTIAL, M("auto_group_ok")


def _secret_for(user: str):
    def check(p):
        name = (p.get("user") or user).strip()
        if name not in _secrets():
            return FAIL, M("no_secret_for", user=name)
        if not _user(name) and not name.startswith("@"):
            return FAIL, M("account_missing", user=name)
        return PARTIAL, M("secret_ok_login_manual", user=name)
    return check


def _etapadmin_pin(p):
    status, detail = _secret_for("etapadmin")(p)
    if status == FAIL:
        return status, detail
    if _pw_status("etapadmin") not in ("P", "?"):
        return FAIL, M("pw_not_set", user="etapadmin", state=_pw_status("etapadmin"))
    return PARTIAL, detail


def _extra_removed(_p):
    extra = _personal_accounts()
    return (FAIL, M("extra_left", users=_join(extra))) if extra else (OK, M("only_defaults"))


def _greeter(_p):
    if not Path("/usr/local/bin/greeter-cache-olustur.sh").exists():
        return FAIL, M("greeter_missing")
    return PARTIAL, M("greeter_ok")


def _group_pin(_p):
    if "@ogretmenler" not in _secrets():
        return FAIL, M("no_secret_for", user="@ogretmenler")
    try:
        members = grp.getgrnam("ogretmenler").gr_mem
    except KeyError:
        return FAIL, M("group_missing")
    if "ogretmen" in members:
        return FAIL, M("ogretmen_in_group")
    return PARTIAL, M("group_pin_ok", count=len(members))


def _time(_p):
    r = _run(["timedatectl", "show", "-p", "NTPSynchronized", "-p", "Timezone"])
    props = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    if props.get("NTPSynchronized") != "yes":
        return FAIL, M("ntp_not_synced", tz=props.get("Timezone", "?"))
    return OK, M("ntp_synced", tz=props.get("Timezone", "?"))


def _timedatectl(p):
    status, detail = _time(p)
    m = re.search(r'Time zone: ([^"]+)"', p.get("tz_check", ""))
    if status == OK and m:
        tz = _run(["timedatectl", "show", "-p", "Timezone", "--value"]).stdout.strip()
        if tz != m.group(1).strip():
            return FAIL, M("tz_wrong", tz=tz, expected=m.group(1).strip())
    return status, detail


def _ssh_active(_p):
    if not _active("ssh"):
        return FAIL, M("unit_inactive", unit="ssh")
    r = _run(["sshd", "-T"])
    cfg = dict(ln.split(" ", 1) for ln in r.stdout.splitlines() if " " in ln)
    root, pw = cfg.get("permitrootlogin", "?"), cfg.get("passwordauthentication", "?")
    if root != "yes" or pw != "yes":
        return FAIL, M("sshd_cfg", root=root, pw=pw)
    if (bad := _ssh_allow_users_bad(r.stdout)) is not None:
        return FAIL, M("sshd_allow_users", users=bad)
    return OK, M("sshd_ok")


def _ssh_allow_users_bad(sshd_t: str) -> str | None:
    """``sshd -T`` çıktısındaki AllowUsers yalnız root ve etapadmin değilse
    bulunan listeyi (boşsa "-") döner; doğruysa None."""
    from ..modules.m04_ssh_server import SSH_ALLOWED_USERS
    users = sorted({
        u for ln in sshd_t.splitlines() if ln.startswith("allowusers ")
        for u in ln.split()[1:]
    })
    return None if users == sorted(SSH_ALLOWED_USERS) else _join(users)


def _ssh_only_admins(_p):
    if not _active("ssh"):
        return FAIL, M("unit_inactive", unit="ssh")
    if (bad := _ssh_allow_users_bad(_run(["sshd", "-T"]).stdout)) is not None:
        return FAIL, M("sshd_allow_users", users=bad)
    return PARTIAL, M("ssh_only_admins_manual")


def _ssh_listen(_p):
    if not _active("ssh"):
        return FAIL, M("unit_inactive", unit="ssh")
    r = _run(["ss", "-ltnH", "sport = :22"])
    if not r.stdout.strip():
        return FAIL, M("not_listening", port=22)
    return PARTIAL, M("listening_remote_manual", port=22)


def _host_key(_p):
    key = Path("/etc/ssh/ssh_host_ed25519_key.pub")
    if not key.exists():
        return FAIL, M("no_host_key")
    fp = _run(["ssh-keygen", "-lf", str(key)]).stdout.split()
    return PARTIAL, M("fingerprint_compare", fp=fp[1] if len(fp) > 1 else "?")


def _identity(p):
    mid = _read("/etc/machine-id").strip()
    if not mid:
        return FAIL, M("machine_id_empty")
    from ..modules.m10_image_sanitize import IDENTITY_LOG_TAG, IDENTITY_MAC_FILE, _local_macs
    signed = _read(IDENTITY_MAC_FILE).split()
    if signed:
        _journal(IDENTITY_LOG_TAG, boot=False)
        if not set(signed) & set(_local_macs()):
            return FAIL, M("clone_identity_not_run")
    status, detail = _host_key(p)
    if status == FAIL:
        return status, detail
    return PARTIAL, M("identity_compare", mid=mid[:12], fp=detail)


def _smbd(_p):
    return (OK, M("unit_ok", unit="smbd")) if _active("smbd") else (FAIL, M("unit_inactive", unit="smbd"))


def _samba_share(_p):
    if not _active("smbd"):
        return FAIL, M("unit_inactive", unit="smbd")
    r = _run(["testparm", "-s"], timeout=20)
    if "[root]" not in r.stdout:
        return FAIL, M("share_missing")
    return PARTIAL, M("share_ok_remote_manual")


def _samba_names(_p):
    if not _active("nmbd"):
        return FAIL, M("unit_inactive", unit="nmbd")
    return PARTIAL, M("netbios_name", name=socket.gethostname())


def _exporter(_p):
    r = _run(["curl", "-fsS", "--max-time", "5", "http://127.0.0.1:9100/metrics"])
    if not r.ok:
        return FAIL, M("exporter_down")
    return PARTIAL, M("exporter_local_ok")


def _rsyslog(_p):
    if not _active("rsyslog"):
        return FAIL, M("unit_inactive", unit="rsyslog")
    if not Path("/etc/rsyslog.d/90-tiha-remote.conf").exists():
        return FAIL, M("file_missing", path="/etc/rsyslog.d/90-tiha-remote.conf")
    # Maddedeki deneme kaydını gönder; sunucuda aranacak olan bu satır.
    if not _run(["logger", "-p", "auth.notice", "tiha-klon-test"]).ok:
        return FAIL, M("logger_failed")
    return PARTIAL, M("rsyslog_ok_server_manual", host=socket.gethostname())


def _rsyslog_queue(_p):
    qdir = Path("/var/lib/rsyslog")
    _run(["ls", "-la", str(qdir)])
    files = [f.name for f in qdir.iterdir() if f.is_file() and not f.name.startswith("imjournal")] \
        if qdir.exists() else []
    return (FAIL, M("queue_files", files=_join(files[:5]))) if files else (OK, M("queue_empty"))


def _virt() -> str:
    """Sanal makinedeyse sanallaştırma adı (ör. vmware), değilse ""."""
    r = _run(["systemd-detect-virt"])
    v = r.stdout.strip()
    return v if r.ok and v and v != "none" else ""


def _smart(_p):
    """smartd (disk sağlığı), lm-sensors (sıcaklık modülleri) ve sensors.

    ``sensors`` bir servis değil komuttur; servis adı ``lm-sensors``dır
    (açılışta modülleri yükleyip çıkar, "active (exited)" görünür)."""
    virt = _virt()
    problems, notes = [], []
    if _active("smartd"):
        notes.append(M("smartd_running"))
    elif virt:
        notes.append(M("smartd_vm", virt=virt))
    elif not _run(["/usr/sbin/smartctl", "--scan"]).stdout.strip():
        notes.append(M("smartd_no_devices"))
    else:
        problems.append(M("unit_inactive", unit="smartd"))
    if not _active("lm-sensors"):
        problems.append(M("unit_inactive", unit="lm-sensors"))
    temps = re.findall(r"[+-]\d+\.\d°C", _run(["sensors"]).stdout)
    if temps:
        notes.append(M("temp_ok", temp=temps[0]))
    elif virt:
        notes.append(M("no_sensors_vm"))
    else:
        problems.append(M("no_sensors"))
    if problems:
        return FAIL, " ".join(problems + notes)
    return (PARTIAL if virt else OK), " ".join(notes)


def _hostname(p):
    name = _run(["hostnamectl", "hostname"]).stdout.strip() or socket.gethostname()
    m = re.search(r"'([^']+)'", p.get("shown", ""))
    prefix = m.group(1) if m else ""
    iface, mac = _wired_iface()
    if not mac:
        return FAIL, M("no_wired")
    _run(["ip", "-br", "link", "show", iface])
    tail = mac.replace(":", "")[-6:]
    if prefix and not name.startswith(prefix):
        return FAIL, M("hostname_prefix", name=name, prefix=prefix)
    if not name.lower().endswith(tail):
        return FAIL, M("hostname_mac", name=name, tail=tail)
    return OK, M("hostname_ok", name=name)


def _hostname_stable(p):
    status, detail = _hostname(p)
    return (PARTIAL, M("reboot_manual", detail=detail)) if status == OK else (status, detail)


def _hosts(_p):
    # Maddedeki `time sudo true`: sudo kendi adını çözmeye çalışır;
    # /etc/hosts güncel değilse DNS zaman aşımını bekler.
    name = socket.gethostname()
    start = time.monotonic()
    r = _run(["sudo", "-n", "true"], timeout=15)
    if not r.ok:  # root değilsek sudo parola ister; adı doğrudan çözdür
        start = time.monotonic()
        r = _run(["getent", "hosts", name], timeout=15)
    took = time.monotonic() - start
    if not r.ok or took > 1.0:
        return FAIL, M("hosts_slow", name=name, sec=f"{took:.1f}")
    return OK, M("hosts_ok", name=name, sec=f"{took:.2f}")


_RUN_CACHE: dict[str, tuple[str, str]] = {}


def _apt(_p):
    # Birden çok madde ister; bir denetim turunda bir kez çalışır.
    if "apt" in _RUN_CACHE:
        return _RUN_CACHE["apt"]
    _RUN_CACHE["apt"] = _apt_run()
    return _RUN_CACHE["apt"]


def _apt_run():
    r = _run(["apt-get", "update"], timeout=180)
    if not r.ok or re.search(r"^(E|Err):", r.stdout + r.stderr, re.M):
        first = next((ln for ln in (r.stdout + r.stderr).splitlines() if ln.startswith(("E:", "Err:"))), "")
        return FAIL, M("apt_failed", line=first[:120])
    return PARTIAL, M("apt_ok_repos_manual")


def _image_info(p):
    path = Path("/etc/tiha-image-info.json")
    if not path.exists():
        return FAIL, M("file_missing", path=str(path))
    _run(["ls", "-l", str(path)])
    st = path.stat()
    if st.st_uid != 0 or (st.st_mode & 0o777) != 0o600:
        return FAIL, M("image_info_mode", mode=oct(st.st_mode & 0o777))
    try:
        version = json.loads(path.read_text(encoding="utf-8")).get("tiha_version", "?")
    except (OSError, ValueError):
        version = "?"
    status, detail = _apt(p)
    if status == FAIL:
        return status, detail
    return PARTIAL, M("image_info_ok", version=version)


def _wired_up(_p):
    r = _run(["nmcli", "-t", "-f", "TYPE,STATE", "device"])
    if re.search(r"^ethernet:connected", r.stdout, re.M):
        return OK, M("wired_connected")
    return FAIL, M("wired_not_connected")


def _ssh_keys(_p):
    keys = list(Path("/etc/ssh").glob("ssh_host_*_key.pub"))
    if keys:
        _run(["ls", *sorted(str(k) for k in keys)])
    if not keys:
        return FAIL, M("no_host_key")
    if _run(["dpkg-query", "-W", "-f=${Status}", "openssh-server"]).stdout.endswith("installed") \
            and not _active("ssh"):
        return FAIL, M("unit_inactive", unit="ssh")
    return OK, M("host_keys_ok", count=len(keys))


def _hardware(_p):
    problems = []
    if _wired_iface()[0] is None:
        problems.append(M("hw_no_wired"))
    if not re.search(r"^card \d", _run(["aplay", "-l"]).stdout, re.M):
        problems.append(M("hw_no_sound"))
    if "touch" not in _read("/proc/bus/input/devices").lower():
        problems.append(M("hw_no_touch"))
    if problems:
        return FAIL, "; ".join(problems)
    return PARTIAL, M("hw_ok_manual")


def _shutdown_conf():
    import configparser
    cfg = configparser.ConfigParser()
    try:
        cfg.read_string(_read("/etc/pardus/eta-shutdown.conf"))
    except configparser.Error:
        pass
    return cfg


def _shutdown_service(_p):
    return (OK, M("unit_ok", unit="eta-shutdown")) if _active("eta-shutdown") \
        else (FAIL, M("unit_inactive", unit="eta-shutdown"))


def _exempt_macs_here() -> list[str]:
    """Bu tahtanın muaf listesinde olan MAC adresleri (boşsa muaf değil)."""
    from ..modules.m11_power_management import _current_exempt_macs, _local_macs
    return sorted(set(_current_exempt_macs()) & _local_macs())


def _shutdown_auto(p):
    cfg = _shutdown_conf()
    if cfg.get("AUTO_SHUTDOWN", "enabled", fallback="False").lower() != "true":
        # Muaf tahtada kapalı olması beklenen durumdur.
        if (macs := _exempt_macs_here()):
            return OK, M("exempt_off", mac=_join(macs))
        return FAIL, M("auto_off")
    at = f'{int(cfg.get("AUTO_SHUTDOWN", "hour", fallback="0")):02d}:{int(cfg.get("AUTO_SHUTDOWN", "minute", fallback="0")):02d}'
    if p.get("at") and p["at"] != at:
        return FAIL, M("auto_time_wrong", at=at, expected=p["at"])
    status, detail = _shutdown_service(p)
    return (status, detail) if status == FAIL else (PARTIAL, M("auto_ok_manual", at=at))


def _shutdown_idle(p):
    cfg = _shutdown_conf()
    if cfg.get("TIMED_MODE", "mode", fallback="none") == "none":
        if (macs := _exempt_macs_here()):
            return OK, M("exempt_off", mac=_join(macs))
        return FAIL, M("idle_off")
    if not Path("/usr/local/sbin/tiha-shutdown-countdown.py").exists():
        return FAIL, M("file_missing", path="/usr/local/sbin/tiha-shutdown-countdown.py")
    return PARTIAL, M("idle_ok_manual", minutes=cfg.get("TIMED_MODE", "minute", fallback="?"))


def _shutdown_off(_p):
    cfg = _shutdown_conf()
    auto = cfg.get("AUTO_SHUTDOWN", "enabled", fallback="False").lower() == "true"
    idle = cfg.get("TIMED_MODE", "mode", fallback="none") != "none"
    return (FAIL, M("shutdown_not_off")) if auto or idle else (OK, M("shutdown_off"))


def _shutdown_exempt(p):
    mac = p.get("mac", "").strip().lower()
    _iface, mine = _wired_iface()
    if mine and mac and mine == mac:
        return _shutdown_off(p)
    return MANUAL, M("exempt_other_board", mac=mine or "?")


def _shutdown_conf_exists(_p):
    if not Path("/etc/pardus/eta-shutdown.conf").exists():
        return FAIL, M("file_missing", path="/etc/pardus/eta-shutdown.conf")
    return PARTIAL, M("shutdown_conf_manual")


def _reclaim_journal(_p):
    out = _journal("tiha-clone-reclaim", boot=False)
    if "ulaşılamadı" in out.lower() or "ulasilamadi" in out.lower():
        return FAIL, M("reclaim_api_down")
    if re.search(r"KAYITLI|KAYITSIZ", out):
        return OK, M("reclaim_ok")
    return FAIL, M("reclaim_no_journal")


def _eba_api(_p):
    r = _run(["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "8",
              "https://api-etap.eba.gov.tr"], timeout=15)
    code = r.stdout.strip()
    if r.ok and code and code != "000":
        return OK, M("eba_reachable", code=code)
    return FAIL, M("eba_unreachable")


def _qr_dialog(_p):
    text = _read("/etc/xdg/autostart/tr.org.eta.password-changer.desktop")
    if text and not re.search(r"^Hidden\s*=\s*true", text, re.M | re.I):
        return FAIL, M("qr_dialog_visible")
    return PARTIAL, M("qr_dialog_hidden_manual")


def _bios_journal(_p):
    out = _journal("tiha-first-boot-bios", boot=False)
    ok_line = "başarılı" in out
    script_gone = not Path("/usr/local/sbin/tiha-first-boot-bios.py").exists()
    if ok_line and script_gone:
        return OK, M("bios_ok")
    if not ok_line:
        return FAIL, M("bios_no_success")
    return FAIL, M("bios_script_left")


def _bios_persist(p):
    status, detail = _bios_journal(p)
    return (PARTIAL, M("bios_enter_manual")) if status == OK else (status, detail)


def _wol(_p):
    iface, mac = _wired_iface()
    if not iface:
        return FAIL, M("no_wired")
    r = _run(["ethtool", iface])
    m = re.search(r"^\s*Wake-on:\s*(\S+)", r.stdout, re.M)
    if not m:
        return FAIL, M("ethtool_unreadable", iface=iface)
    if "g" not in m.group(1):
        return FAIL, M("wol_off", iface=iface, value=m.group(1))
    return OK, M("wol_on", iface=iface, mac=mac)


def _wol_service(p):
    if not _enabled("tiha-wake-on-lan.service"):
        return FAIL, M("unit_disabled", unit="tiha-wake-on-lan.service")
    return _wol(p)


def _wake(p):
    status, detail = _wol(p)
    return (PARTIAL, M("wake_manual", detail=detail)) if status == OK else (status, detail)


def _grub_locked(_p):
    cfg = _grub_cfg()
    if not cfg:
        return MANUAL, M("grub_unreadable")
    if "set superusers" not in cfg or "password_pbkdf2" not in cfg:
        return FAIL, M("grub_no_password")
    return PARTIAL, M("grub_locked_manual")


def _grub_recovery(p):
    status, detail = _grub_locked(p)
    if status != PARTIAL:
        return status, detail
    cfg = _grub_cfg()
    rec = [ln for ln in _menu_entries(cfg) if "recovery" in ln.lower()]
    if any("--unrestricted" in ln for ln in rec):
        return FAIL, M("grub_recovery_open")
    return PARTIAL, M("grub_recovery_locked", count=len(rec))


_MENUENTRY = re.compile(r"^\s*menuentry\s")


def _menu_entries(cfg: str) -> list[str]:
    """Gerçek menü girdisi satırları. ``menuentry_id_option="--id"`` gibi
    başlık değişkenleri de "menuentry" ile başladığından boşlukla ayırt
    edilir."""
    return [ln for ln in cfg.splitlines() if _MENUENTRY.match(ln)]


def _default_entry(cfg: str) -> str:
    """Açılışta seçilecek girdinin satırı. GRUB_DEFAULT=saved ise (ETAP'ta
    öyle) grubenv'deki kayıtlı girdi; bulunamazsa ilk girdi."""
    entries = _menu_entries(cfg)
    if not entries:
        return ""
    if "saved_entry" in cfg:
        m = re.search(r"^saved_entry=(.+)$", _read("/boot/grub/grubenv"), re.M)
        if m:
            # Alt menü yolu "gnulinux-advanced-…>gnulinux-…" biçiminde olabilir.
            target = m.group(1).strip().split(">")[-1]
            for ln in entries:
                if f"'{target}'" in ln:
                    return ln
    return entries[0]


def _grub_default(p):
    status, detail = _grub_locked(p)
    if status != PARTIAL:
        return status, detail
    if "--unrestricted" not in _default_entry(_grub_cfg()):
        return FAIL, M("grub_default_locked")
    return PARTIAL, M("grub_default_ok")


def _grub_after_recovery(p):
    env = _read("/boot/grub/grubenv")
    if "saved_entry=" in env and "recovery" in env.lower():
        return FAIL, M("grub_saved_recovery")
    return _grub_default(p)


def _grub_removed(_p):
    cfg = _grub_cfg()
    if not cfg:
        return MANUAL, M("grub_unreadable")
    return (FAIL, M("grub_still_locked")) if "set superusers" in cfg else (OK, M("grub_unlocked"))


def _light_login(_p):
    from ..modules.m17_performance import LIGHT_AUTOSTART, LIGHT_SETTINGS
    if not (LIGHT_SETTINGS.exists() and LIGHT_AUTOSTART.exists()):
        return FAIL, M("light_missing")
    return PARTIAL, M("light_ok_manual")


def _light_removed(_p):
    from ..modules.m17_performance import LIGHT_AUTOSTART
    return (FAIL, M("light_still_there")) if LIGHT_AUTOSTART.exists() else (PARTIAL, M("light_removed_manual"))


def _xorg(_p):
    from ..modules.m17_performance import CURSOR_XORG_CONF
    if not CURSOR_XORG_CONF.exists():
        return FAIL, M("file_missing", path=str(CURSOR_XORG_CONF))
    log = _read("/var/log/Xorg.0.log")
    if "modesetting" not in log:
        return FAIL, M("xorg_not_modesetting")
    return PARTIAL, M("xorg_ok_manual")


def _xorg_removed(_p):
    from ..modules.m17_performance import CURSOR_XORG_CONF
    return (FAIL, M("file_still_there", path=str(CURSOR_XORG_CONF))) if CURSOR_XORG_CONF.exists() \
        else (PARTIAL, M("xorg_removed_manual"))


def _cursor_refresh(_p):
    from ..modules.m17_performance import CURSOR_AUTOSTART
    return (PARTIAL, M("cursor_service_manual")) if CURSOR_AUTOSTART.exists() \
        else (FAIL, M("file_missing", path=str(CURSOR_AUTOSTART)))


def _cursor_visible(_p):
    from ..modules.m17_performance import CURSOR_EXT_AUTOSTART, CURSOR_EXT_DIR, CURSOR_EXT_UUID
    for path in (CURSOR_EXT_DIR / "extension.js", CURSOR_EXT_AUTOSTART):
        if not path.exists():
            return FAIL, M("file_missing", path=str(path))
    # Denetimi yapan hesabın oturumunda eklenti etkin mi (yardımcı çalışmış mı)?
    r = _run(["gsettings", "get", "org.cinnamon", "enabled-extensions"])
    if r.ok and CURSOR_EXT_UUID not in r.stdout:
        return PARTIAL, M("cursor_ext_not_enabled_here")
    return PARTIAL, M("cursor_ext_ok_manual")


def _numlock(_p):
    from ..modules.m17_performance import NUMLOCK_FILES, NUMLOCK_SCRIPT
    for path in NUMLOCK_FILES:
        if not path.exists():
            return FAIL, M("file_missing", path=str(path))
    r = _run(["/usr/sbin/lightdm", "--show-config"])
    if r.ok and str(NUMLOCK_SCRIPT) not in r.stdout:
        return FAIL, M("numlock_overridden")
    return PARTIAL, M("numlock_ok_manual")


def _session_cleanup(_p):
    # KillUserProcesses systemd-logind biriminin değil logind'in D-Bus
    # özelliği; "systemctl show" onu hiç döndürmez (hep boş gelir).
    from ..modules.m17_performance import _runtime_kill_user_processes
    state = _runtime_kill_user_processes()
    if state is None:
        return MANUAL, M("kill_user_processes_unreadable")
    if not state:
        return FAIL, M("kill_user_processes_off")
    return PARTIAL, M("kill_user_processes_on")


def _failed_units(_p):
    r = _run(["systemctl", "--failed", "--no-legend", "--plain"])
    units = [ln.split()[0] for ln in r.stdout.splitlines() if ln.strip()]
    return (FAIL, M("failed_units", units=_join(units[:6]))) if units else (PARTIAL, M("no_failed_units"))


CHECKS = {
    "m01.report.test_branches": _branches,
    "m01.report.test_branches_deleted": _branches_deleted,
    "m01.report.test_etapadmin": _etapadmin_pw,
    "m01.report.test_ogretmen": _ogretmen_pw,
    "m01.report.test_reserve": _reserve,
    "m01.report.test_reserve_purged": _reserve_purged,
    "m01.report.test_root": _root_pw,
    "m01.report.test_student": _student_gone,
    "m02.report.test_etapadmin": _etapadmin_pw,
    "m02.report.test_journal": _boot_wipe_journal,
    "m02.report.test_ogretmen": _boot_wipe_ran,
    "m02.report.test_pin": _pin_accounts,
    "m03.report.test_auto_group": _auto_group,
    "m03.report.test_etapadmin": _etapadmin_pin,
    "m03.report.test_extra_removed": _extra_removed,
    "m03.report.test_greeter": _greeter,
    "m03.report.test_group": _group_pin,
    "m03.report.test_ogretmen": _secret_for("ogretmen"),
    "m03.report.test_other_teachers": _secret_for(""),
    "m03.report.test_qr": _pin_accounts,
    "m03.report.test_reserve": _secret_for(""),
    "m03.report.test_time": _time,
    "m04.report.test_active": _ssh_active,
    "m04.report.test_fingerprint": _host_key,
    "m04.report.test_only_admins": _ssh_only_admins,
    "m04.report.test_ssh": _ssh_listen,
    "m05.report.test_active": _smbd,
    "m05.report.test_names": _samba_names,
    "m05.report.test_windows": _samba_share,
    "m06.report.test_exporter": _exporter,
    "m06.report.test_logger": _rsyslog,
    "m06.report.test_queue": _rsyslog_queue,
    "m06.report.test_smart": _smart,
    "m07.report.test_off_hours": _time,
    "m07.report.test_timedatectl": _timedatectl,
    "m08.report.test_hostnamectl": _hostname,
    "m08.report.test_reboot": _hostname_stable,
    "m08.report.test_sudo": _hosts,
    "m09.report.test_apt": _apt,
    "m09.report.test_hardware": _hardware,
    "m10.report.test_apt": _image_info,
    "m10.report.test_identity": _identity,
    "m10.report.test_network": _wired_up,
    "m10.report.test_ssh": _ssh_keys,
    "m11.report.test_auto": _shutdown_auto,
    "m11.report.test_exempt": _shutdown_exempt,
    "m11.report.test_idle": _shutdown_idle,
    "m11.report.test_no_params": _shutdown_conf_exists,
    "m11.report.test_off": _shutdown_off,
    "m11.report.test_service": _shutdown_service,
    "m12.report.test_journal": _reclaim_journal,
    "m12.report.test_network": _eba_api,
    "m13.report.test_first_login": _qr_dialog,
    "m14.report.test_journal": _bios_journal,
    "m14.report.test_persist": _bios_persist,
    "m14.report.test_set": _bios_persist,
    "m15.report.test_ethtool": _wol,
    "m15.report.test_skipped_active": _wol_service,
    "m15.report.test_wake": _wake,
    "m16.report.test_advanced": _grub_recovery,
    "m16.report.test_after_recovery": _grub_after_recovery,
    "m16.report.test_console": _grub_locked,
    "m16.report.test_default_boot": _grub_default,
    "m16.report.test_edit": _grub_locked,
    "m16.report.test_recovery": _grub_recovery,
    "m16.report.test_removed": _grub_removed,
    "m17.report.test_cursor_refresh": _cursor_refresh,
    "m17.report.test_cursor_visible": _cursor_visible,
    "m17.report.test_numlock": _numlock,
    "m17.report.test_light_login": _light_login,
    "m17.report.test_light_removed": _light_removed,
    "m17.report.test_session": _session_cleanup,
    "m17.report.test_xorg": _xorg,
    "m17.report.test_xorg_removed": _xorg_removed,
    "core.report.general.first_boot": _failed_units,
    "core.report.general.hardware": _hardware,
}


def check_item(tid: str, text: str) -> ItemResult:
    key, params = identify(text)
    fn = CHECKS.get(key)
    if fn is None:
        return ItemResult(tid, text, key, MANUAL, M("manual"))
    try:
        status, detail = fn(params)
    except Exception as exc:  # bir denetim hatası listeyi düşürmesin
        status, detail = MANUAL, M("check_error", error=exc)
    return ItemResult(tid, text, key, status, detail)


def verify(report, progress=None) -> list[ItemResult]:
    """Raporun "Kontrol et" listesindeki bütün maddeleri denetler.

    Aynı denetim (ör. apt-get update) birden çok maddede geçerse bir kez
    çalışır; sonuç önbellekten gelir.

    ``progress`` bir çağrılabilirse her madde için iki kez çağrılır:
    denetim başlarken ("Denetim: <başlık> — <metin>") ve bittiğinde
    ("  <işaret> <durum> — <ayrıntı>"). Böylece bir stream penceresine
    canlı akış gönderilebilir.
    """
    cache: dict[tuple, tuple[str, str]] = {}
    _RUN_CACHE.clear()
    results = []
    marks = {OK: "✓", FAIL: "✗", PARTIAL: "◐", MANUAL: "☐"}
    labels = {OK: "tamam", FAIL: "hatalı", PARTIAL: "kısmen", MANUAL: "elle"}
    for title, items in report.test_groups():
        if progress:
            progress(f"\n── {title} ──")
        for tid, text in items:
            key, params = identify(text)
            fn = CHECKS.get(key)
            ck = (fn, tuple(sorted(params.items())))
            if progress:
                progress(f"[{tid}] {text}")
            if fn is not None and ck in cache:
                status, detail = cache[ck]
                if progress:
                    progress(
                        f"  {marks[status]} {labels[status]} "
                        f"(önbellek) — {detail}",
                    )
                results.append(ItemResult(tid, text, key, status, detail))
                continue
            _TRACE.clear()
            res = check_item(tid, text)
            if fn is not None:
                cache[ck] = (res.status, res.detail)
            if progress:
                for ln in _TRACE:
                    progress(f"    {ln}")
                progress(
                    f"  {marks.get(res.status, '?')} "
                    f"{labels.get(res.status, res.status)} — {res.detail}",
                )
            results.append(res)
    return results
