#!/usr/bin/env python3
"""Resmi Gazete'yi indir, metni cikar, taranmis/bozuk sayfalari Turkce OCR'la,
ozet girdisi ve GitHub Release dosyalarini hazirla.

Kullanim: python3 scripts/rg_fetch.py [YYYYAAGG]   (bos = bugun, Istanbul saati)
Depo kokunden calistirilir; ciktilar data/YYYY/AA/ altina yazilir.
Ortam: YENIDEN=true (islenmis gunu bastan isle), RG_RELEASE_DIR (release dosyalari)
Cikis kodlari: 0 basarili | 20 henuz yayimlanmamis | 21 zaten islenmis | 1 hata

.github/workflows/resmi-gazete.yml bu betigi calistirir."""
import gzip, json, os, re, subprocess, sys, tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests, urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://www.resmigazete.gov.tr/eskiler"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TIMEOUT = 120
ROOT = Path(".")
TRT = timezone(timedelta(hours=3))

# Bir sayfada bu kadardan az "gercek" metin varsa taranmis kabul edilir.
# Ustbilgi/altbilgi satiri (ornegin "Sayfa : 2  RESMI GAZETE  ...") her sayfada
# cikar, o yuzden esik onu asacak kadar yuksek.
SPARSE_LIMIT = 200
OCR_MAX_PAGES = 250  # pratikte sinirsiz; public repoda Actions dakikasi ucretsiz

# Release: RG_RELEASE_DIR verilmisse yeni PDF'ler ile etiket, baslik ve aciklama
# oraya yazilir, GitHub Release'i sonraki adim olusturur. Depoyu "Watch ->
# Releases" ile izleyenlere GitHub her yeni release'te kendisi mail atar;
# boylece mail icin sifre/SMTP gerekmez. Release dosyasi 2 GB'a kadar olabilir.
RELEASE_DIR = os.environ.get("RG_RELEASE_DIR", "").strip()

def url_for(ymd, suffix=""):
    return f"{BASE}/{ymd[:4]}/{ymd[4:6]}/{ymd}{suffix}.pdf"

def download(url):
    try:
        r = requests.get(url, headers=HEADERS, verify=False, timeout=TIMEOUT)
    except Exception as e:
        print(f"  HATA {url}: {e}", file=sys.stderr)
        return None
    if r.status_code == 200 and r.content[:4] == b"%PDF":
        print(f"  OK   {url}  ({len(r.content):,} bayt)")
        return r.content
    print(f"  YOK  {url}  (durum {r.status_code})")
    return None

def save_tmp(pdf_bytes):
    f = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    f.write(pdf_bytes); f.close()
    return f.name

def pdf_to_text(path):
    out = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", path, "-"],
                         capture_output=True, timeout=900)
    if out.returncode != 0:
        print("  pdftotext hatasi:", out.stderr[:300], file=sys.stderr)
        return ""
    return out.stdout.decode("utf-8", "replace")

