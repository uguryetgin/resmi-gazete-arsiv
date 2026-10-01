#!/usr/bin/env python3
"""Gunluk "Resmi Gazete hazir" bildirimi (Claude Routine'in yerine; yapay zeka/token yok).

Sayfa yayimlandiktan sonra .github/workflows/site.yml calistirir. "📰 Günlük bildirim" Issue'suna
depo sahibini @ ile anan bir yorum yazar; GitHub bunu bildirim (GitHub Mobile'da anlik bildirim,
e-posta) olarak gonderir. Yorumda gunun ★ kalemleri ve sayfanin o gune giden linki vardir.
Ayni gun icin ikinci kez yazmaz (gunluk is gunde birkac kez calisir).

Ortam: GITHUB_TOKEN (issues: write), GITHUB_REPOSITORY, SAYFA_URL"""
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

def ileti(ymd, sahip, url):
    klasor = ROOT / "data" / ymd[:4] / ymd[4:6]
    meta = json.loads((klasor / f"{ymd}.json").read_text(encoding="utf-8"))
    bolumler, _ = ozet_oku(klasor / f"{ymd}.ozet.txt")
    alanlar, haric = rt.ilgi_alanlari()
    toplam, yildiz = 0, []
    for b in bolumler:
        for k in b["kalemler"]:
            toplam += 1
            alan = rt.alan_bul(k["baslik"], alanlar, haric)
            if alan:
                b_ = k["baslik"] if len(k["baslik"]) <= 130 else k["baslik"][:127].rsplit(" ", 1)[0] + "…"
                yildiz.append(f"- ★ **{alan}** — {b_} (s. {k['sayfa']})")
    satir = [f"@{sahip} **Resmî Gazete {meta['tarih']} hazır** (Sayı {meta.get('sayi')}, {toplam} kalem) — "
             f"👉 **[Sayfada aç]({url}#{ymd})** · 🔊 **[Dinle]({url}#{ymd}-dinle)**", ""]
    if yildiz:
        satir += [f"İlgi alanına giren {len(yildiz)} kalem:"] + yildiz[:12]
        if len(yildiz) > 12:
            satir.append(f"- … ve {len(yildiz) - 12} kalem daha")
    else:
        satir.append("İlgi alanına giren kalem yok.")
    satir += ["", IMZA.format(ymd=ymd)]
    return "\n".join(satir)

def main():
    token, repo = os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"]
    sahip = repo.split("/")[0]
    url = os.environ.get("SAYFA_URL") or f"https://{sahip}.github.io/{repo.split('/')[1]}/"
    ymd = json.loads((ROOT / "data" / "latest.json").read_text(encoding="utf-8"))["ymd"]
    acik = gh(token, "GET", f"/repos/{repo}/issues", params={"state": "all", "creator": "github-actions[bot]",
                                                            "per_page": 100})
    issue = next((i for i in acik if i.get("title") == BASLIK and not i.get("pull_request")), None)
    if issue is None:
        issue = gh(token, "POST", f"/repos/{repo}/issues", json={"title": BASLIK, "body":
            "Her gün Resmî Gazete işlenip sayfa yayımlanınca bu konuya bir yorum yazılır ve "
            f"@{sahip} anılır; GitHub bunu bildirim olarak gönderir. Bu konuyu kapatmayın."})
    elif issue.get("state") == "closed":
        gh(token, "PATCH", f"/repos/{repo}/issues/{issue['number']}", json={"state": "open"})
    son = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")
    yorumlar = gh(token, "GET", f"/repos/{repo}/issues/{issue['number']}/comments",
                  params={"per_page": 100, "since": son})
    if any(IMZA.format(ymd=ymd) in (c.get("body") or "") for c in yorumlar):
        print(f"{ymd} icin bildirim zaten gonderilmis.")
        return 0
    gh(token, "POST", f"/repos/{repo}/issues/{issue['number']}/comments", json={"body": ileti(ymd, sahip, url)})
    print(f"{ymd} bildirimi gonderildi (#{issue['number']}).")
    return 0

if __name__ == "__main__":
    sys.exit(main())
