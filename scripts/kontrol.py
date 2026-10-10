#!/usr/bin/env python3
"""Gunluk saglik kontrolu (yapay zeka/token yok): bugunun zinciri eksiksiz mi?

.github/workflows/kontrol.yml her sabah (10:13 TR) calistirir. Sorun varsa "📰 Günlük bildirim"
Issue'suna depo sahibini anan "⚠️ Sistem uyarısı" yorumu yazar (gunde bir kez); GitHub bunu
e-posta/bildirim olarak gonderir. Sorun yoksa hicbir sey yazmaz.

Bakilanlar: gazete islendi mi (ya da "yayimlanmadi" bildirimi var mi), gunluk bildirim gitti mi,
★ kalemlerin otomatik ozeti ve PDF kesiti var mi, ozet PDF'i ve e-posta sayfasi release'te mi,
bugun calisan is akislarinda basarisiz is ya da (continue-on-error ile gizlenen) basarisiz adim var mi.
Ortam: GITHUB_TOKEN (issues: write, actions: read), GITHUB_REPOSITORY; istege bagli KONTROL_TARIH."""
import json, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bildir                    # noqa: E402
import routine_tetikle as rt     # noqa: E402
from sayfa import ozet_oku       # noqa: E402

ROOT = Path(".")
TRT = timezone(timedelta(hours=3))

def sorunlar(token, repo, ymd, yorumlar):
    out = []
    klasor = ROOT / "data" / ymd[:4] / ymd[4:6]
    yorum_metni = "\n".join(c.get("body") or "" for c in yorumlar)
    if not (klasor / f"{ymd}.json").exists():
        if f"rg-bildirim-yok {ymd}" not in yorum_metni:
            out.append("Bugünün gazetesi işlenmedi ve \"yayımlanmadı\" bildirimi de gitmedi.")
        return out
    if bildir.IMZA.format(ymd=ymd) not in yorum_metni:
        out.append("Gazete işlendi ama günlük bildirim (e-posta) gönderilmedi.")
    alanlar, haric = rt.ilgi_alanlari()
    bolumler, _ = ozet_oku(klasor / f"{ymd}.ozet.txt")
    yildiz = [f"{k['baslik']} (s. {k['sayfa']})" for b in bolumler for k in b["kalemler"]
              if rt.alan_bul(k["baslik"], alanlar, haric)]
    if yildiz:
        oku = lambda ad: json.loads((klasor / ad).read_text(encoding="utf-8")) if (klasor / ad).exists() else {}
        yz = oku(f"{ymd}.yz.json").get("kalemler", {})
        eksik = [k for k in yildiz if (yz.get(k) or {}).get("surum", 1) < 2]
        if eksik:
            out.append(f"{len(eksik)}/{len(yildiz)} ilgi alanı kaleminin otomatik özeti yok "
                       "(Gemini kotası ya da hata; \"GitHub Models özetleri\" iş akışını elle çalıştırın).")
        metinsiz = [k for k in yildiz if (yz.get(k) or {}).get("hata")]
        if metinsiz:      # fihristte var, sayfa metninde yok (gorsel basilmis karar vb.): is akisi cozmez
            out.append(f"{len(metinsiz)}/{len(yildiz)} ilgi alanı kaleminin metni gazete metninde bulunamadı "
                       "(görsel/taranmış sayfa), özeti üretilemiyor; PDF'e bakın: "
                       + "; ".join(k[:70] for k in metinsiz))
        kesit = oku(f"{ymd}.kesit.json")
        eksik = [k for k in yildiz if k not in kesit]
        if eksik:
            out.append(f"{len(eksik)}/{len(yildiz)} ilgi alanı kaleminin PDF kesiti yok "
                       "(\"PDF kesitleri\" iş akışını elle çalıştırın).")
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    r = requests.get(f"https://api.github.com/repos/{repo}/releases/tags/rg-{ymd}", headers=h, timeout=30)
    varlik = [a["name"] for a in r.json().get("assets", [])] if r.ok else []
    for ad in (f"Ozet-{ymd}.pdf", f"Eposta-{ymd}.html"):
        if ad not in varlik:
            out.append(f"Release'te {ad} yok (özet PDF'i / e-posta sayfası üretilemedi).")
    return out

