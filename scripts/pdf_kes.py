#!/usr/bin/env python3
"""★ (ilgi alani) kalemlerin sayfalarini gazete PDF'inden kesip kucuk PDF'ler yapar (web sayfasindaki
PDF goruntuleyici icin; yapay zeka kullanmaz).

Cikti: data/YYYY/AA/kesit/YYYYAAGG-sN.pdf ve data/YYYY/AA/YYYYAAGG.kesit.json
       {"Baslik (s. N)": {"dosya": "YYYYAAGG-sN.pdf", "ilk": N, "son": M}}
PDF kaynagi: RG_RELEASE_DIR'deki YYYYAAGG.pdf (gunluk kosum), yoksa GitHub release eki, o da
yoksa resmigazete.gov.tr. Kesidi olan kalem yeniden kesilmez.

Kullanim: python3 scripts/pdf_kes.py [YYYYAAGG ...]   (bos = data/latest.json'daki gun;
          "hepsi" = son KESIT_GUN_SINIR gun)"""
import io, json, os, re, sys
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import routine_tetikle as rt     # noqa: E402
from sayfa import ozet_oku       # noqa: E402

ROOT = Path(".")
REPO = os.environ.get("GITHUB_REPOSITORY", "uguryetgin/resmi-gazete-arsiv")
EN_COK_SAYFA = 12                # bir kalem icin en cok bu kadar sayfa
GUN_SINIR = int(os.environ.get("KESIT_GUN_SINIR", "60") or 60)

def sayfa_araliklari(ozet_yol, son_sayfa):
    """Kalem -> (ilk, son) sayfa. Son sayfa: sonraki kalemin basladigi sayfa (ayni sayfada
       baslayabilir, o yuzden dahil), ilan bolumu ya da gazetenin sonu ile sinirli."""
    bolumler, ilan = ozet_oku(ozet_yol)
    m = re.search(r"\(s\. (\d+)", ilan or "")
    tavan = int(m.group(1)) - 1 if m else son_sayfa
    kalemler = [(b["bolum"], k) for b in bolumler for k in b["kalemler"] if k["sayfa"]]
    baslar = sorted({k["sayfa"] for _, k in kalemler})
    out = {}
    for bolum, k in kalemler:
        sonraki = next((s for s in baslar if s > k["sayfa"]), tavan + 1)
        son = min(sonraki, tavan, k["sayfa"] + EN_COK_SAYFA - 1)
        out[f"{k['baslik']} (s. {k['sayfa']})"] = (bolum, k["baslik"], k["sayfa"], max(son, k["sayfa"]))
    return out

def pdf_al(ymd, meta):
    yerel = Path(os.environ.get("RG_RELEASE_DIR", "") or "/yok") / f"{ymd}.pdf"
    if yerel.exists():
        return yerel.read_bytes()
    for url in (f"https://github.com/{REPO}/releases/download/rg-{ymd}/{ymd}.pdf", meta.get("pdf_url")):
        if not url:
            continue
        try:
            r = requests.get(url, timeout=300)
            if r.ok and r.content[:4] == b"%PDF":
                return r.content
            print(f"  {url}: HTTP {r.status_code}")
        except Exception as e:
            print(f"  {url}: {e}")
    return None

def gun_isle(ymd, alanlar, haric):
    from pypdf import PdfReader, PdfWriter
    klasor = ROOT / "data" / ymd[:4] / ymd[4:6]
    meta_yol = klasor / f"{ymd}.json"
    if not meta_yol.exists():
        return 0
    meta = json.loads(meta_yol.read_text(encoding="utf-8"))
    araliklar = sayfa_araliklari(klasor / f"{ymd}.ozet.txt", meta.get("pages") or 10 ** 6)
    yildiz = {a: v for a, v in araliklar.items() if rt.alan_bul(v[1], alanlar, haric)}
    if not yildiz:
        return 0
    dizin_yol = klasor / f"{ymd}.kesit.json"
    dizin = json.loads(dizin_yol.read_text(encoding="utf-8")) if dizin_yol.exists() else {}
    eksik = {a: v for a, v in yildiz.items()
             if a not in dizin or not (klasor / "kesit" / dizin[a]["dosya"]).exists()}
    if not eksik:
        return 0
    veri = pdf_al(ymd, meta)
    if not veri:
        print(f"::warning::{ymd}: PDF alinamadi, kesit yapilmadi")
        return 0
    okuyucu = PdfReader(io.BytesIO(veri))
    (klasor / "kesit").mkdir(exist_ok=True)
    yeni = 0
    for anahtar, (_, baslik, ilk, son) in eksik.items():
        son = min(son, len(okuyucu.pages))
        if ilk > son:
            continue
        yazici = PdfWriter()
        for i in range(ilk - 1, son):
            yazici.add_page(okuyucu.pages[i])
        yazici.compress_identical_objects()
        dosya = f"{ymd}-s{ilk}.pdf"
        with open(klasor / "kesit" / dosya, "wb") as f:
            yazici.write(f)
        dizin[anahtar] = {"dosya": dosya, "ilk": ilk, "son": son}
        yeni += 1
        print(f"  {dosya}: s. {ilk}-{son} ({(klasor / 'kesit' / dosya).stat().st_size // 1024} KB) {baslik[:60]}")
    dizin_yol.write_text(json.dumps(dizin, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return yeni

def main(argv):
    if argv == ["hepsi"]:
        gunler = sorted((p.stem for p in ROOT.glob("data/[0-9]*/[0-9]*/[0-9]*.json")
                         if re.fullmatch(r"\d{8}", p.stem)), reverse=True)[:GUN_SINIR]
    elif argv:
        gunler = [a for a in argv if re.fullmatch(r"\d{8}", a)]
    else:
        gunler = [json.loads((ROOT / "data" / "latest.json").read_text(encoding="utf-8"))["ymd"]]
    alanlar, haric = rt.ilgi_alanlari()
    toplam = 0
    for ymd in gunler:
        try:
            n = gun_isle(ymd, alanlar, haric)
        except Exception as e:
            print(f"::warning::{ymd}: {e}")
            continue
        if n:
            print(f"{ymd}: {n} kesit")
        toplam += n
    print(f"Toplam {toplam} yeni kesit.")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
