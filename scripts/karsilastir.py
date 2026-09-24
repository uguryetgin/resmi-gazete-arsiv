#!/usr/bin/env python3
"""Ilgi alanindaki "... Degisiklik Yapilmasina Dair ..." kalemlerinde degisen
hukumlerin ESKI ve YENI halini bulur.

rg_fetch.py'den sonra calisir. Gunun gazete metninden her degisiklik maddesini
ayristirir (hedef madde/fikra/bent, "X ibaresi Y" ciftleri, yeniden yazilan metin),
gerekirse degistirilen mevzuatin onceki metnini su siradaki kaynaklardan alir:

1. www.mevzuat.gov.tr: birimde bugunun "RG-g/a/yyyy-sayi" notu yoksa metin tam
   olarak degisiklikten onceki haldir (site degisiklikleri genelde ayni gun isler).
2. Bedesten (bedesten.adalet.gov.tr): degisiklikleri gec isler; ama bazi metinleri
   yillardir guncellenmemis olabilir. Birimdeki son degisiklik notu mevzuat.gov.tr
   ile ayniysa "guncel", degilse "eski olabilir" diye etiketlenir.

"X ibaresi Y seklinde degistirilmistir" turundeki degisikliklerde eski ve yeni
ibare zaten gazetede yazilidir; kaynak yalniz baglam (onceki cumle) icin kullanilir.

Ciktilar: data/YYYY/AA/YYYYAAGG.karsilastirma.json ve RG_RELEASE_DIR/notlar.md'de
ilgili kalemin altina girintili "⇄" satirlari (Routine yukune de boylece girer).
Hicbir hata gunluk isi durdurmaz; toplam sure SURE_SINIRI ile sinirlidir.

Kullanim:
  python3 scripts/karsilastir.py YYYYAAGG            # gunluk is (ilgi alani kalemleri)
  python3 scripts/karsilastir.py --test YYYYAAGG ...  # tum degisiklik kalemleri, yalniz ekrana
  python3 scripts/karsilastir.py --yerel YYYYAAGG ... # --test gibi ama ag erisimi yok"""
import gzip, json, os, re, sys, time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rg_fetch as rg            # noqa: E402  (kalem ayristirma fonksiyonlari)
import routine_tetikle as rt     # noqa: E402  (ilgi alani eslestirme)

ROOT = Path(".")
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
BED = "https://bedesten.adalet.gov.tr/mevzuat/"
BED_H = {"Content-Type": "application/json; charset=utf-8", "AdaletApplicationName": "UyapMevzuat",
         "Origin": "https://mevzuat.adalet.gov.tr", "Referer": "https://mevzuat.adalet.gov.tr/",
         "User-Agent": BROWSER_UA}
MG = "https://www.mevzuat.gov.tr"

MAX_KALEM = 8          # gunde en fazla bu kadar degisiklik kalemi icin kaynak sorgulanir
MAX_DEGISIKLIK = 8     # kalem basina en fazla bu kadar degisiklik satiri
ALINTI = 600           # eski/yeni metin basina karakter
BUTCE = 7000           # notlar.md'ye eklenen karsilastirma satirlarinin toplam siniri
SURE_SINIRI = 240      # saniye; asilinca kalan kalemler yalniz gazeteden ayristirilir

SIRA = {"birinci": 1, "ikinci": 2, "üçüncü": 3, "dördüncü": 4, "beşinci": 5, "altıncı": 6,
        "yedinci": 7, "sekizinci": 8, "dokuzuncu": 9, "onuncu": 10}
EK = r"(?:['’]?(?:inci|ıncı|nci|ncı|üncü|uncu|ncü|ncu))"

def tr_lower(s):
    return s.replace("I", "ı").replace("İ", "i").lower()

def duz(s):
    """Satir sonu tirelemesini birlestir, bosluklari tekle."""
    s = re.sub(r"([a-zçğıöşü])-\s*\n\s*([a-zçğıöşü])", r"\1\2", s)
    return re.sub(r"\s+", " ", s).strip()

