# Katkıda bulunmak

TiHA'ya katkılarınız için teşekkürler! Bu kısa doküman, projeye sağlıklı katkı akışının nasıl olduğunu özetler.

## Geliştirme ortamı

- **Hedef dağıtım:** Pardus ETAP GNU/Linux 23 (Debian 12 bookworm tabanlı).
- **Dil:** Python 3.11+, GTK 3.
- **Yerelde çalıştırma:**
  ```bash
  git clone https://github.com/enseitankado/tiha.git
  cd tiha
  sudo -E PYTHONPATH=. python3 -m tiha
  ```

## Pull Request akışı

1. Önce bir [issue](https://github.com/enseitankado/tiha/issues) açın — yapmak istediğinizi birkaç cümleyle anlatın.
2. Kendi branch'inizde çalışın: `feat/...`, `fix/...`, `docs/...` ön eklerini tercih edin.
3. Küçük, odaklı commitler atın. Commit mesajları **Türkçe** yazılabilir.
4. PR açmadan önce sözdizim denetimi:
   ```bash
   python3 -m py_compile $(find tiha -name '*.py')
   bash -n bootstrap.sh
   ```
5. Yeni özellik eklediyseniz README'yi ve ilgili modülün docstring'ini de güncelleyin.

## Kod konvansiyonları

- **Kullanıcıya görünen metinler (docstring, UI, hata mesajları): Türkçe.**
- Kod kimlikleri (değişken, fonksiyon, sınıf): İngilizce, PEP-8.
- Kod içi yorumlar: gerektiğinde Türkçe. "Ne yaptığı" değil, "neden bu şekilde" anlatılır.
- Modül başlıklarında `Modül N — Başlık` biçimi.
- Her sihirbaz modülü `rationale` (neden gerekli), `preview` (ne olacak), `apply`, `undo` dörtlüsünü sağlar.

## Yeni modül eklerken

Adımları README'deki "Yeni modül nasıl eklenir?" bölümüne bakın.

## Lisans

Katkılarınız projeye **GPL-3.0** altında dahil edilir.
