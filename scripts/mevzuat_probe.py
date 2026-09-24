#!/usr/bin/env python3
"""Degisiklik yapilan mevzuatin ONCEKI halini hangi kaynaktan alabildigimizi dener.

Yalniz test icindir: data/ altina bir sey yazmaz. Sonuclari ekrana ve
probe_report.json'a yazar; ham metinler probe_out/ altina kaydedilir.

Kaynaklar:
- Bedesten (bedesten.adalet.gov.tr, UYAP Mevzuat JSON API): birincil aday.
- www.mevzuat.gov.tr (DataTable arama + MevzuatFihristDetayIframe HTML): yedek aday.
- resmigazete.gov.tr/eskiler: temel teblig'in ilk yayimlanan metni (son care).

Deneme vakalari 23.09.2026 (Sayi 33379) degisiklikleridir; her biri icin degisen
birim ayiklanir ve eski/yeni ibareye gore PRE / CONSOLIDATED / AMBIGUOUS denir."""
import base64, datetime as dt, hashlib, io, json, os, re, subprocess, time
from pathlib import Path
import certifi, requests
from bs4 import BeautifulSoup

OUT = Path("probe_out"); OUT.mkdir(exist_ok=True)
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
BED = "https://bedesten.adalet.gov.tr/mevzuat/"
BED_H = {"Content-Type": "application/json; charset=utf-8", "AdaletApplicationName": "UyapMevzuat",
         "Origin": "https://mevzuat.adalet.gov.tr", "Referer": "https://mevzuat.adalet.gov.tr/",
         "Connection": "close"}
MG = "https://www.mevzuat.gov.tr"
GEOTRUST_URL = ("https://raw.githubusercontent.com/aydincan/turk-hukuku-mevzuat-mcp/"
                "01b344ad47986289c6ef88b8856686c7391ee5ea/turk_hukuku_mevzuat/certs/geotrust-tls-rsa-ca-g1.pem")
GEOTRUST_SHA256 = "C0:6E:30:7F:7C:FC:1D:32:FA:72:A4:C0:33:C8:7B:90:01:9A:F2:16:F0:77:5D:64:97:8A:2E:CA:6C:8A:23:0E"

CASES = [
    {"ad": "masak5", "baslik": "Mali Suçları Araştırma Kurulu Genel Tebliği", "no_re": r"s[ıi]ra\s*no\s*:?\s*5\b",
     "rg_tarih": "2008-04-09", "rg_sayi": "26842",
     "birim": ("ondalik", "2.2.7"), "eski": ["on bin", "yirmi beş bin"], "yeni": ["yirmi bin", "elli bin"]},
    {"ad": "balon2024_8", "baslik": "Balon Balığı Avcılığının Desteklenmesine", "no_re": r"2024/8\b",
     "rg_tarih": "2024-04-13", "rg_sayi": "32516",
     "birim": ("madde", 5), "eski": ["200.000 adede kadar"], "yeni": ["1.000.000 adede kadar"]},
    {"ad": "ihro2021_19", "baslik": "İthalatta Haksız Rekabetin Önlenmesine", "no_re": r"2021/19\b",
     "rg_tarih": "2021-05-22", "rg_sayi": "31488",
     "birim": ("madde", 4), "eski": ["zhejiang juba welding"], "yeni": ["zhejiang juba mechanical"]},
]

rapor = {"baslangic": dt.datetime.now(dt.timezone.utc).isoformat(), "adimlar": [], "vakalar": {}}

def kaydet(adim, **k):
    k["adim"] = adim
    rapor["adimlar"].append(k)
    kisa = {a: (v if not isinstance(v, str) or len(v) < 300 else v[:300] + "…") for a, v in k.items()}
    print(json.dumps(kisa, ensure_ascii=False), flush=True)

def tr_lower(s):
    return s.replace("I", "ı").replace("İ", "i").lower().replace("â", "a")

def html_satirlar(html):
    """HTML'yi satir satir duz metne cevir (tablo satirlari korunur)."""
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style"]):
        t.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for t in soup.find_all(["p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"]):
        t.insert_before("\n"); t.insert_after("\n")
    for t in soup.find_all(["td", "th"]):
        t.insert_after(" | ")
    metin = soup.get_text()
    metin = metin.replace("\xa0", " ")
    return "\n".join(re.sub(r"[ \t]+", " ", l).strip() for l in metin.splitlines() if l.strip())