def kisalt(s, n=ALINTI):
    s = duz(s)
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + " …"

# ---------------------------------------------------------------- ayristirma

def maddeler(metin):
    """Degisiklik metnini kendi maddelerine bol: [(no, metin)]. Tirnak icindeki
       yeni metinde gecen "MADDE 3-" sinir sayilmasin diye hem sira numarasi hem
       tirnak derinligi kontrol edilir."""
    bulunan, beklenen = [], 1
    for m in re.finditer(r"MADDE\s*(\d+)\s*\S?\s*[-–—]", metin):   # OCR: "MADDE 8$-"
        derinlik = metin.count("“", 0, m.start()) - metin.count("”", 0, m.start())
        if int(m.group(1)) == beklenen and derinlik <= 0:
            bulunan.append((beklenen, m.start()))
            beklenen += 1
    return [(no, metin[bas: bulunan[k + 1][1] if k + 1 < len(bulunan) else len(metin)])
            for k, (no, bas) in enumerate(bulunan)]

# Yururluk / yurutme maddeleri ("Bu Yönetmelik yayımı tarihinde yürürlüğe girer.")
KALIP = re.compile(r"^(?:\(\d+\)\s*)?Bu\s+\w+(?:\s+\w+)?\s+(?:.{0,120}?yürürlüğe girer|hükümlerini\b)", re.S)
# Kalemin sonuna tasan metin: bolum basliklari ya da sonraki kalemin yayimlayan kurumu
TASMA = re.compile(r"\b(?:YARGI BÖLÜMÜ|İL[ÂA]N BÖLÜMÜ|YÜRÜTME VE İDARE BÖLÜMÜ)\b|"
                   r"(?<=[.”:])\s+[A-ZÇĞİÖŞÜ][^.:“”]{2,90}(?:ndan|nden|dan|den|tan|ten):\s*(?:[A-ZÇĞİÖŞÜ]|$)")

def temel_referans(metin):
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})\s*tarihli\s*ve\s*(\d+)\s*(?:mükerrer\s*)?sayılı\s*"
                  r"Resm[iîİ]\s*Gazete", metin)
    return {"rg_tarih": f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}", "rg_sayi": m.group(4)} if m else None

def hedef_birim(yer):
    """'2.2.7 nci maddesinin', '4/C maddesinin ikinci fıkrası', '(a) bendi', 'geçici 3 üncü madde'."""
    birim = {}
    m = re.search(rf"(\d+(?:\.\d+)+)\s*{EK}?\s*madde", yer) or \
        re.search(rf"(geçici\s+)?(\d+(?:\s*/\s*[A-ZÇĞİÖŞÜ])?)\s*{EK}?\s*madde", yer, re.I)
    if m:
        if m.re.pattern.startswith(r"(\d+(?:\.\d+)+)"):
            birim["ondalik"] = m.group(1)
        else:
            birim["madde"] = re.sub(r"\s+", "", m.group(2)).upper()
            birim["gecici"] = bool(m.group(1))
    m = re.search(r"(" + "|".join(SIRA) + r")\s*(?:fıkra|paragraf)", yer)
    if m:
        birim["fikra"] = SIRA[m.group(1)]
    m = re.search(r"\(([a-zçğıöşü])\)\s*bend", yer)
    if m:
        birim["bent"] = m.group(1)
    return birim

def tirnakli(s):
    """Ilk “ ile son ” arasi (icte tirnak olabilir)."""
    a, b = s.find("“"), s.rfind("”")
    return s[a + 1:b] if a >= 0 and b > a else None

