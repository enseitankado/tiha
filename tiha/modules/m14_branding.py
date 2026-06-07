"""Modül 14 — Okul logosu (aktif wallpaper dosyasına yerinde bastır).

**Yaklaşım:** Kullanıcının ŞU AN kullandığı duvar kâğıdı dosyasının
**kendisini** yerinde değiştirir; logoyu (şeffaf PNG dahil) seçilen
köşeye bastırır. Dosya yolu ve formatı değişmediği için:

* Hiçbir pencere/proses çalışmaz.
* gsettings ``picture-uri`` değeri değişmez → yeni override, recompile,
  schema senkronu derdi yok.
* Çoklu kullanıcı: dosya ``/usr/share/...`` altında olduğundan tüm
  kullanıcılar aynı resmi görür; sahada yeni oluşan hesaplar da aynı
  logoyu alır.

**Format ve kalite koruma:**

* WEBP / JPEG / PNG: ``GdkPixbuf`` ile orijinal genişlik/yükseklikte
  okunur, logo composite edilir, **aynı formata** yüksek kaliteyle
  yazılır (jpeg quality=98, webp quality=100, png lossless).
* SVG: XML'e ``<image>`` etiketi olarak logo data-URI'sı eklenir;
  vektör kalitesi tamamen korunur.

**Yedek:** İlk apply'da orijinal dosya ``<path>.tiha-original`` olarak
yedeklenir. Undo bu yedeği geri yükler; sonraki apply'larda aynı yedek
kullanılır (idempotent — logo üst üste bastırılmaz).

**Refresh:** Dosya içeriği değiştiği için Cinnamon ``picture-uri``
signal'i doğal olarak tetiklenmeyebilir; ``gsettings`` "toggle"
(boş → aynı değer) ile zorla yeniden yükletilir. Yine de görünmezse
oturum kapatıp açmak yeterlidir.

Eski sürümlerin pencere yaklaşımının artifact'ları (autostart +
viewer script) ve önceki composed/override yöntemi — apply
başında ve undo'da defansif olarak temizlenir.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from ..core.logger import get_logger
from ..core.module import ApplyResult, Module, ProgressCallback

log = get_logger(__name__)

# Kullanıcının seçtiği logo'nun kopyalandığı yer (saha tarafından
# kullanıma açık; gerek görmeyince yok edilebilir)
LOGO_DIR = Path("/usr/share/tiha-branding")

# Konumlar
POSITIONS = {
    "top-right":    ("right",  "top"),
    "top-left":     ("left",   "top"),
    "bottom-right": ("right",  "bottom"),
    "bottom-left":  ("left",   "bottom"),
}

# Yedek dosya suffix'i — orijinal wallpaper'ın yanına yazılır
BACKUP_SUFFIX = ".tiha-original"

# Defansif temizlik için eski sürüm artifact'ları (geçmiş apply'lardan
# kalmış olabilir)
LEGACY_AUTOSTART = Path("/etc/xdg/autostart/tiha-branding-logo.desktop")
LEGACY_VIEWER_SCRIPT = Path("/usr/local/sbin/tiha-branding-logo")
LEGACY_COMPOSED = Path("/usr/share/backgrounds/tiha-branded-wallpaper.png")
SCHEMAS_DIR = Path("/usr/share/glib-2.0/schemas")

# Sistem geneli gschema override — yeni oluşturulacak kullanıcılar
# (EBA QR / USB ile sahada açılanlar dahil) ilk oturumda doğrudan
# branded wallpaper'ı görsün diye yazılır. 90_ öneki Pardus'un
# 20_pardus-etap-settings'ten sonra yüklenmesini garantiler.
OVERRIDE_FILE = Path("/usr/share/glib-2.0/schemas/90_tiha-branding.gschema.override")
# Eski isim geriye-uyumluluk için defansif temizlikte hâlâ tutulur
LEGACY_OVERRIDE = OVERRIDE_FILE

# Apply'ın bıraktığı state — JSON: {"branded": "<path>", "base": "<path>"}.
# - branded: bizim üretip picture-uri'ye verdiğimiz logo'lu dosya
# - base   : apply ÖNCESİ aktif picture-uri (kullanıcının seçtiği orijinal)
# Undo bu dosyadan base'i okuyup picture-uri'yi tam oraya geri set eder
# (gschema default'a "reset" yapmıyoruz — yanlış default'a düşerdi).
BRANDED_MARKER = Path("/var/lib/tiha/state/m14_branding.json")
BRANDED_PREFIX = "tiha-branded-"


# --- Aktif kullanıcı wallpaper tespiti -------------------------------------

def _get_active_user_wallpaper() -> Path:
    """Aktif grafik oturumdaki kullanıcının ŞU ANKİ wallpaper dosyası.

    Önce Cinnamon, sonra GNOME ``picture-uri`` denenir. ``file://``
    prefix'i sıyrılır. XML wrapper (Pardus default'unda olduğu gibi)
    olursa içindeki gerçek görsel çözülür.

    ``picture-uri`` artık var olmayan bir dosyayı gösteriyorsa
    (önceki bozuk apply izleri) → key reset edilir ve okuma tekrarlanır
    (schema default'una düşer). Hiçbir şey bulunamazsa gschema
    default'una bakılır; o da yoksa boş Path döner.
    """
    try:
        from ..core.utils import _find_active_graphical_session
    except ImportError:
        return _detect_gschema_default_wallpaper()

    env = _find_active_graphical_session()
    if not env:
        return _detect_gschema_default_wallpaper()

    base_cmd = (
        ["sudo", "-u", env["USER"], "env"]
        + [f"{k}={v}" for k, v in env.items()]
    )
    schemas = ("org.cinnamon.desktop.background",
               "org.gnome.desktop.background")

    for schema in schemas:
        # En fazla 2 deneme: ilk seferde değer bozuksa reset edip tekrar oku
        for attempt in range(2):
            r = subprocess.run(
                base_cmd + ["gsettings", "get", schema, "picture-uri"],
                capture_output=True, text=True, check=False,
            )
            if r.returncode != 0:
                break
            val = r.stdout.strip().strip("'").strip('"')
            if val.startswith("file://"):
                val = val[len("file://"):]
            if not val:
                break
            path = Path(val)
            # Bizim daha önce yazdığımız branded dosyaya bakıyor olabiliriz;
            # bu durumda marker'dan **orijinal base wallpaper**'ı oku ve
            # onu döndür (gsettings reset YAPMA — kullanıcının seçimini
            # bozar).
            if path.name.startswith(BRANDED_PREFIX):
                marker = _read_marker()
                if marker:
                    base = Path(marker["base"])
                    if base.is_file():
                        return _resolve_xml_wallpaper(base)
                # Marker yoksa: branded'ın "tiha-branded-" prefix'ini soyup
                # aynı dizinde aynı isimde bir dosya var mı bak.
                stripped = path.with_name(path.name[len(BRANDED_PREFIX):])
                if stripped.is_file():
                    return _resolve_xml_wallpaper(stripped)
                # Bulunamazsa bir sonraki schema'ya geç (reset etmiyoruz)
                break
            if path.is_file():
                resolved = _resolve_xml_wallpaper(path)
                if resolved.is_file():
                    return resolved
            # Dosya yok ve branded de değil → bu picture-uri gerçekten
            # kırık (silinmiş bir dosya işaret ediyor). Bu durumda
            # reset güvenli: bir sonraki okuma schema default'unu verir.
            if attempt == 0:
                subprocess.run(
                    base_cmd + ["gsettings", "reset", schema, "picture-uri"],
                    check=False,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                continue
            break

    # Son çare: gschema override default'ları
    return _detect_gschema_default_wallpaper()


# Pardus / Debian fallback wallpaper'ı
FALLBACK_BASE_WALLPAPER = Path("/usr/share/backgrounds/pardusetap23-3_12.webp")


def _detect_gschema_default_wallpaper() -> Path:
    """Tüm gschema override dosyalarındaki son ``picture-uri`` eşleşmesi
    (override semantiği: alfabetik sırada son yüklenen ezer). Önce
    Cinnamon, sonra GNOME. Bizim 90_ override'ımız bilinçli atlanır."""
    import re as _re
    if SCHEMAS_DIR.is_dir():
        candidates = sorted(SCHEMAS_DIR.glob("*.gschema.override"))
        target_order = (
            "[org.cinnamon.desktop.background]",
            "[org.gnome.desktop.background]",
        )
        for target in target_order:
            last_match: Path | None = None
            for ov in candidates:
                if ov.name == LEGACY_OVERRIDE.name:
                    continue
                try:
                    text = ov.read_text(encoding="utf-8")
                except OSError:
                    continue
                in_target = False
                for line in text.splitlines():
                    s = line.strip()
                    if s.startswith("[") and s.endswith("]"):
                        in_target = (s == target)
                        continue
                    if not in_target:
                        continue
                    m = _re.match(
                        r"picture-uri\s*=\s*['\"]?(file://)?([^'\"]+)['\"]?",
                        s,
                    )
                    if m:
                        path = Path(m.group(2))
                        if path.is_file():
                            resolved = _resolve_xml_wallpaper(path)
                            if resolved.is_file():
                                last_match = resolved
            if last_match is not None:
                return last_match
    if FALLBACK_BASE_WALLPAPER.is_file():
        return FALLBACK_BASE_WALLPAPER
    return Path("")


def _resolve_xml_wallpaper(path: Path) -> Path:
    """gnome-background-properties XML wrapper'sı → içindeki ilk gerçek
    görsel. Hem standart (``<file>/path</file>``) hem Pardus'un
    çözünürlük-içeren varyantını (``<file><size ...>/path</size></file>``)
    destekler."""
    if path.suffix.lower() != ".xml":
        return path
    try:
        root = ET.parse(str(path)).getroot()
    except (OSError, ET.ParseError):
        return path
    for elem in root.iter():
        if elem.tag not in ("file", "from", "to", "size"):
            continue
        if elem.text:
            cand = Path(elem.text.strip())
            if cand.is_file():
                return cand
    return path


# --- Yedekleme -------------------------------------------------------------

def _backup_path_for(wallpaper: Path) -> Path:
    """Wallpaper için yedek dosya yolu — aynı dizinde, ``.tiha-original``
    suffix ile."""
    return wallpaper.with_name(wallpaper.name + BACKUP_SUFFIX)


def _ensure_backup(wallpaper: Path) -> Path:
    """Yedek yoksa orijinal wallpaper'ı kopyalar. Varsa dokunmaz —
    sonraki apply'larda hâlâ orijinal kaynak olarak hizmet eder
    (idempotent: logo üst üste bastırılmaz)."""
    backup = _backup_path_for(wallpaper)
    if not backup.exists():
        shutil.copy2(wallpaper, backup)
        try:
            # Yedek root'a ait olsun; tema paketleri tarafından
            # tetiklenen bir reset durumunda silinmesin
            backup.chmod(0o644)
        except OSError:
            pass
    return backup


# --- Logo bastırma — format başına -----------------------------------------

def _get_primary_screen_geometry() -> tuple[int, int] | None:
    """Aktif kullanıcı oturumundaki primary monitör çözünürlüğü.
    ``xrandr --query`` çıktısından ``primary`` etiketli satır
    parse edilir; yoksa ilk ``connected`` satıra düşer."""
    try:
        from ..core.utils import _find_active_graphical_session
    except ImportError:
        return None
    env = _find_active_graphical_session()
    if not env:
        return None
    r = subprocess.run(
        ["sudo", "-u", env["USER"], "env"]
        + [f"{k}={v}" for k, v in env.items()]
        + ["xrandr", "--query"],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        return None
    import re as _re
    primary = None
    first_connected = None
    for line in r.stdout.splitlines():
        m = _re.search(r"\bprimary\b\s+(\d+)x(\d+)\+", line)
        if m:
            primary = (int(m.group(1)), int(m.group(2)))
            break
        m = _re.search(r"\bconnected\b\s+(\d+)x(\d+)\+", line)
        if m and first_connected is None:
            first_connected = (int(m.group(1)), int(m.group(2)))
    return primary or first_connected


def _compute_visible_region(
    wallpaper_w: int, wallpaper_h: int,
    screen_w: int, screen_h: int,
) -> tuple[int, int, int, int]:
    """Cinnamon ``picture-options=zoom`` mantığı: scale = max(sw/ww, sh/wh).
    Bu, hem genişliği hem yüksekliği kapsayan aspect-preserving ölçektir;
    fazla kalan kısım eşit olarak iki kenardan kırpılır.

    Wallpaper'ın **ekrana çıkan** dikdörtgenini (left, top, right, bottom)
    wallpaper koordinatları cinsinden döner."""
    if not (wallpaper_w and wallpaper_h and screen_w and screen_h):
        return (0, 0, wallpaper_w, wallpaper_h)
    sx = screen_w / wallpaper_w
    sy = screen_h / wallpaper_h
    scale = max(sx, sy)
    # Scaled boyutlar ekrandan fazlaysa kırpma
    crop_w_total = max(0, wallpaper_w * scale - screen_w)
    crop_h_total = max(0, wallpaper_h * scale - screen_h)
    left = int((crop_w_total / 2) / scale) if scale else 0
    top = int((crop_h_total / 2) / scale) if scale else 0
    right = wallpaper_w - left
    bottom = wallpaper_h - top
    return (left, top, right, bottom)


def _stamp_raster(
    base: Path, logo: Path,
    *, size: int, margin: int, position: str, out: Path,
    screen_w: int | None = None, screen_h: int | None = None,
) -> None:
    """Wallpaper'a logo bastır — **Pillow LANCZOS** ile yüksek kalite.

    Pillow Pardus ETAP'ta ``python3-pil`` olarak kurulu (cinnamon paketi
    depends ediyor). LANCZOS down-sample filtresi GdkPixbuf'ın
    BILINEAR/HYPER'undan belirgin daha keskin, daha az aliasing'li sonuç
    verir — özellikle logo 982×1206 gibi büyük bir kaynaktan 96 px gibi
    küçük bir hedefe ölçeklendirilirken (12× küçültme).

    SVG kaynaklar önce GdkPixbuf ile 1920×1080 piksele raster'lanır
    (Pillow SVG açmıyor), sonra Pillow akışına girer.

    Ekran boyutu verilirse ``zoom`` modunun kırptığı bölge hesaplanır
    ve logo wallpaper'ın **ekrana görünen** alanının köşesine
    yerleştirilir.
    """
    import math
    from PIL import Image

    lanczos = getattr(Image, "Resampling", Image).LANCZOS

    # 1) Base wallpaper'ı RGBA Pillow Image olarak al.
    # KALİTE STRATEJİSİ: Wallpaper'ı Cinnamon ``picture-options=zoom``
    # ile birebir aynı geometride üretiyoruz (orantılı büyüt + ortadan
    # kırp). Çıktı ekran boyutuyla **aynı** olduğu için Cinnamon'un
    # kendi resample'i devreye girmez → 1:1 piksel gösterim →
    # maksimum keskinlik. SVG için ekstra avantaj: vektörden hedef
    # boyuta tek adım render = tam keskin.
    def _zoom_crop_to_screen(img, sw, sh):
        """Cinnamon picture-options=zoom davranışını birebir taklit:
        aspect korunarak büyüt, fazlasını ortadan kırp."""
        if not (sw and sh) or img.size == (sw, sh):
            return img if img.size == (sw, sh) else img
        W_, H_ = img.size
        # Çift yöndeki orandan max — fit hem genişlik hem yüksekliği
        zoom = max(sw / W_, sh / H_)
        new_w = max(sw, math.ceil(W_ * zoom))
        new_h = max(sh, math.ceil(H_ * zoom))
        resized = img.resize((new_w, new_h), lanczos)
        left = (new_w - sw) // 2
        top = (new_h - sh) // 2
        return resized.crop((left, top, left + sw, top + sh))

    if base.suffix.lower() == ".svg":
        # SVG'yi native boyutta yükle, zoom-and-crop hesabını yap, sonra
        # vektörden **zoom uygulanmış boyutta** doğrudan render et
        # (preserve_aspect=True → orantı korunur, en sağlam).
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        pb_native = GdkPixbuf.Pixbuf.new_from_file(str(base))
        Wn, Hn = pb_native.get_width(), pb_native.get_height()
        if screen_w and screen_h and Wn and Hn:
            zoom = max(screen_w / Wn, screen_h / Hn)
            render_w = max(screen_w, math.ceil(Wn * zoom))
            render_h = max(screen_h, math.ceil(Hn * zoom))
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(base), render_w, render_h, True,
            )
        else:
            pb = pb_native
        if not pb.get_has_alpha():
            pb = pb.add_alpha(False, 0, 0, 0)
        wp_full = Image.frombytes(
            "RGBA",
            (pb.get_width(), pb.get_height()),
            bytes(pb.get_pixels()),
            "raw", "RGBA",
            pb.get_rowstride(),
        )
        # Vektör render zaten target boyuta yakın; gerekirse final crop
        if screen_w and screen_h:
            wp = _zoom_crop_to_screen(wp_full, screen_w, screen_h)
        else:
            wp = wp_full
    else:
        wp_full = Image.open(str(base)).convert("RGBA")
        if screen_w and screen_h:
            wp = _zoom_crop_to_screen(wp_full, screen_w, screen_h)
        else:
            wp = wp_full

    W, H = wp.size

    # 2) Logoyu LANCZOS ile **doğrudan ekran-piksel boyutunda** küçült.
    # Wallpaper zaten ekran çözünürlüğünde olduğu için ek scaling yok —
    # Cinnamon logo'yu olduğu gibi ekrana basar, kullanıcının istediği
    # piksel uzunluğunda görünür.
    logo_img = Image.open(str(logo)).convert("RGBA")
    fw, fh = logo_img.size
    if fw >= fh:
        lw_target = size
        lh_target = max(1, int(round(fh * size / fw)))
    else:
        lh_target = size
        lw_target = max(1, int(round(fw * size / fh)))
    logo_resized = logo_img.resize((lw_target, lh_target), lanczos)
    lw, lh = logo_resized.size

    # 3) Konum — artık zoom kırpma yok, tüm wallpaper görünür.
    hpos, vpos = POSITIONS.get(position, ("right", "top"))
    x = (W - lw - margin) if hpos == "right" else margin
    y = (H - lh - margin) if vpos == "bottom" else margin

    # 4) Alpha-aware composite — logo'nun kendi alpha kanalını mask olarak ver
    wp.paste(logo_resized, (x, y), logo_resized)

    # 5) Out formatına göre yüksek kaliteli kayıt
    ext = out.suffix.lower()
    out.parent.mkdir(parents=True, exist_ok=True)
    if ext in (".jpg", ".jpeg"):
        # JPEG alpha desteklemez → RGB convert; subsampling=0 chroma kayıpsız
        wp.convert("RGB").save(str(out), "JPEG", quality=98, subsampling=0,
                               optimize=True)
    elif ext == ".webp":
        # quality=100 + method=6 (en yüksek sıkıştırma efortu = en iyi kalite/boyut)
        # lossless=True ise gerçekten lossless WebP
        wp.save(str(out), "WEBP", quality=100, method=6)
    elif ext == ".png":
        wp.save(str(out), "PNG", compress_level=6, optimize=True)
    else:
        # Bilinmeyen format — PNG'ye düş (lossless, güvenli)
        png_out = out.with_suffix(".png")
        wp.save(str(png_out), "PNG", compress_level=6, optimize=True)
        if png_out != out:
            shutil.move(str(png_out), str(out))


def _save_params_for(ext: str) -> tuple[str, list[str], list[str]]:
    """Uzantıya göre GdkPixbuf save format adı + kalite parametreleri.
    Bilinmeyen format → PNG (lossless, kalite parametresiz)."""
    ext = ext.lower()
    if ext in (".jpg", ".jpeg"):
        return ("jpeg", ["quality"], ["98"])
    if ext == ".webp":
        return ("webp", ["quality"], ["100"])
    if ext == ".png":
        return ("png", ["compression"], ["6"])
    if ext in (".bmp", ".ico", ".tiff", ".tif"):
        return (ext.lstrip("."), [], [])
    return ("png", [], [])


def _stamp_svg(
    base: Path, logo: Path,
    *, size: int, margin: int, position: str, out: Path,
    screen_w: int | None = None, screen_h: int | None = None,
) -> None:
    """SVG wallpaper'a ``<image>`` etiketi olarak logoyu ekler.

    Logo data-URI olarak gömülür (dosyaya bağımlılık yok). Vektör
    kalitesi tam korunur. Konum SVG koordinatlarındadır (``viewBox``
    veya ``width``/``height`` attribute'larından okunur).
    """
    # SVG namespace
    SVG_NS = "http://www.w3.org/2000/svg"
    XLINK_NS = "http://www.w3.org/1999/xlink"
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)

    tree = ET.parse(str(base))
    root = tree.getroot()

    # SVG mantıksal boyutu — önce viewBox, yoksa width/height
    W = H = None
    vb = root.attrib.get("viewBox")
    if vb:
        parts = vb.replace(",", " ").split()
        if len(parts) == 4:
            try:
                W = float(parts[2])
                H = float(parts[3])
            except ValueError:
                pass
    if W is None or H is None:
        try:
            W = float((root.attrib.get("width") or "").rstrip("px") or 0)
            H = float((root.attrib.get("height") or "").rstrip("px") or 0)
        except ValueError:
            W = H = 0
    if not W or not H:
        # Boyut bulunamadıysa SVG'yi raster'a indirgemek yerine
        # 1920x1080 varsayımı yap
        W, H = 1920, 1080

    if screen_w and screen_h:
        left, top, right, bottom = _compute_visible_region(
            int(W), int(H), int(screen_w), int(screen_h),
        )
    else:
        left, top, right, bottom = 0, 0, int(W), int(H)

    hpos, vpos = POSITIONS.get(position, ("right", "top"))
    x = (right - size - margin) if hpos == "right" else (left + margin)
    y = (bottom - size - margin) if vpos == "bottom" else (top + margin)

    # Logo'yu data URI olarak göm
    logo_bytes = logo.read_bytes()
    mime = _mime_for(logo.suffix.lower())
    data_uri = f"data:{mime};base64,{base64.b64encode(logo_bytes).decode('ascii')}"

    image_elem = ET.SubElement(
        root,
        f"{{{SVG_NS}}}image",
        attrib={
            "x": f"{x:g}",
            "y": f"{y:g}",
            "width": str(size),
            "height": str(size),
            f"{{{XLINK_NS}}}href": data_uri,
            "preserveAspectRatio": "xMidYMid meet",
        },
    )
    # ID ile işaretle ki ileride bulup kaldırabilelim
    image_elem.set("id", "tiha-branding-logo")

    # Önce mevcut tiha-branding-logo varsa kaldır (idempotent)
    # NOT: yedek-temelli idempotency zaten var; bu sadece SVG'nin
    # backup'tan değil direkt diskten okunduğu durumlar için güvenlik
    to_remove = [e for e in list(root) if e.get("id") == "tiha-branding-logo"
                 and e is not image_elem]
    for e in to_remove:
        root.remove(e)

    tree.write(str(out), encoding="utf-8", xml_declaration=True)