def birim_ayikla(metin, birim):
    tur, deger = birim
    if tur == "madde":
        m = re.search(rf"(?mi)^\s*MADDE\s+{deger}\b", metin)
        if not m:
            return None
        s = re.search(r"(?mi)^\s*(?:GEÇİCİ\s+|EK\s+)?MADDE\s+\d+\b", metin[m.end():])
        return metin[m.start(): m.end() + (s.start() if s else 4000)]
    # ondalik birim, or. 2.2.7
    m = re.search(rf"(?m)^\s*{re.escape(deger)}\.?(?=[\s\-–])", metin)
    if not m:
        return None
    s = re.search(r"(?m)^\s*(?:2\.2\.(?:[89]|1\d)|2\.[3-9]|[3-9]\.)\b", metin[m.end():])
    return metin[m.start(): m.end() + (s.start() if s else 3000)]

def durum(parca, eski, yeni):
    # Kaynak HTML cumle ortasinda satir sonu iceriyor ("200.000\nadede kadar"): tum bosluklari tekle
    p = re.sub(r"\s+", " ", tr_lower(parca))
    has = lambda w: re.search(rf"(?<![a-zçğıöşü]){re.escape(tr_lower(w))}(?![a-zçğıöşü])", p) is not None
    e, y = [w for w in eski if has(w)], [w for w in yeni if has(w)]
    if y or "33379" in p or "23/9/2026" in p:
        return "CONSOLIDATED", e, y
    if e and len(e) == len(eski):
        return "PRE", e, y
    return "AMBIGUOUS", e, y

def baglam(metin, ifadeler, pay=140):
    """Eslesen ifadelerin cevresi (kanit icin); en fazla 4 parca."""
    duz = re.sub(r"\s+", " ", metin)
    kucuk, out = tr_lower(duz), []
    for w in ifadeler:
        for m in re.finditer(re.escape(tr_lower(w)), kucuk):
            out.append(duz[max(0, m.start() - pay): m.end() + pay])
            break
    return out[:4]

def siniflandir(kaynak, vaka, metin):
    parca = birim_ayikla(metin, vaka["birim"])
    hedef = parca if parca else metin
    d, e, y = durum(hedef, vaka["eski"], vaka["yeni"])
    # Birimdeki degisiklik notlari (or. "Degisik: RG-2/1/2010-27450"): kaynagin guncelligini gosterir
    notlar = sorted({(int(y_), int(a), int(g)) for g, a, y_ in
                     re.findall(r"RG[-\s]*(\d{1,2})/(\d{1,2})/(\d{4})", hedef)})
    (OUT / f"{vaka['ad']}.{kaynak}.birim.txt").write_text(parca or "(ayiklanamadi)", encoding="utf-8")
    kaydet(f"{kaynak}:siniflandir", vaka=vaka["ad"], birim_bulundu=bool(parca),
           birim_uzunluk=len(parca or ""), durum=d if parca else d + "(tum-metin)",
           son_degisiklik_notu=("%02d/%02d/%d" % (notlar[-1][2], notlar[-1][1], notlar[-1][0])) if notlar else None,
           eski_bulunan=e, yeni_bulunan=y, baglam=baglam(hedef, vaka["eski"] + vaka["yeni"] + ["33379"]),
           birim_ornek=(parca or "")[:400])
    return d

# ---------------- Bedesten ----------------
def bed_post(yol, data, ua=BROWSER_UA, paging=False, deneme=3):
    govde = {"data": data, "applicationName": "UyapMevzuat"}
    if paging:
        govde["paging"] = True
    for k in range(deneme):
        t0 = time.time()
        try:
            r = requests.post(BED + yol, json=govde, headers={**BED_H, "User-Agent": ua}, timeout=(10, 45))
        except Exception as e:
            son = {"hata": type(e).__name__, "mesaj": str(e)[:200], "sure": round(time.time() - t0, 1)}
            time.sleep(3); continue
        son = {"status": r.status_code, "sure": round(time.time() - t0, 1)}
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(int(r.headers.get("Retry-After", "5") or 5)); continue
        try:
            son["json"] = r.json()
        except Exception:
            son["govde"] = r.text[:300]
        return son
    return son

def liste_bul(j):
    """Yanit icindeki ilk 'dict listesi'ni bul (alan adi bilinmiyor olabilir)."""
    if isinstance(j, list) and j and isinstance(j[0], dict):
        return j
    if isinstance(j, dict):
        for v in j.values():
            r = liste_bul(v)
            if r is not None:
                return r
    return None