def degisiklik_ayristir(no, metin):
    d = duz(metin)
    govde = re.sub(r"^MADDE\s*\d+\s*\S?\s*[-–—]\s*", "", d)
    t = TASMA.search(govde)
    if t:
        govde = govde[:t.start()].strip()
    if KALIP.search(govde):
        return None
    ilk_tirnak = govde.find("“")
    yer = govde[:ilk_tirnak] if ilk_tirnak >= 0 else govde
    k = {"madde": no, "talimat": kisalt(govde, 400), "birim": hedef_birim(tr_lower(yer)), "turler": []}
    ciftler = re.findall(r"“([^”]{1,300})”\s*ibare(?:si|leri)\s*“([^”]{0,300})”\s*(?:şeklinde|olarak)\s*değiştiril", govde)
    if ciftler:
        k["turler"].append("ibare")
        k["ibareler"] = [{"eski": e, "yeni": y} for e, y in ciftler]
    kaldirilan = re.findall(r"“([^”]{1,300})”\s*ibare(?:si|leri)\s*(?:yürürlükten kaldırıl|metinden çıkarıl|çıkarıl)", govde)
    if kaldirilan:
        k["turler"].append("ibare-kaldirma")
        k["kaldirilan_ibareler"] = kaldirilan
    m = re.search(r"aşağıdaki\s+şekilde\s+de[gğ]iştiril", govde)
    if m:
        k["turler"].append("yeniden")
        k["yeni_metin"] = kisalt(tirnakli(govde[m.end():]) or "")
    if re.search(r"eklenmiştir|eklenmiş\b", govde):
        k["turler"].append("ekleme")
        if "yeni_metin" not in k:
            y = tirnakli(govde)
            if y and len(y) > 40:
                k["yeni_metin"] = kisalt(y)
    if re.search(r"(?:madde|fıkra|bent|bendi|fıkrası|maddesi)\S*\s+(?:ile\s+.{0,60})?yürürlükten kaldırılmıştır", govde):
        k["turler"].append("mulga")
    if not k["turler"]:
        k["turler"].append("diger")
    return k

def degisiklik_kalemi(kalem_metni):
    """Bir 'Degisiklik Yapilmasina Dair' kaleminin metninden temel referans ve degisiklikler."""
    ic = maddeler(kalem_metni)
    if not ic:
        return None
    ref = temel_referans(duz(ic[0][1]))
    degisiklikler = [x for x in (degisiklik_ayristir(no, m) for no, m in ic) if x]
    return {"temel": ref, "degisiklikler": degisiklikler}

def temel_baslik(kalem_basligi):
    """'X Yönetmeliğinde Değişiklik Yapılmasına Dair Yönetmelik' -> 'X Yönetmeliği'."""
    return re.sub(r"\s*(?:['’]?n?[dt][ae])?\s+Değişiklik\s+Yapılmasına\s+Dair.*$", "", kalem_basligi).strip()

# ---------------------------------------------------------------- kaynaklar

