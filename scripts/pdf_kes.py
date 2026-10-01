#!/usr/bin/env python3
"""★ (ilgi alani) kalemlerin sayfalarini gazete PDF'inden kesip kucuk PDF'ler yapar (web sayfasindaki
PDF goruntuleyici icin; yapay zeka kullanmaz).

Cikti: data/YYYY/AA/kesit/YYYYAAGG-sN.pdf ve data/YYYY/AA/YYYYAAGG.kesit.json
       {"Baslik (s. N)": {"dosya": "YYYYAAGG-sN.pdf", "ilk": N, "son": M, "isaret": [[x, y, g, y], ...]}}
"isaret": basligin kesidin ilk sayfasindaki yeri (sayfaya oranla 0-1), tesseract OCR ile bulunur;
PDF'in metin katmani bozuk ya da sayfa taranmis olsa da calisir (tesseract yoksa atlanir).
PDF kaynagi: RG_RELEASE_DIR'deki YYYYAAGG.pdf (gunluk kosum), yoksa GitHub release eki, o da
yoksa resmigazete.gov.tr. Kesidi olan kalem yeniden kesilmez.

Kullanim: python3 scripts/pdf_kes.py [YYYYAAGG ...]   (bos = data/latest.json'daki gun;
          "hepsi" = son KESIT_GUN_SINIR gun)"""
import csv, io, json, os, re, shutil, subprocess, sys, tempfile
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

def _kok(w):
    return w[:5] if len(w) > 5 else w

def baslik_yeri(pdf_yolu, baslik):
    """Kesidin ilk sayfasinda basligin satirlari: [[x, y, gen, yuk], ...] (0-1). Bulunamazsa []."""
    if not (shutil.which("pdftoppm") and shutil.which("tesseract")):
        return None
    hedef = {_kok(w) for w in rt.norm(baslik).split() if len(w) > 1}
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["pdftoppm", "-cropbox", "-r", "110", "-f", "1", "-l", "1", "-png", str(pdf_yolu), f"{d}/s"],
                       check=True, capture_output=True)
        png = next(Path(d).glob("s*.png"))
        tsv = subprocess.run(["tesseract", str(png), "-", "-l", "tur", "--psm", "3", "tsv"],
                             check=True, capture_output=True, text=True).stdout
    satirlar, gen, yuk = {}, 1, 1
    for r in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if r.get("level") == "1":                       # sayfanin kendisi: goruntu boyutu
            gen, yuk = int(r["width"]) or 1, int(r["height"]) or 1
        if r.get("level") != "5" or not (r.get("text") or "").strip():
            continue
        anahtar = (int(r["block_num"]), int(r["par_num"]), int(r["line_num"]))
        satirlar.setdefault(anahtar, []).append(r)
    sira = [satirlar[k] for k in sorted(satirlar, key=lambda k: min(int(w["top"]) for w in satirlar[k]))]
    def uyum(satir):
        kel = [w for x in satir for w in rt.norm(x["text"]).split() if len(w) > 1]
        es = sum(_kok(w) in hedef for w in kel)
        return es if kel and es >= 2 and es / len(kel) >= 0.6 else 0
    en, enp, cur, curp = [], 0, [], 0
    for satir in sira:
        p = uyum(satir)
        if p:
            cur.append(satir); curp += p
            if curp > enp:
                en, enp = list(cur), curp
        else:
            cur, curp = [], 0
    if enp < min(4, max(2, len(hedef) // 2)):
        return []
    kutular = []
    for satir in en:
        x0 = min(int(w["left"]) for w in satir); y0 = min(int(w["top"]) for w in satir)
        x1 = max(int(w["left"]) + int(w["width"]) for w in satir)
        y1 = max(int(w["top"]) + int(w["height"]) for w in satir)
        kutular.append([round(x0 / gen, 4), round(y0 / yuk, 4), round((x1 - x0) / gen, 4), round((y1 - y0) / yuk, 4)])
    return kutular

def isaretle(klasor, dizin, yildiz):
    """Isareti hesaplanmamis kesitler icin baslik yeri; degisen kayit sayisi."""
    n = 0
    for a in yildiz:
        kayit = dizin.get(a)
        if not kayit or "isaret" in kayit or not (klasor / "kesit" / kayit["dosya"]).exists():
            continue
        try:
            yer = baslik_yeri(klasor / "kesit" / kayit["dosya"], yildiz[a][1])
        except Exception as e:
            print(f"  isaret {kayit['dosya']}: {e}")
            continue
        if yer is None:
            return n
        kayit["isaret"] = yer
        n += 1
        print(f"  isaret {kayit['dosya']}: {len(yer)} satir")
    return n

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
        if isaretle(klasor, dizin, yildiz):
            dizin_yol.write_text(json.dumps(dizin, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return 0
    veri = pdf_al(ymd, meta)
    if not veri:
        print(f"::warning::{ymd}: PDF alinamadi, kesit yapilmadi")
        return 0
    okuyucu = PdfReader(io.BytesIO(veri))
    yeni = 0
    for anahtar, (_, baslik, ilk, son) in eksik.items():
        son = min(son, len(okuyucu.pages))
        if ilk > son:
            continue
        (klasor / "kesit").mkdir(exist_ok=True)
        yazici = PdfWriter()
        for i in range(ilk - 1, son):
            yazici.add_page(okuyucu.pages[i])
        try:                          # ayni nesneleri birlestir (JBIG2 gorsellerde cozucu gerekir, olmazsa atla)
            yazici.compress_identical_objects()
        except Exception:
            pass
        dosya = f"{ymd}-s{ilk}.pdf"
        with open(klasor / "kesit" / dosya, "wb") as f:
            yazici.write(f)
        dizin[anahtar] = {"dosya": dosya, "ilk": ilk, "son": son}
        yeni += 1
        print(f"  {dosya}: s. {ilk}-{son} ({(klasor / 'kesit' / dosya).stat().st_size // 1024} KB) {baslik[:60]}")
    isaretle(klasor, dizin, yildiz)
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
