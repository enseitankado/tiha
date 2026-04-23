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
from dataclasses import dataclass
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


class Module:
    """Tüm modüllerin türediği taban sınıf."""

    # Alt sınıflarca doldurulur
    id: str = ""
    title: str = ""
    rationale: str = ""
    undo_supported: bool = True
    # ``True`` ise UI uygula sırasında canlı çıktı alanı açar.
    streams_output: bool = False

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

    def undo(self, data: dict) -> ApplyResult:
        """Daha önce uygulanan işlemi geri alır."""
        raise NotImplementedError
