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
kaynak yalniz baglam (onceki cumle) icin kullanilir. Yeniden yazilan birimin eski ve
yeni metni birlikte bulunursa kelime duzeyinde degisen kisimlar ("Fark") da yazilir.
Istenen birim (fikra, bent, alt bent, cumle) kaynakta ayrilamazsa bu etikette soylenir.
Hic tahmin yapilmaz.

Ciktilar: data/YYYY/AA/YYYYAAGG[Mn].karsilastirma.json ve RG_RELEASE_DIR/notlar.md'de
ilgili kalemin altina girintili "⇄" karsilastirma tablosu (Madde | Eski | Yeni; kodla, tahminsiz
uretilir, Routine yukune de boylece girer), ardindan baglam ve talimat satirlari.
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
TALIMAT = 450          # gosterilen talimat uzunlugu
TALIMAT_UZUN = 900     # ayristirmanin eksik kalmis olabilecegi (talimati gosterilen) maddelerde
BUTCE = 7000           # notlar.md'ye eklenen karsilastirma satirlarinin toplam siniri
SURE_SINIRI = 240      # saniye; asilinca kalan kalemler yalniz gazeteden ayristirilir
AG_PAYI = 45           # bu kadar sure kalmadiysa yeni ag istegine (ya da yeniden denemeye) baslanmaz;
                       # tek istegin en kotu suresinden (10 + 30 sn) buyuk olmali

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
       satir basinda olup talimat gibi baslamasi ("Aynı ...", "g/a/yyyy tarihli", "Bu ...")
       (acik kalan OCR tirnagi sonraki maddeleri yutmasin; tirnak icindeki yeni
       "MADDE 3- (1)" sinir sayilmasin)."""
    bulunan, beklenen, son = [], 1, 0
    for m in re.finditer(r"MADDE\s*(\d+)\s*\S?\s*[-–—]", metin):   # OCR: "MADDE 8$-"
        if int(m.group(1)) != beklenen:
            continue
        derinlik = metin.count("“", son, m.start()) - metin.count("”", son, m.start())
        satir = metin[metin.rfind("\n", 0, m.start()) + 1: m.start()]
        talimat_gibi = re.match(r"\s*(?:Aynı\b|\d{1,2}/\d{1,2}/\d{4}|Bu\s)", metin[m.end():])
        if derinlik <= 0 or (not satir.strip() and talimat_gibi):
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
    yer = re.sub(r"“[^”]{1,120}”\s*başlıklı", "başlıklı", yer)
    # Degistirilen mevzuatin basligindaki atiflar ("Kanununun 3 üncü Maddesinin (g) Bendi Kapsamında")
    # buyuk harfle yazilir; talimattaki atif ("7 nci maddesinin") kucuk harflidir.
    yer = tr_lower(re.sub(r"\d+\s*(?:['’]?\w+)?\s+Madde[a-zçğıöşü]*(?:\s+\([a-zçğıöşü]\)\s+Bend[a-zçğıöşü]*)?",
                          " ", yer))
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
    m = re.search(r"\((\d+)\)\s*numaralı\s*alt\s*bend", yer)
    if m:
        birim["alt"] = int(m.group(1))
    sira = "|".join(BIRLER)
    m1 = re.search(r"(?<![a-zçğıöşü])(son|ilk)\s+(iki|üç|dört|beş)\s*cümle", yer)
    m2 = re.search(rf"(?<![a-zçğıöşü])({sira})\s*(?:,\s*({sira})\s*)?ve\s*({sira})\s*cümle", yer)
    m = re.search(rf"(?<![a-zçğıöşü])(son|{sira})\s*cümle", yer)
    if m1:
        birim["cumle"] = f"{m1.group(1)} {m1.group(2)} cümle"
    elif m2:
        birim["cumle"] = " ve ".join(f"{BIRLER[g]}." for g in m2.groups() if g) + " cümleler"
    elif m:
        birim["cumle"] = "son" if m.group(1) == "son" else BIRLER[m.group(1)]
    return birim

def coklu_mu(yer):
    """Talimat birden fazla birime mi isaret ediyor? ('3.-9. fıkraları', '22 nci ve 28 inci maddeleri')"""
    y = tr_lower(yer)
    return bool(re.search(r"(?:fıkra|madde|bent|cümle)(?:ları|leri)", y) or
                re.search(r"(?:madde|fıkra|bend)\S*\s+(?:ile|ve)\s+(?:\S+\s+){0,3}?(?:madde|fıkra|bend)", y) or
                len(re.findall(r"\([a-zçğıöşü]\)\s*bend", y)) > 1)

def birlestir(taban, yeni):
    """Talimattaki sonraki birim atfini oncekiyle birlestir (yeni madde -> sifirla, fikra -> bent'i sifirla)."""
    if "madde" in yeni or "ondalik" in yeni:
        return dict(yeni)
    if not yeni:
        return dict(taban)
    b = {k: v for k, v in taban.items() if k in ("madde", "ondalik", "gecici", "ek", "fikra", "bent")}
    if "fikra" in yeni:
        b["fikra"] = yeni["fikra"]
        b.pop("bent", None)
    if "bent" in yeni:
        b["bent"] = yeni["bent"]
    for k in ("alt", "cumle"):
        if k in yeni:
            b[k] = yeni[k]
    return b

def son_birim(taban, parca):
    """Parcadaki yan cumleleri sirayla isleyip en son atfedilen birimi bul:
       '11 inci maddesinin birinci fıkrası ... ve üçüncü fıkrasında' -> 11. madde, 3. fıkra."""
    b = dict(taban)
    for yan in re.split(r",\s|\sve\s|;\s", parca):
        b = birlestir(b, hedef_birim(yan))
    return b

def ilk_birim(parca):
    """Birim atfi iceren ilk yan cumlenin birimi. '11 inci maddesinin birinci fıkrasına ... eklenmiş,
       ikinci fıkrasının ikinci cümlesi ...' -> 11. madde, 1. fıkra (2. cümle ikinci yan cumleye ait)."""
    for yan in re.split(r",\s|\sve\s|;\s", parca):
        b = hedef_birim(yan)
        if "madde" in b or "ondalik" in b:
            return b
    return hedef_birim(parca)

# Madde/fikra metni olmayan nesneler: ek, cizelge, tablo, harita ("ekinde yer alan", "EK-2'si", "Çizelge 1")
NESNE = re.compile(r"(?<![a-zçğıöşü])(?:ek(?:i|in|inde|leri|lerinde|te)?\b|ek\s*-\s*\d)|çizelge|tablo|cetvel|harita|liste|\bform")

def tirnakli(s):
    """Ilk “ ile son ” arasi (icte tirnak olabilir)."""
    a, b = s.find("“"), s.rfind("”")
    return s[a + 1:b] if a >= 0 and b > a else None

def cumle_sonu(govde, bas):
    """bas'tan sonra tirnak disindaki ilk cumle sonu ('.' ya da ardindan tirnak gelen ':'); yoksa None.
       Tirnak icindeki nokta ("… serbesttir.” ibaresi") ve rakam arasi nokta (2.2.12) sayilmaz."""
    derin = 0
    for i in range(bas, len(govde)):
        ch = govde[i]
        if ch == "“":
            derin += 1
        elif ch == "”":
            derin = max(0, derin - 1)
        elif derin == 0 and ch in ".:":
            if govde[i - 1:i].isdigit() and govde[i + 1:i + 2].isdigit():
                continue
            if ch == "." or re.match(r":\s*“", govde[i:]):
                return i
    return None

def blok_basi(govde, bas):
    """Yeni metin blogunun acilis tirnagi: talimat cumlesinin hemen ardindaki “ (OCR noktayi
       dusurduyse fiilin hemen ardindaki “). Cumle bitip tirnak gelmiyorsa (tablo) None."""
    e = cumle_sonu(govde, bas)
    if e is not None:
        b = re.match(r"[.:]\s*“", govde[e:])
        if b:
            return e + b.end() - 1
    b = re.match(r"\S*\s*“", govde[bas:])
    return bas + b.end() - 1 if b else None

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
    blok_bas = blok_basi(govde, asagi.end()) if asagi else None
    talimat = govde[:blok_bas] if blok_bas is not None else govde
    if asagi and blok_bas is None:      # tirnaksiz yeni icerik (tablo): talimat cumle sonunda biter
        e = cumle_sonu(govde, asagi.end())
        talimat = govde[:e + 1] if e is not None else talimat
    ilk = re.sub(r"“[^”]{1,120}”\s*başlıklı", "başlıklı", talimat)
    ilk = ilk[:ilk.find("“")] if "“" in ilk else ilk
    k = {"madde": no, "talimat": kisalt(talimat, TALIMAT), "birim": ilk_birim(ilk),
         "coklu": coklu_mu(talimat), "turler": []}

    # "X" ibaresi "Y" seklinde (listede her cift ayri; birimi kendinden onceki atiftan)
    ciftler, onceki, birim = [], 0, dict(k["birim"])
    if re.search(DEGISTIR, talimat):
        for m in re.finditer(r"“([^”]{1,300})”\s*ibare(si|leri)\s*“([^”]{0,300})”\s*(?:şeklinde|olarak)\b", talimat):
            birim = son_birim(birim, talimat[onceki:m.start()])
            onceki = m.end()
            ciftler.append({"eski": m.group(1), "yeni": m.group(3), "birim": dict(birim),
                            "cogul": m.group(2) == "leri"})
    if ciftler:
        k["turler"].append("ibare")
        tekil = {}
        for c in ciftler:           # ayni cift birden cok bentte (Balon (c) ve (ç)): tek satir
            anahtar = (c["eski"], c["yeni"])
            t = tekil.setdefault(anahtar, {"eski": c["eski"], "yeni": c["yeni"], "birimler": [], "cogul": False})
            t["birimler"].append(c["birim"])
            t["cogul"] = t["cogul"] or c["cogul"]
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
        # Atif yoksa (ek, harita) ilk yan cumlenin maddesi miras alinmaz; ek/cizelge/tablo
        # yeniden yaziliyorsa birim metni aranmaz (maddenin eski metni o nesnenin eski hali degildir).
        yan = re.split(r",\s|\sve\s|;\s", govde[:asagi.start()])[-1]
        yb = hedef_birim(yan)
        k["yeniden_birim"] = birlestir(k["birim"], yb) if yb else {}
        if NESNE.search(tr_lower(yan)):
            k["nesne"] = True
    if re.search(r"eklenmiştir|eklenmiş\b", talimat) or (asagi and "eklen" in asagi.group(0)):
        k["turler"].append("ekleme")
        # Eklenen yer: ilk "asagidaki ... eklen" fiiline kadar atiflar ("aynı fıkraya" onceki fikradir)
        em = re.search(r"aşa[gğ]ıdaki\s+[^“.]{0,60}?eklen", talimat) or re.search(r"eklenmiş", talimat)
        if em:
            k["ekleme_birim"] = son_birim(k["birim"], talimat[:em.end()])
    if blok_bas is not None:
        y = tirnakli(govde[blok_bas:])
        if y:
            k["yeni_metin"] = kisalt(y)
            k["_yeni_tam"] = y
    mk = re.search(r"(?:madde|fıkra|bent|bend|cümle)\S*\s+(?:ile\s+.{0,60})?yürürlükten\s+kald[ıi]r[ıi]lm[ıi]ş(?:t[ıi]r)?\b", talimat)
    if mk:
        k["turler"].append("mulga")
        # Kaldirilan birim: kaldirma fiilinin kendi yan cumlesindeki atif (ilk yan cumle degil)
        yan = re.split(r",\s|\sve\s|;\s", talimat[:mk.end()])[-1]
        mb = hedef_birim(yan)
        k["mulga_birim"] = son_birim(k["birim"], talimat[:mk.end()]) if mb else {}
        if NESNE.search(tr_lower(yan)):
            k["nesne"] = True
    if not k["turler"]:
        k["turler"].append("diger")
    k["talimat_goster"] = talimat_goster(k, talimat)
    k["talimat"] = kisalt(talimat, TALIMAT_UZUN if k["talimat_goster"] else TALIMAT)
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
        self.atlandi = set()         # sure yetmedigi icin sorgulanmayan kaynaklar
        self.hatalar = []
        self.onbellek = {}
        self.turler = None

    def sure_doldu(self, pay=0):
        return time.time() - self.bas > SURE_SINIRI - pay

    def _hata(self, kaynak, e):
        self.hatalar.append(f"{kaynak}: {type(e).__name__ if isinstance(e, Exception) else e}")

    def bed(self, yol, data, paging=False):
        if "bedesten" in self.kapali:
            return None
        if self.sure_doldu(AG_PAYI):
            self.atlandi.add("bedesten")
            return None
        govde = {"data": data, "applicationName": "UyapMevzuat"}
        if paging:
            govde["paging"] = True
        for deneme in range(2):
            try:
                r = self.rq.post(BED + yol, json=govde, headers=BED_H, timeout=(10, 30))
            except Exception as e:
                if deneme or self.sure_doldu(AG_PAYI):
                    self._hata("bedesten", e)
                    self.kapali.add("bedesten")
                    return None
                time.sleep(2)
                continue
            if r.status_code >= 500 or r.status_code == 429:
                if deneme or self.sure_doldu(AG_PAYI):
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
        """Temel mevzuati Bedesten'de RG sayisi ve baslikla bul. Birebir baslik yoksa farkli kelimelerin
           hepsinin karsi tarafta yalniz yazim farki kadar yakin (benzerlik >= 0.85, uzunluk farki <= 2)
           bir karsiligi olmali: 'Yönetmeliği'/'Yönetmelik' olur, ayni sayida yayimlanan 'Lisans' /
           'Lisansüstü' ya da 'Çocuk' / 'Kadın' yonetmelikleri olmaz. Esitlikte bulunmaz."""
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
        en_iyi, puan, esit = None, -1.0, False
        yakin = lambda w, kume: any(abs(len(w) - len(x)) <= 2 and difflib.SequenceMatcher(None, w, x).ratio() >= 0.85
                                    for x in kume)
        for s in satirlar:
            ad = rt.norm(str(s.get("mevzuatAdi") or ""))
            if "DEGISIKLIK YAPILMASINA" in ad or str(s.get("resmiGazeteSayisi")) != ref["rg_sayi"]:
                continue
            if no and not re.search(r"\bNO\s+" + re.escape(no.group(1)) + r"\b", ad):
                continue
            if " ".join(ad.split()) == " ".join(hedef_n.split()):
                return s
            kel = set(w for w in ad.split() if len(w) > 2)
            if not (all(yakin(w, kel) for w in hedef - kel) and all(yakin(w, hedef) for w in kel - hedef)):
                continue
            p = len(hedef & kel) / max(1, len(hedef | kel))
            if p > puan:
                en_iyi, puan, esit = s, p, False
            elif p == puan and en_iyi is not None:
                esit = True
        return en_iyi if en_iyi is not None and not esit else None

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
        if "mevzuat.gov.tr" in self.kapali:
            return None
        if self.sure_doldu(AG_PAYI):
            self.atlandi.add("mevzuat.gov.tr")
            del self.onbellek[anahtar]       # sonraki (sureli) kosuda yeniden denenebilsin
            return None
        q = {k.lower(): v[0] for k, v in parse_qs(urlparse(url).query).items()}
        if not {"mevzuatno", "mevzuattur", "mevzuattertip"} <= q.keys():
            return None
        iframe = (f"{MG}/anasayfa/MevzuatFihristDetayIframe?MevzuatTur={q['mevzuattur']}"
                  f"&MevzuatNo={q['mevzuatno']}&MevzuatTertip={q['mevzuattertip']}")
        for deneme in range(3):
            try:
                r = self.mg.get(iframe, timeout=(8, 20), headers={
                    "User-Agent": BROWSER_UA, "Referer": MG + "/", "Accept-Language": "tr-TR,tr;q=0.9"})
                break
            except Exception as e:
                # Sunucu yeni baglantilarin bir kismini rastgele dusuruyor (GitHub runner'da
                # olculdu; ardindan gelen istek basarili). Baglanti kurulamadiysa yeniden
                # denenir; okuma zaman asimi ya da ucuncu hata: bu kosuda kaynak kapatilir.
                if (deneme < 2 and type(e).__name__ in ("ConnectTimeout", "ConnectionError")
                        and not self.sure_doldu(AG_PAYI)):
                    time.sleep(3)
                    continue
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
    """Sonraki maddenin basligi ve bolum basliklari (or. 'İKİNCİ BÖLÜM / Denetim ve Yaptırımlar /
       Denetim') parcanin sonuna tasmasin: noktalama ile bitmeyen, isaretle baslamayan kisa son
       satirlar tek tek atilir."""
    while True:
        yeni = re.sub(r"\n(?![a-zçğıöşü]\)|\(\d+\)|\d+\))[A-ZÇĞİÖŞÜ][^\n|\d=()]{0,79}(?<![.:;,])[ \t]*\n?$", "\n", parca)
        if yeni == parca:
            return parca
        parca = yeni

def madde_parcasi(metin, birim):
    """Kaynak metinden maddeyi (ya da ondalik birimi) cikar; bulunamazsa None."""
    if "ondalik" in birim:
        no = birim["ondalik"]
        m = re.search(rf"(?m)^\s*{re.escape(no)}(?:\.(?!\d)|\s*[-–—]|\s*\()", metin)
        if not m:
            return None
        k = len(no.split("."))
        s = re.search(r"(?m)^\s*\d+(?:\.\d+){%d,%d}\.(?=\s|\()|^\s*\d+\.\s+[A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜ ,]{3,}$"
                      % (1, max(1, k - 1)), metin[m.end():])
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

def fikra_isareti(metin, i):
    """metin[i]'deki '(n)' gercek fikra basi mi? Atif ('(2) numaralı fıkrası') ya da cumle ortasinda
       kirilan satirin basi degil: onceki satir noktalama ile (ya da madde tiresi/notla) bitmeli."""
    if re.match(r"\(\d+\)\s*(?:numaralı|sayılı|inci|nci|ncı|üncü|uncu|ncü|ncu|ıncı)\b", metin[i:]):
        return False
    once = metin[:i].rstrip(" \t")
    if not once or not once.endswith("\n"):
        return True                 # madde tiresinden hemen sonra
    onceki_satir = once.rstrip()
    return not onceki_satir or onceki_satir[-1] in ".:;)”\"" or re.search(r"[-–—]$", onceki_satir) is not None

def birim_ayikla(metin, birim):
    """Kaynak metinden hedef birimi (madde > fikra > bent > alt bent) cikar; bulunamazsa None."""
    return birim_ayikla_tam(metin, birim)[0]

def birim_ayikla_tam(metin, birim):
    """(parca, eksik, bulunan): eksik, istenip metinde ayrilamayan alt birimlerin adlari
       (or. numarasiz fikrali eski yonetmelikte '3. fıkra', her zaman 'N. cümle'); bulunan,
       parcanin gercekte karsiladigi birim. Fikra/bent isaretleri yalniz satir basinda (ya da
       madde tiresinden hemen sonra) sayilir: metin icindeki '(2) numaralı', dipnot '(4)'
       kesme noktasi olmaz."""
    if not metin:
        return None, [], {}
    parca = madde_parcasi(metin, birim)
    if parca is None:
        return None, [], {}
    bulunan = {k: v for k, v in birim.items() if k in ("madde", "ondalik", "gecici", "ek")}
    eksik = []
    if "fikra" in birim:
        f = birim["fikra"]
        not_ = r"(?:\([^)\n]*RG-[^)\n]*\)(?:\(\d+\)|\[\d+\])?[ \t]*)*"   # "(Değişik:RG-..)(3) "
        bas = rf"(?m)(?:^[ \t]*|^[ \t]*(?:GEÇİCİ\s+|EK\s+)?MADDE\s+[^\n]{{0,12}}?[-–—][ \t]*{not_})"
        a = next((x for x in re.finditer(bas + rf"(\({f}\))(?=\s|\(|$)", parca, re.I)
                  if fikra_isareti(parca, x.start(1))), None)
        if a:
            fa = a.start(1)
            b = next((x for x in re.finditer(rf"(?m)^[ \t]*(\({f + 1}\))(?=\s|\(|$)", parca[fa + 1:])
                      if fikra_isareti(parca, fa + 1 + x.start(1))), None)
            parca = parca[fa: fa + 1 + (b.start() if b else len(parca))]
            bulunan["fikra"] = f
        elif f == 1 and "bent" in birim:
            pass        # numarasiz paragraf (or. MASAK 2.2.11): ilk "a)" zaten birinci paragraftadir
        else:
            # fikra ayrilamadiysa bent/alt bent harfi baska fikradan gelebilir: daha fazla daraltilmaz
            eksik.append(f"{f}. fıkra")
            eksik += [birim_adi({k: birim[k]}) for k in ("bent", "alt", "cumle") if k in birim]
            return parca, eksik, bulunan
    if "bent" in birim:
        h = birim["bent"]
        a = re.search(rf"(?m)^[ \t]*{re.escape(h)}\)\s", parca)
        if a:
            sonraki = HARFLER[HARFLER.index(h) + 1] if h in HARFLER[:-1] else None
            b = re.search(rf"(?m)^[ \t]*{sonraki}\)\s", parca[a.end():]) if sonraki else None
            parca = parca[a.start(): a.end() + (b.start() if b else len(parca))]
            bulunan["bent"] = h
        else:
            eksik += [birim_adi({k: birim[k]}) for k in ("bent", "alt", "cumle") if k in birim]
            return parca, eksik, bulunan
    if "alt" in birim:
        n = birim["alt"]
        a = re.search(rf"(?m)^[ \t]*\(?{n}\)\s", parca)
        if a:
            b = re.search(rf"(?m)^[ \t]*\(?{n + 1}\)\s", parca[a.end():])
            parca = parca[a.start(): a.end() + (b.start() if b else len(parca))]
            bulunan["alt"] = n
        else:
            eksik.append(birim_adi({"alt": n}))
    if "cumle" in birim:        # cumleler guvenilir bolunemez (kisaltma noktalari): birim butun verilir
        eksik.append(birim_adi({"cumle": birim["cumle"]}))
    return parca, eksik, bulunan

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
    """(metin, etiket, not) ya da (None, sebep, None). Sirayla mevzuat.gov.tr, Bedesten.
       not: istenen birim ayrilamadiysa metnin gercekte hangi birim oldugu (yoksa None).
       Etiketler: 'güncel' = notlar karsilikli dogrulandi; 'kısmen doğrulandı' = mevzuat.gov.tr
       maddeyi bugun degistirdigi icin maddenin onceki notlari orada gorulemiyor; 'eski olabilir'
       = diger kaynakta daha yeni degisiklik notu var; 'güncelliği doğrulanamadı' = karsilastirma yok."""
    sinir = tarih(ymd)
    t = lambda n: f"{n[2]}/{n[1]}/{n[0]}" if n != (0, 0, 0) else "yok"
    madde = {k: v for k, v in birim.items() if k in ("madde", "ondalik", "gecici", "ek")}

    def sonuc(metin, etiket, eksik, bulunan):
        return metin, etiket, (f"{', '.join(eksik)} kaynakta ayrılamadı, {birim_adi(bulunan)} gösteriliyor"
                               if eksik else None)

    mg = kaynak.mg_metin(satir.get("url"))
    mg_birim, mg_eksik, mg_bulunan = birim_ayikla_tam(mg, birim)
    mg_madde = (madde_parcasi(mg, madde) or "") if mg else ""
    bed = None
    if mg_birim and not islenmis(mg, sinir):
        # mevzuat.gov.tr de gecikebilir (ayni mevzuat kisa arayla iki kez degisti): Bedesten'de
        # daha yeni bir not varsa metin eski olabilir.
        bed = kaynak.bed_metin(satir.get("mevzuatId"))
        bed_madde = (madde_parcasi(bed, madde) or "") if bed else ""
        if bed and (son_not(bed_madde, sinir) > son_not(mg_madde, sinir) or son_not(bed, sinir) > son_not(mg, sinir)):
            return sonuc(mg_birim, (f"mevzuat.gov.tr (eski olabilir: son değişiklik notu {t(son_not(mg, sinir))}, "
                                    f"Bedesten'de {t(son_not(bed, sinir))})"), mg_eksik, mg_bulunan)
        return sonuc(mg_birim, "mevzuat.gov.tr (değişiklikten önceki güncel metin)", mg_eksik, mg_bulunan)
    if bed is None:
        bed = kaynak.bed_metin(satir.get("mevzuatId"))
    bed_birim, bed_eksik, bed_bulunan = birim_ayikla_tam(bed, birim)
    if bed_birim and not islenmis(bed, sinir):
        if not mg or not mg_madde:      # mevzuat.gov.tr yok ya da gelen sayfa bu belge degil
            return sonuc(bed_birim, "Bedesten (güncelliği doğrulanamadı)", bed_eksik, bed_bulunan)
        bed_madde = madde_parcasi(bed, madde) or ""
        guncel = son_not(bed_madde, sinir) >= son_not(mg_madde, sinir) and son_not(bed, sinir) >= son_not(mg, sinir)
        if guncel:
            etiket = "Bedesten (kısmen doğrulandı)" if islenmis(mg_madde, sinir) else "Bedesten (güncel)"
            return sonuc(bed_birim, etiket, bed_eksik, bed_bulunan)
        return sonuc(bed_birim, (f"Bedesten (eski olabilir: son değişiklik notu {t(son_not(bed, sinir))}, "
                                 f"mevzuat.gov.tr'de {t(son_not(mg, sinir))})"), bed_eksik, bed_bulunan)
    if mg_birim and bed is None:
        return None, "mevzuat.gov.tr değişikliği işlemiş, Bedesten sorgulanamadı", None
    if mg_birim or bed_birim:
        return None, "kaynaklar değişikliği zaten işlemiş", None
    if kaynak.atlandi and (mg is None or bed is None):
        return None, "süre doldu, kaynak sorgulanmadı", None
    if (satir.get("url") and mg is None) or bed is None:
        if kaynak.hatalar:
            return None, "kaynaklara erişilemedi", None
    return None, "hedef birim kaynaklarda bulunamadı", None

SAYI = (r"(?:\d|bir|iki|üç|dört|beş|altı|yedi|sekiz|dokuz|on|yirmi|otuz|kırk|elli|altmış|yetmiş|seksen|"
        r"doksan|yüz|bin|milyon|milyar)")

NOT_KALIBI = re.compile(r"\([^()]*?(?:değişik|ek|mülga|iptal)[^()]*?RG-[^)]*\)(?:\(\d+\)|\[\d+\])?", re.I)

def _normal(s):
    return duz(NOT_KALIBI.sub("", (s or "").replace("’", "'").replace("‘", "'")))

def baglam(metin, ifade, pay=160):
    """(onceki cumle, kaynaktaki yazilis, tam eslesme sayisi). Tam eslesme yoksa (OCR bozuk ibare)
       benzerlik >= 0.9 ve ayni rakamlar."""
    d = _normal(metin)
    kucuk, hedef = tr_lower(d), tr_lower(_normal(ifade))
    if not hedef:
        return None, None, 0
    gecis, atlanan = [], False
    for aday in re.finditer(r"(?<![\wçğıöşü])" + re.escape(hedef) + r"(?![\wçğıöşü])", kucuk):
        once = kucuk[:aday.start()].split()[-1:] or [""]
        if re.match(SAYI, hedef) and (re.fullmatch(SAYI + r"|[\d.,]+", once[0])):
            atlanan = True          # "bin TL'yi" -> "on bin TL'yi" icindeki gecis baska tutardir
            continue
        gecis.append(aday)
    m = gecis[0] if gecis else None
    bas, son, kaynaktaki = (m.start(), m.end(), None) if m else (None, None, None)
    if bas is None and not atlanan and len(d) <= 6000 and len(hedef) >= 8:
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
            while bas > 0 and not kucuk[bas - 1].isspace():
                bas -= 1
            while son < len(kucuk) and not kucuk[son].isspace():
                son += 1
            # "200.000" ile "125.000" benzer gorunur ama farkli ibaredir: rakamlar birebir ayni olmali
            if re.findall(r"\d+", hedef) == re.findall(r"\d+", kucuk[bas:son]):
                kaynaktaki = d[bas:son].strip("…")
            else:
                bas = None
    if bas is None:
        return None, None, 0
    return ("…" if bas > pay else "") + d[max(0, bas - pay): son + pay] + "…", kaynaktaki, len(gecis)

FARK_ORAN = 0.5        # benzerlik bundan dusukse metin bastan yazilmistir, fark gosterilmez
FARK_PARCA = 4         # gosterilen en fazla degisen kisim
FARK_UZUNLUK = 160     # kisim basina karakter

def fark(eski, yeni):
    """Eski ve yeni metnin degisen kisimlari: ([(eski_parca, yeni_parca)], kuyruk) ya da (None, False).
       Degisiklik notlari, tirnak/kesme, kelime kenarindaki noktalama ve şapka farki yok sayilir
       (rakamlar arasindaki '/', '.', ',' korunur: 1/11 ile 11/1, %2,5 ile %25 farklidir); yakin
       kisimlar birlesir. Eski metnin sonunda yalniz eskide olan kisim (birim sinirinin tasmasi
       olabilir) Fark sayilmaz; kuyruk=True dondurulur."""
    def kelimeler(s):
        out = []
        for w in _normal(s).split():
            a = re.sub(r"[’'‘”“\"*]", "", tr_lower(w).translate(str.maketrans("âîû", "aiu")))
            a = re.sub(r"^[^\wçğıöşü%]+|[^\wçğıöşü%]+$", "", a)
            if a:
                out.append((a, w))
        return out
    a, b = kelimeler(eski), kelimeler(yeni)
    if not a or not b:
        return None, False
    sm = difflib.SequenceMatcher(None, [x for x, _ in a], [x for x, _ in b], autojunk=False)
    if sm.ratio() < FARK_ORAN:
        return None, False
    parcalar, kuyruk = [], False
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        if op == "delete" and i2 == len(a) and j1 == len(b):
            kuyruk = True
            continue
        if "".join(x for x, _ in a[i1:i2]) == "".join(x for x, _ in b[j1:j2]):
            continue            # yalniz bosluk farki ("En az" / "Enaz")
        if parcalar and i1 - parcalar[-1][1] <= 2 and j1 - parcalar[-1][3] <= 2:
            parcalar[-1][1], parcalar[-1][3] = i2, j2
        else:
            parcalar.append([i1, i2, j1, j2])
    if not parcalar:
        return None, kuyruk
    out = []
    for i1, i2, j1, j2 in parcalar:
        i1, j1 = max(0, i1 - 2), max(0, j1 - 2)     # onceki iki (ortak) kelime yeri gostersin
        e = " ".join(w for _, w in a[i1:i2])
        y = " ".join(w for _, w in b[j1:j2])
        out.append((kisalt(e, FARK_UZUNLUK), kisalt(y, FARK_UZUNLUK)))
    return out, kuyruk

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
    if "alt" in b:
        p.append(f"({b['alt']}) numaralı alt bendi")
    if "cumle" in b:
        c = b["cumle"]
        p.append("son cümle" if c == "son" else f"{c}. cümle" if isinstance(c, int) else c)
    return ", ".join(p) or "hedef belirsiz"

def talimat_goster(d, talimat=None):
    """Ayristirmanin kacirmis olabilecegi islem varsa talimatin kendisi de gosterilir.
       Karar kisaltilmamis talimat uzerinden verilir (ayristirma aninda; kayitli JSON'da hazir)."""
    if talimat is None:
        if "talimat_goster" in d:
            return d["talimat_goster"]
        talimat = d["talimat"]
    t = talimat
    fiiller = len(set(re.findall(r"(de[gğ]iştiril|kald[ıi]r[ıi]l|eklen|ç[ıi]kar[ıi]l)", t)))
    ibare_sayisi = len(re.findall(r"ibare(?:si|leri)\b", t))
    yakalanan = (sum(len(c["birimler"]) for c in d.get("ibareler", [])) + len(d.get("kaldirilan_ibareler", []))
                 + len(d.get("eklenen_ibareler", [])))
    birimler = {json.dumps(b, sort_keys=True) for c in d.get("ibareler", []) for b in c["birimler"]}
    # Yakalanmayan tirnakli ifade (liste bicimli ekleme/kaldirma, "Lagos" satiri, basliklar)
    yakalanan_ifade = ({c["eski"] for c in d.get("ibareler", [])} | {c["yeni"] for c in d.get("ibareler", [])}
                       | set(d.get("kaldirilan_ibareler", []))
                       | {e[x] for e in d.get("eklenen_ibareler", []) for x in ("yer", "eklenen")})
    kalan = [q for q in re.findall(r"“([^”]{1,300})”(?!\s*başlıklı)", t) if q not in yakalanan_ifade]
    return bool(fiiller > 1 or ibare_sayisi > yakalanan or d.get("coklu") or len(birimler) > 1
                or "diger" in d["turler"] or kalan
                or d.get("nesne")
                or re.search(r"başlığı|satırı|sütun|dipnot|(?:madde|fıkra|bent)\S*\s+(?:önce|sonra)\s+gelmek\s+üzere", t))

def isle(ymd, text, sadece_ilgi=True, ag=True, kaynak=None):
    pages = rg.sayfalar(text)
    ocr = {int(n) for n in re.findall(r"=== Sayfa (\d+) \(OCR\) ===", text)}
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
        bitis = kalemler[i + 1][2] if i + 1 < len(kalemler) else son_sayfa
        k.update({"baslik": baslik, "sayfa": s, "alan": alan, "temel_baslik": temel_baslik(baslik),
                  "ocr": any(n in ocr for n in range(s, min(max(bitis, s), s + 4) + 1))})
        k["kesilen"] = max(0, len(k["degisiklikler"]) - MAX_DEGISIKLIK)
        k["degisiklikler"] = k["degisiklikler"][:MAX_DEGISIKLIK]
        if ag and k["temel"] and not kaynak.sure_doldu(AG_PAYI):
            try:
                kaynakla(kaynak, k, ymd)
            except Exception as e:           # kaynak hatasi karsilastirmayi durdurmasin
                k["hata"] = f"{type(e).__name__}: {str(e)[:120]}"
        for d in k["degisiklikler"]:
            d.pop("_yeni_tam", None)
        sonuc.append(k)
    return sonuc

def hedef_birimi(d):
    """Eski metni aranacak birim: yeniden yazilan ya da kaldirilan birim. Birden cok birim, ek/cizelge
       gibi madde disi nesne ya da kendi atfi olmayan yan cumle icin None (tahmin yapilmaz)."""
    if d.get("coklu") or d.get("nesne"):
        return None
    if "yeniden" in d["turler"]:
        return d.get("yeniden_birim") or None
    if "mulga" in d["turler"]:
        return d.get("mulga_birim") or None
    return None

def kaynakla(kaynak, k, ymd):
    """Yeniden yazma / kaldirma icin onceki metin, ibare ciftleri icin kaynaktaki cumle."""
    gerekli = [d for d in k["degisiklikler"] if hedef_birimi(d) or d.get("ibareler")]
    if not gerekli:
        return
    satir = kaynak.bed_ara(k["temel"], k["temel_baslik"])
    if not satir:
        if "bedesten" in kaynak.atlandi:
            k["temel"]["atlandi"] = True
            sebep = "süre doldu, kaynak sorgulanmadı"
        elif "bedesten" in kaynak.kapali or any(h.startswith("bedesten") for h in kaynak.hatalar):
            k["temel"]["erisilemedi"] = True
            sebep = "kaynaklara erişilemedi"
        else:
            k["temel"]["bulunamadi"] = True
            sebep = "değiştirilen mevzuat kaynaklarda bulunamadı"
        for d in gerekli:
            if hedef_birimi(d):
                d["onceki_kaynak"] = sebep
        return
    k["temel"].update({"mevzuatId": satir.get("mevzuatId"), "url": satir.get("url"),
                       "ad": duz(str(satir.get("mevzuatAdi") or ""))})
    for d in gerekli:
        if kaynak.sure_doldu(AG_PAYI):
            break
        hedef = hedef_birimi(d)
        if hedef:
            eski, etiket, eksik = onceki_birim(kaynak, satir, hedef, ymd)
            d["onceki_kaynak"] = etiket + (f"; {eksik}" if eksik else "")
            if eski is not None:
                d["eski_metin"] = kisalt(eski)
                # Fark yalniz birim tam ayrildiysa. Gazete sayfasi OCR'liysa okuma hatalari degisiklik
                # gibi gorunebilir: Fark "(OCR, doğrulayın)" diye isaretlenir, eski/yeni metin kisaltilmaz.
                # Blokta birden cok tirnakli parca varsa ilki yeniden yazilan birimdir (ASAGI ilk
                # "asagidaki sekilde degistirilmis"i bulur).
                if not eksik and d.get("_yeni_tam") and "yeniden" in d["turler"]:
                    f, kuyruk = fark(eski, re.split(r"”\s*“", d["_yeni_tam"])[0])
                    if f:
                        d["fark"] = f
                        if kuyruk:
                            d["fark_kuyruk"] = True
                        if k.get("ocr"):
                            d["fark_ocr"] = True
        for c in d.get("ibareler", []):
            b = c["birimler"][0]
            eski, etiket, eksik = onceki_birim(kaynak, satir, b, ymd) if b else (None, None, None)
            if eski is None:
                continue
            baglam_, kaynaktaki, adet = baglam(eski, c["eski"])
            c["baglam_kaynak"] = etiket
            if adet > 1 and (eksik or not c.get("cogul", True)):
                # Tekil "ibaresi" birimde birden cok geciyor ya da birim ayrilamadi: hangi gecisin
                # degistigi bilinemez; yanlis cumle gostermektense gosterme
                c["belirsiz"] = True
            elif baglam_:
                c["baglam"], c["kaynaktaki"] = baglam_, kaynaktaki
            else:
                c["kaynakta_yok"] = etiket

HUCRE = 160            # tablo hucresi basina karakter
BAGLAM = 220           # "Bağlam" satiri basina karakter

def kisa_birim(b):
    """Tablo icin kisa birim: '9/1-g-1', '2.2.7 f.1', '4/C f.2', 'geçici 3', '11/2, son cümle'."""
    b = b or {}
    s = b.get("ondalik") or ((("geçici " if b.get("gecici") else "ek " if b.get("ek") else "") + b["madde"])
                             if "madde" in b else "")
    if "fikra" in b:
        s += f"/{b['fikra']}" if s and not re.search(r"[./]", s) else f" f.{b['fikra']}"
    if "bent" in b:
        s += f"-{b['bent']}"
    if "alt" in b:
        s += f"-{b['alt']}"
    if "cumle" in b:
        c = b["cumle"]
        s += ", " + ("son cümle" if c == "son" else f"{c}. cümle" if isinstance(c, int) else c)
    return s.strip(" ,") or "birim belirsiz"

def kaynak_notu(etiket):
    """Kaynak etiketinden Eski hucresine eklenecek kisa not ('' = guvenilir)."""
    if not etiket:
        return ""
    notlar_ = []
    if "eski olabilir" in etiket:
        notlar_.append("kaynak eski olabilir")
    elif "kısmen doğrulandı" in etiket:
        notlar_.append("kısmen doğrulandı")
    elif "doğrulanamadı" in etiket:
        notlar_.append("doğrulanamadı")
    m = re.search(r"; (.+?) kaynakta ayrılamadı, (.+?) gösteriliyor", etiket)
    if m:
        notlar_.append(f"{m.group(2)} metni; {m.group(1)} ayrılamadı")
    return f" ({'; '.join(notlar_)})" if notlar_ else ""

def _hucre(x):
    return (x or "").replace("|", "/").strip()

def _bloklar(d):
    """Yeni metnin tirnakli parcalari (yeniden yazma + ekleme ayni blokta olabilir)."""
    return [b.strip() for b in re.split(r"”\s*“", d.get("yeni_metin") or "") if b.strip()]

def eklenen_birim(birim, bloklar):
    """Eklenen metnin kendi numarasi: 'MADDE 6/A-' -> '6/A (yeni madde)', '(14) …' -> '13/14 (yeni fıkra)',
       'ş) …' -> '17/1-ş (yeni bent)'. Numara okunamazsa talimattaki birim + '(ekleme)'."""
    ilk = bloklar[0] if bloklar else ""
    m = re.search(r"\b(GEÇİCİ\s+|EK\s+)?MADDE\s+(\d+(?:\s*/\s*[A-ZÇĞİÖŞÜ])?)\s*[-–—]", ilk[:200])
    if m:
        onek = {"G": "geçici ", "E": "ek "}.get((m.group(1) or " ")[0], "")
        no = re.sub(r"\s+", "", m.group(2))
        return f"{onek}{no} (yeni {'geçici ' if onek == 'geçici ' else 'ek ' if onek == 'ek ' else ''}madde)"
    m = re.match(r"\((\d+)\)\s", ilk)
    if m and ("madde" in birim or "ondalik" in birim):
        return f"{kisa_birim({k: v for k, v in birim.items() if k in ('madde', 'ondalik', 'gecici', 'ek')})}/{m.group(1)} (yeni fıkra)"
    m = re.match(r"([a-zçğıöşü])\)\s", ilk)
    if m and "fikra" in birim:
        return f"{kisa_birim({k: v for k, v in birim.items() if k in ('madde', 'ondalik', 'gecici', 'ek', 'fikra')})}-{m.group(1)} (yeni bent)"
    return kisa_birim({k: v for k, v in birim.items() if k not in ("cumle", "alt")}) + " (ekleme)"

def tablo_satirlari(d):
    """Bir degisiklik maddesinin tablo satirlari: [(birim, eski, yeni)]. Gazete ciftleri kesin; kaynaktan
       gelen eski metin notuyla; bulunamayan ya da ayrıştırılamayan kisim '—' ile gosterilir."""
    coklu = d.get("coklu")
    yer = lambda b: "birden fazla birim" if coklu and not b else kisa_birim(b)
    out = []
    for c in d.get("ibareler", []):
        eski = f"“{c['eski']}”"
        if c.get("kaynaktaki"):
            eski += f" (kaynakta: “{c['kaynaktaki']}”)"
        elif c.get("kaynakta_yok"):
            eski += f" (kaynakta birebir bulunamadı{'; ' + kaynak_notu(c['kaynakta_yok']).strip(' ()') if kaynak_notu(c['kaynakta_yok']) else ''})"
        elif c.get("belirsiz"):
            eski += " (birimde birden çok geçiyor)"
        out.append((" ve ".join(kisa_birim(b) for b in c["birimler"]), eski, f"“{c['yeni']}”"))
    for e in d.get("kaldirilan_ibareler", []):
        out.append(("birden fazla birim" if coklu else yer(d["birim"]), f"“{e}”", "— (kaldırıldı)"))
    for e in d.get("eklenen_ibareler", []):
        out.append(("birden fazla birim" if coklu else yer(d["birim"]), "—",
                    f"“{e['eklenen']}” (“{e['yer']}” ibaresine bitişik)"))
    bloklar = _bloklar(d)
    not_ = kaynak_notu(d.get("onceki_kaynak"))
    if "yeniden" in d["turler"]:
        hedef = "birden fazla birim" if coklu else yer(d.get("yeniden_birim") or d["birim"])
        if d.get("fark"):
            f = d["fark"][:FARK_PARCA]
            fazla = f" (+{len(d['fark']) - FARK_PARCA} kısım)" if len(d["fark"]) > FARK_PARCA else ""
            eski = " / ".join(f"“{a}”" if a else "—" for a, _ in f) + fazla + not_
            yeni = " / ".join(f"“{y}”" if y else "— (çıkarıldı)" for _, y in f) + fazla
            if d.get("fark_ocr"):
                yeni += " (OCR, doğrulayın)"
            if d.get("fark_kuyruk"):
                eski += " (eski metnin sonu ayrıca değişmiş olabilir)"
        elif d.get("eski_metin"):
            eski = kisalt(_normal(d["eski_metin"]), HUCRE) + not_
            yeni = kisalt(bloklar[0], HUCRE) if bloklar else "— (bkz. talimat)"
        else:
            eski = (f"— ({d['onceki_kaynak']})" if d.get("onceki_kaynak")
                    else "— (eski metin aranmadı)" if hedef_birimi(d) else "— (bkz. talimat)")
            yeni = kisalt(bloklar[0], HUCRE) if bloklar else "— (bkz. talimat)"
        out.append((hedef, eski, yeni))
        bloklar = bloklar[1:]
    if "mulga" in d["turler"]:
        hedef = "birden fazla birim" if coklu else yer(d.get("mulga_birim") or d["birim"])
        if d.get("eski_metin") and "yeniden" not in d["turler"]:
            eski = kisalt(_normal(d["eski_metin"]), HUCRE) + not_
        elif d.get("onceki_kaynak") and "yeniden" not in d["turler"]:
            eski = f"— ({d['onceki_kaynak']})"
        elif hedef_birimi(d) and "yeniden" not in d["turler"]:
            eski = "— (eski metin aranmadı)"
        else:
            eski = "— (bkz. talimat)"
        out.append((hedef, eski, "— (kaldırıldı)"))
    if "ekleme" in d["turler"]:
        out.append(("birden fazla birim" if coklu else eklenen_birim(d.get("ekleme_birim") or d["birim"], bloklar), "—",
                    kisalt(" ".join(bloklar), HUCRE) if bloklar else "— (bkz. talimat)"))
    if not out:
        out.append((yer(d["birim"]), "—", "— (bkz. talimat)"))
    return [tuple(_hucre(x) for x in r) for r in out]

def satirlar(k):
    """notlar.md'de kalemin altina eklenecek girintili satirlar: bir 'Madde | Eski | Yeni' tablosu
       (kodla, tahminsiz uretilir; Routine onu aynen aktarir), ardindan baglam ve talimat satirlari."""
    if not k["degisiklikler"]:
        return []
    out = ["  ⇄ Eski / yeni karşılaştırması", "  | Madde | Eski | Yeni |", "  |---|---|---|"]
    notlar_, gorulen = [], set()
    for d in k["degisiklikler"]:
        for r in tablo_satirlari(d):
            out.append(f"  | {r[0]} | {r[1]} | {r[2]} |")
        for c in d.get("ibareler", []):
            yer = " ve ".join(kisa_birim(b) for b in c["birimler"])
            if c.get("baglam") and yer not in gorulen:      # birim basina tek baglam
                gorulen.add(yer)
                notlar_.append(f"  Bağlam ({yer}): {kisalt(c['baglam'], BAGLAM)}")
        if talimat_goster(d):
            notlar_.append(f"  Talimat (MADDE {d['madde']}): {d['talimat']}")
    if k.get("kesilen"):
        notlar_.append(f"  +{k['kesilen']} değişiklik maddesi tabloda yok (gazete metnine bakın)")
    # GFM'de tablodan hemen sonraki satir da tablo satiri sayilir: araya (liste ogesi icinde kalan) bos satir
    return out + (["  "] + notlar_ if notlar_ else [])

def bolum_araligi(satir, mukerrer=None):
    """notlar.md'de ana sayinin (mukerrer=None) ya da n. mukerrerin '## n. Mükerrer' bolumunun satir araligi."""
    son = next((j for j, l in enumerate(satir) if l == "---"), len(satir))
    muk = [(j, int(m.group(1))) for j, l in enumerate(satir) if (m := re.match(r"## (\d+)\. Mükerrer\s*$", l))]
    if mukerrer is None:
        return 0, muk[0][0] if muk else son
    for i, (j, n) in enumerate(muk):
        if n == mukerrer:
            return j, muk[i + 1][0] if i + 1 < len(muk) else son
    return 0, 0

def notlara_ekle(notlar_yolu, sonuc, dosya="", butce=BUTCE, mukerrer=None):
    """Her kalemin notlar.md satirinin altindaki duz alintiyi karsilastirma blogu ile degistir.
       Butce (ayni notlar.md'ye yazan ana sayi ve mukerrerler icin ortak) asilirsa blok kesilir ve
       devaminin JSON dosyasinda oldugu yazilir; ilk degisiklik bile sigmiyorsa alinti kalir, altina
       yalniz dosyanin yeri eklenir. Kalem, kendi bolumunde (ana sayi / n. mukerrer) tam
       '- Baslik (s. N)' satiriyla bulunur; ayni baslik iki kez varsa ikincisi sonraki satira gider.
       (eklenen, kalan_butce) dondurur."""
    if not sonuc or not notlar_yolu.exists():
        return 0, butce
    satir = notlar_yolu.read_text(encoding="utf-8").split("\n")
    eklenen, yer, kullanilan = 0, dosya or "karşılaştırma dosyasında", []
    for k in sonuc:
        ek = satirlar(k)
        if not ek:
            continue
        bas, bit = bolum_araligi(satir, mukerrer)
        tam = f"- {k['baslik']} (s. {k.get('sayfa')})"
        adaylar = [j for j in range(bas, bit) if satir[j] == tam and j not in kullanilan]
        if not adaylar:
            ayni = [j for j in range(bas, bit) if satir[j].startswith(f"- {k['baslik']}")]
            adaylar = [j for j in ayni if j not in kullanilan] if len(ayni) == 1 else []
        if not adaylar:
            continue
        j = adaylar[0]
        n = j + 1
        while n < len(satir) and satir[n].startswith("  "):
            n += 1
        ilk = min(len(ek), 4)          # "⇄ …", tablo basligi, ayrac ve ilk satir
        boy = lambda xs: sum(len(x) + 1 for x in xs)
        if boy(ek) <= butce:
            sigan = ek
        elif boy(ek[:ilk]) <= butce - 120:
            sigan = []
            for x in ek:
                if boy(sigan) + len(x) + 1 > butce - 120:
                    break
                sigan.append(x)
            sigan.append(f"    … (devamı {yer})")
        else:
            isaret = f"  ⇄ karşılaştırma: {yer}"
            if len(isaret) + 1 <= butce:
                satir[n:n] = [isaret]
                kullanilan = [u + 1 if u > j else u for u in kullanilan] + [j]
                butce -= len(isaret) + 1
            continue
        # Kalemin duz alintisi ayni bilgiyi tasir: karsilastirma blogu onun yerini alir (token)
        fark_ = len(sigan) - (n - j - 1)
        kullanilan = [u + fark_ if u > j else u for u in kullanilan] + [j]
        satir[j + 1:n] = sigan
        butce -= boy(sigan)
        eklenen += 1
    notlar_yolu.write_text("\n".join(satir), encoding="utf-8")
    return eklenen, butce

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
    kaynak, butce = Kaynak(), BUTCE
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
                if k.get("hata"):
                    print(f"::warning::karsilastirma kalem hatasi ({k['baslik'][:60]}): {k['hata']}")
                print(f"- {k['baslik'][:100]}: " + ", ".join(
                    f"M{d['madde']} {'/'.join(d['turler'])} [{d.get('onceki_kaynak') or '-'}]"
                    for d in k["degisiklikler"]))
        if notlar:
            n, butce = notlara_ekle(notlar, sonuc, f"data/{ymd[:4]}/{ymd[4:6]}/{cikti.name}", butce,
                                    int(ek[1:]) if ek else None)
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
