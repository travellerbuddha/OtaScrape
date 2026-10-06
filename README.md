# OtaScrape

Seçtiğiniz otellerin fiyatlarını OTA ve metasearch kanallarında (Booking, Expedia, Trip.com, Trivago, Check24, TripAdvisor) **seçtiğiniz konaklama şekline göre** (ör. 7 gece / 2 yetişkin) düzenli tarar, SQLite'a kaydeder; toplam, **gecelik** ve **kişi başı gecelik** fiyatı hesaplar, **kanallar/acentalar arası farkı** ve **parite ihlallerini** Excel + HTML raporu olarak üretir.

## Durum

| Parça | Durum |
|---|---|
| Yapılandırma, DB, hesaplama, karşılaştırma, Excel/HTML rapor, CLI | Hazır, testli |
| Ortak tarayıcı katmanı (engel tespiti, retry, HTML dökümü, `probe`) | Hazır; gerçek ağda Booking, Trivago, Trip.com ile denendi |
| **Booking** | Yazıldı; ayrıştırma sentetik HTML ile test edildi. **Gerçek fiyat tablosu görülemedi** (aşağıya bakın) |
| **Trivago** | URL oluşturucu kaynaklı biçime göre yazıldı; JSON ayrıştırma sezgisel. **Deneysel**: geliştirme ortamından HTTP 403 verdi, gerçek yanıt hiç görülmedi |
| **Trip.com** | URL oluşturucu kaynaklı biçime göre yazıldı. Sayfa açılıyor (200) ama oda fiyatları ilk yüklemede yok; fiyat verisinin nereden geldiği doğrulanmadı. **Deneysel** |
| **Check24, TripAdvisor, Expedia** | Yalnızca URL şablonu + sezgisel JSON ayrıştırma. Hiç denenmedi. **Deneysel** |

"Deneysel" kanallar çalıştırmada uyarı loglar. Sonuçlarına güvenmeden önce `otascrape probe` ile bir yakalama alıp kontrol edin.

### Geliştirme ortamında gözlenenler (kendi ağınızda farklı olabilir)
- **Booking**, WAF doğrulamasını headless Chromium'da kendiliğinden geçiyor ama parametreli URL'yi (`?checkin=…`) 301 ile parametresiz adrese yönlendiriyor; sayfa "Select dates to see availability" diyor ve fiyat tablosu gelmiyor. Adaptör bu durumu artık "tarih parametrelerini uygulamadı" hatası olarak bildirir (önceden yanlışlıkla "engellendi" diyordu). Kendi ağınızda/proxy'nizle aynı şey oluyorsa çözüm, tarihleri sayfadaki arama formundan seçtirmektir (henüz yazılmadı).
- **Trivago** bu ortamdan 403 "Access Denied" döndürdü; residential proxy gerekebilir.

## Pratik akış (yeni bir kanal/otel ekleme)
1. `config.yaml`'a otel + kanal URL'sini ekleyin.
2. `otascrape probe --hotel <id> --channel <kanal>`: sayfayı bir kez açar, `data/debug/probe_*.html/.json` dosyalarını kaydeder, kaç teklif ayrıştırabildiğini yazar.
3. Teklif sayısı 0 veya hata ise `otascrape inspect data/debug/probe_<...>.json` çalıştırın: hangi istekte, hangi JSON yolunda fiyat/acenta alanı olduğunu kısa bir özet olarak yazar (büyük dosyaları paylaşmadan sorun giderilebilir). Fiyat alanı belirsizse `price_basis` ekleyin.
4. Sonra `otascrape run`.

