#!/usr/bin/env python3
"""★ kalemler icin yapay zeka ile kisa ozet (web sayfasi icin; Claude token'i harcamaz).

Her ★ kalemin gazetedeki tam metni (routine_tetikle.tam_metinler) YZ_URL'deki modele gonderilir,
donen "Ne getiriyor" maddeleri ve yururluk satiri data/YYYY/AA/YYYYAAGG.yz.json'a yazilir.
Ozeti olan kalem yeniden sorulmaz. Kota/hata durumunda uyari yazar, isi basarisiz saymaz.

Kullanim: python3 scripts/yz_ozet.py [YYYYAAGG ...]   (bos = data/latest.json'daki gun;
          "hepsi" = ozeti eksik tum gunler, en yeniden eskiye, YZ_GUN_SINIR kadar)
Ortam: YZ_TOKEN (saglayici anahtari; yoksa GITHUB_TOKEN), YZ_URL (OpenAI uyumlu
       chat/completions adresi; varsayilan GitHub Models), YZ_MODEL (varsayilan openai/gpt-4.1-mini)"""
import json, os, re, sys, time
from datetime import datetime, timezone
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rg_fetch as rg            # noqa: E402
import routine_tetikle as rt     # noqa: E402
from sayfa import ozet_oku       # noqa: E402  (scripts/sayfa.py)

ROOT = Path(".")
# OpenAI uyumlu herhangi bir uc nokta: GitHub Models (varsayilan) ya da Gemini, Groq vb.
URL = os.environ.get("YZ_URL", "").strip() or "https://models.github.ai/inference/chat/completions"
MODEL = os.environ.get("YZ_MODEL", "").strip() or "openai/gpt-4.1-mini"
# Ana model yogunluktan (503) ya da kaldirildigi icin (404) yanit vermezse sirayla denenenler
YEDEKLER = [m.strip() for m in os.environ.get("YZ_YEDEK_MODELLER", "").split(",") if m.strip()]
SON_MODEL = MODEL          # son basarili cagrinin modeli (kayit/etiket icin)
METIN_SINIR = 30000        # karakter (Gemini girdisi genis; uzun kalemlerin tamami ozetlensin)
SURUM = 2                  # ozet bicimi; eski surumdeki ozetler yeniden uretilir
BEKLE = 12                 # istekler arasi saniye (ucretsiz katman dakikalik kotasi dusuk)
GUN_SINIR = int(os.environ.get("YZ_GUN_SINIR", "30") or 30)

SISTEM = ("Türkçe hukuk metinlerini özetleyen dikkatli bir asistansın. Yalnız verilen metinde yazanı "
          "kullan; metinde olmayan bilgi, gerekçe ya da yorum ekleme. Rakamları, tarihleri, kanun ve "
          "madde numaralarını aynen aktar. Yanıtın yalnız geçerli bir JSON nesnesi olsun.")
ISTEK = ("Aşağıdaki Resmî Gazete kaleminin metnini özetle.\n"
         "JSON biçimi: {{\"ne_getiriyor\": [\"...\", ...], \"yururluk\": \"...\"}}\n"
         "- ne_getiriyor: 5-10 madde (her biri 1-2 cümle, en çok 45 kelime), metnin sırasıyla tüm "
         "esaslı hükümleri kapsasın: kimi/neyi kapsıyor, ne yapılıyor ya da neyi nasıl değiştiriyor "
         "(eski/yeni hâli belliyse ikisini de yaz), tutar/oran/süre/tarih gibi somut rakamlar, "
         "yükümlülükler, yasaklar, yaptırımlar, istisnalar ve geçiş hükümleri. Okuyan metni açmadan "
         "ne getirdiğini anlamalı. Tanım, amaç, dayanak ve yürütme maddelerini atla.\n"
         "- yururluk: yürürlük tarihi ve varsa geçiş/son başvuru süreleri, yürürlükten kaldırılan "
         "mevzuat; metinde yoksa \"metinde belirtilmemiş\".\n\n"
         "Başlık: {baslik}\n\nMetin:\n{metin}")

def yol(ymd):
    return ROOT / "data" / ymd[:4] / ymd[4:6] / f"{ymd}.yz.json"

def istek(token, govde, deneme=3):
    """POST; kota (429) ve gecici sunucu hatalarinda (5xx) bekleyip yeniden dener, olmazsa ya da
       model bulunamazsa (404) YEDEKLER'deki sonraki modele gecer (her modelin kotasi ayri)."""
    global SON_MODEL
    for model in dict.fromkeys([govde.get("model") or MODEL] + YEDEKLER):
        govde = dict(govde, model=model)
        for i in range(deneme):
            r = requests.post(URL, json=govde, timeout=120, headers={
                "Authorization": f"Bearer {token}", "Accept": "application/json",
                "Content-Type": "application/json"})
            if r.status_code not in (429, 500, 502, 503, 504):
                break
            if i < deneme - 1:      # 429: dakikalik kota; ucretsiz katmanda ~1 dk beklemek yeter
                time.sleep(30 * (i + 1) if r.status_code == 429 else 10 * (i + 1))
        if r.status_code not in (404, 429, 500, 502, 503, 504):
            SON_MODEL = model
            return r
        print(f"::warning::{model}: HTTP {r.status_code}, sonraki model deneniyor")
    return r

