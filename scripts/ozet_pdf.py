#!/usr/bin/env python3
"""Gunluk ozet PDF'i (yazdirmaya uygun; yapay zeka/token yok).

bildir.py gunun bildirimini yazarken cagirir: pdf_yap(ymd) gunun ★ kalemleri (otomatik ozet,
yururluk, etiketler, sayfa araligi) ve diger kalemlerin listesinden HTML uretir, Chrome ile PDF'e
basar; release_yukle() PDF'i o gunun GitHub release'ine (rg-YYYYAAGG) "Ozet-YYYYAAGG.pdf" olarak
ekler. Bildirim yorumunda (GitHub bildirim e-postasinda da) bu PDF'in linki olur.
Tek basina deneme: python3 scripts/ozet_pdf.py YYYYAAGG [cikti.pdf]   (yalniz PDF uretir)"""
import html, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etiket                    # noqa: E402
import routine_tetikle as rt     # noqa: E402
import sayfa                     # noqa: E402

ROOT = Path(".")
GUNLER = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim",
         "Kasım", "Aralık"]

def tarih_uzun(ymd):
    from datetime import date
    d = date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]))
    return f"{d.day} {AYLAR[d.month - 1]} {d.year} {GUNLER[d.weekday()]}"

def gun_al(ymd):
    alanlar, haric = rt.ilgi_alanlari()
    return sayfa.gun_isle(ROOT / "data" / ymd[:4] / ymd[4:6] / f"{ymd}.json", alanlar, haric, etiket.kurallar())

def yildizlar(gun):
    return [(s, b, k) for s in gun["sayilar"] for b in s["bolumler"] for k in b["kalemler"] if k.get("alan")]

def ozet_html(gun, url):
    e = html.escape
    ys = yildizlar(gun)
    toplam = sum(len(b["kalemler"]) for s in gun["sayilar"] for b in s["bolumler"])
    h = [f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 16mm 15mm 16mm 15mm; }}
