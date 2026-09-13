#!/usr/bin/env python3
"""Resmi Gazete'yi dogrudan indir, metni cikar, taranmis sayfalari Turkce OCR'la.
Kullanim: python3 rg_fetch.py [YYYYAAGG]   (bos = bugun, Istanbul saati)
Cikis kodlari: 0 basarili | 20 gazete henuz yayimlanmamis | 1 hata"""
import gzip, json, os, re, subprocess, sys, tempfile, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://www.resmigazete.gov.tr/eskiler"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TRT = timezone(timedelta(hours=3))
OUT = Path(os.environ.get("RG_OUT", "/home/claude/rg"))
SPARSE_LIMIT = 200          # bu kadardan az gercek metin varsa sayfa taranmistir
OCR_MAX_PAGES = 400

def url_for(ymd, suffix=""):
    return f"{BASE}/{ymd[:4]}/{ymd[4:6]}/{ymd}{suffix}.pdf"

def download(url, tries=3):
    for k in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, verify=False, timeout=180)
        except Exception as e:
            print(f"  HATA {url}: {type(e).__name__} {e}", file=sys.stderr)
            time.sleep(3); continue
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            print(f"  OK   {url} ({len(r.content):,} bayt)"); return r.content
        if r.status_code == 404:
            print(f"  YOK  {url} (404)"); return None
        print(f"  durum {r.status_code}, tekrar…", file=sys.stderr); time.sleep(3)
    return None

def save_tmp(b):
    f = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False); f.write(b); f.close()
    return f.name

def pdf_to_text(path):
    o = subprocess.run(["pdftotext","-layout","-enc","UTF-8",path,"-"],
                       capture_output=True, timeout=1800)
    return o.stdout.decode("utf-8","replace") if o.returncode == 0 else ""

