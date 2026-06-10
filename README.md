# Emas Tracker — Harga Emas ANTAM Harian

Scraper + website gratis untuk memantau harga emas batangan ANTAM dan harga buyback
dari logammulia.com.

- **Scraper otomatis**: GitHub Actions berjalan setiap hari pkl 09.05 WIB
- **Database**: file JSON di folder `data/` (1 snapshot per tanggal)
- **Website**: GitHub Pages menampilkan harga hari ini, pilihan tanggal, dan grafik tren

## Jalankan manual
Buka tab **Actions** → pilih **Scrape Harga Emas Harian** → klik **Run workflow**.

## Jalankan lokal
```
pip install -r requirements.txt
python harga_emas.py
```
