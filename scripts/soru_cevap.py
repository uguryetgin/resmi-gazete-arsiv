#!/usr/bin/env python3
"""GitHub Issue'daki soruyu yapay zeka (OpenAI uyumlu uc nokta; bkz. yz_ozet.URL) ile cevapla (web sayfasindaki "Sor" dugmeleri).

Issue govdesindeki "Gün: YYYYAAGG" ve (varsa) "Kalem: Baslik (s. N)" satirlarindan gunun
metni bulunur; kalem varsa onun gazetedeki metni, yoksa gunun ozeti baglam olarak gonderilir.
Issue'daki onceki yorumlar sohbet gecmisi olur. Cevap Issue'ya yorum olarak yazilir.

.github/workflows/soru.yml calistirir (yalniz depo sahibinin issue/yorumlarinda).
Ortam: GITHUB_TOKEN (issues: write), YZ_TOKEN (Models: read izinli kisisel token; yoksa
       GITHUB_TOKEN), GITHUB_REPOSITORY, GITHUB_EVENT_PATH"""
import gzip, json, os, re, sys
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rg_fetch as rg            # noqa: E402
from yz_ozet import URL, MODEL   # noqa: E402

ROOT = Path(".")
API = "https://api.github.com"
IMZA = "<!-- rg-soru-bot -->"
BAGLAM_SINIR = 14000             # karakter; ucretsiz katmanda istek basina girdi siniri dusuk
GECMIS_SINIR = 6                 # en son bu kadar soru/cevap mesaji

SISTEM = ("Resmî Gazete metinleri hakkında soruları yanıtlayan bir asistansın. Türkçe, kısa ve net "
          "yanıt ver. Yalnız verilen gazete metnine dayan; metinde yoksa \"metinde bu bilgi yok\" de ve "
          "tahmin yürütme. Madde, tarih, tutar ve kanun numaralarını aynen aktar; yanıtın sonunda "
          "dayandığın maddeyi belirt. Yorum yaparsan \"(yorum)\" diye işaretle. Hukuki danışmanlık yapma.")

def gh(token, yontem, yol, **kw):
    r = requests.request(yontem, API + yol, timeout=30, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"}, **kw)
    r.raise_for_status()
    return r.json()

def baglam(govde):
    """Issue govdesinden (gun, kalem, metin). Gun yoksa son gun."""
    m = re.search(r"Gün:\s*(\d{8})", govde or "")
    ymd = m.group(1) if m else json.loads((ROOT / "data" / "latest.json").read_text(encoding="utf-8"))["ymd"]
    k = re.search(r"Kalem:\s*(.+)", govde or "")
    kalem = k.group(1).strip() if k else ""
    yol = ROOT / "data" / ymd[:4] / ymd[4:6]
    meta = json.loads((yol / f"{ymd}.json").read_text(encoding="utf-8"))
    baslik = f"Resmî Gazete {meta['tarih']} – Sayı {meta.get('sayi')}"
    if kalem:
        text = gzip.open(yol / f"{ymd}.txt.gz", "rt", encoding="utf-8").read()
        pages = rg.sayfalar(text)
        kalemler, ilan = rg.icindekiler_kalemleri(rg.fihrist(text))
        son_sayfa = (ilan or max(pages)) - 1
        for i, (_, b, s) in enumerate(kalemler):
            if f"{b} (s. {s})" == kalem or b == kalem:
                metin = rg.kalem_metni(pages, kalemler, i, son_sayfa, ek_sayfa=60) or ""
                metin = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", metin)).strip()
                return ymd, kalem, f"{baslik}\nKalem: {kalem}\n\n{metin[:BAGLAM_SINIR]}"
    ozet = (yol / f"{ymd}.ozet.txt").read_text(encoding="utf-8") if (yol / f"{ymd}.ozet.txt").exists() else ""
    return ymd, "", f"{baslik}\nGünün kalemleri ve ilk madde alıntıları:\n\n{ozet[:BAGLAM_SINIR]}"

def temizle(govde):
    """Soru metni: sablon satirlari ve HTML yorumlari atilir."""
    govde = re.sub(r"<!--.*?-->", "", govde or "", flags=re.S)
    satir = [l for l in govde.splitlines() if not re.match(r"\s*(Gün|Kalem|Sayfa):", l)]
    return "\n".join(satir).replace("Sorunuz:", "").strip()

def sor(token, mesajlar):
    r = requests.post(URL, timeout=120, json={"model": MODEL, "temperature": 0.2, "max_tokens": 1200,
                                              "messages": mesajlar}, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
    if r.status_code == 429:
        return None
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def main():
    token, repo = os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"]
    olay = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    issue = olay["issue"]
    no = issue["number"]
    if IMZA in ((olay.get("comment") or {}).get("body") or ""):
        return 0                                  # kendi cevabimiz
    ymd, kalem, metin = baglam(issue.get("body"))
    gecmis = [{"role": "user", "content": temizle(issue.get("body")) or issue.get("title", "")}]
    for c in gh(token, "GET", f"/repos/{repo}/issues/{no}/comments", params={"per_page": 100}):
        bot = IMZA in (c.get("body") or "")
        icerik = (c.get("body") or "").split(IMZA)[0].strip() if bot else temizle(c.get("body"))
        if icerik:
            gecmis.append({"role": "assistant" if bot else "user", "content": icerik})
    gecmis = gecmis[:1] + gecmis[1:][-GECMIS_SINIR:]
    if gecmis[-1]["role"] != "user":
        return 0                                  # cevaplanacak yeni soru yok
    mesajlar = [{"role": "system", "content": SISTEM},
                {"role": "user", "content": "Gazete metni (yalnız buna dayan):\n\n" + metin},
                {"role": "assistant", "content": "Metni okudum; sorunuzu yanıtlayabilirim."}] + gecmis
    try:
        cevap = sor(os.environ.get("YZ_TOKEN", "").strip() or token, mesajlar)
    except Exception as e:
        cevap = f"Yanıt alınamadı ({e.__class__.__name__}). Biraz sonra yeni bir yorumla tekrar deneyin."
    if cevap is None:
        cevap = "Yapay zekâ servisinin ücretsiz kotası şu an dolu. Biraz sonra yeni bir yorumla tekrar sorun."
    kaynak = f"RG {ymd[6:]}.{ymd[4:6]}.{ymd[:4]}" + (f" · {kalem}" if kalem else " · günün özeti")
    gh(token, "POST", f"/repos/{repo}/issues/{no}/comments", json={"body":
        f"{cevap}\n\n{IMZA}\n<sub>Kaynak: {kaynak} · yapay zekâ ({MODEL}); hata içerebilir, kesin bilgi "
        f"için gazete metnine bakın. Devam sorusu için yorum yazın.</sub>"})
    print(f"#{no} cevaplandi ({ymd}, {kalem[:60] or 'gun'})")
    return 0

if __name__ == "__main__":
    sys.exit(main())
