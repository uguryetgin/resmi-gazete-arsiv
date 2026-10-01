#!/usr/bin/env python3
"""Gunluk ozet PDF'i (yazdirmaya uygun) ve e-posta / ntfy telefon bildirimi (yapay zeka/token yok).

bildir.py gunun bildirimini ilk kez yazdiginda cagirir (gunde bir kez):
- pdf_yap(ymd): gunun ★ kalemleri (otomatik ozet, yururluk, etiketler, sayfa araligi) ve diger
  kalemlerin listesinden HTML uretir, Chrome/Chromium ile PDF'e basar.
- eposta(ymd, url, pdf): MAIL_ADRES/MAIL_SIFRE (Gmail uygulama sifresi) ile MAIL_ALICI'ya
  (yoksa MAIL_ADRES) PDF ekli e-posta.
- ntfy(ymd, url): NTFY_KONU tanimliysa ntfy.sh uzerinden telefona anlik bildirim (dokununca sayfa).
Tek basina deneme: python3 scripts/eposta.py YYYYAAGG [cikti.pdf]   (yalniz PDF uretir)"""
import html, json, os, re, shutil, smtplib, subprocess, sys, tempfile
from email.message import EmailMessage
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

def baslik(ymd):
    gun = gun_al(ymd)
    n = len(yildizlar(gun))
    return gun, f"Resmî Gazete {gun['tarih']} – " + (f"{n} ilgi alanı kalemi" if n else "ilgi alanı kalemi yok")

def eposta(ymd, url, pdf):
    adres, sifre = os.environ.get("MAIL_ADRES", "").strip(), os.environ.get("MAIL_SIFRE", "").strip()
    if not (adres and sifre):
        print("MAIL_ADRES/MAIL_SIFRE yok, e-posta atlandi.")
        return False
    alici = os.environ.get("MAIL_ALICI", "").strip() or adres
    gun, konu = baslik(ymd)
    ys = yildizlar(gun)
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = konu, f"Resmî Gazete Arşivi <{adres}>", alici
    satir = "".join(f"<li><b>{html.escape(k['alan'])}</b> — {html.escape(k['baslik'])}</li>" for _, _, k in ys)
    m.set_content(f"{konu}\n\nSayfada aç: {url}#{ymd}\nDinle: {url}#{ymd}-dinle\n\nÖzet PDF ektedir.")
    m.add_alternative(f"""<p><b>{html.escape(konu)}</b></p>{'<ul>' + satir + '</ul>' if satir else ''}
<p>👉 <a href="{url}#{ymd}">Sayfada aç</a> · 🔊 <a href="{url}#{ymd}-dinle">Dinle</a> ·
<a href="{html.escape(gun['pdf'])}">Gazetenin PDF'i</a></p><p style="color:#888">Özet PDF ektedir.</p>""",
                      subtype="html")
    if pdf and Path(pdf).exists():
        m.add_attachment(Path(pdf).read_bytes(), maintype="application", subtype="pdf",
                         filename=f"Resmi-Gazete-{ymd}.pdf")
    with smtplib.SMTP_SSL(os.environ.get("MAIL_SUNUCU", "smtp.gmail.com"), 465, timeout=60) as s:
        s.login(adres, sifre)
        s.send_message(m)
    print(f"E-posta gonderildi: {alici}")
    return True

def ntfy(ymd, url):
    konu_adi = os.environ.get("NTFY_KONU", "").strip()
    if not konu_adi:
        return False
    gun, konu = baslik(ymd)
    ys = yildizlar(gun)
    metin = "\n".join(f"★ {k['baslik'][:90]}" for _, _, k in ys[:5]) or "İlgi alanına giren kalem yok."
    r = requests.post("https://ntfy.sh/", timeout=30, json={
        "topic": konu_adi, "title": konu, "message": metin, "tags": ["newspaper"], "click": f"{url}#{ymd}",
        "actions": [{"action": "view", "label": "🔊 Dinle", "url": f"{url}#{ymd}-dinle"},
                    {"action": "view", "label": "Sayfada aç", "url": f"{url}#{ymd}"}]})
    r.raise_for_status()
    print("ntfy bildirimi gonderildi.")
    return True

def gonder(ymd, url):
    """bildir.py'den: telefon bildirimi ve PDF ekli e-posta (her biri ayri; hata digerini durdurmaz)."""
    try:
        ntfy(ymd, url)
    except Exception as e:
        print(f"::warning::ntfy: {e}")
    if os.environ.get("MAIL_ADRES") and os.environ.get("MAIL_SIFRE"):
        try:
            eposta(ymd, url, pdf_yap(ymd, url))
        except Exception as e:
            print(f"::warning::e-posta: {e.__class__.__name__}: {str(e)[:200]}")

if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or not re.fullmatch(r"\d{8}", a[0]):
        print(__doc__)
        sys.exit(1)
    print(pdf_yap(a[0], "https://uguryetgin.github.io/resmi-gazete-arsiv/", a[1] if len(a) > 1 else None))
