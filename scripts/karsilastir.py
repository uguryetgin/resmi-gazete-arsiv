#!/usr/bin/env python3
"""Ilgi alanindaki "... Degisiklik Yapilmasina Dair ..." kalemlerinde degisen
hukumlerin ESKI ve YENI halini bulur.

rg_fetch.py'den sonra calisir. Gunun gazete metninden her degisiklik maddesini
ayristirir: hedef birim (madde, 4/C, 2.2.7, gecici/ek madde; fikra; bent), her
"X ibaresi Y" cifti kendi birimiyle, yeniden yazilan / eklenen metin, kaldirmalar.
Yeniden yazma ve kaldirmada degistirilen mevzuatin ONCEKI metnini su sirayla arar:

1. www.mevzuat.gov.tr: belgede islenen gunun (ya da daha yeni) "RG-g/a/yyyy" notu
   hic yoksa metin tam olarak degisiklikten onceki guncel haldir.
2. Bedesten (bedesten.adalet.gov.tr): degisiklikleri gec isler ama bazi metinleri
   yillardir guncellenmemistir. Son degisiklik notu (madde ve belge duzeyinde)
   mevzuat.gov.tr'deki onceki notlardan eski degilse "guncel", eskiyse "eski olabilir".

"X ibaresi Y seklinde degistirilmistir" turunde eski/yeni ibare gazetede yazilidir;
kaynak yalniz baglam (onceki cumle) icin kullanilir. Hic tahmin yapilmaz.

Ciktilar: data/YYYY/AA/YYYYAAGG[Mn].karsilastirma.json ve RG_RELEASE_DIR/notlar.md'de
ilgili kalemin altina girintili "⇄" satirlari (Routine yukune de boylece girer).
Hicbir hata gunluk isi durdurmaz; toplam sure SURE_SINIRI ile sinirlidir.

Kullanim:
  python3 scripts/karsilastir.py YYYYAAGG            # gunluk is (ilgi alani kalemleri)
  python3 scripts/karsilastir.py --test YYYYAAGG ...  # tum degisiklik kalemleri, yalniz ekrana
  python3 scripts/karsilastir.py --yerel YYYYAAGG ... # --test gibi ama ag erisimi yok
Ortam: RG_RELEASE_DIR, YENIDEN (true: bugunun karsilastirmasini bastan yap)"""
import datetime as dt
import difflib, gzip, json, os, re, sys, time
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
TRT = dt.timezone(dt.timedelta(hours=3))

MAX_KALEM = 8          # gunde en fazla bu kadar degisiklik kalemi icin kaynak sorgulanir
MAX_DEGISIKLIK = 8     # kalem basina en fazla bu kadar degisiklik maddesi
ALINTI = 600           # eski/yeni metin basina karakter
TALIMAT = 300          # gosterilen talimat uzunlugu
BUTCE = 7000           # notlar.md'ye eklenen karsilastirma satirlarinin toplam siniri
SURE_SINIRI = 240      # saniye; asilinca kalan kalemler yalniz gazeteden ayristirilir
AG_PAYI = 45           # bu kadar sure kalmadiysa yeni ag istegine baslanmaz

BIRLER = {"birinci": 1, "ikinci": 2, "üçüncü": 3, "dördüncü": 4, "beşinci": 5, "altıncı": 6,
          "yedinci": 7, "sekizinci": 8, "dokuzuncu": 9}
ONLAR = {"on": 10, "yirmi": 20, "otuz": 30}
ONLUK = {"onuncu": 10, "yirminci": 20, "otuzuncu": 30}
EK = r"(?:['’]?(?:inci|ıncı|nci|ncı|üncü|uncu|ncü|ncu))"
HARFLER = "abcçdefgğhıijklmnoöprsştuüvyz"
DEGISTIR = r"de[gğ]iştiril"
ASAGI = re.compile(r"aşa[gğ]ıdaki\s+(?:şekilde\s+" + DEGISTIR + r"|[^“.]{0,60}?eklen)")

def tr_lower(s):
    return s.replace("I", "ı").replace("İ", "i").lower()

def duz(s):
    """Satir sonu tirelemesini birlestir, bosluklari tekle."""
    s = re.sub(r"([a-zçğıöşü])-\s*\n\s*([a-zçğıöşü])", r"\1\2", s or "")
    return re.sub(r"\s+", " ", s).strip()

def kisalt(s, n=ALINTI):
    s = duz(s)
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + " …"

def tarih(ymd):
    return (int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))

# ---------------------------------------------------------------- ayristirma

