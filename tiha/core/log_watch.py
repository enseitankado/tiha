"""Merkezi log sunucusuna ulaşılamazsa ntfy ile haber veren izleyici.

Merkezi log adımı kurar (isteğe bağlı, varsayılan kapalı). Tahtada bir
zamanlayıcı 15 dakikada bir log sunucusunu dener. Sunucuya ayarlanan
süreden uzun ulaşılamazsa nedenini teşhis eder (yerel rsyslog, ağ, ad
çözümü/mDNS, yönlendirme, ARP, TCP) ve sonucu ntfy konusuna tek mesaj
olarak gönderir. Tahta başına günde en fazla bir mesaj gider; düzelince
mesaj gönderilmez.

Betik bağımsızdır (Python standart kütüphanesi ve tahtadaki araçlar:
ip, ping, getent, avahi-resolve, avahi-browse, rsyslogd); klonda TiHA
olmadan çalışır. Sihirbazdaki "Şimdi teşhis et" düğmesi de aynı betiği
``--diagnose`` ile çalıştırır.

Mesaj gizli bilgi taşımaz: yalnız ağ ve servis durumu (kullanıcı adı,
komut ya da log içeriği yok). Konu adını bilen herkes okuyabileceği için
konu adı uzun ve rastgele üretilir.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from .logger import get_logger
from .paths import ETC_ROOT, RSYSLOG_CONF, VAR_ROOT

log = get_logger(__name__)

WATCH_SCRIPT = Path("/usr/local/sbin/tiha-log-watch.py")
WATCH_SERVICE = Path("/etc/systemd/system/tiha-log-watch.service")
WATCH_TIMER = Path("/etc/systemd/system/tiha-log-watch.timer")
WATCH_CONFIG = ETC_ROOT / "log-watch.json"
WATCH_STATE = VAR_ROOT / "log-watch-state.json"

DEFAULT_SERVER = "https://ntfy.sh"
DEFAULT_THRESHOLD_MIN = 60
CHECK_INTERVAL_MIN = 15
# ntfy.sh mesaj gövdesi sınırı 4096 bayt; altında kalınır.
MAX_BODY_BYTES = 3500


def new_topic() -> str:
    """Tahmin edilemeyen bir konu adı (ntfy: harf, rakam, - ve _)."""
    return "tiha-" + secrets.token_hex(12)


def valid_topic(topic: str) -> bool:
    import re

    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", topic or ""))


def current_config() -> dict:
    """Kurulu izleyicinin ayarı (yoksa boş sözlük)."""
    try:
        data = json.loads(WATCH_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def is_installed() -> bool:
    return WATCH_TIMER.exists() and WATCH_SCRIPT.exists() and WATCH_CONFIG.exists()


def _script_content() -> str:
    from .. import __version__

    return (WATCH_SCRIPT_CONTENT
            .replace("@@CONFIG@@", str(WATCH_CONFIG))
            .replace("@@STATE@@", str(WATCH_STATE))
            .replace("@@RSYSLOG@@", str(RSYSLOG_CONF))
            .replace("@@VERSION@@", __version__)
            .replace("@@MAX_BODY@@", str(MAX_BODY_BYTES)))


def write_script(path: Path = WATCH_SCRIPT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_script_content(), encoding="utf-8")
    path.chmod(0o755)
    return path


def install(topic: str, server: str, token: str, threshold_min: int) -> bool:
    """Betiği, ayarı ve zamanlayıcıyı kurar. Durum sıfırlanır."""
    from .utils import run_cmd

    config = {"topic": topic, "server": server.rstrip("/") or DEFAULT_SERVER,
              "threshold_min": int(threshold_min)}
    if token:
        config["token"] = token
    try:
        write_script()
        WATCH_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        WATCH_CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
        # Erişim jetonu taşıyabilir: yalnız root.
        WATCH_CONFIG.chmod(0o600)
        WATCH_SERVICE.write_text(SERVICE_CONTENT, encoding="utf-8")
        WATCH_TIMER.write_text(TIMER_CONTENT, encoding="utf-8")
        reset_state()
    except OSError as exc:
        log.error("Log izleyicisi kurulamadı: %s", exc)
        return False
    run_cmd(["systemctl", "daemon-reload"])
    return run_cmd(["systemctl", "enable", "--now", WATCH_TIMER.name]).ok


def remove() -> bool:
    """Zamanlayıcıyı, betiği, ayarı ve durumu kaldırır; bir şey kaldırıldıysa True."""
    from .utils import run_cmd

    existed = any(p.exists() for p in (WATCH_TIMER, WATCH_SERVICE, WATCH_SCRIPT, WATCH_CONFIG))
    if WATCH_TIMER.exists():
        run_cmd(["systemctl", "disable", "--now", WATCH_TIMER.name])
    # Denetim o an çalışıyorsa durum dosyasını silindikten sonra yeniden yazmasın.
    run_cmd(["systemctl", "stop", WATCH_SERVICE.name])
    for path in (WATCH_TIMER, WATCH_SERVICE, WATCH_SCRIPT, WATCH_CONFIG, WATCH_STATE):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("%s silinemedi: %s", path, exc)
    if existed:
        run_cmd(["systemctl", "daemon-reload"])
    return existed


def reset_state() -> None:
    """Kesinti sayacını ve son gönderim zamanını sıfırlar (imaj öncesi
    temizlik: her klon kendi sayacıyla başlar)."""
    try:
        WATCH_STATE.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Log izleyici durumu silinemedi: %s", exc)


SERVICE_CONTENT = f"""[Unit]
Description=TiHA — Merkezi log sunucusuna erişimi denetle, ulaşılamıyorsa ntfy ile haber ver
After=network-online.target rsyslog.service
Wants=network-online.target
ConditionPathExists={WATCH_CONFIG}

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 {WATCH_SCRIPT}
# Gönderimden önce rastgele en çok 10 dakika beklenir (bütün tahtalar
# aynı anda yazmasın); teşhis en çok ~1 dakika sürer.
TimeoutStartSec=900
Nice=10
"""

TIMER_CONTENT = f"""[Unit]
Description=TiHA — Merkezi log sunucusu erişim denetimi ({CHECK_INTERVAL_MIN} dakikada bir)