def strip_chrome(s):
    """Her sayfada tekrarlayan ustbilgi/altbilgiyi at, geriye kalan gercek metni olc."""
    s = re.sub(r"Sayfa\s*:\s*\d+", " ", s)
    s = re.sub(r"RESM[İI]\s*GAZETE", " ", s, flags=re.I)
    s = re.sub(r"\d{1,2}\s+\w+\s+\d{4}\s*[–-]\s*Say[ıi]\s*:\s*\d+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

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

# Bazi sayfalarda PDF'in metin katmani bozuk (fontun karakter eslemesi yok):
# pdftotext sayfayi dolu gosterir ama cikan sey "6.8730(086-6,8!8243+8/855"
# gibi anlamsiz dizilerdir, bu yuzden SPARSE_LIMIT onlari yakalamaz.
# Kelimelerin cogu duz harf/sayi degilse sayfa bozuk sayilir ve OCR'lanir.
# Eylul 2026 arsivinde bozuk sayfalar %35'in, saglamlar %85'in uzerinde.
HARF = "A-Za-zÇĞİÖŞÜçğıöşüÂâÎîÛûÊê"
TEMIZ_KELIME = re.compile(
    rf"^[(\[“\"'‘«]*(?:[{HARF}]+(?:[-'’][{HARF}]+)*|[\d.,/:%-]*\d[\d.,/:%-]*)"
    rf"[)\]”\"'’».,;:!?%-]*$")
BOZUK_ESIK = 0.5

def temiz_oran(s):
    kel = s.split()
    if not kel:
        return 1.0
    return sum(1 for w in kel if TEMIZ_KELIME.match(w)) / len(kel)

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
    """[2,3,16,39,40,41] -> [(2,3),(16,16),(39,41)] — bitisik sayfalari grupla."""
    out = []
    for n in sorted(nums):
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return [tuple(r) for r in out]

def ocr_range(path, a, b):
    """a..b sayfalarini TEK pdftoppm cagrisiyla render edip OCR'la.
       32 MB'lik PDF her sayfa icin yeniden acilmasin diye aralik halinde."""
    res = {}
    with tempfile.TemporaryDirectory() as td:
        pre = os.path.join(td, "pg")
        r = subprocess.run(["pdftoppm", "-r", "300", "-png", "-f", str(a), "-l", str(b),
                            path, pre], capture_output=True, timeout=900)
        if r.returncode != 0:
            print(f"    pdftoppm hatasi {a}-{b}:", r.stderr[:200], file=sys.stderr)
            return res
        for png in sorted(Path(td).glob("pg*.png")):
            m = re.search(r"pg-?0*(\d+)\.png$", png.name)
            if not m:
                continue
            n = int(m.group(1))
            res[n] = ocr_png(png)
            png.unlink(missing_ok=True)
    return res

def build_text(path, label=""):
    """Sayfa sayfa metin; bos/seyrek sayfalari OCR ile doldur.
       Doner: (isaretli tam metin, ocr'lanan sayfa numaralari, toplam sayfa)"""
    raw = pdf_to_text(path)
    pages = raw.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    sparse = [i for i, p in enumerate(pages, 1) if len(strip_chrome(p)) < SPARSE_LIMIT]
    bozuk = [i for i, p in enumerate(pages, 1)
             if i not in sparse and temiz_oran(p) < BOZUK_ESIK]
    print(f"  {label}toplam {len(pages)} sayfa, {len(sparse)} tanesi seyrek/taranmis, "
          f"{len(bozuk)} tanesinin metin katmani bozuk")
    # Sinir asilirsa once bozuk sayfalar: bunlar genelde Yurutme ve Idare
    # bolumu ile icindekiler, ilan bolumunden daha degerli.
    todo = sorted((bozuk + sparse)[:OCR_MAX_PAGES])
    capped = len(sparse) + len(bozuk) > len(todo)
    ocred = []
    rngs = to_ranges(todo)
    print(f"  {label}{len(todo)} sayfa OCR'lanacak, {len(rngs)} aralikta")
    for k, (a, b) in enumerate(rngs, 1):
        got = ocr_range(path, a, b)
        for n, txt in got.items():
            if n - 1 >= len(pages):
                continue
            eski = pages[n-1]
            if n in bozuk:
                iyi = bool(strip_chrome(txt)) and temiz_oran(txt) > temiz_oran(eski)
            else:
                iyi = len(strip_chrome(txt)) > len(strip_chrome(eski))
            if iyi:
                pages[n-1] = txt
                ocred.append(n)
        print(f"    [{k}/{len(rngs)}] s.{a}-{b} bitti (toplam OCR: {len(ocred)})",
              flush=True)
    ocred.sort()
    if capped:
        print(f"    UYARI: OCR siniri ({OCR_MAX_PAGES}) asildi, kalan sayfalar atlandi")
    out = []
    for i, p in enumerate(pages, 1):
        if not p.strip():
            continue
        tag = " (OCR)" if i in ocred else ""
        out.append(f"\n=== Sayfa {i}{tag} ===\n{p}")
    return "\n".join(out), ocred, len(pages)

def find_sayi(text):
    """Sayi numarasi her sayfanin ustbilgisinde tekrarlanir ("Sayi : 33380").
       Kapak bozuk ya da OCR'lanamamis olabilir, o yuzden tum metinde en
       sik gecen deger alinir."""
    say = Counter(re.findall(r"Say[ıi]\s*[:=]\s*(\d{4,6})\b", text))
    if say:
        return int(say.most_common(1)[0][0])
    m = re.search(r"(\d{5})\s*Say[ıi]l[ıi]", text[:8000])
    return int(m.group(1)) if m else None

def fihrist(text):
    """Kapakta "Icindekiler 152. Sayfadadir" yazar: o sayfadan sona kadar
       fihristtir. Kapakta bulunamazsa son 3 sayfa alinir."""
    parts = re.split(r"\n=== Sayfa (\d+)(?: \(OCR\))? ===\n", text)
    pages = {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    nums = sorted(pages)
    m = re.search(r"[İIi][çc][iİı]ndek[iİı]ler\s*:?\s*(\d+)\s*\.?\s*Sayfada",
                  pages.get(1, ""), re.I)
    bas = int(m.group(1)) if m else 0
    secili = [n for n in nums if n >= bas] if bas > 1 and bas in pages else nums[-3:]
    return "\n".join(pages[n] for n in secili)

# Yapay zeka ozeti icin kompakt girdi: ilan ve kur tablolari (metnin ~%90'i) atilir,
# Yurutme ve Idare / Yargi bolumundeki her kalem icin baslik + ilk maddeden kisa alinti.
OZET_ALINTI = 900       # kalem basina karakter (degisiklik maddelerindeki rakamlar sigsin)
OZET_BUTCE = 7000       # alintilarin toplam siniri; asilirsa kalan kalemler yalniz baslik
# Her kalemde tekrarlanan, ozete bilgi katmayan kalip maddeler
OZET_KALIP = re.compile(
    r"MADDE\s*\d+\s*[-–—]?\s*(?:\(1\)\s*)?Bu \w+ (?:yayımı|yayımlandığı) tarihinde yürürlüğe girer\.?"
    r"|MADDE\s*\d+\s*[-–—]?\s*(?:\(1\)\s*)?Bu \w+ hükümlerini .{0,120}? yürütür\.?"
    r"|MADDE\s*\d+\s*[-–—]?\s*Bu Karara karşı.{0,300}?dava açılabilir\.?"
    r"|Dayanak\s*MADDE\s*\d+\s*[-–—]?.{0,600}?hazırlanmıştır\.?"
    r"|Tanımlar\s*MADDE\s*\d+\s*[-–—]?.{0,3000}?ifade eder\.?")

_TR_ASCII = str.maketrans("çğıöşüâîûêÇĞİÖŞÜÂÎÛÊ", "cgiosuaiueCGIOSUAIUE")

def _norm(s):
    """Karakter karakter (uzunluk korunarak) buyuk ASCII harf/rakam; digerleri bosluk.
       Boylece icindekilerdeki 'Yonetmelik' ile metindeki 'YÖNETMELİK' eslesir."""
    s = s.translate(_TR_ASCII)
    return "".join(c.upper() if c.isascii() and c.isalnum() else " " for c in s)

def icindekiler_kalemleri(fih):
    """Icindekiler metninden [(bolum, baslik, sayfa)] ve ilan bolumunun ilk sayfasi."""
    kalemler, bolum, acik, ilan = [], "", None, None
    icerde = False
    for ham in fih.splitlines():
        l = ham.strip()
        if not l:
            continue
        if re.search(r"YÜRÜTME VE İDARE BÖLÜMÜ|YARGI BÖLÜMÜ", l):
            icerde, acik = True, None
            if "YARGI" in l:
                bolum = "YARGI BÖLÜMÜ"
            continue
        if re.search(r"İL[ÂA]N BÖLÜMÜ", l):
            icerde = False
            continue
        if not icerde:
            m = re.search(r"Yargı İl[âa]n\w*\s+(\d+)\s*$", l)
            if m and ilan is None:
                ilan = int(m.group(1))
            continue
        if l == "Sayfa" or re.search(r"RESM[İI] GAZETE", l):
            continue
        m = re.match(r"^[–—-]+\s*(.*)$", l)
        if m:
            acik = m.group(1)
        elif acik is not None:
            acik += " " + l
        else:
            bolum = re.sub(r"\s+\d+$", "", l)
            continue
        m = re.search(r"^(.*\S)\s+(\d{1,4})$", acik)
        if m:
            kalemler.append((bolum, re.sub(r"\s+", " ", m.group(1)), int(m.group(2))))
            acik = None
    # Sayfa numaralari artan olmali; olmayan (yanlis okunan) kalemi at
    temiz, son = [], 0
    for k in kalemler:
        if k[2] >= son and (ilan is None or k[2] < ilan):
            temiz.append(k)
            son = k[2]
    return temiz, ilan

def _konum(metin, baslik):
    """Basligin ilk 4 anlamli kelimesinin metindeki yeri (yoksa None)."""
    kel = [w for w in _norm(baslik).split() if len(w) > 1][:4]
    if not kel:
        return None
    m = re.search(r"\s+".join(map(re.escape, kel)), _norm(metin))
    return m.start() if m else None

def _govde(p):
    """Sayfanin ust/alt bilgi satirlarini at."""
    return "\n".join(l for l in p.splitlines()
                     if not re.search(r"RESM[İIÎ]\s*GAZETE|^\s*Sayfa\s*:?\s*\d+\s*$|Kuruluşu\s*:|İçindekiler\s*\d+\s*\.", l))

def sayfalar(text):
    """{sayfa no: metin}"""
    parts = re.split(r"\n=== Sayfa (\d+)(?: \(OCR\))? ===\n", text)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}

def kalem_metni(pages, kalemler, i, son_sayfa, ek_sayfa=2):
    """i. kalemin gazetedeki metni: basligindan, sonraki kalemin basligina ya da
       "—— • ——" ayracina kadar; kendi sayfasindan en fazla ek_sayfa sonrasina.
       Baslik bulunamaz ve ayni sayfada onceki bir kalem varsa (onun metni
       karismasin diye) None."""
    _, baslik, s = kalemler[i]
    bitis = kalemler[i + 1][2] if i + 1 < len(kalemler) else son_sayfa
    govde, sonraki_bas = "", None
    for n in range(s, min(bitis, s + ek_sayfa) + 1):
        if n == bitis and n > s:
            sonraki_bas = len(govde)   # sonraki kalemin sayfasi burada basliyor
        govde += _govde(pages.get(n, "")) + "\n"
    bas = _konum(govde, baslik)
    if bas is None:
        if i and kalemler[i - 1][2] == s:
            return None
        bas = 0
    # Sonraki kalemin basligi yalniz onun sayfasinda aranir: metin icinde ayni
    # adla anilan baska bir mevzuati (or. degistirilen teblig) bitis sanmasin.
    if i + 1 < len(kalemler) and (bitis == s or sonraki_bas is not None):
        ara = bas + 1 if bitis == s else max(bas + 1, sonraki_bas)
        son = _konum(govde[ara:], kalemler[i + 1][1])
        if son is not None:
            govde = govde[:ara + son]
    govde = govde[bas:]
    # Ayni sayfadaki kalemler "—— • ——" ile, ilan bolumu kendi basligiyla ayrilir
    m = re.search(r"—+\s*•+\s*—+|İL[ÂA]N BÖLÜMÜ", govde[1:])
    if m:
        govde = govde[:m.start() + 1]
    return govde

def ozet_girdisi(text):
    pages = sayfalar(text)
    kalemler, ilan = icindekiler_kalemleri(fihrist(text))
    if not kalemler:
        return None   # icindekiler cozulemedi (or. bazi mukerrerlerde yok)
    son_sayfa = (ilan or max(pages)) - 1
    satirlar, butce, bolum = [], OZET_BUTCE, None
    for i, (b, baslik, s) in enumerate(kalemler):
        if b != bolum:
            satirlar += ["", b]
            bolum = b
        satirlar.append(f"• {baslik} (s. {s})")
        if butce <= 0:
            continue
        govde = kalem_metni(pages, kalemler, i, son_sayfa)
        if govde is None:
            continue
        # Ilk madde varsa oradan basla: baslik/imza/yururluk kalibini atlar
        m = re.search(r"MADDE\s*1\s*[-–—]", govde)
        if m:
            govde = govde[m.start():]
        alinti = re.sub(r"\s+", " ", govde).strip()
        alinti = re.sub(r"([a-zçğıöşü])- ([a-zçğıöşü])", r"\1\2", alinti)   # satir sonu tirelemesi
        alinti = re.sub(r"\s+", " ", OZET_KALIP.sub("", alinti)).strip()
        if len(alinti) < 40 or temiz_oran(alinti) < BOZUK_ESIK:
            continue
        if len(alinti) > OZET_ALINTI:
            alinti = alinti[:OZET_ALINTI].rsplit(" ", 1)[0] + " …"
        satirlar.append(f"  {alinti}")
        butce -= len(alinti)
    if ilan:
        satirlar += ["", f"İLÂN BÖLÜMÜ (s. {ilan}–{max(pages)}): yargı, ihale ve çeşitli ilanlar, "
                         "Merkez Bankası kurları (özete alınmadı)."]
    return "\n".join(satirlar).strip()

def mb(n):
    return f"{n / 1024 / 1024:.1f} MB"

def release_yaz(meta, yeni_ana, yeni_muk, pdfler):
    """Etiket, baslik, aciklama ve PDF'leri RELEASE_DIR'e yazar. yeni_muk: bu
       kosumda eklenen mukerrer numaralari, pdfler: {dosya adi: pdf baytlari}."""
    d = Path(RELEASE_DIR)
    d.mkdir(parents=True, exist_ok=True)
    for ad, veri in pdfler.items():
        (d / ad).write_bytes(veri)
    ymd = meta["ymd"]
    baslik = f"Resmî Gazete {meta['tarih']}"
    if meta.get("sayi"):
        baslik += f" – Sayı {meta['sayi']}"
    if yeni_ana:
        etiket = f"rg-{ymd}"
    else:
        # Mukerrer gun icinde sonradan cikarsa ayri release: izleyenlere yeniden mail gitsin
        etiket = f"rg-{ymd}-M{yeni_muk[0]}"
        baslik += " – " + ", ".join(f"{n}. Mükerrer" for n in yeni_muk)

    # Aciklama maile aynen girer ve maildeki yapay zeka ozetinin girdisidir: once
    # kompakt ozet (kalem basliklari + ilk madde), en sonda linkler. Ozet
    # cikarilamazsa icindekiler sayfasi konur.
    def oku(yol):
        fp = ROOT / yol if yol else None
        return fp.read_text(encoding="utf-8").strip() if fp and fp.exists() else ""

    def md(oz):
        out = []
        for l in oz.splitlines():
            if l.startswith("• "):
                out.append("- " + l[2:])
            elif l.startswith("  "):
                out.append(l)
            elif l.startswith("İLÂN BÖLÜMÜ"):
                out.append("_" + l + "_")
            elif l:
                out.append("### " + l)
            else:
                out.append("")
        return "\n".join(out)

    notlar, linkler = [], []
    if yeni_ana:
        oz = oku(meta.get("ozet_file"))
        if oz:
            notlar.append(md(oz))
        else:
            fih = oku(meta.get("fihrist_file")).replace("```", "` ` `")
            fih = re.sub(r"\n{3,}", "\n\n", "\n".join(l.rstrip() for l in fih.splitlines()))
            if fih:
                notlar += ["### İçindekiler", "", "```text", fih, "```"]
        linkler.append(f"- PDF ({meta.get('pages')} sayfa, {mb(meta['pdf_bytes'])}): "
                       f"{meta['pdf_url']}")
    for m in meta.get("mukerrer") or []:
        if m["no"] in yeni_muk:
            moz = oku(m.get("ozet_file"))
            if moz:
                notlar += ["", f"## {m['no']}. Mükerrer", "", md(moz)]
            linkler.append(f"- {m['no']}. Mükerrer ({m['pages']} sayfa, {mb(m['pdf_bytes'])}): {m['url']}")
    notlar += ["", "---"] + linkler + ["", "PDF'ler bu release'in ekinde."]
    (d / "etiket.txt").write_text(etiket, encoding="utf-8")
    (d / "baslik.txt").write_text(baslik, encoding="utf-8")
    (d / "notlar.md").write_text("\n".join(notlar) + "\n", encoding="utf-8")
    print(f"Release hazir: {etiket} – {baslik} ({len(pdfler)} PDF)")

def main():
    ymd = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else datetime.now(TRT).strftime("%Y%m%d")
    if not re.fullmatch(r"\d{8}", ymd):
        print("Gecersiz tarih:", ymd, file=sys.stderr)
        return 1

    force = os.environ.get("YENIDEN", "").strip().lower() in ("1", "true", "yes", "evet")
    outdir = ROOT / "data" / ymd[:4] / ymd[4:6]
    meta_path = outdir / f"{ymd}.json"

    # Ayni gun icin birden fazla slot kosuyor. Ana sayi zaten indirilmisse
    # 118 MB'lik PDF'i tekrar indirip yuzlerce sayfayi tekrar OCR'lamanin
    # anlami yok; yalniz "yeni mukerrer cikmis mi" diye bakilir.
    existing = None
    if meta_path.exists() and not force:
        try:
            existing = json.loads(meta_path.read_text(encoding="utf-8"))
            print(f"{ymd} daha once islenmis (sayi {existing.get('sayi')}, "
                  f"{existing.get('pages')} sayfa) - ana sayi yeniden indirilmeyecek.")
        except Exception as e:
            print("  Mevcut meta okunamadi, bastan islenecek:", e, file=sys.stderr)
            existing = None

    outdir.mkdir(parents=True, exist_ok=True)
    pdfler = {}

    # ---------- ANA SAYI ----------
    if existing:
        meta = dict(existing)
    else:
        print("Resmi Gazete indiriliyor:", ymd)
        main_pdf = download(url_for(ymd))
        if main_pdf is None:
            print("Gazete henuz yayimlanmamis gorunuyor.")
            return 20

        path = save_tmp(main_pdf)
        try:
            text, ocred, page_count = build_text(path)
        finally:
            os.unlink(path)
        sayi = find_sayi(text)
        pdfler[f"{ymd}.pdf"] = main_pdf

        with gzip.open(outdir / f"{ymd}.txt.gz", "wt", encoding="utf-8") as f:
            f.write(text)

        (outdir / f"{ymd}.fihrist.txt").write_text(fihrist(text), encoding="utf-8")
        oz = ozet_girdisi(text)
        if oz:
            (outdir / f"{ymd}.ozet.txt").write_text(oz + "\n", encoding="utf-8")

        meta = {
            "tarih": f"{ymd[6:8]}.{ymd[4:6]}.{ymd[:4]}", "ymd": ymd, "sayi": sayi,
            "pdf_url": url_for(ymd), "pdf_bytes": len(main_pdf), "pages": page_count,
            "ocr_pages": ocred,
            "text_file": f"data/{ymd[:4]}/{ymd[4:6]}/{ymd}.txt.gz",
            "fihrist_file": f"data/{ymd[:4]}/{ymd[4:6]}/{ymd}.fihrist.txt",
            "ozet_file": f"data/{ymd[:4]}/{ymd[4:6]}/{ymd}.ozet.txt" if oz else None,
            "mukerrer": []}

    # ---------- MUKERRER ----------
    # Mukerrer sayilar gun icinde sonradan yayimlanabilir; ana sayi islenmis
    # olsa bile her kosumda bakilir, yalniz YENI olanlar indirilip OCR'lanir.
    muk_meta = list(meta.get("mukerrer") or [])
    mevcut = {m["no"] for m in muk_meta}
    yeni_muk = []
    for i in range(1, 11):
        if i in mevcut:
            continue
        data = download(url_for(ymd, f"M{i}"))
        if data is None:
            break
        mp = save_tmp(data)
        try:
            mtext, mocr, mpages = build_text(mp, label=f"M{i} ")
        finally:
            os.unlink(mp)
        with gzip.open(outdir / f"{ymd}M{i}.txt.gz", "wt", encoding="utf-8") as f:
            f.write(mtext)
        moz = ozet_girdisi(mtext)
        if moz:
            (outdir / f"{ymd}M{i}.ozet.txt").write_text(moz + "\n", encoding="utf-8")
        muk_meta.append({
            "no": i, "url": url_for(ymd, f"M{i}"),
            "pdf_bytes": len(data), "pages": mpages, "ocr_pages": mocr,
            "text_file": f"data/{ymd[:4]}/{ymd[4:6]}/{ymd}M{i}.txt.gz",
            "ozet_file": f"data/{ymd[:4]}/{ymd[4:6]}/{ymd}M{i}.ozet.txt" if moz else None})
        yeni_muk.append(i)
        pdfler[f"{ymd}M{i}.pdf"] = data
    yeni = len(yeni_muk)

    if existing and yeni == 0:
        print("Degisiklik yok: bu sayi zaten islenmis, yeni mukerrer cikmamis.")
        return 21

    muk_meta.sort(key=lambda m: m["no"])
    meta["mukerrer"] = muk_meta
    meta["fetched_at"] = datetime.now(timezone.utc).isoformat()

    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # latest.json yalniz ILERI giderek guncellenir: gecmise donuk bir
    # backfill kosumu (workflow_dispatch + tarih) "bugunku sayi" isaretini
    # eski bir gune cevirmesin.
    latest_path = ROOT / "data" / "latest.json"
    onceki = None
    if latest_path.exists():
        try:
            onceki = json.loads(latest_path.read_text(encoding="utf-8")).get("ymd")
        except Exception:
            onceki = None
    if onceki is None or str(ymd) >= str(onceki):
        latest_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(f"latest.json guncellenmedi (kayitli {onceki} daha yeni).")

    if RELEASE_DIR:
        release_yaz(meta, not existing, yeni_muk, pdfler)

    print(f"\nBitti: sayi {meta.get('sayi')}, {meta.get('pages')} sayfa, "
          f"{len(meta.get('ocr_pages') or [])} sayfa OCR, "
          f"{len(muk_meta)} mukerrer" + (f" ({yeni} yeni)" if yeni else ""))
    return 0

if __name__ == "__main__":
    sys.exit(main())
