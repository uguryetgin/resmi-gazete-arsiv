# Resmî Gazete arşivi — Copilot için yönergeler

Bu depo Türkiye Resmî Gazetesi'ni her gün otomatik indirip arşivler. Sorular genellikle
Türkçedir; **Türkçe yanıt ver**.

## Veri nerede?
- `data/YYYY/AA/YYYYAAGG.ozet.txt` — günün kalemleri: bölüm başlığı, `• Başlık (s. N)` ve
  altında ilk maddeden alıntı. Bir günün içeriği sorulunca önce buraya bak.
- `data/YYYY/AA/YYYYAAGG.fihrist.txt` — gazetenin içindekiler sayfası.
- `data/YYYY/AA/YYYYAAGG.json` — sayı, PDF linki, sayfa sayısı, mükerrerler.
- `data/YYYY/AA/YYYYAAGG.txt.gz` — gazetenin tam metni (gzip; sayfalar `=== Sayfa N ===` ile ayrılır).
- `data/YYYY/AA/YYYYAAGG.karsilastirma.json` — "Değişiklik Yapılmasına Dair" kalemlerinde
  değişen hükümlerin eski/yeni hali (varsa).
- `data/YYYY/AA/YYYYAAGG.yz.json` — ilgi alanı kalemlerinin otomatik özetleri (varsa).
- `data/latest.json` — en son indirilen gün.
- `ilgi.txt` — kullanıcının ilgi alanları (Enerji Piyasaları, Sanayi ve Teknoloji, Ekonomi ve Finans)
  ve anahtar kelimeleri.

## Yanıt verirken
- Yalnız arşivdeki metne dayan; tarih, sayı, madde, tutar ve kanun numaralarını aynen aktar,
  dayandığın dosyayı ve sayfayı (s. N) belirt. Metinde olmayanı tahmin etme; yorumu "(yorum)" diye işaretle.
- Tarih belirtilmezse `data/latest.json`'daki günü kullan. "Bu hafta" gibi aralıklarda ilgili
  günlerin `ozet.txt` dosyalarını tara.
- Kesin hukuki bilgi için gazete PDF'ine (`json` dosyasındaki `pdf_url`) yönlendir.