def iso_pencere(tarih):
    d = dt.date.fromisoformat(tarih)
    return ((d - dt.timedelta(days=1)).isoformat() + "T21:00:00.000Z", d.isoformat() + "T21:00:00.000Z")

def satir_uygun(s, vaka):
    ad = tr_lower(re.sub(r"<[^>]+>", "", str(s.get("mevzuatAdi") or s.get("mevAdi") or "")))
    sayi = str(s.get("resmiGazeteSayisi") or s.get("resmiGazeteSayi") or "")
    return (sayi == vaka["rg_sayi"] and re.search(vaka["no_re"], ad) is not None
            and "değişiklik yapılmasına" not in ad)

def bed_ara(vaka):
    bas, bit = iso_pencere(vaka["rg_tarih"])
    denemeler = [("tarih-penceresi", {"sortFields": ["RESMI_GAZETE_TARIHI"], "sortDirection": "desc",
                                      "mevzuatTurList": ["TEBLIGLER"], "resmiGazeteTarihiStart": bas,
                                      "resmiGazeteTarihiEnd": bit}),
                 ("baslik", {"sortFields": ["RESMI_GAZETE_TARIHI"], "sortDirection": "desc",
                             "mevzuatTurList": ["TEBLIGLER"], "mevzuatAdi": vaka["baslik"]})]
    for ad, filtre in denemeler:
        adaylar = []
        for sayfa in range(1, 11):
            r = bed_post("searchDocuments", {"pageSize": 20, "pageNumber": sayfa, **filtre}, paging=True)
            j = r.get("json") or {}
            meta = j.get("metadata") if isinstance(j, dict) else None
            satirlar = liste_bul(j.get("data") if isinstance(j, dict) else None) or []
            if sayfa == 1:
                kaydet("bedesten:ara", vaka=vaka["ad"], yontem=ad, status=r.get("status"), hata=r.get("hata"),
                       sure=r.get("sure"), metadata=meta, satir=len(satirlar),
                       ilk_satir_alanlari=sorted(satirlar[0].keys()) if satirlar else None,
                       govde=r.get("govde"))
            adaylar += satirlar
            if len(satirlar) < 20:
                break
        uygun = [s for s in adaylar if satir_uygun(s, vaka)]
        kaydet("bedesten:secim", vaka=vaka["ad"], yontem=ad, aday=len(adaylar), uygun=len(uygun),
               adaylar=[{k: s.get(k) for k in ("mevzuatId", "mevzuatAdi", "mevzuatNo", "resmiGazeteSayisi",
                                                "resmiGazeteTarihi", "mevzuatTertip", "kayitTarihi", "url")}
                        for s in adaylar[:8]])
        if uygun:
            return uygun[0]
    return None

def bed_icerik(mid, belge="MEVZUAT"):
    r = bed_post("getDocumentContent", {"documentType": belge, "id": str(mid)})
    j = r.get("json") or {}
    d = j.get("data") if isinstance(j, dict) else None
    icerik = (d or {}).get("content") if isinstance(d, dict) else None
    mime = (d or {}).get("mimeType") if isinstance(d, dict) else None
    ham = base64.b64decode(icerik) if icerik else b""
    return r, ham, mime, (j.get("metadata") if isinstance(j, dict) else None)

def pdf_metin(ham):
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(ham)).pages)

def coz(ham, mime):
    if (mime and "pdf" in mime) or ham[:4] == b"%PDF":
        return pdf_metin(ham)
    for enc in ("utf-8", "windows-1254"):
        try:
            return html_satirlar(ham.decode(enc))
        except UnicodeDecodeError:
            continue
    return html_satirlar(ham.decode("utf-8", "replace"))