def basarisiz_isler(token, repo, ymd):
    """Bugun (TR) baslayan kosumlarda basarisiz is ya da basarisiz adim: [(metin, link)]."""
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    bas = datetime(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]), tzinfo=TRT).astimezone(timezone.utc)
    r = requests.get(f"https://api.github.com/repos/{repo}/actions/runs", headers=h, timeout=30,
                     params={"per_page": 100, "created": f">={bas.strftime('%Y-%m-%dT%H:%M:%SZ')}"})
    out, kosumlar = [], (r.json().get("workflow_runs", []) if r.ok else [])
    # Ayni is akisinin daha sonraki bir kosumu basariliysa onceki hata kendiliginden duzelmistir
    son_basari = {}
    for run in kosumlar:
        if run.get("conclusion") == "success":
            son_basari[run["name"]] = max(son_basari.get(run["name"], ""), run["created_at"])
    for run in kosumlar:
        if run.get("status") != "completed" or run.get("name") == "Sağlık kontrolü":
            continue
        if son_basari.get(run["name"], "") > run["created_at"]:
            continue
        if run.get("conclusion") in ("failure", "timed_out", "startup_failure"):
            out.append((f"**{run['name']}** başarısız ({run['conclusion']})", run["html_url"]))
            continue
        isler = requests.get(run["jobs_url"], headers=h, timeout=30).json().get("jobs", [])
        for j in isler:
            for s in j.get("steps") or []:
                if s.get("conclusion") == "failure":
                    out.append((f"**{run['name']}** → \"{s['name']}\" adımı hata verdi (iş devam etti)", j["html_url"]))
    return out

def main():
    token, repo = os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"]
    sahip = repo.split("/")[0]
    ymd = os.environ.get("KONTROL_TARIH") or datetime.now(TRT).strftime("%Y%m%d")
    acik = bildir.gh(token, "GET", f"/repos/{repo}/issues",
                     params={"state": "all", "creator": "github-actions[bot]", "per_page": 100})
    issue = next((i for i in acik if i.get("title") == bildir.BASLIK and not i.get("pull_request")), None)
    son = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")
    yorumlar = bildir.gh(token, "GET", f"/repos/{repo}/issues/{issue['number']}/comments",
                         params={"per_page": 100, "since": son}) if issue else []
    yorumlar = [c for c in yorumlar if (c.get("user") or {}).get("login") == "github-actions[bot]"]
    liste = sorunlar(token, repo, ymd, yorumlar)
    liste += [f"{m} — [kayıt]({u})" for m, u in basarisiz_isler(token, repo, ymd)]
    tarih = f"{ymd[6:]}.{ymd[4:6]}.{ymd[:4]}"
    if not liste:
        print(f"{tarih}: sorun yok.")
        return 0
    print("\n".join(liste))
    imza = f"<!-- rg-uyari {ymd} -->"
    if not issue or any(imza in (c.get("body") or "") for c in yorumlar):
        return 0
    bildir.gh(token, "POST", f"/repos/{repo}/issues/{issue['number']}/comments", json={"body":
        f"@{sahip} ⚠️ **Sistem uyarısı {tarih}** — sabah kontrolünde sorun bulundu:\n\n"
        + "\n".join(f"- {x}" for x in liste)
        + f"\n\n[Actions sayfası](https://github.com/{repo}/actions) · Düzeltmek için Claude'a bu yorumu iletebilirsiniz."
        + f"\n\n{imza}"})
    return 0

if __name__ == "__main__":
    sys.exit(main())
