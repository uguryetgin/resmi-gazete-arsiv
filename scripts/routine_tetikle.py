#!/usr/bin/env python3
"""Gunun release aciklamasinda ilgi alanina giren kalem varsa Claude Routine'i tetikle.

rg_fetch.py'den sonra calisir ve onun RG_RELEASE_DIR'e yazdigi notlar.md ile
baslik.txt'yi okur. Kalem basliklari ilgi.txt'deki anahtar kelimelerle eslestirilir:
- Eslesme yoksa: Routine tetiklenmez, gunun basliklari data/bekleyen.json'a eklenir.
- Eslesme varsa: gunun ozeti (eslesen kalemler ★ ile isaretli; eslesmeyenlerin kisaltilmis
  alintisi), ★ kalemlerin gazetedeki tam metni ve bekleyen gunlerin basliklari (kisa
  alintilariyla) Routine'e metin olarak gonderilir; basarili olursa bekleyen.json bosaltilir.

Ortam: CLAUDE_ROUTINE_URL (.../routines/<id>/fire), CLAUDE_ROUTINE_TOKEN. Ikisi de
yoksa hicbir sey yapmaz. Hata durumunda isi basarisiz saymaz (cikis 0)."""
import json, os, re, sys
from pathlib import Path
import requests

ROOT = Path(".")
BEKLEYEN = ROOT / "data" / "bekleyen.json"
BEKLEYEN_GUN = 14          # yuke en fazla bu kadar bekleyen gun eklenir
BEKLEYEN_KALEM = 15        # gun basina en fazla bu kadar baslik
KISA_ALINTI = 350          # ★ olmayan ve bekleyen kalemlerin alintisi (tek cumlelik ozet icin)
TAM_KALEM = 15000          # ★ kalem basina tam metin siniri
TAM_BUTCE = 40000          # tum ★ kalemlerin tam metin toplami
SON_KISIM = 3000           # kesilen metinde sondan korunan kisim
YUK_SINIR = 60000

_TR_ASCII = str.maketrans("çğıöşüâîûêÇĞİÖŞÜÂÎÛÊ", "cgiosuaiueCGIOSUAIUE")

def norm(s):
    return re.sub(r"[^A-Z0-9]+", " ", s.translate(_TR_ASCII).upper())

def ilgi_alanlari(yol=ROOT / "ilgi.txt"):
    """[(alan, desen)] ve haric deseni (yoksa None)."""
    alanlar, haric = [], None
    for l in yol.read_text(encoding="utf-8").splitlines():
        l = l.strip()
        if not l or l.startswith("#") or ":" not in l:
            continue
        ad, kel = l.split(":", 1)
        desen = re.compile("|".join(r"\b" + r"\s+".join(norm(k).split())
                                    for k in kel.split(",") if norm(k).strip()))
        if norm(ad).strip() == "HARIC":
            haric = desen
        else:
            alanlar.append((ad.strip(), desen))
    return alanlar, haric

def alan_bul(baslik, alanlar, haric):
    """Basligin ilk eslestigi ilgi alani (yoksa None)."""
    n = norm(re.sub(r"\(s\. \d+\)$", "", baslik))
    if haric and haric.search(n):
        return None
    return next((ad for ad, desen in alanlar if desen.search(n)), None)

def kalemler(notlar):
    """notlar.md'deki '- Baslik (s. N)' satirlari ('---' ayracindan onceki kisim)."""
    govde = notlar.split("\n---\n", 1)[0]
    return [l[2:].strip() for l in govde.splitlines() if l.startswith("- ")]

def kisalt(metin, n=KISA_ALINTI):
    metin = metin.strip()
    return metin if len(metin) <= n else metin[:n].rsplit(" ", 1)[0].rstrip(" …") + " …"

def isaretle(notlar, eslesen):
    """Eslesen kalemleri ★ ile isaretler; eslesmeyen kalemlerin yalniz ilk alinti satiri
    kisaltilarak kalir (Routine onlari tek cumleyle ozetler, token)."""
    satirlar, ilgili, alinti_var = [], None, False
    for l in notlar.splitlines():
        if l.startswith("- "):
            ilgili, alinti_var = l[2:].strip() in eslesen, False
            if ilgili:
                l = f"- ★ [{eslesen[l[2:].strip()]}] {l[2:]}"
        elif l.startswith(" "):
            if ilgili is False:
                if alinti_var or not l.strip():
                    continue
                l, alinti_var = "  " + kisalt(l), True
        else:
            ilgili = None
        satirlar.append(l)
    return satirlar

def alintilar(notlar):
    """{'Baslik (s. N)': ilk alinti satiri} (yoksa bos)."""
    out, son = {}, None
    for l in notlar.split("\n---\n", 1)[0].splitlines():
        if l.startswith("- "):
            son = l[2:].strip()
            out[son] = ""
        elif l.startswith("  ") and son and not out[son] and l.strip():
            out[son] = l.strip()
    return out