def strip_chrome(s):
    s = re.sub(r"Sayfa\s*:\s*\d+"," ",s)
    s = re.sub(r"RESM[İI]\s*GAZETE"," ",s,flags=re.I)
    s = re.sub(r"\d{1,2}\s+\w+\s+\d{4}\s*[–-]\s*Say[ıi]\s*:\s*\d+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

TR_KELIME = re.compile(
    r"\b(ve|bir|bu|için|ile|olarak|madde|sayılı|tarihli|kanun\w*|yönetmelik|"
    r"karar\w*|gazete|bakanlığı\w*|başkanlığı\w*|ilan\w*|üzere|göre|olan|edilmek)\b",
    re.I)
OCR_ESIK = 2.0   # 100 kelimede bu kadar Turkce belirtec altinda kalirsa cikti supheli

def tr_skor(s):
    """Metnin ne kadar 'Turkce metin' gorundugu. Dondurulmus sayfada
       varsayilan OCR anlamsiz harf dizisi uretir ve bu skor sifira yakin cikar."""
    kel = s.split()
    if not kel:
        return 0.0
    return 100.0 * len(TR_KELIME.findall(s)) / len(kel)

def ocr_png(png):
    """Once --psm 1 (OSD: dondurulmus/yatay sayfayi otomatik cevirir).
       Sonuc supheliyse varsayilan segmentasyonla tekrar dener, iyisini alir.
       Resmi Gazete'nin ILAN bolumundeki yatay tablolar 90/270 derece
       dondurulmus geliyor; OSD olmadan tamamen cop metin cikiyor."""
    def kos(*ek):
        r = subprocess.run(["tesseract", str(png), "-", "-l", "tur", *ek],
                           capture_output=True, timeout=600)
        return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""
    a = kos("--psm", "1")
    if tr_skor(a) >= OCR_ESIK:
        return a
    b = kos()
    return a if tr_skor(a) >= tr_skor(b) else b

def to_ranges(nums):
    out=[]
    for n in sorted(nums):
        if out and n==out[-1][1]+1: out[-1][1]=n
        else: out.append([n,n])
    return [tuple(r) for r in out]

def ocr_range(path,a,b):
    res={}
    with tempfile.TemporaryDirectory() as td:
        pre=os.path.join(td,"pg")
        r=subprocess.run(["pdftoppm","-r","300","-png","-f",str(a),"-l",str(b),path,pre],
                         capture_output=True,timeout=1800)
        if r.returncode!=0:
            print(f"    pdftoppm hata {a}-{b}: {r.stderr[:160]}",file=sys.stderr); return res
        for png in sorted(Path(td).glob("pg*.png")):
            m=re.search(r"pg-?0*(\d+)\.png$",png.name)
            if not m: continue
            res[int(m.group(1))]=ocr_png(png)
            png.unlink(missing_ok=True)
    return res

def build_text(path,label=""):
    pages=pdf_to_text(path).split("\f")
    if pages and not pages[-1].strip(): pages.pop()
    sparse=[i for i,p in enumerate(pages,1) if len(strip_chrome(p))<SPARSE_LIMIT]
    print(f"  {label}{len(pages)} sayfa, {len(sparse)} taranmis -> OCR")
    todo=sparse[:OCR_MAX_PAGES]; ocred=[]
    rngs=to_ranges(todo)
    for k,(a,b) in enumerate(rngs,1):
        for n,txt in ocr_range(path,a,b).items():
            if n-1<len(pages) and len(strip_chrome(txt))>len(strip_chrome(pages[n-1])):
                pages[n-1]=txt; ocred.append(n)
        print(f"    [{k}/{len(rngs)}] s.{a}-{b} bitti (OCR {len(ocred)})",flush=True)
    ocred.sort()
    out=[]
    for i,p in enumerate(pages,1):
        if not p.strip(): continue
        out.append(f"\n=== Sayfa {i}{' (OCR)' if i in ocred else ''} ===\n{p}")
    return "\n".join(out),ocred,len(pages)

def find_sayi(t):
    for pat in (r"Say[ıi]\s*[:=]\s*(\d{4,6})", r"(\d{5})\s*Say[ıi]l[ıi]"):
        m=re.search(pat,t[:8000])
        if m: return int(m.group(1))
    return None

def main():
    ymd = sys.argv[1] if len(sys.argv)>1 and sys.argv[1] else datetime.now(TRT).strftime("%Y%m%d")
    if not re.fullmatch(r"\d{8}",ymd): print("Gecersiz tarih",file=sys.stderr); return 1
    OUT.mkdir(parents=True,exist_ok=True)
    t0=time.time()
    print("Resmi Gazete:",ymd)
    pdf=download(url_for(ymd))
    if pdf is None: print("Gazete henuz yayimlanmamis."); return 20
    p=save_tmp(pdf)
    try: text,ocred,pages=build_text(p)
    finally: os.unlink(p)
    (OUT/f"{ymd}.txt").write_text(text,encoding="utf-8")
    parts=re.split(r"\n=== Sayfa \d+(?: \(OCR\))? ===\n",text)
    (OUT/f"{ymd}.fihrist.txt").write_text("\n".join(parts[-3:]),encoding="utf-8")
    muk=[]
    for i in range(1,11):
        d=download(url_for(ymd,f"M{i}"))
        if d is None: break
        mp=save_tmp(d)
        try: mt,mo,mpg=build_text(mp,label=f"M{i} ")
        finally: os.unlink(mp)
        (OUT/f"{ymd}M{i}.txt").write_text(mt,encoding="utf-8")
        muk.append({"no":i,"url":url_for(ymd,f"M{i}"),"pages":mpg,"ocr_pages":mo,
                    "text_file":str(OUT/f"{ymd}M{i}.txt")})
    meta={"tarih":f"{ymd[6:8]}.{ymd[4:6]}.{ymd[:4]}","ymd":ymd,"sayi":find_sayi(text),
          "pdf_url":url_for(ymd),"pdf_bytes":len(pdf),"pages":pages,"ocr_pages":ocred,
          "text_file":str(OUT/f"{ymd}.txt"),"fihrist_file":str(OUT/f"{ymd}.fihrist.txt"),
          "mukerrer":muk,"kaynak":"dogrudan","sure_sn":round(time.time()-t0,1),
          "fetched_at":datetime.now(timezone.utc).isoformat()}
    (OUT/"latest.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"\nBitti: sayi {meta['sayi']}, {pages} sayfa, {len(ocred)} OCR, "
          f"{len(muk)} mukerrer, {meta['sure_sn']} sn")
    return 0

sys.exit(main())
