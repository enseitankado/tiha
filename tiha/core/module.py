"""Tüm sihirbaz modüllerinin taban sınıfı.

Her modül tek bir işlemi (parola sıfırlama, SSH kurma, sanitize etme vb.)
temsil eder ve şu arayüzü uygular:

- :attr:`id`          — Kalıcı ve benzersiz kısa ad (ör. ``"m01_passwords"``)
- :attr:`title`       — Kullanıcıya görünen başlık (Türkçe, kısa)
- :attr:`rationale`   — "Neden gerekli" açıklaması (Türkçe, 2-4 cümle)
- :meth:`preview`     — Uygulamadan önce ekran özeti üretir
- :meth:`apply`       — İşlemi uygular ve :class:`ApplyResult` döndürür;
                        uzun süren modüller ``progress`` geri-çağrısıyla
                        canlı ilerleme satırı yayınlayabilir
- :meth:`undo`        — İşlemi geri alır (destekleniyorsa)

Modüller, kendi "önce yedek al" davranışını kurmak için
:mod:`tiha.core.paths` altındaki ``STATE_DIR`` dizinini kullanır.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .logger import get_logger
from .paths import STATE_DIR

log = get_logger(__name__)


# Uzun işlemlerde satır-bazlı ilerleme iletmek için kullanılan tip.
ProgressCallback = Callable[[str], None]


@dataclass
class ApplyResult:
    """Modül uygulanma sonucu. UI bu nesneyi doğrudan ekrana basar."""

    success: bool
    summary: str                     # Tek satır genel sonuç
    details: str = ""                # Çok satırlı ayrıntı (opsiyonel)
    copyable: str | None = None      # UI'da "Kopyala" butonu ile sunulacak metin
    data: dict = field(default_factory=dict)  # undo için modülün sakladığı durum


class Module:
    """Tüm modüllerin türediği taban sınıf."""

    # Alt sınıflarca doldurulur
    id: str = ""
    title: str = ""
    # Sol kenar çubuğunda görünen kısa ad. Boşsa ``title`` kullanılır.
    sidebar_title: str = ""
    rationale: str = ""
    undo_supported: bool = True
    # ``True`` ise UI uygula sırasında canlı çıktı alanı açar.
    streams_output: bool = False
    # ``True`` ise UI sayfaya gelindiğinde Apply'ı otomatik tetikler ve
    # Apply düğmesini gizler (salt-okuma modüller için uygundur).
    auto_apply: bool = False
    # Apply düğmesinin yanında gösterilecek tek cümlelik ipucu metni
    # ("Uygulandığında: ..." şeklinde okunur). Boşsa ipucu gizlenir.
    apply_hint: str = ""
    # "Dosyaya kaydet" butonuna tıklandığında önerilen varsayılan dosya adı.
    # Boş bırakılırsa UI ``tiha-<id>.txt`` gibi teknik bir ad üretir.
    save_filename: str = ""
    # ``True`` ise apply başarıyla tamamlandığında UI bir bilgi popup'ı
    # gösterir (sayfa içi sonuç kutusuna ek olarak). Uzun süren ya da
    # kullanıcının "tamamlandı" sinyalini özellikle görmesi gereken
    # adımlar için.
    popup_on_success: bool = False

    # --- Yardımcılar ------------------------------------------------------

    @property
    def state_dir(self) -> Path:
        """Modüle özel yedek/durum dizini."""
        return STATE_DIR / self.id

    def ensure_state_dir(self) -> Path:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        return self.state_dir

    # --- Arayüz -----------------------------------------------------------

    def preview(self) -> str:
        """Uygulamadan önce durumu özetleyen kısa metin."""
        return ""

    def prefetch_preview_state(
        self, on_ready: Callable[[object], object] | None = None
    ) -> None:
        """Sayfa açıldığında main_window tarafından çağrılır. Yavaş
        senkron işleri (apt sorgusu, dpkg-query, ağ indirme vb.) arka
        planda başlatmak için override edilir.

        Worker tamamlandığında ``on_ready(value)`` UI thread'inde
        çağrılır; main_window bunu yakalayıp sayfanın ``_refresh_preview``
        + gate güncellemesini tetikler. Varsayılan implementasyon
        hiçbir şey yapmaz (modülün senkron işi hızlıdır)."""
        return None

    def apply_with_logging(
        self,
        params: dict | None = None,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        """İşlemi detaylı loglama ile uygular."""
        log.info("=== MODÜL APPLY BAŞLADI ===")
        log.info("Modül: %s (%s)", self.id, self.title)
        if params:
            # Parolaları loglamaktan kaçın
            safe_params = {}
            for key, value in params.items():
                if "password" in key.lower() or "parola" in key.lower():
                    safe_params[key] = f"[{len(value)} karakter]" if value else "[boş]"
                else:
                    safe_params[key] = value
            log.info("Parametreler: %s", json.dumps(safe_params, ensure_ascii=False, indent=2))
        else:
            log.info("Parametreler: yok")

        start_time = time.time()
        try:
            result = self.apply(params, progress)
            duration = time.time() - start_time

            log.info("=== MODÜL APPLY SONUCU ===")
            log.info("Modül: %s", self.id)
            log.info("Süre: %.2f saniye", duration)
            log.info("Başarı: %s", result.success)
            log.info("Özet: %s", result.summary)
            if result.details:
                log.info("Detaylar: %s", result.details)
            if result.data:
                log.info("Veri: %s", json.dumps(result.data, ensure_ascii=False))
            if not result.success:
                log.error("MODÜL APPLY BAŞARISIZ: %s - %s", self.id, result.summary)

            return result

        except Exception as exc:
            duration = time.time() - start_time
            log.error("=== MODÜL APPLY İSTİSNASI ===")
            log.error("Modül: %s", self.id)
            log.error("Süre: %.2f saniye", duration)
            log.error("İstisna: %s", exc, exc_info=True)
            return ApplyResult(False, f"Beklenmeyen hata: {exc}")

    def apply(
        self,
        params: dict | None = None,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        """İşlemi uygular. Alt sınıfta uygulanmalıdır.

        ``progress`` verilmişse uzun süren alt-işlemlerin çıktılarını
        satır satır bu fonksiyona geçmek gerekir.
        """
        raise NotImplementedError

    def pre_undo_prompt(self, data: dict) -> dict | None:
        """Geri alma öncesi UI'a sorulacak bir onay olup olmadığını söyler.

        ``None`` döndürülürse UI doğrudan :meth:`undo` çağırır. Bir sözlük
        döndürülürse UI bir evet/hayır diyaloğu gösterir ve sonucuna göre
        ``undo(params=...)``'ı çağırır. Beklenen anahtarlar:

        ``title``, ``message``          — Diyalog metni.
        ``yes_params``, ``no_params``   — Kullanıcının verdiği yanıtın
                                          modüle hangi ``params`` olarak
                                          geçeceği.
        """
        return None

    def undo_with_logging(self, data: dict, params: dict | None = None) -> ApplyResult:
        """İşlemi detaylı loglama ile geri alır."""
        log.info("=== MODÜL UNDO BAŞLADI ===")
        log.info("Modül: %s (%s)", self.id, self.title)
        if data:
            log.info("Veri: %s", json.dumps(data, ensure_ascii=False, indent=2))
        if params:
            log.info("Parametreler: %s", json.dumps(params, ensure_ascii=False, indent=2))

        start_time = time.time()
        try:
            result = self.undo(data, params)
            duration = time.time() - start_time

            log.info("=== MODÜL UNDO SONUCU ===")
            log.info("Modül: %s", self.id)
            log.info("Süre: %.2f saniye", duration)
            log.info("Başarı: %s", result.success)
            log.info("Özet: %s", result.summary)
            if result.details:
                log.info("Detaylar: %s", result.details)
            if not result.success:
                log.error("MODÜL UNDO BAŞARISIZ: %s - %s", self.id, result.summary)

            return result

        except Exception as exc:
            duration = time.time() - start_time
            log.error("=== MODÜL UNDO İSTİSNASI ===")
            log.error("Modül: %s", self.id)
            log.error("Süre: %.2f saniye", duration)
            log.error("İstisna: %s", exc, exc_info=True)
            return ApplyResult(False, f"Beklenmeyen hata: {exc}")

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        """Daha önce uygulanan işlemi geri alır.

        ``params``, geri alma sırasında kullanıcıdan ek bir tercih alındıysa
        (ör. "bu kullanıcıları sil mi?") UI tarafından geçirilir. Modüller
        gerekmiyorsa bu argümanı görmezden gelir.
        """
        raise NotImplementedError