def tam_metinler(baslik, eslesen, butce=TAM_BUTCE):
    """★ kalemlerin gazetedeki tam metni: [(kalem satiri, metin, kesildi)]. Gunun metni
    data/YYYY/AA/YYYYAAGG.txt.gz'den okunur (isleme adimindan once yerelde vardir)."""
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", baslik)
    if not m:
        return []
    ymd = m.group(3) + m.group(2) + m.group(1)
    yol = ROOT / "data" / ymd[:4] / ymd[4:6] / f"{ymd}.txt.gz"
    if not yol.exists():
        return []
    import gzip
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import rg_fetch as rg
    text = gzip.open(yol, "rt", encoding="utf-8").read()
    pages = rg.sayfalar(text)
    kalemler, ilan = rg.icindekiler_kalemleri(rg.fihrist(text))
    if not kalemler:
        return []
    son_sayfa = (ilan or max(pages)) - 1
    sira = {f"{b} (s. {sf})": i for i, (_, b, sf) in enumerate(kalemler)}
    out = []
    for k in eslesen:                      # notlar sirasiyla
        i = sira.get(k)
        if i is None or butce <= 500:
            continue
        govde = rg.kalem_metni(pages, kalemler, i, son_sayfa, ek_sayfa=60)
        if not govde:
            continue
        govde = re.sub(r"([a-zçğıöşü])-\s*\n\s*([a-zçğıöşü])", r"\1\2", govde)   # tireleme
        govde = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", govde)).strip()
        sinir = min(TAM_KALEM, butce)
        kesildi = len(govde) > sinir
        if kesildi:   # bas + yururluk maddeleri cevresi (ekler/cetveller genelde onlardan sonra)
            son = min(SON_KISIM, sinir // 3)
            y = [m.end() for m in re.finditer(r"yürürlüğe\s+girer", govde)]
            bitis = min(len(govde), y[-1] + 300) if y and y[-1] > sinir - son else len(govde)
            kuyruk = govde[max(0, bitis - son):bitis].split(" ", 1)[-1]
            govde = (govde[:sinir - len(kuyruk)].rsplit(" ", 1)[0] + "\n[… orta kısım kesildi …]\n"
                     + kuyruk)
        out.append((k, govde, kesildi))
        butce -= len(govde)
    return out

def yukle():
    try:
        return json.loads(BEKLEYEN.read_text(encoding="utf-8"))
    except Exception:
        return []

def kaydet(liste):
    BEKLEYEN.write_text(json.dumps(liste, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

def main():
    url = os.environ.get("CLAUDE_ROUTINE_URL", "").strip()
    token = os.environ.get("CLAUDE_ROUTINE_TOKEN", "").strip()
    d = Path(os.environ.get("RG_RELEASE_DIR", "").strip() or "/nonexistent")
    if not (url and token):
        print("CLAUDE_ROUTINE_URL/TOKEN tanimli degil, atlandi.")
        return 0
    if not (d / "notlar.md").exists():
        print("Release aciklamasi yok, atlandi.")
        return 0
    notlar = (d / "notlar.md").read_text(encoding="utf-8")
    baslik = (d / "baslik.txt").read_text(encoding="utf-8").strip()
    alanlar, haric = ilgi_alanlari()

    eslesen = {}                       # baslik satiri -> alan adi
    for k in kalemler(notlar):
        ad = alan_bul(k, alanlar, haric)
        if ad:
            eslesen[k] = ad
    bekleyen = yukle()

    if not eslesen:
        al = alintilar(notlar)
        liste = [re.sub(r"\s*\(s\. \d+\)$", "", k) for k in kalemler(notlar)]
        gun = {"baslik": baslik, "kalemler": liste or ["(kalemler çözülemedi; PDF'e bakın)"]}
        if liste:
            gun["alintilar"] = [kisalt(al.get(k, ""), 250) for k in kalemler(notlar)]
        bekleyen.append(gun)
        kaydet(bekleyen)
        print(f"Ilgi alani eslesmesi yok; {baslik} bekleyenlere eklendi ({len(bekleyen)} gun).")
        return 0

    satirlar = isaretle(notlar, eslesen)
    sayim = {}
    for ad in eslesen.values():
        sayim[ad] = sayim.get(ad, 0) + 1
    yuk = [baslik, "İlgi alanı eşleşmeleri: " + ", ".join(f"{a} ({n})" for a, n in sayim.items()),
           "", "\n".join(satirlar).strip()]
    onceki = []
    if bekleyen:
        onceki += ["", "=== Bildirim gönderilmeyen önceki günler (ilgi alanı eşleşmesi yok) ==="]
        for g in bekleyen[-BEKLEYEN_GUN:]:
            onceki += ["", g["baslik"]]
            al = g.get("alintilar") or []
            for j, k in enumerate(g["kalemler"][:BEKLEYEN_KALEM]):
                onceki.append(f"- {k}")
                if j < len(al) and al[j]:
                    onceki.append(f"  {al[j]}")
            if len(g["kalemler"]) > BEKLEYEN_KALEM:
                onceki.append(f"- … ve {len(g['kalemler']) - BEKLEYEN_KALEM} kalem daha")
    # Tam metin, ozet ve onceki gunlerden kalan yere sigar (onceki gunler kesilmesin)
    kalan = YUK_SINIR - len("\n".join(yuk + onceki)) - 2000
    try:
        tam = tam_metinler(baslik, eslesen, min(TAM_BUTCE, kalan))
    except Exception as e:                 # tam metin ek bilgi; yoksa ozet yine gider
        print(f"::warning::tam metin cikarilamadi: {e}")
        tam = []
    if tam:
        yuk += ["", "=== ★ kalemlerin gazetedeki tam metni ==="]
        for k, govde, kesildi in tam:
            yuk += ["", f"--- ★ {k}", govde]
            if kesildi:
                yuk.append("(metnin ortası kesildi; tamamı gazetede)")
    yuk += onceki
    metin = "\n".join(yuk)[:YUK_SINIR]

    try:
        r = requests.post(url, timeout=60, json={"text": metin}, headers={
            "Authorization": f"Bearer {token}", "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"})
    except Exception as e:
        print(f"::warning::Routine tetiklenemedi: {e}")
        return 0
    if r.status_code != 200:
        print(f"::warning::Routine tetiklenemedi: HTTP {r.status_code} {r.text[:300]}")
        return 0
    print("Routine tetiklendi:", r.json().get("claude_code_session_url"))
    if bekleyen:
        kaydet([])
    return 0

if __name__ == "__main__":
    sys.exit(main())
