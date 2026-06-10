#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
harga_emas.py — Scraper harga emas ANTAM (logammulia.com) untuk deploy GitHub Actions.

Setiap dijalankan:
  1. Ambil halaman harga & buyback
  2. Simpan snapshot ke  data/harga_YYYY-MM-DD.json   (idempotent: hari sama -> ditimpa)
  3. Bangun ulang        data/index.json              (daftar tanggal tersedia)
  4. Bangun ulang        data/history.json            (deret harga 1gr & buyback untuk grafik)
"""

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from bs4 import BeautifulSoup

URL_HARGA = "https://www.logammulia.com/id/harga-emas-hari-ini"
URL_BUYBACK = "https://www.logammulia.com/id/sell/gold"

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
WIB = timezone(timedelta(hours=7))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    "Referer": "https://www.logammulia.com/",
}


# ---------------------------------------------------------------------------
# FETCH — beberapa strategi berurutan untuk lolos antibot Cloudflare
# ---------------------------------------------------------------------------
def fetch(url: str) -> str:
    errors = []

    # 1) curl_cffi: meniru TLS fingerprint Chrome — paling ampuh vs Cloudflare
    try:
        from curl_cffi import requests as curl_requests  # type: ignore
        r = curl_requests.get(url, impersonate="chrome", timeout=40)
        if r.status_code == 200:
            return r.text
        errors.append(f"curl_cffi status={r.status_code}")
    except ImportError:
        errors.append("curl_cffi tidak terinstall")
    except Exception as e:
        errors.append(f"curl_cffi: {e}")

    # 2) cloudscraper
    try:
        import cloudscraper  # type: ignore
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "desktop": True}
        )
        r = scraper.get(url, timeout=40)
        if r.status_code == 200:
            return r.text
        errors.append(f"cloudscraper status={r.status_code}")
    except ImportError:
        errors.append("cloudscraper tidak terinstall")
    except Exception as e:
        errors.append(f"cloudscraper: {e}")

    # 3) requests biasa
    try:
        import requests
        r = requests.get(url, headers=HEADERS, timeout=40)
        if r.status_code == 200:
            return r.text
        errors.append(f"requests status={r.status_code}")
    except Exception as e:
        errors.append(f"requests: {e}")

    raise RuntimeError(f"Gagal mengambil {url}. Detail: {'; '.join(errors)}")


# ---------------------------------------------------------------------------
# PARSER
# ---------------------------------------------------------------------------
def _to_int(text: str):
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def parse_harga(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    result = {"tanggal_halaman": None, "kategori": {}}

    m = re.search(r"Harga Emas Hari Ini[,]?\s*([\d]{1,2}\s+\w+\s+\d{4})",
                  soup.get_text(" "))
    if m:
        result["tanggal_halaman"] = m.group(1)

    current_category = None
    for table in soup.find_all("table"):
        if "Emas Batangan" not in table.get_text(" "):
            continue
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            cells = [c for c in cells if c]
            if not cells:
                continue
            first = cells[0]
            if first.lower().startswith("berat"):
                continue
            # Baris kategori: kolom terakhir bukan angka harga
            if len(cells) == 1 or _to_int(cells[-1]) is None:
                current_category = first
                result["kategori"].setdefault(current_category, [])
                continue
            if current_category and len(cells) >= 3:
                berat = re.sub(r"\s*gr\.?$", "", first, flags=re.I).strip()
                harga_dasar = _to_int(cells[1])
                harga_pajak = _to_int(cells[2])
                try:
                    berat_f = float(berat.replace(",", "."))
                except ValueError:
                    continue
                if harga_dasar and harga_pajak:
                    result["kategori"][current_category].append({
                        "berat_gr": berat_f,
                        "harga_dasar": harga_dasar,
                        "harga_pajak": harga_pajak,
                    })
        break
    return result


def parse_buyback(html: str) -> dict:
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    out = {"buyback_per_gram": None, "perubahan": None, "update_terakhir": None}

    m = re.search(r"Harga Buyback:?\s*Rp\.?\s*([\d.,]+)", text)
    if m:
        out["buyback_per_gram"] = _to_int(m.group(1))

    m = re.search(r"Perubahan:?\s*Rp\.?\s*(-?\s*[\d.,]+)", text)
    if m:
        raw = m.group(1).replace(" ", "")
        val = _to_int(raw)
        if val is not None and raw.startswith("-"):
            val = -val
        out["perubahan"] = val

    m = re.search(r"Perubahan Terakhir:?\s*([\d]{1,2}\s+\w+\s+\d{4}\s+[\d:]+)", text)
    if m:
        out["update_terakhir"] = m.group(1)
    return out


# ---------------------------------------------------------------------------
# DATABASE (file JSON di folder data/)
# ---------------------------------------------------------------------------
def rebuild_index_and_history():
    """Scan semua snapshot harian, bangun index.json + history.json."""
    files = sorted(DATA_DIR.glob("harga_*.json"))
    dates, history = [], []

    for f in files:
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        tanggal = f.stem.replace("harga_", "")
        dates.append(tanggal)

        batangan = snap.get("harga", {}).get("kategori", {}).get("Emas Batangan", [])
        satu = next((x for x in batangan if x.get("berat_gr") == 1.0), None)
        bb = snap.get("buyback", {}).get("buyback_per_gram")
        history.append({
            "tanggal": tanggal,
            "harga_dasar_1gr": satu["harga_dasar"] if satu else None,
            "harga_pajak_1gr": satu["harga_pajak"] if satu else None,
            "buyback_per_gram": bb,
            "spread_1gr": (satu["harga_dasar"] - bb) if (satu and bb) else None,
        })

    (DATA_DIR / "index.json").write_text(
        json.dumps({"tanggal_tersedia": dates,
                    "terakhir": dates[-1] if dates else None},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    (DATA_DIR / "history.json").write_text(
        json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    html_harga = fetch(URL_HARGA)
    html_buyback = fetch(URL_BUYBACK)

    now_wib = datetime.now(WIB)
    snapshot = {
        "diambil_pada": now_wib.isoformat(timespec="seconds"),
        "sumber": {"harga": URL_HARGA, "buyback": URL_BUYBACK},
        "harga": parse_harga(html_harga),
        "buyback": parse_buyback(html_buyback),
    }

    # Validasi: jangan timpa data lama dengan hasil kosong (mis. diblokir antibot)
    batangan = snapshot["harga"]["kategori"].get("Emas Batangan", [])
    if not batangan:
        print("PERINGATAN: tabel Emas Batangan tidak terbaca — kemungkinan "
              "halaman diblokir atau struktur berubah. Data tidak disimpan.")
        sys.exit(1)

    DATA_DIR.mkdir(exist_ok=True)
    tanggal = now_wib.strftime("%Y-%m-%d")
    out = DATA_DIR / f"harga_{tanggal}.json"
    out.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False),
                   encoding="utf-8")

    rebuild_index_and_history()

    satu = next((x for x in batangan if x["berat_gr"] == 1.0), None)
    bb = snapshot["buyback"]["buyback_per_gram"]
    print(f"OK {tanggal} | 1gr: {satu['harga_dasar']:,} | buyback: "
          f"{bb:,} " if (satu and bb) else f"OK {tanggal}")
    print(f"Tersimpan: {out.name}, index.json, history.json")


if __name__ == "__main__":
    main()