def bedesten():
    for ua_ad, ua in (("tarayici", BROWSER_UA), ("ozel", "resmi-gazete-arsiv-probe/1.0")):
        r = bed_post("mevzuatTypes", {}, ua=ua)
        j = r.get("json")
        kaydet("bedesten:erisim", ua=ua_ad, status=r.get("status"), hata=r.get("hata"), mesaj=r.get("mesaj"),
               sure=r.get("sure"), metadata=(j or {}).get("metadata") if isinstance(j, dict) else None,
               ornek=json.dumps((liste_bul(j) or [None])[0], ensure_ascii=False)[:300] if j else r.get("govde"))
    # RG sayi filtre anahtari hangisi? (vaka 2)
    for anahtar in (None, "resmiGazeteSayisi", "resmiGazeteSayi"):
        filtre = {"pageSize": 20, "pageNumber": 1, "mevzuatTurList": ["TEBLIGLER"]}
        if anahtar:
            filtre[anahtar] = "32516"
        r = bed_post("searchDocuments", filtre, paging=True)
        j = r.get("json") or {}
        d = j.get("data") if isinstance(j, dict) else {}
        satir = liste_bul(d) or []
        kaydet("bedesten:sayi-filtresi", anahtar=anahtar, status=r.get("status"),
               toplam=(d or {}).get("total") if isinstance(d, dict) else None,
               satir_sayilari=sorted({str(s.get("resmiGazeteSayisi")) for s in satir})[:5])
    for vaka in CASES:
        v = rapor["vakalar"].setdefault(vaka["ad"], {})
        satir = bed_ara(vaka)
        if not satir:
            v["bedesten"] = "NOT_FOUND"; continue
        mid = satir.get("mevzuatId")
        r, ham, mime, meta = bed_icerik(mid)
        kaydet("bedesten:icerik", vaka=vaka["ad"], mevzuatId=mid, status=r.get("status"), metadata=meta,
               mime=mime, bayt=len(ham), sha256=hashlib.sha256(ham).hexdigest() if ham else None,
               kayitTarihi=satir.get("kayitTarihi"))
        v["bedesten_satir"] = {k: satir.get(k) for k in ("mevzuatId", "mevzuatNo", "mevzuatTertip", "url",
                                                          "kayitTarihi", "mevzuatAdi")}
        v["bedesten_satir"]["mevzuatTur"] = satir.get("mevzuatTur")
        if not ham:
            v["bedesten"] = "NO_CONTENT"; continue
        (OUT / f"{vaka['ad']}.bedesten.raw").write_bytes(ham)
        metin = coz(ham, mime)
        (OUT / f"{vaka['ad']}.bedesten.txt").write_text(metin, encoding="utf-8")
        v["bedesten"] = siniflandir("bedesten", vaka, metin)
        # madde agaci (bilgi amacli)
        t = bed_post("mevzuatMaddeTree", {"mevzuatId": str(mid)})
        j = t.get("json") or {}
        dugum = []
        def gez(n):
            if isinstance(n, dict):
                if n.get("maddeId"):
                    dugum.append({k: n.get(k) for k in ("maddeId", "maddeNo", "title", "maddeBaslik")})
                for c in n.get("children") or []:
                    gez(c)
            elif isinstance(n, list):
                for c in n: gez(c)
        gez(j.get("data") if isinstance(j, dict) else None)
        kaydet("bedesten:madde-agaci", vaka=vaka["ad"], status=t.get("status"), dugum=len(dugum), ilk=dugum[:6])

# ---------------- mevzuat.gov.tr ----------------
def ca_paketi():
    try:
        pem = requests.get(GEOTRUST_URL, timeout=20).text
        p = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "geotrust.pem"
        p.write_text(pem)
        fp = subprocess.run(["openssl", "x509", "-noout", "-fingerprint", "-sha256", "-in", str(p)],
                            capture_output=True, text=True).stdout.strip().split("=")[-1]
        tamam = fp.upper() == GEOTRUST_SHA256
        paket = p.with_name("ca.pem")
        paket.write_text(Path(certifi.where()).read_text() + "\n" + pem)
        kaydet("mg:ca", parmak_izi_dogru=tamam, parmak_izi=fp)
        return str(paket) if tamam else certifi.where()
    except Exception as e:
        kaydet("mg:ca", hata=type(e).__name__, mesaj=str(e)[:200])
        return certifi.where()

def mg_get(oturum, url, verify, ua=BROWSER_UA, **k):
    t0 = time.time()
    try:
        r = oturum.get(url, headers={"User-Agent": ua, "Referer": MG + "/", "Accept-Language": "tr-TR,tr;q=0.9"},
                       timeout=(10, 20), verify=verify, **k)
        return r, {"status": r.status_code, "sure": round(time.time() - t0, 1),
                   "ctype": r.headers.get("Content-Type"), "location": r.headers.get("Location")}
    except Exception as e:
        return None, {"hata": type(e).__name__, "mesaj": str(e)[:200], "sure": round(time.time() - t0, 1)}