class Kaynak:
    def __init__(self):
        import requests
        self.rq = requests
        self.bas = time.time()
        self.mg_kapali = False
        self.mg = requests.Session()
        self.turler = None

    def sure_doldu(self):
        return time.time() - self.bas > SURE_SINIRI

    def bed(self, yol, data, paging=False):
        govde = {"data": data, "applicationName": "UyapMevzuat"}
        if paging:
            govde["paging"] = True
        for _ in range(2):
            try:
                r = self.rq.post(BED + yol, json=govde, headers=BED_H, timeout=(10, 30))
            except Exception:
                time.sleep(2)
                continue
            if r.status_code >= 500 or r.status_code == 429:
                time.sleep(3)
                continue
            try:
                j = r.json()
            except Exception:
                return None
            meta = j.get("metadata") or {}
            return j.get("data") if meta.get("FMTY", "SUCCESS") == "SUCCESS" else None
        return None

    def bed_ara(self, ref, baslik):
        """Temel mevzuati Bedesten'de RG sayisi (+tarih) ve baslik benzerligiyle bul."""
        if self.turler is None:
            d = self.bed("mevzuatTypes", {})
            self.turler = [t.get("mevzuatTur") for t in (d if isinstance(d, list) else []) if t.get("mevzuatTur")]
        filtre = {"pageSize": 20, "pageNumber": 1, "resmiGazeteSayisi": ref["rg_sayi"]}
        if self.turler:
            filtre["mevzuatTurList"] = self.turler
        d = self.bed("searchDocuments", filtre, paging=True) or {}
        satirlar = rt_liste(d)
        hedef = set(w for w in rt.norm(baslik).split() if len(w) > 2)
        no = re.search(r"\bNO\s+(\d+(?:\s\d+)?)\b", rt.norm(baslik))   # "Tebliğ No: 2024/8" -> "2024 8"
        en_iyi, puan = None, 0.0
        for s in satirlar:
            ad = rt.norm(str(s.get("mevzuatAdi") or ""))
            if "DEGISIKLIK YAPILMASINA" in ad or str(s.get("resmiGazeteSayisi")) != ref["rg_sayi"]:
                continue
            if no and not re.search(r"\bNO\s+" + re.escape(no.group(1)) + r"\b", ad):
                continue
            kelimeler = set(w for w in ad.split() if len(w) > 2)
            p = len(hedef & kelimeler) / max(1, len(hedef))
            if p > puan:
                en_iyi, puan = s, p
        return en_iyi if puan >= 0.5 else None

    def bed_metin(self, mid):
        import base64
        d = self.bed("getDocumentContent", {"documentType": "MEVZUAT", "id": str(mid)}) or {}
        icerik = d.get("content") if isinstance(d, dict) else None
        if not icerik:
            return None
        ham = base64.b64decode(icerik)
        if ham[:4] == b"%PDF":
            return None
        for enc in ("utf-8", "windows-1254"):
            try:
                return html_satirlar(ham.decode(enc))
            except UnicodeDecodeError:
                continue
        return None

    def mg_metin(self, url):
        """mevzuat.gov.tr/mevzuat?MevzuatNo=..&MevzuatTur=..&MevzuatTertip=.. -> iframe HTML metni."""
        if self.mg_kapali or not url:
            return None
        q = {k.lower(): v[0] for k, v in parse_qs(urlparse(url).query).items()}
        if not {"mevzuatno", "mevzuattur", "mevzuattertip"} <= q.keys():
            return None
        iframe = (f"{MG}/anasayfa/MevzuatFihristDetayIframe?MevzuatTur={q['mevzuattur']}"
                  f"&MevzuatNo={q['mevzuatno']}&MevzuatTertip={q['mevzuattertip']}")
        try:
            r = self.mg.get(iframe, timeout=(10, 20), headers={
                "User-Agent": BROWSER_UA, "Referer": MG + "/", "Accept-Language": "tr-TR,tr;q=0.9"})
        except Exception:
            self.mg_kapali = True        # zaman asimi: bu kosuda bir daha deneme
            return None
        if r.status_code != 200 or re.search(r"404\s*[-–—]?\s*Sayfa\s+Bulunamad", r.text):
            return None
        return html_satirlar(r.content.decode(r.encoding or "utf-8", "replace"))

def rt_liste(j):
    if isinstance(j, list) and j and isinstance(j[0], dict):
        return j
    if isinstance(j, dict):
        for v in j.values():
            r = rt_liste(v)
            if r:
                return r
    return []

