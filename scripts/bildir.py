#!/usr/bin/env python3
"""Gunluk "Resmi Gazete hazir" bildirimi (Claude Routine'in yerine; yapay zeka/token yok).

Sayfa yayimlandiktan sonra .github/workflows/site.yml calistirir. "📰 Günlük bildirim" Issue'suna
depo sahibini @ ile anan bir yorum yazar; GitHub bunu bildirim (GitHub Mobile'da anlik bildirim,
e-posta) olarak gonderir. Yorumda gunun icerigi (★ kalemlerin otomatik ozeti, diger kalemler) ve linkler vardir.
Ayni gun icin ikinci kez yazmaz (gunluk is gunde birkac kez calisir).

Yorumda gunun ozet PDF'inin (scripts/ozet_pdf.py; release eki) linki de olur.
Ortam: GITHUB_TOKEN (issues: write, contents: write), GITHUB_REPOSITORY, SAYFA_URL,
       BILDIRIM_TEKRAR=true (ayni gunu yeniden gonder)"""
import json, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import routine_tetikle as rt     # noqa: E402
from sayfa import ozet_oku       # noqa: E402

ROOT = Path(".")
API = "https://api.github.com"
BASLIK = "📰 Günlük bildirim"
IMZA = "<!-- rg-bildirim {ymd} -->"

