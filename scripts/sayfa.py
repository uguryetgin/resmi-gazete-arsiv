#!/usr/bin/env python3
"""Arsivden statik web sayfasi uret (GitHub Pages). Yapay zeka cagirmaz; scripts/yz_ozet.py'nin
onceden yazdigi GitHub Models ozetleri varsa ekler.

Cikti site/ altina:
  index.html                 tek sayfalik arayuz (scripts/site_sablon.html)
  gunler.json                tum gunlerin listesi: tarih, sayi, ★ kalemler, basliklar ve kisa alintilar (arama)
  gun/YYYYAAGG.json          bir gunun ayrintisi: bolumler, kalemler, alintilar, ★ tam metin,
                             eski/yeni karsilastirma tablosu ve GitHub Models ozeti (varsa)

Sifre: SITE_SIFRE ortam degiskeni varsa gunler.json ve gun/*.json AES-GCM ile sifrelenir
(.enc; anahtar PBKDF2-SHA256), sifre.json'a yalniz tuz ve tur sayisi yazilir; sayfa sifreyi
tarayicida sorar. SITE_SIFRE_ZORUNLU=1 iken sifre yoksa site uretilmez.

Kullanim: python3 scripts/sayfa.py [cikti_klasoru]   (depo kokunden; varsayilan site/)
.github/workflows/site.yml her gunluk indirmeden sonra calistirip yayimlar."""
import base64, hashlib, json, os, re, shutil, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import routine_tetikle as rt     # noqa: E402  (ilgi alani eslestirme, ★ tam metin)
import etiket                    # noqa: E402  (ayrintili etiketler: etiketler.txt)

ROOT = Path(".")
SABLON = Path(__file__).resolve().parent / "site_sablon.html"
TAM_BUTCE = 60000                 # gun basina ★ tam metin toplami
PBKDF2_TUR = 310000               # tarayicida bir kez, giriste hesaplanir
ARAMA_ALINTI = 900                # gunler.json'da aranan alinti uzunlugu (kalem basina)

def ozet_oku(yol):
    """ozet.txt -> [{'bolum', 'kalemler': [{'baslik', 'sayfa', 'alinti'}]}] ve ilan satiri."""
    bolumler, ilan = [], ""
    if not yol.exists():
        return bolumler, ilan
    for l in yol.read_text(encoding="utf-8").splitlines():
        if l.startswith("• "):
            m = re.match(r"• (.*?)(?: \(s\. (\d+)\))?$", l)
            if not bolumler:
                bolumler.append({"bolum": "", "kalemler": []})
            bolumler[-1]["kalemler"].append({"baslik": m.group(1), "sayfa": int(m.group(2) or 0),
                                             "alinti": ""})
        elif l.startswith("  ") and bolumler and bolumler[-1]["kalemler"]:
            bolumler[-1]["kalemler"][-1]["alinti"] = l.strip()
        elif l.startswith("İLÂN BÖLÜMÜ"):
            ilan = l.strip()
        elif l.strip():
            bolumler.append({"bolum": l.strip(), "kalemler": []})
    return bolumler, ilan

def karsilastirma(yol):
    """karsilastirma.json -> {baslik: {'satirlar': [[birim, eski, yeni]], 'notlar': [...]}}"""
    if not yol.exists():
        return {}
    import karsilastir as ks
    out = {}
    for k in json.loads(yol.read_text(encoding="utf-8")):
        if not k.get("degisiklikler"):
            continue
        satir = [ln.strip() for ln in ks.satirlar(k)]
        tablo = [[h.strip() for h in ln.strip("|").split("|")] for ln in satir
                 if ln.startswith("|") and not ln.startswith("|---") and not ln.startswith("| Madde |")]
        notlar = [ln for ln in satir if ln and not ln.startswith("|") and not ln.startswith("⇄")]
        out[k["baslik"]] = {"satirlar": tablo, "notlar": notlar}
    return out

def gun_isle(meta_yol, alanlar, haric, kur=None):
    meta = json.loads(meta_yol.read_text(encoding="utf-8"))
    ymd, klasor = meta["ymd"], meta_yol.parent
    sayilar = [("", None, meta)] + [(f"M{m['no']}", m["no"], m) for m in meta.get("mukerrer") or []]
    gun = {"ymd": ymd, "tarih": meta["tarih"], "sayi": meta.get("sayi"), "pdf": meta["pdf_url"],
           "sayfa": meta.get("pages"), "sayilar": []}
    for ek, no, m in sayilar:
        bolumler, ilan = ozet_oku(klasor / f"{ymd}{ek}.ozet.txt")
        kars = karsilastirma(klasor / f"{ymd}{ek}.karsilastirma.json")
        yz_yol = klasor / f"{ymd}{ek}.yz.json"     # scripts/yz_ozet.py (GitHub Models)
        yz = json.loads(yz_yol.read_text(encoding="utf-8")) if yz_yol.exists() else {}
        eslesen = {}
        for b in bolumler:
            for k in b["kalemler"]:
                k["alan"] = rt.alan_bul(k["baslik"], alanlar, haric) if alanlar else None
                if k["baslik"] in kars:
                    k["karsilastirma"] = kars[k["baslik"]]
                anahtar = f"{k['baslik']} (s. {k['sayfa']})"
                if anahtar in yz.get("kalemler", {}):
                    k["yz"] = dict(yz["kalemler"][anahtar], model=yz.get("model", ""))
                if kur:
                    ozet = " ".join((k.get("yz") or {}).get("ne_getiriyor") or [])
                    k["etiket"] = etiket.etiketle(kur, b["bolum"], k["baslik"],
                                                  (k.get("alinti") or "") + " " + ozet, ymd)
                if k["alan"] and not ek:
                    eslesen[anahtar] = k["alan"]
        if eslesen:              # ★ kalemlerin tam metni (yalniz ana sayi; mukerrer metni ayri dosyada)
            try:
                tam = {s: (g, kes) for s, g, kes in rt.tam_metinler(f"Resmî Gazete {meta['tarih']}",
                                                                    eslesen, TAM_BUTCE)}
            except Exception as e:
                print(f"::warning::{ymd} tam metin: {e}")
                tam = {}
            for b in bolumler:
                for k in b["kalemler"]:
                    t = tam.get(f"{k['baslik']} (s. {k['sayfa']})")
                    if t:
                        k["tam_metin"], k["kesildi"] = t
        gun["sayilar"].append({"mukerrer": no, "pdf": m.get("url") or m.get("pdf_url"),
                               "bolumler": bolumler, "ilan": ilan})
    return gun