def mg_ara(oturum, verify, vaka, token):
    bas = {"User-Agent": BROWSER_UA, "X-Requested-With": "XMLHttpRequest",
           "Content-Type": "application/json; charset=UTF-8", "Origin": MG, "Referer": MG + "/"}
    kol = {"data": None, "name": "", "searchable": True, "orderable": False, "search": {"value": "", "regex": False}}
    varyantlar = [
        ("B", {"MevzuatTur": 9, "AranacakIfade": vaka["baslik"], "AranacakYer": "2"}, []),
        ("A", {"MevzuatTur": "Teblig", "YonetmelikMevzuatTur": "OsmanliKanunu", "AranacakIfade": vaka["baslik"],
               "TamCumle": "false", "AranacakYer": "2", "MevzuatNo": "", "KurumId": "0", "AltKurumId": "0",
               "BaslangicTarihi": "", "BitisTarihi": "", "antiforgerytoken": token or ""}, [kol, kol, kol]),
        ("C", {"AranacakIfade": base64.b64encode(vaka["baslik"].encode()).decode(), "AranacakYer": "Baslik",
               "TamCumle": False, "MevzuatTur": 9, "GenelArama": True}, [kol, kol, kol]),
    ]
    for ad, param, kolonlar in varyantlar:
        adaylar, toplam = [], None
        for start in range(0, 200, 20):
            govde = {"draw": 1, "columns": kolonlar, "order": [], "start": start, "length": 20,
                     "search": {"value": "", "regex": False}, "parameters": param}
            t0, r, j = time.time(), None, None
            try:
                r = oturum.post(MG + "/anasayfa/MevzuatDatatable", json=govde, headers=bas, timeout=(10, 20),
                                verify=verify)
                durum_ = {"status": r.status_code, "sure": round(time.time() - t0, 1)}
                j = r.json()
            except Exception as e:
                durum_ = {"hata": type(e).__name__, "mesaj": str(e)[:160],
                          "govde": r.text[:120] if r is not None else None}
            if start == 0:
                kaydet("mg:ara", vaka=vaka["ad"], varyant=ad, **durum_,
                       toplam=(j or {}).get("recordsTotal") if isinstance(j, dict) else None)
            if not isinstance(j, dict):
                break
            toplam = j.get("recordsTotal")
            satirlar = j.get("data") or []
            adaylar += satirlar
            time.sleep(1)
            if len(satirlar) < 20:
                break
        uygun = [s for s in adaylar if satir_uygun(s, vaka)]
        kaydet("mg:secim", vaka=vaka["ad"], varyant=ad, toplam=toplam, aday=len(adaylar), uygun=len(uygun),
               ornek=[{k: s.get(k) for k in ("mevzuatNo", "mevAdi", "resmiGazeteSayisi", "resmiGazeteTarihi",
                                              "mevzuatTur", "mevzuatTertip")} for s in adaylar[:5]])
        if uygun:
            return uygun[0], ad
    return None, None