## Kurulum

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[browser,dev]'
playwright install chromium
cp config.example.yaml config.yaml   # otellerinizi, konaklama şekillerini, direkt satıcı adlarını düzenleyin
```

## Kullanım

```bash
otascrape run --mock      # siteye gitmeden sahte veriyle uçtan uca deneme
otascrape run             # gerçek tarama + rapor
otascrape report          # son taramadan yeniden rapor
otascrape probe --hotel mardan-palace --channel trivago --url "<otel sayfası URL'si>" --headed   # tek aramayı yakala ve ayrıştırmayı dene
otascrape inspect data/debug/probe_trivago_<...>.json   # yakalamanın kısa özeti
pytest                    # testler
```

Çıktılar `data/reports/rapor_<tarih>.xlsx` ve `.html` dosyalarıdır (sekmeler: Karşılaştırma, Parite İhlalleri, Fiyat Değişimi, Ham Teklifler, Hatalar). Çıkış kodu: `0` hatasız, `1` en az bir arama hata verdi, `2` yapılandırma sorunu.

Zamanlama (günlük 06:00):
```cron
0 6 * * * cd /opt/otascrape && .venv/bin/otascrape run >> data/cron.log 2>&1
```

## Nasıl hesaplıyor?

- **Toplam** = kanalda gösterilen konaklama toplamı → `base_currency`'ye `fx` kurlarıyla çevrilir. Kuru olmayan para birimi atlanır ve loglanır.
- **Gecelik** = toplam / gece. **Kişi başı gecelik** = toplam / gece / kişi (`pax_basis: adults` yalnız yetişkin, `all_guests` yetişkin+çocuk).
- **Karşılaştırma grubu** = otel + giriş tarihi + konaklama + **pansiyon tipi** (RO/BB/HB/FB/AI/UAI). Farklı pansiyonlar birbiriyle kıyaslanmaz. Grup içinde her satıcının en ucuz teklifi alınır.
- **Referans** = `direct_sellers` ile eşleşen satıcı (yoksa en ucuz teklif). Fark referansa göre tutar ve % olarak verilir.
- **Parite ihlali** = direkt fiyat varken başka bir satıcı onun `parite_toleransı`'ndan fazla altında.
- **Fiyat değişimi** = aynı otel/satıcı/tarih/konaklama/pansiyon için bir önceki taramaya göre fark.
- `only_free_cancellation: true` ile yalnız ücretsiz iptalli teklifler kıyaslanır.

## Yeni kanal eklemek

Playwright kullanan kanallar için `adapters/browser.py` içindeki `BrowserAdapter`'ı (veya JSON tabanlı kanallar için `scan_channels.ScanAdapter`'ı) türetin; `build_url` ve `parse` yazmanız yeterli. Genel olarak:

1. `src/otascrape/adapters/<kanal>.py` içinde `Adapter`'ı türetin; `fetch(search)` aynı formatta `Offer` listesi döndürsün. Metasearch'te her acenta ayrı `Offer`, `seller` alanı acentanın adı olmalı. Müsaitlik yoksa `[]`, bot engeli için `BlockedError`, beklenmeyen yapı için `ScrapeError` fırlatın.
2. `adapters/__init__.py` içindeki `_FACTORIES` sözlüğüne ekleyin.
3. HTML ayrıştırmayı Booking'deki gibi `fetch`'ten ayrı bir fonksiyonda tutup fixture ile test edin.

Veri çekme katmanı tamamen adaptörün içindedir; bir kanal için Apify actor veya scraping API kullanmak isterseniz yalnızca o adaptörü yazmanız yeterli, geri kalan her şey aynı kalır.

## Dikkat

- Booking ve diğer kanalların kullanım şartları otomatik veri çekmeyi kısıtlayabilir; kullanımdan önce hukuki/sözleşmesel durumu kontrol edin. İstekler arası rastgele bekleme ve engel görülünce kanalı bırakma varsayılandır, yine de düşük hacimde tutun.
- Bot korumaları (AWS WAF vb.) nedeniyle veri merkezi IP'leri engellenir; Booking için residential proxy kullanın (`scraper.proxy_*`).
- Seçiciler site değişince bozulur. Hata anında ham HTML `data/debug/` altına kaydedilir.