def sor(token, baslik, metin):
    govde = {"model": MODEL, "temperature": 0.1, "max_tokens": 8000,   # dusunen modeller payi
             "response_format": {"type": "json_object"},
             "messages": [{"role": "system", "content": SISTEM},
                          {"role": "user", "content": ISTEK.format(baslik=baslik, metin=metin)}]}
    r = istek(token, govde)
    if r.status_code == 429:
        raise KotaDoldu(r.text[:200])
    r.raise_for_status()
    try:
        icerik = r.json()["choices"][0]["message"]["content"] or ""
        m = re.search(r"\{.*\}", icerik, re.S)
        veri = json.loads(m.group(0) if m else icerik)
    except Exception as e:
        raise ValueError(f"{e} | HTTP {r.status_code} {r.headers.get('content-type')} | {r.text[:300]!r}")
    maddeler = [str(x).strip() for x in veri.get("ne_getiriyor") or [] if str(x).strip()][:10]
    if not maddeler:
        raise ValueError("bos yanit")
    return {"ne_getiriyor": maddeler, "yururluk": str(veri.get("yururluk") or "").strip(), "surum": SURUM}

class KotaDoldu(Exception):
    pass

def gun_isle(ymd, token, alanlar, haric):
    """Gunun eksik ★ ozetlerini doldurur; (yeni ozet sayisi) dondurur. KotaDoldu yukari gecer."""
    meta_yol = ROOT / "data" / ymd[:4] / ymd[4:6] / f"{ymd}.json"
    if not meta_yol.exists():
        return 0
    meta = json.loads(meta_yol.read_text(encoding="utf-8"))
    bolumler, _ = ozet_oku(meta_yol.with_name(f"{ymd}.ozet.txt"))
    eslesen = {f"{k['baslik']} (s. {k['sayfa']})": a for b in bolumler for k in b["kalemler"]
               if (a := rt.alan_bul(k["baslik"], alanlar, haric))}
    if not eslesen:
        return 0
    kayit = json.loads(yol(ymd).read_text(encoding="utf-8")) if yol(ymd).exists() else {"kalemler": {}}
    eksik = {k: a for k, a in eslesen.items()
             if (kayit["kalemler"].get(k) or {}).get("surum", 1) < SURUM}
    if not eksik:
        return 0
    tam = rt.tam_metinler(f"Resmî Gazete {meta['tarih']}", eksik, 10 ** 7, METIN_SINIR)
    yeni = 0
    try:
        for k, metin, _ in tam:
            if rg.temiz_oran(metin) < rg.BOZUK_ESIK:   # PDF metin katmani bozuk, OCR'lanmamis
                print(f"::warning::{ymd} '{k[:60]}': metin bozuk (OCR yok), ozet atlandi")
                continue
            if len(metin) > METIN_SINIR:
                metin = metin[:METIN_SINIR].rsplit(" ", 1)[0] + " …"
            try:
                kayit["kalemler"][k] = sor(token, k, metin)
                yeni += 1
                kaydet(ymd, kayit)          # adim zaman asimina ugrarsa yazilanlar kaybolmasin
            except KotaDoldu:
                raise
            except Exception as e:
                print(f"::warning::{ymd} '{k[:60]}': {e}")
            time.sleep(BEKLE)
    finally:
        if yeni:
            kaydet(ymd, kayit)
    return yeni

def kaydet(ymd, kayit):
    kayit.update({"model": SON_MODEL, "guncelleme": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    yol(ymd).write_text(json.dumps(kayit, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

def tani(token):
    """Gecici: saglayicida hangi modeller calisiyor."""
    taban = URL.rsplit("/chat/completions", 1)[0]
    try:
        r = requests.get(taban + "/models", timeout=30, headers={"Authorization": f"Bearer {token}"})
        adlar = [m.get("id") for m in (r.json().get("data") or [])] if r.ok else []
        print(f"TANI modeller: HTTP {r.status_code} | {', '.join(a for a in adlar if 'gemini' in str(a))[:900] or r.text[:300]}")
    except Exception as e:
        print(f"TANI modeller: {e}")
    for model in [MODEL, "gemini-3.8-flash-lite", "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash-lite"]:
        for rf in (False, True):
            govde = {"model": model, "max_tokens": 30, "messages": [{"role": "user", "content": "Sadece 'merhaba' yaz."}]}
            if rf:
                govde["response_format"] = {"type": "json_object"}
                govde["messages"][0]["content"] = 'Yalnız {"selam":"merhaba"} JSON\'unu yaz.'
            try:
                r = requests.post(URL, json=govde, timeout=60, headers={"Authorization": f"Bearer {token}"})
                print(f"TANI {model} json={rf}: HTTP {r.status_code} | {r.text[:220]!r}")
            except Exception as e:
                print(f"TANI {model} json={rf}: {e}")

def main(argv):
    token = (os.environ.get("YZ_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")).strip()
    if not token:
        print("YZ_TOKEN/GITHUB_TOKEN yok, atlandi.")
        return 0
    if argv == ["tani"]:
        tani(token)
        return 0
    if argv == ["hepsi"]:
        gunler = sorted((p.stem for p in ROOT.glob("data/[0-9]*/[0-9]*/[0-9]*.json")
                         if re.fullmatch(r"\d{8}", p.stem)), reverse=True)[:GUN_SINIR]
    elif argv:
        gunler = [a for a in argv if re.fullmatch(r"\d{8}", a)]
    else:
        gunler = [json.loads((ROOT / "data" / "latest.json").read_text(encoding="utf-8"))["ymd"]]
    alanlar, haric = rt.ilgi_alanlari()
    toplam = 0
    for ymd in gunler:
        try:
            n = gun_isle(ymd, token, alanlar, haric)
        except KotaDoldu as e:
            print(f"::warning::Yapay zeka kotasi doldu ({ymd}); kalan gunler sonraki kosuma: {e}")
            break
        except Exception as e:
            print(f"::warning::{ymd}: {e}")
            continue
        if n:
            print(f"{ymd}: {n} ★ ozet ({SON_MODEL})")
        toplam += n
    print(f"Toplam {toplam} yeni ozet.")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
