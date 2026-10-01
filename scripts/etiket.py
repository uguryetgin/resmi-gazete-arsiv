#!/usr/bin/env python3
"""Kalemlere anahtar kelimeyle ayrintili etiket (etiketler.txt + etiket_duzeltme.txt; yapay zeka yok).

Kullanim: python3 scripts/etiket.py rapor   -> arsivdeki tum kalemlerin etiket raporu (Markdown):
          etiket sayilari, etiketsiz kalemler ve her etiketin kalemleri (gozden gecirmek icin).
Modul olarak: kurallar() bir kez yuklenir, etiketle(kurallar, bolum, baslik, metin, ymd) -> [etiket]."""
import re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from routine_tetikle import norm  # noqa: E402  (buyuk harf + ASCII; ilgi.txt ile ayni eslesme)

ROOT = Path(".")
GRUPLAR = ["Tür", "İşlem", "Kurum", "Konu"]
ALINTI_SINIR = 500
ETIKETSIZ = "Etiketsiz"

def _desen(kelimeler, yalniz_baslik=False):
    """Anahtar kelimelerden desen. "=" tam kelime; "^" yalniz baslikta aranir
       (yalniz_baslik=True "^"lileri, False digerlerini toplar)."""
    parca = []
    for k in kelimeler:
        k = k.strip()
        if k.startswith("^") != yalniz_baslik:
            continue
        k = k.lstrip("^")
        tam = k.startswith("=")
        n = norm(k.lstrip("=")).split()
        if n:
            parca.append(r"\b" + r"\s+".join(n) + (r"\b" if tam else ""))
    return re.compile("|".join(parca)) if parca else None

def kurallar(yol=ROOT / "etiketler.txt", duzeltme=ROOT / "etiket_duzeltme.txt"):
    """{'etiketler': [(grup, ad, desen, haric)], 'duzeltme': [(ymd, baslik_basi, ekle, cikar)]}"""
    out, grup = {"etiketler": [], "duzeltme": []}, None
    if yol.exists():
        for l in yol.read_text(encoding="utf-8").splitlines():
            l = l.strip()
            if not l or l.startswith("#"):
                continue
            m = re.fullmatch(r"\[(.+)\]", l)
            if m:
                grup = m.group(1).strip()
                continue
            if ":" not in l or not grup:
                continue
            ad, govde = l.split(":", 1)
            govde, _, haric = govde.partition("|")
            haric = re.sub(r"^\s*Hariç\s*:", "", haric, flags=re.I)
            desen = (_desen(govde.split(",")), _desen(govde.split(","), True))
            if any(desen):
                out["etiketler"].append((grup, ad.strip(), desen, _desen(haric.split(","))))
    if duzeltme.exists():
        for l in duzeltme.read_text(encoding="utf-8").splitlines():
            if l.strip().startswith("#") or l.count("|") < 2:
                continue
            ymd, bas, deg = [x.strip() for x in l.split("|", 2)]
            ekle = [d[1:].strip() for d in deg.split(",") if d.strip().startswith("+")]
            cikar = [d[1:].strip() for d in deg.split(",") if d.strip().startswith("-")]
            out["duzeltme"].append((ymd, norm(bas).strip(), ekle, cikar))
    return out

def etiketle(kur, bolum, baslik, metin="", ymd=""):
    """Kalemin etiketleri (gruplarin sirasiyla). Kurum/Konu yoksa sona 'Etiketsiz' eklenir."""
    nb, nt = norm(bolum or ""), norm(baslik)
    ni = nt + " " + norm((metin or "")[:ALINTI_SINIR])
    bulunan = []
    for grup, ad, desen, haric in kur["etiketler"]:
        hedef = nb if grup == "Tür" else nt if grup == "İşlem" else ni
        genel, baslikta = desen
        eslesti = (genel and genel.search(hedef)) or (baslikta and baslikta.search(nt))
        if eslesti and not (haric and haric.search(nt)) and ad not in bulunan:
            bulunan.append(ad)
    for d_ymd, bas, ekle, cikar in kur["duzeltme"]:
        if (not d_ymd or d_ymd == ymd) and nt.strip().startswith(bas):
            bulunan = [e for e in bulunan if e not in cikar] + [e for e in ekle if e not in bulunan]
    grubu = {ad: g for g, ad, _, _ in kur["etiketler"]}
    if not any(grubu.get(e) in ("Kurum", "Konu") or e not in grubu for e in bulunan):
        bulunan.append(ETIKETSIZ)
    return bulunan

def katalog(kur):
    """{grup: [etiket adlari]} (sayfadaki filtre listesi icin)."""
    out = {}
    for g, ad, _, _ in kur["etiketler"]:
        out.setdefault(g, []).append(ad)
    return out

def rapor():
    from sayfa import ozet_oku
    import json
    kur = kurallar()
    sayim, liste = {}, {}
    toplam = 0
    for meta_yol in sorted(ROOT.glob("data/[0-9]*/[0-9]*/[0-9]*.json")):
        if not re.fullmatch(r"\d{8}\.json", meta_yol.name):
            continue
        ymd = meta_yol.stem
        yz = meta_yol.with_name(f"{ymd}.yz.json")
        yz = json.loads(yz.read_text(encoding="utf-8")).get("kalemler", {}) if yz.exists() else {}
        bolumler, _ = ozet_oku(meta_yol.with_name(f"{ymd}.ozet.txt"))
        for b in bolumler:
            for k in b["kalemler"]:
                ozet = " ".join((yz.get(f"{k['baslik']} (s. {k['sayfa']})") or {}).get("ne_getiriyor") or [])
                ets = etiketle(kur, b["bolum"], k["baslik"], (k["alinti"] or "") + " " + ozet, ymd)
                toplam += 1
                for e in ets:
                    sayim[e] = sayim.get(e, 0) + 1
                    liste.setdefault(e, []).append(f"{ymd} · {k['baslik'][:140]} — _{', '.join(x for x in ets if x != e)}_")
    satir = [f"# Etiket raporu\n\n{toplam} kalem. Etiketsiz: {sayim.get(ETIKETSIZ, 0)}.\n",
             "| Grup | Etiket | Kalem |", "|---|---|---|"]
    for g, adlar in katalog(kur).items():
        for ad in adlar:
            satir.append(f"| {g} | {ad} | {sayim.get(ad, 0)} |")
    for ad in [ETIKETSIZ] + [a for adlar in katalog(kur).values() for a in adlar]:
        if liste.get(ad):
            satir += [f"\n## {ad} ({len(liste[ad])})"] + [f"- {x}" for x in liste[ad]]
    return "\n".join(satir) + "\n"

if __name__ == "__main__":
    if sys.argv[1:] == ["rapor"]:
        sys.stdout.write(rapor())
    else:
        print(__doc__)