def gh(token, yontem, yol, **kw):
    r = requests.request(yontem, API + yol, timeout=30, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"}, **kw)
    r.raise_for_status()
    return r.json() if r.text else {}

def ileti(ymd, sahip, url, pdf_link=None):
    """Bildirim yorumu: linkler + gunun icerigi (★ kalemlerin uzun ozeti, yururluk, etiketler, sayfa;
       diger kalemlerin listesi). GitHub bildirim e-postasi yorumun tamamini icerir."""
    import etiket
    import ozet_pdf
    gun = ozet_pdf.gun_al(ymd)
    ys = ozet_pdf.yildizlar(gun)
    toplam = sum(len(b["kalemler"]) for s in gun["sayilar"] for b in s["bolumler"])
    satir = [f"@{sahip} **Resmî Gazete {gun['tarih']} hazır** (Sayı {gun['sayi']}, {toplam} kalem, "
             f"{len(ys)} ilgi alanı kalemi)", "",
             f"👉 **[Sayfada aç]({url}#{ymd})** · 🔊 **[Dinle]({url}#{ymd}-dinle)**"
             + (f" · 📄 **[Özet PDF]({pdf_link})**" if pdf_link else "")
             + f" · [Gazetenin PDF'i]({gun['pdf']})", ""]
    satir.append(f"## ★ İlgi alanına girenler ({len(ys)})" if ys else "## İlgi alanına giren kalem yok")
    for i, (s, b, k) in enumerate(ys, 1):
        ar = k.get("kesit") or {}
        sf = f"s. {ar['ilk']}–{ar['son']}" if ar and ar["son"] > ar["ilk"] else f"s. {k['sayfa']}"
        ets = [x for x in k.get("etiket") or [] if x != etiket.ETIKETSIZ]
        satir += ["", f"### {i}. {k['baslik']}",
                  f"**{k['alan']}** · {b['bolum']} · [{sf} (PDF)]({s.get('pdf') or gun['pdf']}#page={k['sayfa']})"
                  + (" · " + " ".join(f"`{x}`" for x in ets) if ets else ""), ""]
        yz = k.get("yz") or {}
        if yz.get("ne_getiriyor"):
            satir += [f"- {m}" for m in yz["ne_getiriyor"]]
            if yz.get("yururluk"):
                satir += ["", f"**Yürürlük:** {yz['yururluk']}"]
        elif k.get("alinti"):
            satir.append(f"> {k['alinti'][:600]}")
    diger = [(s, b, [k for k in b["kalemler"] if not k.get("alan")]) for s in gun["sayilar"] for b in s["bolumler"]]
    diger = [(s, b, ks) for s, b, ks in diger if ks]
    if diger:
        satir += ["", f"## Diğer kalemler ({sum(len(ks) for _, _, ks in diger)})"]
        n = len(ys)                                   # numaralar ★ listesinden devam eder (geri bildirim icin)
        for s, b, ks in diger:
            satir += ["", f"**{(str(s['mukerrer']) + '. Mükerrer · ') if s['mukerrer'] else ''}{b['bolum']}**"]
            for k in ks:
                n += 1
                satir.append(f"- **{n}.** {k['baslik']} (s. {k['sayfa']})")
    satir += ["", "<sub>Otomatik özetler yapay zekâ ile üretilir, hata içerebilir; kesin metin için PDF.</sub>",
              "", IMZA.format(ymd=ymd)]
    metin = "\n".join(satir)
    return metin if len(metin) < 60000 else metin[:59000] + "\n\n… (devamı sayfada)\n\n" + IMZA.format(ymd=ymd)

def main(argv=()):
    token, repo = os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"]
    sahip = repo.split("/")[0]
    url = os.environ.get("SAYFA_URL") or f"https://{sahip}.github.io/{repo.split('/')[1]}/"
    yok = len(argv) == 2 and argv[0] == "yok"      # "bugun yayimlanmadi" bildirimi (resmi-gazete.yml)
    ymd = argv[1] if yok else json.loads((ROOT / "data" / "latest.json").read_text(encoding="utf-8"))["ymd"]
    imza = f"<!-- rg-bildirim-yok {ymd} -->" if yok else IMZA.format(ymd=ymd)
    acik = gh(token, "GET", f"/repos/{repo}/issues", params={"state": "all", "creator": "github-actions[bot]",
                                                            "per_page": 100})
    issue = next((i for i in acik if i.get("title") == BASLIK and not i.get("pull_request")), None)
    if issue is None:
        issue = gh(token, "POST", f"/repos/{repo}/issues", json={"title": BASLIK, "body":
            "Her gün Resmî Gazete işlenip sayfa yayımlanınca bu konuya bir yorum yazılır ve "
            f"@{sahip} anılır; GitHub bunu bildirim olarak gönderir. Bu konuyu kapatmayın."})
    elif issue.get("state") == "closed":
        gh(token, "PATCH", f"/repos/{repo}/issues/{issue['number']}", json={"state": "open"})
    if not issue.get("locked"):          # depo herkese acik: yalniz ortak calisanlar yorum yazabilsin
        try:
            gh(token, "PUT", f"/repos/{repo}/issues/{issue['number']}/lock", json={"lock_reason": "resolved"})
        except Exception as e:
            print(f"::warning::Issue kilitlenemedi: {e}")
    son = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")
    yorumlar = gh(token, "GET", f"/repos/{repo}/issues/{issue['number']}/comments",
                  params={"per_page": 100, "since": son})
    tekrar = os.environ.get("BILDIRIM_TEKRAR", "").lower() == "true"
    # Yalniz botun kendi yorumlari sayilir: baskasinin yazdigi sahte isaret bildirimi engelleyemesin
    yorumlar = [c for c in yorumlar if (c.get("user") or {}).get("login") == "github-actions[bot]"]
    if not tekrar and any(imza in (c.get("body") or "") for c in yorumlar):
        print(f"{ymd} icin bildirim zaten gonderilmis.")
        return 0
    if yok:
        tarih = f"{ymd[6:]}.{ymd[4:6]}.{ymd[:4]}"
        gh(token, "POST", f"/repos/{repo}/issues/{issue['number']}/comments", json={"body":
           f"@{sahip} **Resmî Gazete {tarih} yayımlanmadı** — bugün sabah 09:11'e kadar yeni sayı bulunamadı "
           f"(resmigazete.gov.tr'de o güne ait PDF yok).\n\n{imza}"})
        print(f"{ymd} 'yayimlanmadi' bildirimi gonderildi.")
        return 0
    import ozet_pdf              # gunun ozet PDF'i -> release eki; linki yorumda
    pdf_link = ozet_pdf.ozet_pdf_linki(ymd, url, token, repo)
    gh(token, "POST", f"/repos/{repo}/issues/{issue['number']}/comments",
       json={"body": ileti(ymd, sahip, url, pdf_link)})
    print(f"{ymd} bildirimi gonderildi (#{issue['number']}).")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