def maddeler(metin):
    """Degisiklik metnini kendi maddelerine bol: [(no, metin)].
       Sinir: sirasi gelen "MADDE n-" ve son sinirdan beri tirnak dengesi <= 0, ya da
       satir basinda olup "(1)" ile baslamamasi (acik kalan OCR tirnagi sonraki
       maddeleri yutmasin; tirnak icindeki yeni "MADDE 3- (1)" sinir sayilmasin)."""
    bulunan, beklenen, son = [], 1, 0
    for m in re.finditer(r"MADDE\s*(\d+)\s*\S?\s*[-–—]", metin):   # OCR: "MADDE 8$-"
        if int(m.group(1)) != beklenen:
            continue
        derinlik = metin.count("“", son, m.start()) - metin.count("”", son, m.start())
        satir = metin[metin.rfind("\n", 0, m.start()) + 1: m.start()]
        if derinlik <= 0 or (not satir.strip() and not re.match(r"\s*\(1\)", metin[m.end():])):
            bulunan.append((beklenen, m.start()))
            beklenen += 1
            son = m.start()
    return [(no, metin[bas: bulunan[k + 1][1] if k + 1 < len(bulunan) else len(metin)])
            for k, (no, bas) in enumerate(bulunan)]

# Yururluk / yurutme maddeleri ("Bu Yönetmelik yayımı tarihinde yürürlüğe girer.")
KALIP = re.compile(r"^(?:\(\d+\)\s*)?Bu\s+\w+(?:\s+\w+)?\s+(?:.{0,120}?yürürlüğe girer|hükümlerini\b)", re.S)
# Kalemin sonuna tasan metin: bolum basliklari ya da sonraki kalemin yayimlayan kurumu
TASMA = re.compile(r"\b(?:YARGI BÖLÜMÜ|İL[ÂA]N BÖLÜMÜ|YÜRÜTME VE İDARE BÖLÜMÜ)\b|"
                   r"(?<=[.”:])\s+[A-ZÇĞİÖŞÜ][^.:“”]{2,90}(?:ndan|nden|dan|den|tan|ten):\s*(?:[A-ZÇĞİÖŞÜ]|$)")