[Timer]
OnBootSec=10min
OnUnitActiveSec={CHECK_INTERVAL_MIN}min
RandomizedDelaySec=60

[Install]
WantedBy=timers.target
"""


WATCH_SCRIPT_CONTENT = r'''#!/usr/bin/python3
# TiHA — merkezi log sunucusuna erişim denetimi ve teşhisi.
# Merkezi log adımı kurar. Zamanlayıcıyla çalışınca: sunucuya ayarlanan
# süreden uzun ulaşılamazsa nedeni teşhis edilip ntfy konusuna gönderilir
# (günde en fazla bir mesaj; düzelince mesaj yok).
#   --diagnose   teşhisi ekrana yazar, göndermez
#   --send       teşhisi hemen gönderir (eşik ve günlük sınır yok sayılır)
import ipaddress
import json
import os
import random
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

CONFIG = Path("@@CONFIG@@")
STATE = Path("@@STATE@@")
RSYSLOG = Path("@@RSYSLOG@@")
QUEUE_DIRS = (Path("/var/lib/rsyslog"), Path("/var/spool/rsyslog"))
TIHA_VERSION = "@@VERSION@@"
MAX_BODY = @@MAX_BODY@@
DAY = 24 * 3600


def sh(cmd, timeout=8):
    """Komutu çalıştırır: (çıkış kodu, stdout). Yoksa ya da zaman aşımı: (None, "")."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return None, ""


def load_json(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state) + "\n", encoding="utf-8")
    except OSError:
        pass


def target():
    try:
        text = RSYSLOG.read_text(encoding="utf-8")
    except OSError:
        return None
    host = re.search(r'target="([^"]+)"', text)
    port = re.search(r'port="(\d+)"', text)
    proto = re.search(r'protocol="(\w+)"', text)
    profile = re.search(r"^# TIHA_PROFILE=(\w+)", text, re.M)
    if not host:
        return None
    return {"host": host.group(1), "port": int(port.group(1)) if port else 514,
            "proto": (proto.group(1) if proto else "udp").lower(),
            "profile": profile.group(1) if profile else "?"}