def _mime_for(ext: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml",
        ".webp": "image/webp",
    }.get(ext.lower(), "image/png")


def _stamp_logo_on_wallpaper(
    base: Path, logo: Path, *, out: Path,
    size: int, margin: int, position: str,
    screen_w: int | None = None, screen_h: int | None = None,
) -> None:
    """Tüm formatları raster yolla compose eder; SVG ise GdkPixbuf'la
    1920×1080'e (veya viewBox'ın native pikseline) render edilir ki
    ``size`` parametresi gerçek piksel olarak tutarlı kalsın.

    ``out``'un uzantısı (PNG/JPEG/WEBP) çıktı formatını belirler;
    ``_branded_target_path`` SVG kaynak için PNG döner.
    """
    _stamp_raster(
        base, logo,
        size=size, margin=margin, position=position, out=out,
        screen_w=screen_w, screen_h=screen_h,
    )


# --- Cinnamon refresh ------------------------------------------------------

def _branded_target_path(base: Path) -> Path:
    """Branded dosyanın hedef yolu — orijinalle aynı dizinde, farklı isim.
    SVG kaynaklar için ``.png`` uzantısıyla raster'lanır (SVG koordinat
    sistemi piksel değil → ``size`` parametresi raster'a render sonrası
    tutarlı çalışır)."""
    parent = base.parent if base.parent.is_dir() else Path("/usr/share/backgrounds")
    if base.suffix.lower() == ".svg":
        # SVG'yi raster'a render edip PNG olarak yazıyoruz
        name = f"{BRANDED_PREFIX}{base.stem}.png"
    else:
        name = f"{BRANDED_PREFIX}{base.name}"
    return parent / name