def ozet_satiri(gun):
    yildiz, basliklar, metinler, etiketler = [], [], [], []
    for s in gun["sayilar"]:
        for b in s["bolumler"]:
            for k in b["kalemler"]:
                basliklar.append(k["baslik"])
                metinler.append(k.get("alinti", "")[:ARAMA_ALINTI])
                etiketler.append(k.get("etiket") or [])
                if k.get("alan"):
                    yildiz.append({"baslik": k["baslik"], "alan": k["alan"]})
    return {"ymd": gun["ymd"], "tarih": gun["tarih"], "sayi": gun["sayi"], "kalem": len(basliklar),
            "mukerrer": len(gun["sayilar"]) - 1, "yildiz": yildiz, "basliklar": basliklar,
            "alintilar": metinler, "etiketler": etiketler}

def sifrele(klasor, sifre):
    """gunler.json ve gun/*.json -> .enc (12 bayt iv + AES-GCM sifreli metin); sifre.json."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    # Sabit tuz: anahtar yayindan yayina degismesin; yoksa acik sekme ve "hatirla"
    # ile saklanan anahtar her gunluk yayinda yeni dosyalari cozemez.
    tuz = hashlib.sha256(b"resmi-gazete-arsiv/site/v1").digest()[:16]
    anahtar = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=tuz,
                         iterations=PBKDF2_TUR).derive(sifre.encode("utf-8"))
    aes = AESGCM(anahtar)
    for yol in [p for p in [klasor / "gunler.json", klasor / "yz.json"] if p.exists()] + sorted((klasor / "gun").glob("*.json")):
        iv = os.urandom(12)
        yol.with_suffix(".enc").write_bytes(iv + aes.encrypt(iv, yol.read_bytes(), None))
        yol.unlink()
    (klasor / "sifre.json").write_text(json.dumps(
        {"v": 1, "tuz": base64.b64encode(tuz).decode(), "tur": PBKDF2_TUR}), encoding="utf-8")

def main(argv):
    cikti = Path(argv[0]) if argv else ROOT / "site"
    if cikti.exists():
        shutil.rmtree(cikti)
    (cikti / "gun").mkdir(parents=True)
    alanlar, haric = rt.ilgi_alanlari() if (ROOT / "ilgi.txt").exists() else ([], None)
    liste, kur = [], etiket.kurallar()
    for meta_yol in sorted(ROOT.glob("data/[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9]*.json")):
        if not re.fullmatch(r"\d{8}\.json", meta_yol.name):
            continue
        gun = gun_isle(meta_yol, alanlar, haric, kur)
        (cikti / "gun" / f"{gun['ymd']}.json").write_text(
            json.dumps(gun, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        liste.append(ozet_satiri(gun))
    liste.sort(key=lambda g: g["ymd"], reverse=True)
    (cikti / "gunler.json").write_text(json.dumps(
        {"alanlar": [a for a, _ in alanlar], "gunler": liste,
         "etiket_gruplari": dict(etiket.katalog(kur), **{"Diğer": [etiket.ETIKETSIZ]})}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    shutil.copy(SABLON, cikti / "index.html")
    (cikti / ".nojekyll").write_text("")
    (cikti / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    sifre = os.environ.get("SITE_SIFRE", "")
    yz_anahtar = os.environ.get("SITE_YZ_TOKEN", "").strip()
    if sifre and yz_anahtar:      # sohbet: anahtar yalniz sifreli yayina konur, sifresiz asla
        modeller = [m.strip() for m in [os.environ.get("YZ_MODEL", "")] +
                    os.environ.get("YZ_YEDEK_MODELLER", "").split(",") if m.strip()] or ["gemini-flash-latest"]
        (cikti / "yz.json").write_text(json.dumps({"anahtar": yz_anahtar, "modeller": modeller}), encoding="utf-8")
    if sifre:
        sifrele(cikti, sifre)
        print("Veri sifrelendi.")
    elif os.environ.get("SITE_SIFRE_ZORUNLU") == "1":
        print("::error::SITE_SIFRE tanimli degil; sayfa sifresiz yayimlanmasin diye durduruldu. "
              "Repo Settings > Secrets and variables > Actions > New repository secret: SITE_SIFRE")
        return 1
    print(f"Site hazir: {len(liste)} gun, {sum(len(g['yildiz']) for g in liste)} ★ kalem -> {cikti}")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