def html_satirlar(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style"]):
        t.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for t in soup.find_all(["p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"]):
        t.insert_before("\n")
        t.insert_after("\n")
    for t in soup.find_all(["td", "th"]):
        t.insert_after(" | ")
    metin = soup.get_text().replace("\xa0", " ")
    return "\n".join(re.sub(r"[ \t]+", " ", l).strip() for l in metin.splitlines() if l.strip())

# ---------------------------------------------------------------- birim ayiklama

HARFLER = "abcçdefgğhıijklmnoöprsştuüvyz"

def birim_ayikla(metin, birim):
    """Kaynak metinden hedef birimi (madde > fikra > bent) cikar; bulunamazsa None."""
    if "ondalik" in birim:
        m = re.search(rf"(?m)^\s*{re.escape(birim['ondalik'])}\.?(?=[\s\-–(])", metin)
        if not m:
            return None
        parcalar = birim["ondalik"].split(".")
        s = re.search(r"(?m)^\s*\d+(?:\.\d+){%d}\.?(?=[\s\-–(])" % (len(parcalar) - 1), metin[m.end():])
        return metin[m.start(): m.end() + (s.start() if s else 3000)]
    if "madde" not in birim:
        return None
    no = birim["madde"].replace("/", r"\s*/\s*")
    onek = r"GEÇİCİ\s+" if birim.get("gecici") else r"(?<!GEÇİCİ )"
    m = re.search(rf"(?m)^\s*{onek}MADDE\s+{no}\b\s*[-–—]", metin, re.I)
    if not m:
        return None
    s = re.search(r"(?mi)^\s*(?:GEÇİCİ\s+|EK\s+)?MADDE\s+\d+[^\n]{0,4}[-–—]", metin[m.end():])
    parca = metin[m.start(): m.end() + (s.start() if s else 5000)]
    # Madde basligi bir onceki satirda olabilir; madde metni yeter.
    if "fikra" in birim:
        f = birim["fikra"]
        a = re.search(rf"\({f}\)", parca)
        if a:
            b = re.search(rf"\({f + 1}\)", parca[a.end():])
            parca = parca[a.start(): a.end() + (b.start() if b else len(parca))]
    if "bent" in birim:
        h = birim["bent"]
        a = re.search(rf"(?m)(?:^|\s){re.escape(h)}\)\s", parca)
        if a:
            sonraki = HARFLER[HARFLER.index(h) + 1] if h in HARFLER[:-1] else None
            b = re.search(rf"(?m)(?:^|\s){sonraki}\)\s", parca[a.end():]) if sonraki else None
            parca = parca[a.start(): a.end() + (b.start() if b else len(parca))]
    return parca

def notlar_(metin):
    """Birimdeki 'RG-g/a/yyyy' degisiklik notlari: [(yyyy, a, g)] sirali."""
    return sorted({(int(y), int(a), int(g)) for g, a, y in re.findall(r"RG[-\s]*(\d{1,2})/(\d{1,2})/(\d{4})", metin)})

def bugun_notu(ymd, sayi):
    y, a, g = int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8])
    return re.compile(rf"RG[-\s]*0?{g}/0?{a}/{y}(?:[-\s]*{sayi})?")

def onceki_birim(kaynak, satir, birim, ymd, sayi):
    """(metin, etiket) ya da (None, sebep). Sirayla mevzuat.gov.tr, Bedesten."""
    isaret = bugun_notu(ymd, sayi)
    mg_birim = None
    mg = kaynak.mg_metin(satir.get("url"))
    if mg:
        mg_birim = birim_ayikla(mg, birim)
        if mg_birim and not isaret.search(mg_birim):
            return mg_birim, "mevzuat.gov.tr (değişiklikten önceki güncel metin)"
    bed = kaynak.bed_metin(satir.get("mevzuatId"))
    bed_birim = birim_ayikla(bed, birim) if bed else None
    if bed_birim and not isaret.search(bed_birim):
        if mg_birim is None:
            return bed_birim, "Bedesten (güncelliği doğrulanamadı)"
        mg_notlar = [n for n in notlar_(mg_birim) if not isaret.search(f"RG-{n[2]}/{n[1]}/{n[0]}-{sayi}")]
        b, m_ = (notlar_(bed_birim) or [None])[-1], (mg_notlar or [None])[-1]
        if b == m_:
            return bed_birim, "Bedesten (güncel)"
        tarih = lambda n: f"{n[2]}/{n[1]}/{n[0]}" if n else "yok"
        return bed_birim, f"Bedesten (eski olabilir: son değişiklik notu {tarih(b)}, mevzuat.gov.tr'de {tarih(m_)})"
    if mg_birim or bed_birim:
        return None, "kaynaklar değişikliği zaten işlemiş"
    return None, "hedef birim kaynaklarda bulunamadı"

