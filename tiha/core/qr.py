"""QR kod üretimi — HTML'e gömülebilen satır içi SVG.

Neden SVG ve neden satır içi?
=============================

PIN kâğıdı imaj hazırlanan tahtada üretilip yazdırılıyor; sahada internet
olmayabilir. Bu yüzden QR'lar ne bir CDN'den JavaScript ile, ne de ayrı
resim dosyalarıyla üretilir — doğrudan HTML'in içine SVG olarak gömülür.
Böylece kâğıt tek dosyadır: taşınabilir, yazdırılabilir, çevrimdışı açılır.

``python3-qrcode`` paketinin yalnız ``get_matrix()`` çıktısını kullanırız;
PNG üretimi ``pillow``, hazır SVG fabrikaları ise ``lxml`` ister. İkisi de
Pardus ETAP'ta varsayılan kurulumda bulunmaz. Matristen SVG'yi kendimiz
yazınca ek bağımlılık gerekmez.

Kütüphane hiç yoksa :func:`qr_svg` ``None`` döner; çağıran taraf QR'sız
devam eder (anahtar metni kâğıtta zaten yazılı).
"""

from __future__ import annotations

from .logger import get_logger

log = get_logger(__name__)

# Kütüphaneyi bir kez arar, sonucu saklarız: kâğıt yüzlerce QR üretebilir.
_qrcode_module = None
_qrcode_checked = False


def qrcode_available() -> bool:
    """``qrcode`` kütüphanesi kullanılabilir mi?"""
    return _load_qrcode() is not None


def _load_qrcode():
    global _qrcode_module, _qrcode_checked
    if _qrcode_checked:
        return _qrcode_module
    _qrcode_checked = True
    try:
        import qrcode  # noqa: PLC0415 — isteğe bağlı bağımlılık
    except ImportError as exc:
        log.warning(
            "qrcode kütüphanesi yok, QR kodları atlanacak "
            "(kurulum: apt install python3-qrcode): %s", exc
        )
        _qrcode_module = None
    else:
        _qrcode_module = qrcode
    return _qrcode_module


def qr_svg(data: str, *, size_px: int = 150, quiet_zone: int = 2) -> str | None:
    """``data``yı kodlayan, HTML'e gömülmeye hazır bir SVG dizgesi döner.

    ``size_px`` SVG'nin görüntülenme boyutu; iç koordinat sistemi modül
    sayısına göre kurulur, yani ölçek kayıpsızdır (yazıcıda da nettir).
    ``quiet_zone`` QR'ın etrafında bırakılan zorunlu boşluk (modül sayısı;
    standart en az 4, baskıda 2 modül pratikte yeterli okunurluk verir).

    Kütüphane yoksa ``None`` döner.
    """
    qrcode = _load_qrcode()
    if qrcode is None:
        return None

    try:
        qr = qrcode.QRCode(
            version=None,                                   # veriye göre seç
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=1,
            border=0,                                        # boşluğu biz veriyoruz
        )
        qr.add_data(data)
        qr.make(fit=True)
        matrix = qr.get_matrix()
    except Exception as exc:
        # Kâğıdın tamamı bir QR yüzünden kaybolmasın.
        log.warning("QR üretilemedi: %s", exc)
        return None

    if not matrix or not matrix[0]:
        return None

    modules = len(matrix)
    span = modules + quiet_zone * 2

    # Komşu koyu modülleri satır bazında birleştirip tek bir <path>
    # içinde toplarız. Modül başına ayrı <rect> yazmak 41x41 bir QR'da
    # ~500 eleman demek; yüzlerce öğretmenlik bir kâğıtta bu megabaytlara
    # çıkıyor. Birleştirilmiş path aynı görüntüyü ~3 kat küçük verir.
    segments: list[str] = []
    for y, row in enumerate(matrix):
        x = 0
        while x < modules:
            if not row[x]:
                x += 1
                continue
            run = 1
            while x + run < modules and row[x + run]:
                run += 1
            segments.append(f"M{x + quiet_zone} {y + quiet_zone}h{run}v1h-{run}z")
            x += run

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {span} {span}" width="{size_px}" height="{size_px}" '
        f'shape-rendering="crispEdges" role="img">'
        f'<rect width="{span}" height="{span}" fill="#fff"/>'
        f'<path fill="#000" d="{"".join(segments)}"/>'
        f'</svg>'
    )
