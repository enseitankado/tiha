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

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .paths import STATE_DIR


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

    def undo(self, data: dict, params: dict | None = None) -> ApplyResult:
        """Daha önce uygulanan işlemi geri alır.

        ``params``, geri alma sırasında kullanıcıdan ek bir tercih alındıysa
        (ör. "bu kullanıcıları sil mi?") UI tarafından geçirilir. Modüller
        gerekmiyorsa bu argümanı görmezden gelir.
        """
        raise NotImplementedError