def baglam(metin, ifade, pay=160):
    d = duz(metin)
    i = tr_lower(d).find(tr_lower(duz(ifade)))
    if i < 0:
        return None
    return ("…" if i > pay else "") + d[max(0, i - pay): i + len(ifade) + pay] + "…"

# ---------------------------------------------------------------- ana akis

def birim_adi(k):
    b, p = k.get("birim") or {}, []
    if "ondalik" in b:
        p.append(f"{b['ondalik']} madde")
    elif "madde" in b:
        p.append(("geçici " if b.get("gecici") else "") + f"{b['madde']}. madde")
    if "fikra" in b:
        p.append(f"{b['fikra']}. fıkra")
    if "bent" in b:
        p.append(f"({b['bent']}) bendi")
    return ", ".join(p) or "hedef belirsiz"

def isle(ymd, text, sadece_ilgi=True, ag=True):
    pages = rg.sayfalar(text)
    kalemler, ilan = rg.icindekiler_kalemleri(rg.fihrist(text))
    if not kalemler:
        return []
    son_sayfa = (ilan or max(pages)) - 1
    sayi = str(rg.find_sayi(text) or "")
    alanlar, haric = rt.ilgi_alanlari() if (ROOT / "ilgi.txt").exists() else ([], None)
    kaynak = Kaynak() if ag else None
    sonuc = []
    for i, (_, baslik, s) in enumerate(kalemler):
        if "Değişiklik Yapılmasına" not in baslik:
            continue
        alan = rt.alan_bul(baslik, alanlar, haric) if alanlar else None
        if sadece_ilgi and not alan:
            continue
        if len(sonuc) >= MAX_KALEM:
            break
        metin = rg.kalem_metni(pages, kalemler, i, son_sayfa, ek_sayfa=4)
        k = degisiklik_kalemi(metin or "")
        if not k:
            continue
        k.update({"baslik": baslik, "sayfa": s, "alan": alan, "temel_baslik": temel_baslik(baslik)})
        k["degisiklikler"] = k["degisiklikler"][:MAX_DEGISIKLIK]
        gerekli = any(t in ("yeniden", "mulga") or "ibare" in t for d in k["degisiklikler"] for t in d["turler"])
        if ag and k["temel"] and gerekli and not kaynak.sure_doldu():
            try:
                satir = kaynak.bed_ara(k["temel"], k["temel_baslik"])
                if satir:
                    k["temel"].update({"mevzuatId": satir.get("mevzuatId"), "url": satir.get("url"),
                                       "ad": duz(str(satir.get("mevzuatAdi") or ""))})
                    for d in k["degisiklikler"]:
                        if kaynak.sure_doldu() or not d.get("birim"):
                            continue
                        if not ({"yeniden", "mulga", "ibare", "ibare-kaldirma"} & set(d["turler"])):
                            continue
                        eski, etiket = onceki_birim(kaynak, satir, d["birim"], ymd, sayi)
                        d["onceki_kaynak"] = etiket
                        if eski is None:
                            continue
                        if {"yeniden", "mulga"} & set(d["turler"]):
                            d["eski_metin"] = kisalt(eski)
                        for c in d.get("ibareler", []):
                            c["baglam"] = baglam(eski, c["eski"])
                else:
                    k["temel"]["bulunamadi"] = True
            except Exception as e:           # kaynak hatasi karsilastirmayi durdurmasin
                k["hata"] = f"{type(e).__name__}: {str(e)[:120]}"
        sonuc.append(k)
    return sonuc