def _write_marker(branded: Path, base: Path) -> None:
    """Geriye-uyumlu marker yazıcı — branded + base (eski sürümlerden
    kalmış kayıtlarla uyumlu kalmak için)."""
    _write_marker_full(branded=branded, base=base, previous_user_uri=None)


def _write_marker_full(
    branded: Path, base: Path, previous_user_uri: str | None,
) -> None:
    """Apply state'ini JSON marker'a yaz:
      • branded: bizim ürettiğimiz logo'lu dosya
      • base: composite kaynağı (sistem default wallpaper)
      • previous_user_uri: apply ÖNCESİ aktif kullanıcının picture-uri
        değeri — undo o değere geri set eder (kullanıcının seçimi
        korunur, gschema default'a düşmez)."""
    import json as _json
    try:
        BRANDED_MARKER.parent.mkdir(parents=True, exist_ok=True)
        BRANDED_MARKER.write_text(
            _json.dumps({
                "branded": str(branded),
                "base": str(base),
                "previous_user_uri": previous_user_uri or "",
            }, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _read_marker() -> dict | None:
    """Marker'dan {branded, base} oku. Yoksa veya bozuksa None."""
    import json as _json
    try:
        if not BRANDED_MARKER.is_file():
            return None
        data = _json.loads(BRANDED_MARKER.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("branded") and data.get("base"):
            return data
    except (OSError, _json.JSONDecodeError):
        pass
    return None


def _clear_marker() -> None:
    try:
        if BRANDED_MARKER.is_file():
            BRANDED_MARKER.unlink()
    except OSError:
        pass


def _render_override(uri: str) -> str:
    """``picture-uri`` ve ``picture-uri-dark`` için sistem geneli
    gschema override içeriği. Hem Cinnamon hem GNOME schema'larını
    set eder."""
    return (
        "[org.cinnamon.desktop.background]\n"
        f"picture-uri='{uri}'\n"
        f"picture-uri-dark='{uri}'\n"
        "picture-options='zoom'\n"
        "\n"
        "[org.gnome.desktop.background]\n"
        f"picture-uri='{uri}'\n"
        f"picture-uri-dark='{uri}'\n"
        "picture-options='zoom'\n"
    )


def _write_system_default_override(uri: str) -> bool:
    """Sistem geneli gschema override yaz + schemas'ı recompile.
    Bu, **yeni oluşturulan kullanıcılar** dahil tüm hesaplar için
    default wallpaper'ı branded URI yapar. True dönerse başarılı."""
    try:
        OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
        OVERRIDE_FILE.write_text(_render_override(uri), encoding="utf-8")
        OVERRIDE_FILE.chmod(0o644)
    except OSError as exc:
        log.warning("Override yazılamadı: %s", exc)
        return False
    r = subprocess.run(
        ["glib-compile-schemas", str(SCHEMAS_DIR)],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        log.warning("glib-compile-schemas hata: %s", r.stderr or r.stdout)
        return False
    return True


def _remove_system_default_override() -> bool:
    """Override'ı sil + recompile. True başarılı."""
    removed = False
    try:
        if OVERRIDE_FILE.is_file():
            OVERRIDE_FILE.unlink()
            removed = True
    except OSError:
        pass
    if removed or SCHEMAS_DIR.is_dir():
        subprocess.run(
            ["glib-compile-schemas", str(SCHEMAS_DIR)],
            check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    return removed


def _reset_picture_uri_for_active_session() -> None:
    """Aktif kullanıcının user-dconf'unda picture-uri override'ını sil.
    Böylece kullanıcı, **gschema default**'una (bizim override'ımız) düşer
    — yeni kullanıcılarla aynı değeri görür, tutarlılık sağlanır."""
    try:
        from ..core.utils import _find_active_graphical_session
    except ImportError:
        return
    env = _find_active_graphical_session()
    if not env:
        return
    base_cmd = (
        ["sudo", "-u", env["USER"], "env"]
        + [f"{k}={v}" for k, v in env.items()]
    )
    for schema in ("org.cinnamon.desktop.background",
                   "org.gnome.desktop.background"):
        for key in ("picture-uri", "picture-uri-dark", "picture-options"):
            subprocess.run(
                base_cmd + ["gsettings", "reset", schema, key],
                check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )


def _read_active_user_picture_uri() -> str | None:
    """Aktif kullanıcının ŞU ANKİ picture-uri değerini ham string olarak
    döner (file:// prefix dahil). Bulunamazsa None. Undo'da kullanıcının
    apply öncesi seçimine geri dönmek için kullanılır."""
    try:
        from ..core.utils import _find_active_graphical_session
    except ImportError:
        return None
    env = _find_active_graphical_session()
    if not env:
        return None
    base_cmd = (
        ["sudo", "-u", env["USER"], "env"]
        + [f"{k}={v}" for k, v in env.items()]
    )
    r = subprocess.run(
        base_cmd + ["gsettings", "get",
                    "org.cinnamon.desktop.background", "picture-uri"],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        return None
    val = r.stdout.strip().strip("'").strip('"')
    return val or None


def _set_picture_uri_for_active_session(uri: str) -> None:
    """Aktif user için picture-uri (ve dark eşi) anında set edilir.
    Mevcut Cinnamon değerini değiştirir; toggle gerekmez çünkü URI değer
    olarak değişiyor → Cinnamon yeni dosyayı yükler."""
    try:
        from ..core.utils import _find_active_graphical_session
    except ImportError:
        return
    env = _find_active_graphical_session()
    if not env:
        return
    base_cmd = (
        ["sudo", "-u", env["USER"], "env"]
        + [f"{k}={v}" for k, v in env.items()]
    )
    for schema in ("org.cinnamon.desktop.background",
                   "org.gnome.desktop.background"):
        subprocess.run(
            base_cmd + ["gsettings", "set", schema, "picture-options", "zoom"],
            check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for key in ("picture-uri", "picture-uri-dark"):
            subprocess.run(
                base_cmd + ["gsettings", "set", schema, key, uri],
                check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )


def _reset_picture_uri_for_active_session() -> None:
    """picture-uri / picture-uri-dark / picture-options reset — gschema
    default'a düşer (orijinal Pardus wallpaper'ı)."""
    try:
        from ..core.utils import _find_active_graphical_session
    except ImportError:
        return
    env = _find_active_graphical_session()
    if not env:
        return
    base_cmd = (
        ["sudo", "-u", env["USER"], "env"]
        + [f"{k}={v}" for k, v in env.items()]
    )
    for schema in ("org.cinnamon.desktop.background",
                   "org.gnome.desktop.background"):
        for key in ("picture-uri", "picture-uri-dark", "picture-options"):
            subprocess.run(
                base_cmd + ["gsettings", "reset", schema, key],
                check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )


def _find_decoy_wallpaper(skip: Path) -> Path | None:
    """``/usr/share/backgrounds`` altında ``skip``'ten farklı bir mevcut
    wallpaper dosyası bul — Cinnamon'ı dosya cache'inden çıkarmak için
    geçici olarak set edeceğiz."""
    bg_dir = Path("/usr/share/backgrounds")
    if not bg_dir.is_dir():
        return None
    skip_resolved = skip.resolve()
    for p in sorted(bg_dir.iterdir()):
        if p.suffix.lower() not in (".webp", ".jpg", ".jpeg", ".png", ".svg"):
            continue
        if p.resolve() == skip_resolved:
            continue
        if p.is_file():
            return p
    return None


def _force_refresh_active_session(decoy: Path | None = None) -> None:
    """Aktif kullanıcı için wallpaper'ı yeniden yüklet.

    ``picture-uri`` değer değişmediği için Cinnamon dosya değişikliğini
    fark etmeyebilir. Stratejimiz:

    1. (Varsa) decoy mevcut başka bir wallpaper'a set et — Cinnamon
       gerçek bir başka dosya yükler, kendi dosya cache'ini değiştirir.
    2. Kısa bekleme.
    3. Hedef URI'ye geri set et — Cinnamon hedef dosyayı **diskten
       yeniden okur** (cache'inde olmadığı için).

    Decoy yoksa boş→değer toggle ile yetiniriz (signal garantisi).

    Nemo-desktop'a kesinlikle DOKUNULMAZ (signal gönderirsek süreç
    kapanır → masaüstü ikonları kaybolur).
    """
    try:
        from ..core.utils import _find_active_graphical_session
    except ImportError:
        return
    env = _find_active_graphical_session()
    if not env:
        return
    base_cmd = (
        ["sudo", "-u", env["USER"], "env"]
        + [f"{k}={v}" for k, v in env.items()]
    )
    import time as _time
    for schema in ("org.cinnamon.desktop.background",
                   "org.gnome.desktop.background"):
        r = subprocess.run(
            base_cmd + ["gsettings", "get", schema, "picture-uri"],
            capture_output=True, text=True, check=False,
        )
        if r.returncode != 0:
            continue
        current = r.stdout.strip().strip("'").strip('"')
        if not current:
            continue
        if decoy and decoy.is_file():
            decoy_uri = f"file://{decoy}"
            subprocess.run(
                base_cmd + ["gsettings", "set", schema, "picture-uri", decoy_uri],
                check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            _time.sleep(0.3)
        else:
            subprocess.run(
                base_cmd + ["gsettings", "set", schema, "picture-uri", ""],
                check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        subprocess.run(
            base_cmd + ["gsettings", "set", schema, "picture-uri", current],
            check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


# --- Defansif: eski sürüm artifact temizliği ------------------------------

def _cleanup_legacy_artifacts(progress: ProgressCallback | None = None) -> list[str]:
    """Önceki sürümlerin yapısı (autostart pencere + 90_ override +
    composed wallpaper) — varsa temizle."""
    removed: list[str] = []
    # Eski viewer süreçlerini durdur
    subprocess.run(["pkill", "-f", LEGACY_VIEWER_SCRIPT.name],
                   check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for legacy in (LEGACY_AUTOSTART, LEGACY_VIEWER_SCRIPT,
                   LEGACY_COMPOSED, LEGACY_OVERRIDE):
        try:
            if legacy.is_file():
                legacy.unlink()
                removed.append(str(legacy))
                if progress:
                    progress(f"Eski artifact silindi: {legacy}")
        except OSError:
            pass
    # Eski override silindiyse schemas'ı recompile et
    if LEGACY_OVERRIDE.parent.is_dir() and removed:
        if any(LEGACY_OVERRIDE.name in r for r in removed):
            subprocess.run(["glib-compile-schemas", str(SCHEMAS_DIR)],
                           check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return removed


# --- Yardımcı: mevcut logo ------------------------------------------------

def _find_existing_logo() -> Path | None:
    if not LOGO_DIR.is_dir():
        return None
    for p in sorted(LOGO_DIR.glob("logo.*")):
        if p.is_file():
            return p
    return None


# --- Modül -----------------------------------------------------------------

class BrandingModule(Module):
    id = "m14_branding"
    title = "Okul logosu"
    sidebar_title = "Okul Logosu"
    apply_hint = "Seçilen logo masaüstüne yerleştirilir."
    rationale = (
        "Tahtanın hangi okula ait olduğu uzaktan görüntüde ya da serviste "
        "hemen ayırt edilebilsin diye okul logosu masaüstüne eklenir. "
        "Tüm kullanıcılar aynı görseli görür. Geri al ile her zaman "
        "orijinal duvar kâğıdına dönülür."
    )
    undo_supported = True

    def preview(self) -> str:
        existing_logo = _find_existing_logo()
        lines: list[str] = []
        if existing_logo:
            lines.append(f"Mevcut logo: {existing_logo.name}")
            lines.append("")
        lines.append("Şeffaf arka planlı (PNG) logolar daha temiz görünür.")
        return "\n".join(lines)

    def apply(
        self,
        params: dict | None = None,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        params = params or {}
        src = (params.get("logo_path") or "").strip()
        size = int(params.get("size") or 96)
        margin = int(params.get("margin") or 24)
        position = (params.get("position") or "top-right").strip()

        if not src:
            return ApplyResult(False, "Logo dosyası seçilmedi.")
        src_path = Path(src).expanduser()
        if not src_path.is_file():
            return ApplyResult(False, f"Logo dosyası bulunamadı: {src_path}")
        ext = src_path.suffix.lower()
        if ext not in (".png", ".jpg", ".jpeg", ".svg", ".webp"):
            return ApplyResult(
                False,
                f"Desteklenmeyen logo uzantısı: {ext} (PNG/JPG/SVG/WEBP).",
            )
        if size < 16 or size > 1024:
            return ApplyResult(False, "Boyut 16–1024 piksel olmalı.")
        if margin < 0 or margin > 500:
            return ApplyResult(False, "Köşeden mesafe 0–500 piksel olmalı.")
        if position not in POSITIONS:
            return ApplyResult(
                False,
                f"Geçersiz konum: {position} "
                "(top-right/top-left/bottom-right/bottom-left).",
            )

        # 0) Eski sürüm artifact'ları → defansif temizlik (sessiz)
        legacy_removed = _cleanup_legacy_artifacts()

        # 1) Logo kopyasını /usr/share/tiha-branding/ altına al
        try:
            LOGO_DIR.mkdir(parents=True, exist_ok=True)
            for old in LOGO_DIR.glob("logo.*"):
                old.unlink(missing_ok=True)
            logo_dest = LOGO_DIR / f"logo{ext}"
            shutil.copy2(src_path, logo_dest)
            logo_dest.chmod(0o644)
        except OSError as exc:
            return ApplyResult(False, f"Logo kopyalanamadı: {exc}")

        # 2) Kaynak wallpaper = AKTİF KULLANICININ ŞU AN GÖRDÜĞÜ wallpaper.
        # Eğer kullanıcı kendi wallpaper'ını seçmişse onun üzerine logo
        # bastırılır → kullanıcı "wallpaper'ım değişti" hissi yaşamaz.
        # Helper akıllı: user dconf'ta zaten bizim branded'imiz varsa
        # marker'dan ORİJİNAL base'i okur (recursive composition'ı önler).
        # Sonra bu branded gschema override'a yazılır → yeni kullanıcılar
        # da aynı wallpaper + logo'yu görür (sınıfta tutarlı görsel).
        base_wallpaper = _get_active_user_wallpaper()
        if not base_wallpaper or not base_wallpaper.is_file():
            return ApplyResult(False, "Aktif duvar kâğıdı tespit edilemedi.")

        # Aktif kullanıcının ŞU ANKİ picture-uri'sini hatırla — undo'da
        # buraya geri döndürmek için. AMA: eğer şu anki değer zaten
        # bizim önceki apply'ımızın branded'i ise gerçek kullanıcı
        # seçimini eski marker'dan al (idempotent: tekrar apply'da
        # gerçek seçim kaybolmasın).
        current_user_uri = _read_active_user_picture_uri()
        existing_marker = _read_marker()
        if current_user_uri and BRANDED_PREFIX in current_user_uri:
            previous_user_uri = (existing_marker or {}).get("previous_user_uri") or None
        else:
            previous_user_uri = current_user_uri

        # 3) Ekran çözünürlüğü (zoom-and-crop ile aspect korunarak)
        screen = _get_primary_screen_geometry()
        screen_w, screen_h = screen if screen else (None, None)

        # 4) Hedef branded dosya yolu — orijinalden **farklı** isim.
        # Cinnamon cache'inde olmadığı için **diskten yeniden okur**;
        # böylece logo görünür. SVG kaynak için PNG'ye raster'lanır
        # (size piksel olarak tutarlı çalışsın diye).
        branded = _branded_target_path(base_wallpaper)
        try:
            branded.parent.mkdir(parents=True, exist_ok=True)
            _stamp_logo_on_wallpaper(
                base_wallpaper, logo_dest, out=branded,
                size=size, margin=margin, position=position,
                screen_w=screen_w, screen_h=screen_h,
            )
            branded.chmod(0o644)
        except Exception as exc:
            try:
                if branded.is_file():
                    branded.unlink()
            except OSError:
                pass
            return ApplyResult(False, f"Logo uygulanamadı: {exc}")

        # 5) Sistem geneli gschema override → YENİ KULLANICILAR default
        # olarak branded URI alır. Aktif kullanıcı için ayrıca user
        # dconf'a branded URI SET et — reset Cinnamon'u güvenilir
        # tetiklemiyor, ama farklı bir URI'ye set kesin tetikler.
        new_uri = f"file://{branded}"
        previous_marker = _read_marker()
        _write_system_default_override(new_uri)
        _set_picture_uri_for_active_session(new_uri)
        # Eski branded'i sessizce sil
        if previous_marker:
            prev_branded = Path(previous_marker["branded"])
            if prev_branded != branded and prev_branded.is_file():
                try:
                    prev_branded.unlink()
                except OSError:
                    pass
        # Marker: branded + base + apply öncesi user dconf değeri
        # (undo'da kullanıcının seçimine geri dönmek için)
        _write_marker_full(
            branded=branded, base=base_wallpaper,
            previous_user_uri=previous_user_uri,
        )

        details = (
            f"Boyut: {size} piksel · Konum: {position} · "
            f"Köşeden mesafe: {margin} piksel"
        )

        return ApplyResult(
            True,
            "Okul logosu masaüstüne yerleştirildi.",
            details=details,
            data={
                "branded_path": str(branded),
                "base_wallpaper": str(base_wallpaper),
                "logo_path": str(logo_dest),
                "previous_picture_uri": f"file://{base_wallpaper}",
            },
        )

    def undo(
        self,
        data: dict,
        params: dict | None = None,
    ) -> ApplyResult:
        data = data or {}
        removed: list[str] = []
        marker = _read_marker()

        # 1) Bu apply'ın yazdığı branded dosya
        for src in (data.get("branded_path"),
                    marker.get("branded") if marker else None):
            if not src:
                continue
            br = Path(src)
            try:
                if br.is_file():
                    br.unlink()
                    removed.append(str(br))
            except OSError:
                pass
        _clear_marker()

        # 2) Eski sürümlerden kalma .tiha-original yedek dosyaları varsa
        # orijinal dosyayı yedekten geri yükleyip yedeği temizle.
        bg_dir = Path("/usr/share/backgrounds")
        if bg_dir.is_dir():
            for backup in bg_dir.glob("*" + BACKUP_SUFFIX):
                original = backup.with_name(backup.name[: -len(BACKUP_SUFFIX)])
                try:
                    if original.exists():
                        shutil.copy2(backup, original)
                        removed.append(f"(orijinal geri yüklendi) {original}")
                    backup.unlink()
                    removed.append(str(backup))
                except OSError:
                    pass

        # 3) Logo kopyasını sil
        try:
            if LOGO_DIR.is_dir():
                for f in LOGO_DIR.glob("logo.*"):
                    f.unlink()
                    removed.append(str(f))
                try:
                    LOGO_DIR.rmdir()
                except OSError:
                    pass
        except OSError:
            pass

        # 4) Eski sürüm artifact'ları — defansif
        legacy = _cleanup_legacy_artifacts()
        removed.extend(legacy)

        # 5) Sistem geneli override'ı sil → yeni kullanıcılar artık
        # Pardus orijinal default'unu görür.
        _remove_system_default_override()
        # Aktif kullanıcı için: marker'da apply öncesi user picture-uri
        # varsa oraya geri set et (kullanıcının kişisel seçimini koru);
        # yoksa user dconf'u reset et (gschema default'a düşer).
        prev_user_uri = (marker or {}).get("previous_user_uri") or ""
        if prev_user_uri:
            _set_picture_uri_for_active_session(prev_user_uri)
        else:
            _reset_picture_uri_for_active_session()

        return ApplyResult(
            True,
            "Okul logosu kaldırıldı; orijinal duvar kâğıdına dönüldü.",
        )