def mevzuat_gov():
    verify = ca_paketi()
    s = requests.Session()
    r, d = mg_get(s, MG + "/", verify)
    token = next((v for k, v in s.cookies.items() if "Antiforgery" in k), None)
    kaydet("mg:erisim", secenek="a:tarayici+ca", **d, antiforgery_cerez=bool(token))
    _, d = mg_get(requests.Session(), MG + "/", verify, ua=requests.utils.default_user_agent())
    kaydet("mg:erisim", secenek="b:python-ua+ca", **d)
    _, d = mg_get(requests.Session(), MG + "/", certifi.where())
    kaydet("mg:erisim", secenek="c:tarayici+certifi", **d)
    zincir = subprocess.run("openssl s_client -connect www.mevzuat.gov.tr:443 -servername www.mevzuat.gov.tr "
                            "-showcerts </dev/null 2>/dev/null | grep -c 'BEGIN CERTIFICATE'",
                            shell=True, capture_output=True, text=True, timeout=30).stdout.strip()
    kaydet("mg:tls-zinciri", sertifika_sayisi=zincir)
    if r is None:
        for vaka in CASES:
            rapor["vakalar"].setdefault(vaka["ad"], {})["mevzuat_gov"] = "UNREACHABLE"
        return
    for vaka in CASES:
        v = rapor["vakalar"].setdefault(vaka["ad"], {})
        bs = v.get("bedesten_satir") or {}
        no, tertip = bs.get("mevzuatNo"), bs.get("mevzuatTertip") or 5
        satir, varyant = mg_ara(s, verify, vaka, token)
        if satir:
            no, tertip = satir.get("mevzuatNo"), satir.get("mevzuatTertip") or tertip
        kaydet("mg:kimlik", vaka=vaka["ad"], mevzuatNo=no, tertip=tertip, kaynak=varyant or "bedesten")
        if not no:
            v["mevzuat_gov"] = "NOT_FOUND"; continue
        url = f"{MG}/anasayfa/MevzuatFihristDetayIframe?MevzuatTur=9&MevzuatNo={no}&MevzuatTertip={tertip}"
        r, d = mg_get(s, url, verify)
        kaydet("mg:iframe", vaka=vaka["ad"], url=url, **d, bayt=len(r.content) if r is not None else 0)
        if r is None or r.status_code != 200 or re.search(r"404\s*[-–—]?\s*Sayfa\s+Bulunamad", r.text):
            v["mevzuat_gov"] = "NO_CONTENT"; continue
        (OUT / f"{vaka['ad']}.mevzuatgov.html").write_bytes(r.content)
        metin = html_satirlar(r.content.decode(r.encoding or "utf-8", "replace"))
        (OUT / f"{vaka['ad']}.mevzuatgov.txt").write_text(metin, encoding="utf-8")
        v["mevzuat_gov"] = siniflandir("mevzuatgov", vaka, metin)
        time.sleep(1)
        if vaka["ad"] == "balon2024_8":
            for yol in (f"/MevzuatMetin/9.{tertip}.{no}.pdf", f"/MevzuatMetin/9.{tertip}.{no}.htm",
                        f"/MevzuatMetin/9.{tertip}.{no}.doc",
                        f"/File/GeneratePdf?mevzuatNo={no}&mevzuatTur=Teblig&mevzuatTertip={tertip}"):
                r2, d2 = mg_get(s, MG + yol, verify, allow_redirects=False)
                kaydet("mg:alternatif", yol=yol, **d2, bayt=len(r2.content) if r2 is not None else 0)
                time.sleep(1)

# ---------------- Resmi Gazete ilk metin ----------------
def resmi_gazete_ilk():
    for vaka in CASES:
        d = dt.date.fromisoformat(vaka["rg_tarih"])
        url = f"https://www.resmigazete.gov.tr/eskiler/{d:%Y}/{d:%m}/{d:%Y%m%d}.htm"
        try:
            r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=60, verify=False)
            r.encoding = r.apparent_encoding
            soup = BeautifulSoup(r.text, "html.parser")
            hedef = tr_lower(vaka["baslik"])
            linkler = [a.get("href") for a in soup.find_all("a") if hedef in tr_lower(a.get_text(" "))]
            kaydet("rg:gun", vaka=vaka["ad"], status=r.status_code, link=linkler[:3])
            durum_ = "NOT_FOUND"
            for href in linkler:
                u = requests.compat.urljoin(url, href)
                r2 = requests.get(u, headers={"User-Agent": BROWSER_UA}, timeout=60, verify=False)
                if r2.status_code != 200:
                    continue
                if u.lower().endswith(".pdf") or r2.content[:4] == b"%PDF":
                    metin = pdf_metin(r2.content)
                else:
                    r2.encoding = r2.apparent_encoding
                    metin = html_satirlar(r2.text)
                durum_ = siniflandir("resmigazete-ilk", vaka, metin)
                break
            rapor["vakalar"].setdefault(vaka["ad"], {})["resmigazete_ilk"] = durum_
        except Exception as e:
            kaydet("rg:gun", vaka=vaka["ad"], hata=type(e).__name__, mesaj=str(e)[:200])

if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings()
    for ad, f in (("bedesten", bedesten), ("mevzuat.gov.tr", mevzuat_gov), ("resmigazete", resmi_gazete_ilk)):
        try:
            f()
        except Exception as e:
            kaydet(f"{ad}:beklenmeyen-hata", hata=type(e).__name__, mesaj=str(e)[:300])
    rapor["bitis"] = dt.datetime.now(dt.timezone.utc).isoformat()
    Path("probe_report.json").write_text(json.dumps(rapor, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n===== OZET =====")
    for ad, v in rapor["vakalar"].items():
        print(ad, {k: v[k] for k in ("bedesten", "mevzuat_gov", "resmigazete_ilk") if k in v})