def hhmm(seconds):
    seconds = int(seconds)
    h, m = divmod(seconds // 60, 60)
    return f"{h} sa {m} dk" if h else f"{m} dk"


# --- Teşhis parçaları -------------------------------------------------------

def default_route():
    code, out = sh(["ip", "-j", "route", "show", "default"])
    try:
        routes = json.loads(out) if code == 0 and out.strip() else []
    except ValueError:
        routes = []
    return routes[0] if routes else None


def iface_info(dev):
    info = {"dev": dev, "kind": "kablosuz" if Path(f"/sys/class/net/{dev}/wireless").exists()
            else "kablolu", "mac": "", "cidr": ""}
    try:
        info["mac"] = Path(f"/sys/class/net/{dev}/address").read_text().strip()
    except OSError:
        pass
    code, out = sh(["ip", "-j", "-4", "addr", "show", "dev", dev])
    try:
        for item in json.loads(out) if code == 0 else []:
            for a in item.get("addr_info", []):
                info["cidr"] = f"{a['local']}/{a['prefixlen']}"
                break
    except (ValueError, KeyError):
        pass
    return info


def ping(ip, wait=2):
    code, _ = sh(["ping", "-c", "1", "-W", str(wait), ip], timeout=wait + 3)
    return code == 0


def dns_servers():
    try:
        return re.findall(r"^nameserver\s+(\S+)", Path("/etc/resolv.conf").read_text(), re.M)
    except OSError:
        return []


def nsswitch_hosts():
    try:
        m = re.search(r"^hosts:\s*(.*)$", Path("/etc/nsswitch.conf").read_text(), re.M)
        return m.group(1).strip() if m else ""
    except OSError:
        return ""


def svc_active(unit):
    code, _ = sh(["systemctl", "is-active", "--quiet", unit])
    return code == 0


def resolve_getent(host):
    code, out = sh(["getent", "ahostsv4", host], timeout=6)
    if code != 0:
        return []
    return sorted({line.split()[0] for line in out.splitlines() if line.strip()})


def resolve_avahi(host):
    code, out = sh(["avahi-resolve", "-4", "-n", host], timeout=6)
    if code is None:
        return None  # araç yok
    ips = re.findall(r"\s(\d+\.\d+\.\d+\.\d+)\s*$", out, re.M)
    return sorted(set(ips))


def mdns_neighbours():
    """Ağda görülen mDNS cihazı sayısı (çoklu yayın çalışıyor mu)."""
    code, out = sh(["avahi-browse", "-a", "-t", "-r", "-p"], timeout=8)
    if code is None:
        return None
    hosts = set()
    for line in out.splitlines():
        parts = line.split(";")
        if len(parts) > 6 and parts[0] == "=":
            hosts.add(parts[6])
    return len(hosts)


def arp_state(ip):
    code, out = sh(["ip", "neigh", "show", ip])
    m = re.search(r"lladdr\s+(\S+).*\s(\w+)\s*$", out.strip()) if code == 0 else None
    return (m.group(1), m.group(2)) if m else (None, out.strip().split()[-1] if out.strip() else "")


def tcp_try(ip, port):
    try:
        with socket.create_connection((ip, port), timeout=5):
            return "ok"
    except ConnectionRefusedError:
        return "refused"
    except socket.timeout:
        return "timeout"
    except OSError as exc:
        return f"error:{exc.strerror or exc}"


def rsyslog_state():
    active = svc_active("rsyslog")
    code, _ = sh(["rsyslogd", "-N1"], timeout=20)
    valid = code == 0 if code is not None else None
    _c, out = sh(["journalctl", "-u", "rsyslog", "--since", "-3h", "--no-pager", "-q"], timeout=10)
    suspended = bool(re.search(r"suspended", out, re.I))
    return active, valid, suspended


def queue_state():
    files = []
    for d in QUEUE_DIRS:
        try:
            files += [p for p in d.glob("tiha_remote*") if p.is_file()]
        except OSError:
            pass
    if not files:
        return None
    size = sum(p.stat().st_size for p in files)
    oldest = min(p.stat().st_mtime for p in files)
    return len(files), size, oldest


def reachable(tgt):
    """Zamanlayıcının hızlı denetimi: sunucuya ulaşılıyor mu?"""
    ips = resolve_getent(tgt["host"])
    if not ips:
        return False
    if tgt["proto"] == "tcp":
        return any(tcp_try(ip, tgt["port"]) == "ok" for ip in ips)
    # UDP'de ulaşılabilirlik kanıtlanamaz: ad çözülüyor ve sunucu yanıt veriyorsa yeterli.
    return any(ping(ip) or arp_state(ip)[0] for ip in ips)


def diagnose(tgt, down_since=None):
    """(kod, öneri düzeyi, başlık özeti, gövde satırları)."""
    host, port, proto = tgt["host"], tgt["port"], tgt["proto"]
    hostname = socket.gethostname()
    route = default_route()
    dev = route.get("dev") if route else None
    iface = iface_info(dev) if dev else {"dev": "-", "kind": "-", "mac": "", "cidr": ""}
    gw = route.get("gateway") if route else None
    gw_ok = ping(gw) if gw else False
    r_active, r_valid, r_susp = rsyslog_state()
    is_local = host.endswith(".local")
    avahi = svc_active("avahi-daemon") if is_local else None
    nss = nsswitch_hosts()
    getent_ips = resolve_getent(host)
    avahi_ips = resolve_avahi(host) if is_local else None
    ips = getent_ips or (avahi_ips or [])
    neighbours = None
    q = queue_state()

    code, reason, advice = None, "", ""
    detail = {}
    if not r_active:
        code, reason = "YEREL", "rsyslog servisi çalışmıyor."
        advice = "Tahtada: sudo systemctl restart rsyslog; olmazsa Merkezi log adımını yeniden uygulayın."
    elif r_valid is False:
        code, reason = "YEREL", "rsyslog yapılandırması geçersiz (rsyslogd -N1 hata veriyor)."
        advice = "Merkezi log adımını yeniden uygulayın."
    elif not route or not iface.get("cidr"):
        code, reason = "AĞ", "Tahtanın IP adresi ya da varsayılan ağ geçidi yok."
        advice = "Kablo/Wi-Fi bağlantısını ve DHCP'yi denetleyin."
    elif not ips:
        if is_local:
            neighbours = mdns_neighbours()
            if avahi is False:
                code, reason = "AD", f"{host} çözülemiyor: avahi-daemon çalışmıyor (.local adları mDNS ister)."
                advice = "Tahtada: sudo systemctl enable --now avahi-daemon"
            elif "mdns" not in nss:
                code, reason = "AD", f"{host} çözülemiyor: nsswitch.conf'ta mdns yok ({nss or 'hosts satırı yok'})."
                advice = "libnss-mdns kurulu ve nsswitch.conf'ta 'mdns4_minimal' olmalı."
            elif neighbours == 0:
                code, reason = "AD", (f"{host} çözülemiyor ve ağda hiç mDNS cihazı görünmüyor: "
                                      "ya ağda çoklu yayın (multicast) engelli (Wi-Fi istemci "
                                      "yalıtımı, anahtar ayarı) ya da sunucu bu ağda değil.")
                advice = "Merkezi log adımında sunucu olarak IP ya da DNS adı kullanın."
            else:
                code, reason = "AD", (f"{host} mDNS ile bulunamadı; ağda {neighbours} başka mDNS "
                                      "cihazı görünüyor. Sunucu büyük olasılıkla başka bir alt ağda "
                                      "(mDNS yönlendiriciden geçmez) ya da sunucunun avahi'si kapalı.")
                advice = "Sunucu aynı ağda değilse Merkezi log adımında IP ya da DNS adı kullanın."
        else:
            code, reason = "AD", f"{host} DNS ile çözülemiyor."
            advice = "DNS kaydını ya da Merkezi log adımındaki sunucu adını denetleyin."
    else:
        detail["ips"] = ips
        if len(ips) > 1 and is_local:
            detail["conflict"] = True
        ip = ips[0]
        same_net = False
        try:
            same_net = ipaddress.ip_address(ip) in ipaddress.ip_interface(iface["cidr"]).network
        except ValueError:
            pass
        _c, rget = sh(["ip", "route", "get", ip])
        detail["route"] = (re.sub(r"\s+uid \d+.*$", "", rget.strip().splitlines()[0])
                           if rget.strip() else "yok")
        if not same_net and "unreachable" in rget:
            code, reason = "YÖNLENDİRME", f"{ip} adresine giden rota yok."
            advice = "Ağ geçidi ve yönlendirmeyi denetleyin."
        else:
            reply = ping(ip)
            mac, nstate = arp_state(ip) if same_net else (None, "")
            detail["ping"] = reply
            if same_net:
                detail["arp"] = mac or nstate or "yok"
            if same_net and not mac and not reply:
                code, reason = "SUNUCU YOK", (f"{ip} aynı alt ağda ama yanıt vermiyor (ARP yok): "
                                              "sunucu kapalı ya da IP'si değişmiş.")
                advice = "Sunucunun açık olduğunu ve IP'sini denetleyin."
            elif proto == "tcp":
                res = tcp_try(ip, port)
                detail["tcp"] = res
                if res == "refused":
                    code, reason = "SERVİS", (f"{ip} açık ama {port}/tcp bağlantıyı reddediyor: "
                                              "log servisi çalışmıyor.")
                    advice = "Sunucuda log alıcısını (rsyslog / Ankamon) başlatın."
                elif res == "timeout" and reply:
                    code, reason = "GÜVENLİK DUVARI", (f"{ip} ping'e yanıt veriyor ama {port}/tcp "
                                                       "zaman aşımına uğruyor: güvenlik duvarı ya da "
                                                       "filtre bağlantıyı düşürüyor.")
                    advice = f"Sunucuda {port}/tcp girişine izin verin; aradaki filtreyi denetleyin."
                elif res == "timeout":
                    code, reason = "YANIT YOK", (f"{ip} ne ping'e ne {port}/tcp'ye yanıt veriyor: "
                                                 "sunucu kapalı, adres yanlış ya da aradaki bir "
                                                 "güvenlik duvarı her şeyi düşürüyor.")
                    advice = "Sunucunun açık olduğunu ve adresini denetleyin; aradaki ağı ve filtreyi kontrol edin."
                elif res == "ok":
                    code, reason = "RSYSLOG TAKILDI", (f"{ip}:{port}/tcp açık ama rsyslog iletimi "
                                                       "ilerlemiyor (eski adrese kilitlenmiş olabilir).")
                    advice = "Tahtada: sudo systemctl restart rsyslog"
                else:
                    code, reason = "AĞ", f"{ip}:{port}/tcp bağlantı hatası: {res.split(':', 1)[-1]}"
                    advice = "Ağ yolunu denetleyin."
            else:
                code, reason = "UDP", (f"{host} çözülüyor ve yanıt veriyor; UDP'de iletimin "
                                       "ulaştığı doğrulanamaz.")
                advice = "Sunucuda 514/udp alıcısını denetleyin ya da TCP'ye geçin."
        if detail.get("conflict"):
            reason += f" Dikkat: {host} birden çok IP'ye çözülüyor ({', '.join(ips)}): ad çakışması."

    lines = [f"Teşhis : {code} — {reason}", f"Öneri  : {advice}", ""]
    ctx = [
        f"Tahta  : {hostname} {iface.get('mac', '')}  TiHA {TIHA_VERSION}",
        f"Ağ     : {iface['kind']} {iface['dev']} {iface.get('cidr') or 'IP yok'}"
        + (f" gw {gw} (ping {'ok' if gw_ok else 'yok'})" if gw else " gw yok"),
        f"DNS    : {', '.join(dns_servers()) or 'yok'}",
        f"Hedef  : {host}:{port}/{proto}, profil {tgt['profile']}",
    ]
    if is_local:
        ctx.append("mDNS   : avahi " + ("çalışıyor" if avahi else "çalışmıyor")
                   + f"; nsswitch: {nss or '-'}"
                   + (f"; avahi çözümü: {', '.join(avahi_ips)}" if avahi_ips else "; avahi çözümü yok")
                   + (f"; görülen cihaz {neighbours}" if neighbours is not None else ""))
    if detail.get("ips"):
        ctx.append(f"Çözüm  : {', '.join(detail['ips'])}; rota: {detail.get('route', '-')}")
        bits = []
        if "ping" in detail:
            bits.append("ping " + ("ok" if detail["ping"] else "yok"))
        if "arp" in detail:
            bits.append(f"ARP {detail['arp']}")
        if "tcp" in detail:
            bits.append(f"TCP {detail['tcp']}")
        if bits:
            ctx.append("Bağlantı: " + ", ".join(bits))
    ctx.append("rsyslog: " + ("çalışıyor" if r_active else "çalışmıyor")
               + (", yapılandırma geçerli" if r_valid else ", yapılandırma geçersiz" if r_valid is False else "")
               + (", iletim askıda" if r_susp else ""))
    if q:
        n, size, oldest = q
        ctx.append(f"Kuyruk : {n} dosya {size / 1048576:.1f} MB, en eski "
                   f"{datetime.fromtimestamp(oldest).strftime('%d.%m %H:%M')}")
    else:
        ctx.append("Kuyruk : bekleyen kayıt yok")
    if down_since:
        ctx.append(f"Kesinti: {datetime.fromtimestamp(down_since).strftime('%d.%m %H:%M')}'dan beri "
                   f"({hhmm(time.time() - down_since)})")
    lines += ctx
    # ntfy gövde sınırı: önce bağlam satırları kısaltılır, teşhis ve öneri kalır.
    body = "\n".join(lines)
    while len(body.encode("utf-8")) > MAX_BODY and len(lines) > 3:
        lines.pop()
        body = "\n".join(lines)
    if len(body.encode("utf-8")) > MAX_BODY:
        body = body.encode("utf-8")[:MAX_BODY].decode("utf-8", "ignore")
    since = f" {hhmm(time.time() - down_since)}'dır" if down_since else ""
    title = f"{hostname}: {host} log sunucusuna{since} ulaşılamıyor ({code})"
    priority = 4 if code in ("AD", "SUNUCU YOK", "YANIT YOK", "YEREL", "AĞ") else 3
    return code, title[:200], body, priority


def send(cfg, title, body, priority):
    server = (cfg.get("server") or "https://ntfy.sh").rstrip("/")
    payload = json.dumps({"topic": cfg["topic"], "title": title, "message": body,
                          "priority": priority, "tags": ["warning", "satellite"]}).encode("utf-8")
    req = urllib.request.Request(server, data=payload, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "tiha-log-watch"})
    if cfg.get("token"):
        req.add_header("Authorization", f"Bearer {cfg['token']}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return 200 <= resp.status < 300, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        return False, str(getattr(exc, "reason", exc))


def note(msg):
    subprocess.run(["logger", "-t", "tiha-log-watch", "--", msg], check=False)


def main(argv):
    tgt = target()
    if tgt is None:
        print("Merkezi log sunucusu ayarlı değil (/etc/rsyslog.d/90-tiha-remote.conf yok).")
        return 0 if "--diagnose" not in argv else 1
    cfg = load_json(CONFIG)
    state = load_json(STATE)

    if "--diagnose" in argv or "--send" in argv:
        down = state.get("down_since")
        code, title, body, prio = diagnose(tgt, down)
        print(title)
        print()
        print(body)
        if "--send" in argv:
            if not cfg.get("topic"):
                print("\nntfy konusu ayarlı değil; gönderilmedi.")
                return 1
            ok, info = send(cfg, title, body, prio)
            print(f"\nntfy: {'gönderildi' if ok else 'gönderilemedi'} ({info})")
            return 0 if ok else 1
        return 0

    # Zamanlayıcı kipi
    if not cfg.get("topic"):
        return 0
    now = time.time()
    if reachable(tgt):
        if state.get("down_since"):
            state.pop("down_since", None)
            save_state(state)
        return 0
    down = state.get("down_since") or now
    state["down_since"] = down
    save_state(state)
    if now - down < int(cfg.get("threshold_min", 60)) * 60:
        return 0
    if now - float(state.get("last_sent", 0)) < DAY:
        return 0
    # Okuldaki bütün tahtalar aynı anda yazmasın.
    if "--no-delay" not in argv:
        time.sleep(random.uniform(0, 600))
    code, title, body, prio = diagnose(tgt, down)
    ok, info = send(cfg, title, body, prio)
    if ok:
        state["last_sent"] = time.time()
        save_state(state)
        note(f"Log sunucusuna ulaşılamıyor ({code}); ntfy ile bildirildi.")
    else:
        # Gönderilemedi (internet yok?): sonraki denetimde yeniden denenir.
        note(f"Log sunucusuna ulaşılamıyor ({code}); ntfy'ye gönderilemedi: {info}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''
