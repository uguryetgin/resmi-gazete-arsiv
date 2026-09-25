#!/usr/bin/env python3
"""Gunun release aciklamasinda ilgi alanina giren kalem varsa Claude Routine'i tetikle.

rg_fetch.py'den sonra calisir ve onun RG_RELEASE_DIR'e yazdigi notlar.md ile
baslik.txt'yi okur. Kalem basliklari ilgi.txt'deki anahtar kelimelerle eslestirilir:
- Eslesme yoksa: Routine tetiklenmez, gunun basliklari data/bekleyen.json'a eklenir.
- Eslesme varsa: gunun ozeti (eslesen kalemler ★ ile isaretli; eslesmeyenlerin yalniz
  basligi) ve bekleyen gunlerin basliklari Routine'e metin olarak gonderilir; basarili olursa bekleyen.json bosaltilir.

Ortam: CLAUDE_ROUTINE_URL (.../routines/<id>/fire), CLAUDE_ROUTINE_TOKEN. Ikisi de
yoksa hicbir sey yapmaz. Hata durumunda isi basarisiz saymaz (cikis 0)."""
import json, os, re, sys
from pathlib import Path
import requests

ROOT = Path(".")
BEKLEYEN = ROOT / "data" / "bekleyen.json"
BEKLEYEN_GUN = 14          # yuke en fazla bu kadar bekleyen gun eklenir
BEKLEYEN_KALEM = 15        # gun basina en fazla bu kadar baslik

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

def isaretle(notlar, eslesen):
    """Eslesen kalemleri ★ ile isaretler; eslesmeyen kalemlerin girintili alintilari atilir
    (Routine onlardan yalniz basligi kullanir, token)."""
    satirlar, ilgili = [], None
    for l in notlar.splitlines():
        if l.startswith("- "):
            ilgili = l[2:].strip() in eslesen
            if ilgili:
                l = f"- ★ [{eslesen[l[2:].strip()]}] {l[2:]}"
        elif l.startswith(" "):
            if ilgili is False:
                continue
        else:
            ilgili = None
        satirlar.append(l)
    return satirlar

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
        liste = [re.sub(r"\s*\(s\. \d+\)$", "", k) for k in kalemler(notlar)]
        bekleyen.append({"baslik": baslik, "kalemler": liste or ["(kalemler çözülemedi; PDF'e bakın)"]})
        kaydet(bekleyen)
        print(f"Ilgi alani eslesmesi yok; {baslik} bekleyenlere eklendi ({len(bekleyen)} gun).")
        return 0

    satirlar = isaretle(notlar, eslesen)
    sayim = {}
    for ad in eslesen.values():
        sayim[ad] = sayim.get(ad, 0) + 1
    yuk = [baslik, "İlgi alanı eşleşmeleri: " + ", ".join(f"{a} ({n})" for a, n in sayim.items()),
           "", "\n".join(satirlar).strip()]
    if bekleyen:
        yuk += ["", "=== Bildirim gönderilmeyen önceki günler (ilgi alanı eşleşmesi yok) ==="]
        for g in bekleyen[-BEKLEYEN_GUN:]:
            yuk += ["", g["baslik"]] + [f"- {k}" for k in g["kalemler"][:BEKLEYEN_KALEM]]
            if len(g["kalemler"]) > BEKLEYEN_KALEM:
                yuk.append(f"- … ve {len(g['kalemler']) - BEKLEYEN_KALEM} kalem daha")
    metin = "\n".join(yuk)[:60000]

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