def satirlar(k):
    """notlar.md'de kalemin altina eklenecek girintili satirlar."""
    out = []
    for d in k["degisiklikler"]:
        tur = "/".join(d["turler"])
        out.append(f"  ⇄ Değişiklik MADDE {d['madde']} → {birim_adi(d)} [{tur}]")
        for c in d.get("ibareler", []):
            out.append(f"    Eski: “{c['eski']}” → Yeni: “{c['yeni']}”")
            if c.get("baglam"):
                out.append(f"    Önceki cümle ({d.get('onceki_kaynak')}): {c['baglam']}")
        for e in d.get("kaldirilan_ibareler", []):
            out.append(f"    Kaldırılan ibare: “{e}”")
        if d.get("eski_metin"):
            out.append(f"    Eski metin ({d['onceki_kaynak']}): {d['eski_metin']}")
        elif ({"yeniden", "mulga"} & set(d["turler"])) and d.get("onceki_kaynak"):
            out.append(f"    Eski metin: yok ({d['onceki_kaynak']})")
        if d.get("yeni_metin"):
            out.append(f"    Yeni metin: {d['yeni_metin']}")
        if not (d.get("ibareler") or d.get("yeni_metin") or d.get("eski_metin") or d.get("kaldirilan_ibareler")):
            out.append(f"    Talimat: {d['talimat']}")
    return out

def notlara_ekle(notlar_yolu, sonuc):
    """Her kalemin notlar.md satirinin (ve altindaki alintinin) arkasina karsilastirmayi ekle."""
    if not sonuc or not notlar_yolu.exists():
        return 0
    satir = notlar_yolu.read_text(encoding="utf-8").split("\n")
    butce, eklenen = BUTCE, 0
    for k in sonuc:
        ek = satirlar(k)
        uzunluk = sum(len(x) + 1 for x in ek)
        if not ek or uzunluk > butce:
            continue
        for j, l in enumerate(satir):
            if l.startswith("- ") and l[2:].startswith(k["baslik"]):
                n = j + 1
                while n < len(satir) and satir[n].startswith("  "):
                    n += 1
                satir[n:n] = ek
                butce -= uzunluk
                eklenen += 1
                break
    notlar_yolu.write_text("\n".join(satir), encoding="utf-8")
    return eklenen

def metin_oku(ymd):
    yol = ROOT / "data" / ymd[:4] / ymd[4:6] / f"{ymd}.txt.gz"
    return gzip.open(yol, "rt", encoding="utf-8").read() if yol.exists() else None

def main(argv):
    if argv and argv[0] in ("--test", "--yerel"):
        ag = argv[0] == "--test"
        for ymd in argv[1:]:
            t = metin_oku(ymd)
            if not t:
                print(ymd, "metin yok")
                continue
            for k in isle(ymd, t, sadece_ilgi=False, ag=ag):
                print(f"\n##### {ymd} s.{k['sayfa']} [{k.get('alan') or '-'}] {k['baslik'][:110]}")
                print("  temel:", json.dumps(k.get("temel"), ensure_ascii=False)[:300], k.get("hata") or "")
                print("\n".join(satirlar(k)))
        return 0
    ymd = argv[0] if argv else ""
    if not re.fullmatch(r"\d{8}", ymd):
        print("Kullanim: karsilastir.py YYYYAAGG", file=sys.stderr)
        return 0
    t = metin_oku(ymd)
    if not t:
        print("Gunun metni yok, atlandi.")
        return 0
    sonuc = isle(ymd, t)
    if not sonuc:
        print("Ilgi alaninda degisiklik kalemi yok.")
        return 0
    cikti = ROOT / "data" / ymd[:4] / ymd[4:6] / f"{ymd}.karsilastirma.json"
    cikti.write_text(json.dumps(sonuc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    rel = os.environ.get("RG_RELEASE_DIR", "").strip()
    n = notlara_ekle(Path(rel) / "notlar.md", sonuc) if rel else 0
    print(f"{len(sonuc)} degisiklik kalemi karsilastirildi, {n} tanesi release aciklamasina eklendi.")
    for k in sonuc:
        print(f"- {k['baslik'][:100]}: " + ", ".join(
            f"M{d['madde']} {'/'.join(d['turler'])} [{d.get('onceki_kaynak') or '-'}]" for d in k["degisiklikler"]))
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:                   # gunluk isi asla durdurma
        print(f"::warning::karsilastirma basarisiz: {type(e).__name__}: {e}")
        sys.exit(0)