def temel_referans(metin):
    """Degistirilen mevzuatin RG tarih/sayisi: ilk tirnaktan once gecen ilk atif."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})\s*tarihli\s*ve\s*(\d+)\s*"
                  r"(?:(?:\d+\s*\.|birinci|ikinci|üçüncü|dördüncü|beşinci)\s*)?(?:mükerrer\s*)?"
                  r"sayılı\s*Resm[iîİ]\s*Gazete", metin)
    ilk_tirnak = metin.find("“")
    if not m or (ilk_tirnak >= 0 and m.start() > ilk_tirnak):
        return None
    return {"rg_tarih": f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}", "rg_sayi": m.group(4)}

def fikra_no(yer):
    """'birinci fıkra' -> 1, 'on birinci fıkra' -> 11, 'yirminci fıkra' -> 20 (ilk gecen)."""
    m = re.search(r"(?<![a-zçğıöşü])(?:(on|yirmi|otuz)\s*)?(" + "|".join(BIRLER) + r")\s*(?:fıkra|paragraf)|"
                  r"(?<![a-zçğıöşü])(" + "|".join(ONLUK) + r")\s*(?:fıkra|paragraf)", yer)
    if not m:
        return None
    return ONLUK[m.group(3)] if m.group(3) else ONLAR.get(m.group(1), 0) + BIRLER[m.group(2)]

def hedef_birim(yer):
    """Talimat parcasindaki birim: '2.2.7 nci maddesinin', '4/C maddesinin ikinci fıkrası',
       'ek 1 inci madde', 'geçici 3 üncü madde', '(a) bendi'. Bulunamayan anahtar yoktur."""
    yer = tr_lower(re.sub(r"“[^”]{1,120}”\s*başlıklı", "başlıklı", yer))
    birim = {}
    m = re.search(rf"(\d+(?:\.\d+)+)\s*{EK}?\s*madde", yer)
    if m:
        birim["ondalik"] = m.group(1)
    else:
        m = re.search(rf"(?<![a-zçğıöşü])(geçici\s+|ek\s+)?(\d+(?:\s*/\s*[a-zçğıöşü])?)\s*{EK}?\s*madde", yer)
        if m:
            birim["madde"] = re.sub(r"\s+", "", m.group(2)).upper()
            if m.group(1):
                birim["gecici" if m.group(1).startswith("geçici") else "ek"] = True
    f = fikra_no(yer)
    if f:
        birim["fikra"] = f
    m = re.search(r"\(([a-zçğıöşü])\)\s*bend", yer)
    if m:
        birim["bent"] = m.group(1)
    return birim

def coklu_mu(yer):
    """Talimat birden fazla birime mi isaret ediyor? ('3.-9. fıkraları', '22 nci ve 28 inci maddeleri')"""
    y = tr_lower(yer)
    return bool(re.search(r"(?:fıkra|madde|bent)(?:ları|leri)", y) or
                re.search(r"(?:madde|fıkra|bend)\S*\s+ile\s+\S+\s+(?:\S+\s+)?(?:madde|fıkra|bend)", y) or
                len(re.findall(r"\([a-zçğıöşü]\)\s*bend", y)) > 1)

def birlestir(taban, yeni):
    """Talimattaki sonraki birim atfini oncekiyle birlestir (yeni madde -> sifirla, fikra -> bent'i sifirla)."""
    if "madde" in yeni or "ondalik" in yeni:
        return dict(yeni)
    b = {k: v for k, v in taban.items() if k in ("madde", "ondalik", "gecici", "ek", "fikra", "bent")}
    if "fikra" in yeni:
        b["fikra"] = yeni["fikra"]
        b.pop("bent", None)
    if "bent" in yeni:
        b["bent"] = yeni["bent"]
    return b

def son_birim(taban, parca):
    """Parcadaki yan cumleleri sirayla isleyip en son atfedilen birimi bul:
       '11 inci maddesinin birinci fıkrası ... ve üçüncü fıkrasında' -> 11. madde, 3. fıkra."""
    b = dict(taban)
    for yan in re.split(r",\s|\sve\s|;\s", parca):
        b = birlestir(b, hedef_birim(yan))
    return b

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
    asagi = ASAGI.search(govde)
    # Talimat: yeni metin blogundan (cumle bittikten sonra acilan tirnak) onceki kisim.
    # "... asagidaki sekilde degistirilmis ve ... “X” ibaresi “Y” seklinde degistirilmistir."
    # gibi cumlelerde ibare ciftleri de talimattadir.
    blok_bas = None
    if asagi:
        b = re.search(r"[.:]\s*“", govde[asagi.end():])
        blok_bas = asagi.end() + b.end() - 1 if b else None
    talimat = govde[:blok_bas] if blok_bas is not None else govde
    ilk = re.sub(r"“[^”]{1,120}”\s*başlıklı", "başlıklı", talimat)
    ilk = ilk[:ilk.find("“")] if "“" in ilk else ilk
    k = {"madde": no, "talimat": kisalt(talimat, TALIMAT), "birim": hedef_birim(ilk),
         "coklu": coklu_mu(talimat), "turler": []}

    # "X" ibaresi "Y" seklinde (listede her cift ayri; birimi kendinden onceki atiftan)
    ciftler, onceki, birim = [], 0, dict(k["birim"])
    if re.search(DEGISTIR, talimat):
        for m in re.finditer(r"“([^”]{1,300})”\s*ibare(?:si|leri)\s*“([^”]{0,300})”\s*(?:şeklinde|olarak)\b", talimat):
            birim = son_birim(birim, talimat[onceki:m.start()])
            onceki = m.end()
            ciftler.append({"eski": m.group(1), "yeni": m.group(2), "birim": dict(birim)})
    if ciftler:
        k["turler"].append("ibare")
        tekil = {}
        for c in ciftler:           # ayni cift birden cok bentte (Balon (c) ve (ç)): tek satir
            anahtar = (c["eski"], c["yeni"])
            tekil.setdefault(anahtar, {"eski": c["eski"], "yeni": c["yeni"], "birimler": []})["birimler"].append(c["birim"])
        k["ibareler"] = list(tekil.values())
    kaldirilan = re.findall(r"“([^”]{1,300})”\s*ibare(?:si|leri)\s*(?:yürürlükten\s+kald[ıi]r[ıi]l|"
                            r"metinden\s+ç[ıi]kar[ıi]l|ç[ıi]kar[ıi]l)", talimat)
    if kaldirilan:
        k["turler"].append("ibare-kaldirma")
        k["kaldirilan_ibareler"] = kaldirilan
    eklenen = re.findall(r"“([^”]{1,300})”\s*ibare(?:sinden|lerinden)\s*(?:önce|sonra)\s*gelmek\s*üzere\s*"
                         r"“([^”]{1,300})”\s*ibare(?:si|leri)\s*eklen", talimat)
    if eklenen:
        k["turler"].append("ibare-ekleme")
        k["eklenen_ibareler"] = [{"yer": a, "eklenen": b} for a, b in eklenen]
    if asagi and re.search(DEGISTIR, asagi.group(0)):
        k["turler"].append("yeniden")
        # Yeniden yazilan birim: "asagidaki" ifadesinden hemen onceki yan cumledeki atif
        yan = re.split(r",\s|\sve\s|;\s", govde[:asagi.start()])[-1]
        k["yeniden_birim"] = birlestir(k["birim"], hedef_birim(yan))
    if re.search(r"eklenmiştir|eklenmiş\b", talimat) or (asagi and "eklen" in asagi.group(0)):
        k["turler"].append("ekleme")
    if blok_bas is not None:
        y = tirnakli(govde[blok_bas:])
        if y:
            k["yeni_metin"] = kisalt(y)
    if re.search(r"(?:madde|fıkra|bent|bend|cümle)\S*\s+(?:ile\s+.{0,60})?yürürlükten\s+kald[ıi]r[ıi]lm[ıi]ş(?:t[ıi]r)?\b", talimat):
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
    return re.sub(r"\s*(?:['’]?n?[dt][ae])?\s+Değişiklik\s+Yapılmas(?:ına|ı)\s+(?:Dair|İlişkin|Hakkında).*$",
                  "", kalem_basligi).strip()

# ---------------------------------------------------------------- kaynaklar

class Kaynak:
    """Bedesten ve mevzuat.gov.tr erisimi; belge basina tek istek (onbellek), hata kaydi."""

    def __init__(self):
        import requests
        self.rq = requests
        self.bas = time.time()
        self.mg = requests.Session()
        self.kapali = set()          # zaman asimi/erisim hatasi alan kaynaklar: bu kosuda bir daha denenmez
        self.hatalar = []
        self.onbellek = {}
        self.turler = None

    def sure_doldu(self, pay=0):
        return time.time() - self.bas > SURE_SINIRI - pay

    def _hata(self, kaynak, e):
        self.hatalar.append(f"{kaynak}: {type(e).__name__ if isinstance(e, Exception) else e}")

    def bed(self, yol, data, paging=False):
        if "bedesten" in self.kapali or self.sure_doldu(AG_PAYI):
            return None
        govde = {"data": data, "applicationName": "UyapMevzuat"}
        if paging:
            govde["paging"] = True
        for deneme in range(2):
            try:
                r = self.rq.post(BED + yol, json=govde, headers=BED_H, timeout=(10, 30))
            except Exception as e:
                if deneme:
                    self._hata("bedesten", e)
                    self.kapali.add("bedesten")
                    return None
                time.sleep(2)
                continue
            if r.status_code >= 500 or r.status_code == 429:
                if deneme:
                    self._hata("bedesten", f"HTTP {r.status_code}")
                    return None
                time.sleep(3)
                continue
            try:
                j = r.json()
            except Exception as e:
                self._hata("bedesten", e)
                return None
            meta = j.get("metadata") or {}
            if meta.get("FMTY", "SUCCESS") != "SUCCESS":
                self._hata("bedesten", f"FMTY={meta.get('FMTY')}")
                return None
            return j.get("data")
        return None

    def bed_ara(self, ref, baslik):
        """Temel mevzuati Bedesten'de RG sayisi ve baslikla bul (Jaccard >= 0.8, numara eslesmesi)."""
        if self.turler is None:
            d = self.bed("mevzuatTypes", {})
            self.turler = [t.get("mevzuatTur") for t in (d if isinstance(d, list) else []) if t.get("mevzuatTur")]
        satirlar = []
        for tur_filtresi in ([self.turler] if self.turler else []) + [None]:
            for sayfa in range(1, 6):
                filtre = {"pageSize": 20, "pageNumber": sayfa, "resmiGazeteSayisi": ref["rg_sayi"]}
                if tur_filtresi:
                    filtre["mevzuatTurList"] = tur_filtresi
                parca = liste_bul(self.bed("searchDocuments", filtre, paging=True) or {})
                satirlar += parca
                if len(parca) < 20:
                    break
            if satirlar:
                break
        hedef_n = rt.norm(baslik)
        hedef = set(w for w in hedef_n.split() if len(w) > 2)
        no = re.search(r"\bNO\s+(\d+(?:\s\d+)?)\b", hedef_n)   # "Tebliğ No: 2024/8" -> "2024 8"
        en_iyi, puan = None, 0.0
        for s in satirlar:
            ad = rt.norm(str(s.get("mevzuatAdi") or ""))
            if "DEGISIKLIK YAPILMASINA" in ad or str(s.get("resmiGazeteSayisi")) != ref["rg_sayi"]:
                continue
            if no and not re.search(r"\bNO\s+" + re.escape(no.group(1)) + r"\b", ad):
                continue
            if " ".join(ad.split()) == " ".join(hedef_n.split()):
                return s
            kel = set(w for w in ad.split() if len(w) > 2)
            p = len(hedef & kel) / max(1, len(hedef | kel))
            if p > puan:
                en_iyi, puan = s, p
        return en_iyi if puan >= 0.8 else None

    def bed_metin(self, mid):
        import base64
        anahtar = ("bed", mid)
        if anahtar not in self.onbellek:
            d = self.bed("getDocumentContent", {"documentType": "MEVZUAT", "id": str(mid)}) or {}
            icerik = d.get("content") if isinstance(d, dict) else None
            metin = None
            if icerik:
                ham = base64.b64decode(icerik)
                if ham[:4] != b"%PDF":
                    for enc in ("utf-8", "windows-1254"):
                        try:
                            metin = html_satirlar(ham.decode(enc))
                            break
                        except UnicodeDecodeError:
                            continue
            self.onbellek[anahtar] = metin
        return self.onbellek[anahtar]

    def mg_metin(self, url):
        """mevzuat.gov.tr/mevzuat?MevzuatNo=..&MevzuatTur=..&MevzuatTertip=.. -> iframe HTML metni."""
        if not url:
            return None
        anahtar = ("mg", url)
        if anahtar in self.onbellek:
            return self.onbellek[anahtar]
        self.onbellek[anahtar] = None
        if "mevzuat.gov.tr" in self.kapali or self.sure_doldu(AG_PAYI):
            return None
        q = {k.lower(): v[0] for k, v in parse_qs(urlparse(url).query).items()}
        if not {"mevzuatno", "mevzuattur", "mevzuattertip"} <= q.keys():
            return None
        iframe = (f"{MG}/anasayfa/MevzuatFihristDetayIframe?MevzuatTur={q['mevzuattur']}"
                  f"&MevzuatNo={q['mevzuatno']}&MevzuatTertip={q['mevzuattertip']}")
        try:
            r = self.mg.get(iframe, timeout=(10, 20), headers={
                "User-Agent": BROWSER_UA, "Referer": MG + "/", "Accept-Language": "tr-TR,tr;q=0.9"})
        except Exception as e:
            self._hata("mevzuat.gov.tr", e)
            self.kapali.add("mevzuat.gov.tr")
            return None
        if r.status_code != 200:
            self._hata("mevzuat.gov.tr", f"HTTP {r.status_code}")
            return None
        if re.search(r"404\s*[-–—]?\s*Sayfa\s+Bulunamad", r.text):
            return None
        self.onbellek[anahtar] = html_satirlar(r.content.decode(r.encoding or "utf-8", "replace"))
        return self.onbellek[anahtar]

def liste_bul(j):
    if isinstance(j, list) and j and isinstance(j[0], dict):
        return j
    if isinstance(j, dict):
        for v in j.values():
            r = liste_bul(v)
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

MADDE_BASI = r"(?mi)^\s*(?:GEÇİCİ\s+|EK\s+)?MADDE\s+\d+[^\n]{0,4}[-–—]"

def _baslik_at(parca):
    """Sonraki maddenin basligi (or. 'Yürürlük', 'Tip Emisyon Belgesi') parcanin sonuna tasmasin."""
    return re.sub(r"\n(?![a-zçğıöşü]\)|\(\d+\))[A-ZÇĞİÖŞÜ][^\n.,:;|]{0,59}\n?$", "\n", parca)

def madde_parcasi(metin, birim):
    """Kaynak metinden maddeyi (ya da ondalik birimi) cikar; bulunamazsa None."""
    if "ondalik" in birim:
        no = birim["ondalik"]
        m = re.search(rf"(?m)^\s*{re.escape(no)}(?:\.|\s*[-–—]|\s*\()", metin)
        if not m:
            return None
        k = len(no.split("."))
        s = re.search(r"(?m)^\s*\d+(?:\.\d+){%d,%d}\.(?=\s|\()" % (1, max(1, k - 1)), metin[m.end():])
        return _baslik_at(metin[m.start(): m.end() + (s.start() if s else 3000)])
    if "madde" not in birim:
        return None
    no = birim["madde"].replace("/", r"\s*/\s*")
    # Satir basi zorunlu: duz "MADDE 5" aramasi "GEÇİCİ MADDE 5" / "EK MADDE 5" ile karismaz
    onek = r"GEÇİCİ\s+" if birim.get("gecici") else r"EK\s+" if birim.get("ek") else r""
    m = re.search(rf"(?m)^\s*{onek}MADDE\s+{no}\b\s*[-–—]", metin, re.I)
    if not m:
        return None
    s = re.search(MADDE_BASI, metin[m.end():])
    return _baslik_at(metin[m.start(): m.end() + (s.start() if s else 5000)])

def birim_ayikla(metin, birim):
    """Kaynak metinden hedef birimi (madde > fikra > bent) cikar; bulunamazsa None.
       Fikra/bent isaretleri yalniz satir basinda (ya da madde tiresinden hemen sonra)
       sayilir: metin icindeki '(2) numaralı', dipnot '(4)' kesme noktasi olmaz."""
    if not metin:
        return None
    parca = madde_parcasi(metin, birim)
    if parca is None or "ondalik" in birim:
        return parca
    if "fikra" in birim:
        f = birim["fikra"]
        bas = rf"(?m)(?:^[ \t]*|^[ \t]*(?:GEÇİCİ\s+|EK\s+)?MADDE\s+[^\n]{{0,12}}?[-–—][ \t]*)"
        a = re.search(bas + rf"\({f}\)(?=\s|\(|$)", parca, re.I)
        if a:
            fa = parca.find(f"({f})", a.start())
            b = re.search(rf"(?m)^[ \t]*\({f + 1}\)(?=\s|\(|$)", parca[fa + 1:])
            parca = parca[fa: fa + 1 + (b.start() if b else len(parca))]
    if "bent" in birim:
        h = birim["bent"]
        a = re.search(rf"(?m)^[ \t]*{re.escape(h)}\)\s", parca)
        if a:
            sonraki = HARFLER[HARFLER.index(h) + 1] if h in HARFLER[:-1] else None
            b = re.search(rf"(?m)^[ \t]*{sonraki}\)\s", parca[a.end():]) if sonraki else None
            parca = parca[a.start(): a.end() + (b.start() if b else len(parca))]
    return parca

def notlar_(metin):
    """Metindeki 'RG-g/a/yyyy' degisiklik notlari: [(yyyy, a, g)] sirali."""
    return sorted({(int(y), int(a), int(g)) for g, a, y in re.findall(r"RG[-\s]*(\d{1,2})/(\d{1,2})/(\d{4})", metin or "")})

def son_not(metin, sinir):
    """sinir tarihinden onceki en son not; yoksa (0, 0, 0)."""
    return max([n for n in notlar_(metin) if n < sinir] or [(0, 0, 0)])

def islenmis(metin, sinir):
    """Metin islenen gunun (ya da daha yeni) bir degisikligini tasiyor mu?"""
    return any(n >= sinir for n in notlar_(metin))

def onceki_birim(kaynak, satir, birim, ymd):
    """(metin, etiket) ya da (None, sebep). Sirayla mevzuat.gov.tr, Bedesten."""
    sinir = tarih(ymd)
    mg = kaynak.mg_metin(satir.get("url"))
    mg_birim = birim_ayikla(mg, birim) if mg else None
    if mg_birim and not islenmis(mg, sinir):
        return mg_birim, "mevzuat.gov.tr (değişiklikten önceki güncel metin)"
    bed = kaynak.bed_metin(satir.get("mevzuatId"))
    bed_birim = birim_ayikla(bed, birim) if bed else None
    if bed_birim and not islenmis(bed, sinir):
        if not mg:
            return bed_birim, "Bedesten (güncelliği doğrulanamadı)"
        madde = {k: v for k, v in birim.items() if k in ("madde", "ondalik", "gecici", "ek")}
        mg_madde, bed_madde = madde_parcasi(mg, madde) or "", madde_parcasi(bed, madde) or ""
        guncel = son_not(bed_madde, sinir) >= son_not(mg_madde, sinir) and son_not(bed, sinir) >= son_not(mg, sinir)
        if guncel:
            return bed_birim, "Bedesten (güncel)"
        t = lambda n: f"{n[2]}/{n[1]}/{n[0]}" if n != (0, 0, 0) else "yok"
        return bed_birim, (f"Bedesten (eski olabilir: son değişiklik notu {t(son_not(bed, sinir))}, "
                           f"mevzuat.gov.tr'de {t(son_not(mg, sinir))})")
    if mg_birim or bed_birim:
        return None, "kaynaklar değişikliği zaten işlemiş"
    if (satir.get("url") and mg is None) or bed is None:
        if kaynak.hatalar:
            return None, "kaynaklara erişilemedi"
    return None, "hedef birim kaynaklarda bulunamadı"

NOT_KALIBI = re.compile(r"\((?:Değişik|Ek|Mülga)[^)]*RG-[^)]*\)(?:\(\d+\)|\[\d+\])?", re.I)

def _normal(s):
    return duz(NOT_KALIBI.sub("", (s or "").replace("’", "'").replace("‘", "'")))

def baglam(metin, ifade, pay=160):
    """Eski ibarenin kaynaktaki cumlesi. Tam eslesme yoksa (OCR bozuk ibare) benzerlik >= 0.9."""
    d = _normal(metin)
    kucuk, hedef = tr_lower(d), tr_lower(_normal(ifade))
    if not hedef:
        return None, None
    m = re.search(r"(?<![\wçğıöşü])" + re.escape(hedef) + r"(?![\wçğıöşü])", kucuk)
    bas, son, kaynaktaki = (m.start(), m.end(), None) if m else (None, None, None)
    if bas is None and len(d) <= 6000 and len(hedef) >= 8:
        en, n = (0.0, None), len(hedef)
        for i in range(0, max(1, len(kucuk) - n + 1)):
            for w in (n - 3, n, n + 3):
                parca = kucuk[i:i + w]
                sm = difflib.SequenceMatcher(None, hedef, parca)
                if sm.quick_ratio() >= 0.9:
                    r = sm.ratio()
                    if r > en[0]:
                        en = (r, (i, i + w))
        if en[0] >= 0.9:
            bas, son = en[1]
            kaynaktaki = d[bas:son]
    if bas is None:
        return None, None
    return ("…" if bas > pay else "") + d[max(0, bas - pay): son + pay] + "…", kaynaktaki

# ---------------------------------------------------------------- ana akis

def birim_adi(b):
    b, p = b or {}, []
    if "ondalik" in b:
        p.append(f"{b['ondalik']} madde")
    elif "madde" in b:
        p.append(("geçici " if b.get("gecici") else "ek " if b.get("ek") else "") + f"{b['madde']}. madde")
    if "fikra" in b:
        p.append(f"{b['fikra']}. fıkra")
    if "bent" in b:
        p.append(f"({b['bent']}) bendi")
    return ", ".join(p) or "hedef belirsiz"

def talimat_goster(d):
    """Ayristirmanin kacirmis olabilecegi islem varsa talimatin kendisi de gosterilir."""
    t = d["talimat"]
    fiiller = len(set(re.findall(r"(de[gğ]iştiril|kald[ıi]r[ıi]l|eklen|ç[ıi]kar[ıi]l)", t)))
    ibare_sayisi = len(re.findall(r"ibare(?:si|leri)\b", t))
    yakalanan = (sum(len(c["birimler"]) for c in d.get("ibareler", [])) + len(d.get("kaldirilan_ibareler", []))
                 + len(d.get("eklenen_ibareler", [])))
    birimler = {json.dumps(b, sort_keys=True) for c in d.get("ibareler", []) for b in c["birimler"]}
    return (fiiller > 1 or ibare_sayisi > yakalanan or d.get("coklu") or len(birimler) > 1
            or "diger" in d["turler"])

def isle(ymd, text, sadece_ilgi=True, ag=True, kaynak=None):
    pages = rg.sayfalar(text)
    kalemler, ilan = rg.icindekiler_kalemleri(rg.fihrist(text))
    if not kalemler:
        return []
    son_sayfa = (ilan or max(pages)) - 1
    alanlar, haric = rt.ilgi_alanlari() if (ROOT / "ilgi.txt").exists() else ([], None)
    if ag and kaynak is None:
        kaynak = Kaynak()
    sonuc = []
    for i, (_, baslik, s) in enumerate(kalemler):
        if not re.search(r"Değişiklik\s+Yapılmas", baslik):
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
        k["kesilen"] = max(0, len(k["degisiklikler"]) - MAX_DEGISIKLIK)
        k["degisiklikler"] = k["degisiklikler"][:MAX_DEGISIKLIK]
        if ag and k["temel"] and not kaynak.sure_doldu(AG_PAYI):
            try:
                kaynakla(kaynak, k, ymd)
            except Exception as e:           # kaynak hatasi karsilastirmayi durdurmasin
                k["hata"] = f"{type(e).__name__}: {str(e)[:120]}"
        sonuc.append(k)
    return sonuc

def kaynakla(kaynak, k, ymd):
    """Yeniden yazma / kaldirma icin onceki metin, ibare ciftleri icin kaynaktaki cumle."""
    gerekli = [d for d in k["degisiklikler"]
               if ({"yeniden", "mulga"} & set(d["turler"]) and not d.get("coklu")) or d.get("ibareler")]
    if not gerekli:
        return
    satir = kaynak.bed_ara(k["temel"], k["temel_baslik"])
    if not satir:
        k["temel"]["bulunamadi"] = True
        return
    k["temel"].update({"mevzuatId": satir.get("mevzuatId"), "url": satir.get("url"),
                       "ad": duz(str(satir.get("mevzuatAdi") or ""))})
    for d in gerekli:
        if kaynak.sure_doldu(AG_PAYI):
            break
        if {"yeniden", "mulga"} & set(d["turler"]) and not d.get("coklu"):
            hedef = d.get("yeniden_birim") or d["birim"]
            if hedef:
                eski, etiket = onceki_birim(kaynak, satir, hedef, ymd)
                d["onceki_kaynak"] = etiket
                if eski is not None:
                    d["eski_metin"] = kisalt(eski)
        for c in d.get("ibareler", []):
            b = c["birimler"][0]
            eski, etiket = onceki_birim(kaynak, satir, b, ymd) if b else (None, None)
            if eski is not None:
                c["baglam"], c["kaynaktaki"] = baglam(eski, c["eski"])
                c["baglam_kaynak"] = etiket

def satirlar(k):
    """notlar.md'de kalemin altina eklenecek girintili satirlar."""
    out = []
    for d in k["degisiklikler"]:
        tur = "/".join(d["turler"])
        hedef = d.get("yeniden_birim") or d["birim"]
        out.append(f"  ⇄ Değişiklik MADDE {d['madde']} → {birim_adi(hedef)}"
                   + (" (birden fazla birim)" if d.get("coklu") else "") + f" [{tur}]")
        if talimat_goster(d):
            out.append(f"    Talimat: {d['talimat']}")
        for c in d.get("ibareler", []):
            yer = " ve ".join(birim_adi(b) for b in c["birimler"])
            out.append(f"    Eski: “{c['eski']}” → Yeni: “{c['yeni']}” ({yer})")
            if c.get("kaynaktaki"):
                out.append(f"    Kaynakta yazılışı: “{c['kaynaktaki']}”")
            if c.get("baglam"):
                out.append(f"    Önceki cümle ({c.get('baglam_kaynak')}): {c['baglam']}")
        for e in d.get("kaldirilan_ibareler", []):
            out.append(f"    Kaldırılan ibare: “{e}”")
        for e in d.get("eklenen_ibareler", []):
            out.append(f"    Eklenen ibare: “{e['eklenen']}” (“{e['yer']}” ibaresine bitişik)")
        if d.get("eski_metin"):
            out.append(f"    Eski metin ({d['onceki_kaynak']}): {d['eski_metin']}")
        elif ({"yeniden", "mulga"} & set(d["turler"])) and d.get("onceki_kaynak"):
            out.append(f"    Eski metin: yok ({d['onceki_kaynak']})")
        if d.get("yeni_metin"):
            out.append(f"    Yeni metin: {d['yeni_metin']}")
    if k.get("kesilen"):
        out.append(f"  ⇄ … ve {k['kesilen']} değişiklik maddesi daha (gazete metnine bakın)")
    return out

def notlara_ekle(notlar_yolu, sonuc, dosya=""):
    """Her kalemin notlar.md satirinin altindaki duz alintiyi karsilastirma blogu ile degistir.
       Butce asilirsa blok kesilir ve devaminin JSON dosyasinda oldugu yazilir."""
    if not sonuc or not notlar_yolu.exists():
        return 0
    satir = notlar_yolu.read_text(encoding="utf-8").split("\n")
    butce, eklenen = BUTCE, 0
    for k in sonuc:
        ek = satirlar(k)
        if not ek or butce <= 200:
            continue
        j = next((j for j, l in enumerate(satir) if l.startswith("- ") and l[2:].startswith(k["baslik"])), None)
        if j is None:
            continue
        sigan, uzunluk = [], 0
        for x in ek:
            if uzunluk + len(x) + 1 > butce - 120:
                sigan.append(f"    … (devamı {dosya or 'karşılaştırma dosyasında'})")
                break
            sigan.append(x)
            uzunluk += len(x) + 1
        # Kalemin duz alintisi ayni bilgiyi tasir: karsilastirma blogu onun yerini alir (token)
        n = j + 1
        while n < len(satir) and satir[n].startswith("  "):
            n += 1
        satir[j + 1:n] = sigan
        butce -= uzunluk
        eklenen += 1
    notlar_yolu.write_text("\n".join(satir), encoding="utf-8")
    return eklenen

def metin_oku(ymd, ek=""):
    yol = ROOT / "data" / ymd[:4] / ymd[4:6] / f"{ymd}{ek}.txt.gz"
    return gzip.open(yol, "rt", encoding="utf-8").read() if yol.exists() else None

def gunluk(ymd):
    """Ana sayi ve (varsa) mukerrerler; onceden yapilmis karsilastirmayi korur."""
    klasor = ROOT / "data" / ymd[:4] / ymd[4:6]
    bugun = dt.datetime.now(TRT).strftime("%Y%m%d")
    yeniden = os.environ.get("YENIDEN", "").strip().lower() in ("1", "true", "yes", "evet")
    rel = os.environ.get("RG_RELEASE_DIR", "").strip()
    notlar = Path(rel) / "notlar.md" if rel else None
    kaynak = Kaynak()
    for ek in [""] + [p.name[len(ymd):-len(".txt.gz")] for p in sorted(klasor.glob(f"{ymd}M*.txt.gz"))]:
        t = metin_oku(ymd, ek)
        if not t:
            continue
        cikti = klasor / f"{ymd}{ek}.karsilastirma.json"
        # Kaynaklar degisikligi isledikten sonra (gecmis gun, mukerrer icin tekrar kosum)
        # yeniden sorgulamak eski metni kaybettirir: kayitli sonuc korunur.
        if cikti.exists() and not (yeniden and ymd == bugun):
            sonuc = json.loads(cikti.read_text(encoding="utf-8"))
            print(f"{cikti.name} korundu ({len(sonuc)} kalem).")
        else:
            sonuc = isle(ymd, t, kaynak=kaynak)
            if not sonuc:
                continue
            cikti.write_text(json.dumps(sonuc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            for k in sonuc:
                print(f"- {k['baslik'][:100]}: " + ", ".join(
                    f"M{d['madde']} {'/'.join(d['turler'])} [{d.get('onceki_kaynak') or '-'}]"
                    for d in k["degisiklikler"]))
        if notlar:
            n = notlara_ekle(notlar, sonuc, f"data/{ymd[:4]}/{ymd[4:6]}/{cikti.name}")
            print(f"{ymd}{ek}: {len(sonuc)} degisiklik kalemi, {n} tanesi release aciklamasina eklendi.")
    for h in sorted(set(kaynak.hatalar)):
        print(f"::warning::karsilastirma kaynak hatasi: {h}")

def main(argv):
    if argv and argv[0] in ("--test", "--yerel"):
        ag = argv[0] == "--test"
        for ymd in argv[1:]:
            t = metin_oku(ymd)
            if not t:
                print(ymd, "metin yok")
                continue
            kaynak = Kaynak() if ag else None
            for k in isle(ymd, t, sadece_ilgi=False, ag=ag, kaynak=kaynak):
                print(f"\n##### {ymd} s.{k['sayfa']} [{k.get('alan') or '-'}] {k['baslik'][:110]}")
                print("  temel:", json.dumps(k.get("temel"), ensure_ascii=False)[:300], k.get("hata") or "")
                print("\n".join(satirlar(k)))
            if kaynak and kaynak.hatalar:
                print("  kaynak hatalari:", sorted(set(kaynak.hatalar)))
        return 0
    ymd = argv[0] if argv else ""
    if not re.fullmatch(r"\d{8}", ymd):
        print("Kullanim: karsilastir.py YYYYAAGG", file=sys.stderr)
        return 0
    gunluk(ymd)
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:                   # gunluk isi asla durdurma
        print(f"::warning::karsilastirma basarisiz: {type(e).__name__}: {e}")
        sys.exit(0)