body {{ font: 10.5pt/1.45 "DejaVu Serif", Georgia, serif; color: #1d1f23; }}
h1 {{ font-size: 19pt; margin: 0 0 2pt; }} .alt {{ color: #62666d; margin-bottom: 10pt; }}
.ust {{ border-bottom: 2.5pt solid #b4232a; padding-bottom: 6pt; margin-bottom: 12pt; }}
h2 {{ font: 700 9pt "DejaVu Sans", Arial, sans-serif; letter-spacing: .08em; text-transform: uppercase;
      color: #62666d; margin: 14pt 0 6pt; }}
.kalem {{ border-left: 3pt solid #b4232a; padding: 2pt 0 2pt 9pt; margin: 0 0 12pt; page-break-inside: avoid; }}
.kalem h3 {{ font-size: 11.5pt; margin: 2pt 0 3pt; }}
.meta {{ font: 8.5pt "DejaVu Sans", Arial, sans-serif; color: #62666d; }}
.alan {{ color: #b35c00; font-weight: 700; }} ul {{ margin: 4pt 0; padding-left: 14pt; }} li {{ margin: 2pt 0; }}
.et {{ display: inline-block; border: .6pt solid #c9c6c0; border-radius: 3pt; padding: 0 4pt; margin: 0 2pt 0 0;
       font: 7.5pt "DejaVu Sans", Arial, sans-serif; color: #62666d; }}
.diger li {{ font-size: 9.5pt; }} .bol {{ font-weight: 700; margin-top: 6pt; font-size: 9.5pt; }}
a {{ color: #1f5fbf; }} .son {{ margin-top: 14pt; font-size: 8.5pt; color: #62666d; }}
</style></head><body>
<div class="ust"><h1>Resmî Gazete · {e(tarih_uzun(gun['ymd']))}</h1>
<div class="alt">Sayı {e(str(gun['sayi']))} · {toplam} kalem · {len(ys)} ilgi alanı kalemi ·
<a href="{e(url)}#{gun['ymd']}">Sayfada aç</a> · <a href="{e(gun['pdf'])}">Gazetenin PDF'i</a></div></div>"""]
    h.append(f"<h2>★ İlgi alanına girenler ({len(ys)})</h2>" if ys else "<h2>İlgi alanına giren kalem yok</h2>")
    for s, b, k in ys:
        ets = [x for x in k.get("etiket") or [] if x != etiket.ETIKETSIZ]
        ar = k.get("kesit") or {}
        sayfa_ = f"s. {ar['ilk']}–{ar['son']}" if ar and ar["son"] > ar["ilk"] else f"s. {k['sayfa']}"
        h.append(f'<div class="kalem"><div class="meta"><span class="alan">{e(k["alan"])}</span> · {e(b["bolum"])} · '
                 f'<a href="{e(gun["pdf"])}#page={k["sayfa"]}">{sayfa_}</a></div><h3>{e(k["baslik"])}</h3>')
        if ets:
            h.append("<div>" + "".join(f'<span class="et">{e(x)}</span>' for x in ets) + "</div>")
        yz = k.get("yz") or {}
        if yz.get("ne_getiriyor"):
            h.append("<ul>" + "".join(f"<li>{e(m)}</li>" for m in yz["ne_getiriyor"]) + "</ul>")
            if yz.get("yururluk"):
                h.append(f"<div><b>Yürürlük:</b> {e(yz['yururluk'])}</div>")
            h.append('<div class="meta">Otomatik özet (yapay zekâ); hata içerebilir, kesin metin için PDF.</div>')
        elif k.get("alinti"):
            h.append(f"<p>{e(k['alinti'])}</p>")
        h.append("</div>")
    diger = [(s, b, [k for k in b["kalemler"] if not k.get("alan")]) for s in gun["sayilar"] for b in s["bolumler"]]
    diger = [(s, b, ks) for s, b, ks in diger if ks]
    if diger:
        h.append('<h2>Diğer kalemler</h2><div class="diger">')
        for s, b, ks in diger:
            h.append(f'<div class="bol">{e((str(s["mukerrer"]) + ". Mükerrer · ") if s["mukerrer"] else "")}{e(b["bolum"])}</div><ul>')
            for k in ks:
                ets = [x for x in k.get("etiket") or [] if x != etiket.ETIKETSIZ]
                h.append(f"<li>{e(k['baslik'])} <span class='meta'>(s. {k['sayfa']})</span> "
                         + "".join(f'<span class="et">{e(x)}</span>' for x in ets[:4]) + "</li>")
            h.append("</ul>")
        h.append("</div>")
    h.append('<div class="son">GitHub Actions tarafından gazetenin kendi metninden otomatik üretildi.</div></body></html>')
    return "\n".join(h)

def chrome():
    for ad in (os.environ.get("CHROME", ""), "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if ad and shutil.which(ad):
            return shutil.which(ad)
    return None

def pdf_yap(ymd, url="", cikti=None):
    """Gunun ozet PDF'inin yolu (Chrome yoksa None)."""
    exe = chrome()
    if not exe:
        print("::warning::Chrome/Chromium yok, ozet PDF'i uretilmedi")
        return None
    gun = gun_al(ymd)
    d = Path(tempfile.mkdtemp())
    (d / "ozet.html").write_text(ozet_html(gun, url), encoding="utf-8")
    cikti = Path(cikti or d / f"Resmi-Gazete-{ymd}.pdf").resolve()
    subprocess.run([exe, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={cikti}", (d / "ozet.html").as_uri()],
                   check=True, capture_output=True, timeout=120)
    return cikti

def release_yukle(ymd, pdf, token, repo):
    """Ozet PDF'ini o gunun release'ine (rg-YYYYAAGG) ekler; indirme linkini dondurur (yoksa None)."""
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    r = requests.get(f"https://api.github.com/repos/{repo}/releases/tags/rg-{ymd}", headers=h, timeout=30)
    if not r.ok:
        print(f"rg-{ymd} release'i yok, ozet PDF'i eklenmedi.")
        return None
    rel, ad = r.json(), f"Ozet-{ymd}.pdf"
    for a in rel.get("assets") or []:
        if a["name"] == ad:                       # yeniden gonderimde eskisini degistir
            requests.delete(a["url"], headers=h, timeout=30)
    r = requests.post(f"https://uploads.github.com/repos/{repo}/releases/{rel['id']}/assets",
                      params={"name": ad}, data=Path(pdf).read_bytes(), timeout=120,
                      headers=dict(h, **{"Content-Type": "application/pdf"}))
    r.raise_for_status()
    return r.json()["browser_download_url"]

def ozet_pdf_linki(ymd, url, token, repo):
    """bildir.py'den: ozet PDF'ini uret, release'e ekle, linkini dondur (hata olursa None)."""
    try:
        pdf = pdf_yap(ymd, url)
        return release_yukle(ymd, pdf, token, repo) if pdf else None
    except Exception as e:
        print(f"::warning::ozet PDF'i: {e.__class__.__name__}: {str(e)[:200]}")
        return None

if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or not re.fullmatch(r"\d{8}", a[0]):
        print(__doc__)
        sys.exit(1)
    print(pdf_yap(a[0], "https://uguryetgin.github.io/resmi-gazete-arsiv/", a[1] if len(a) > 1 else None))
