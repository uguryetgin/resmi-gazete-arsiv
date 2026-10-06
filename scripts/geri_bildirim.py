#!/usr/bin/env python3
"""Günlük bildirim konusundaki ★/✗ yorumlarını altin_kume.txt'e işler (ilgi seçimi geri bildirimi).

Depo sahibi "📰 Günlük bildirim" konusuna yorum yazar; her satır bir işaret:
  ✗ 2            -> günün bildirimindeki 2 numaralı kalem ilgisiz (yanlış ★)
  ★ 7            -> 7 numaralı kalem ilgili (eksik ★)
  ★ 7 9 12       -> birden çok numara
  ✗ Noterlik     -> numara yerine başlık başı (tek kalemle eşleşmeli)
İlgili: ★ + ✓   İlgisiz: ✗ x -
Numaralar, yorumdan önceki son bildirim yorumundaki (<!-- rg-bildirim --> imzalı) sıraya göredir.

Sonuç: altin_kume.txt'in GÜNLÜK GERİ BİLDİRİM bölümüne satır eklenir (aynı başlık varsa işareti
güncellenir) ve main'e commit edilir (git kimliği iş akışında ayarlanır). Yeni ★ eklendiyse o gün için
özet ve kesit iş akışları, yoksa sayfa iş akışı tetiklenir. Yoruma 👍 konur ve kısa bir onay yazılır.
Hata işi başarısız saymaz. Ortam: GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_EVENT_PATH."""
import json, os, re, subprocess, sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bildir                       # noqa: E402
import routine_tetikle as rt        # noqa: E402

DOSYA = Path("altin_kume.txt")
BOLUM = "# ===== GÜNLÜK GERİ BİLDİRİM (bildirim yorumlarındaki ★/✗) ====="
IMZA = "<!-- rg-geri-bildirim -->"
ILGILI, ILGISIZ = "★+✓*", "✗xX-–—"

def isaretler(govde):
    """Yorum govdesi -> [(isaret '+'/'-', hedef)]; hedef numara ya da baslik basi."""
    out = []
    for satir in (govde or "").splitlines():
        s = satir.strip()
        if not s or s[0] not in ILGILI + ILGISIZ:
            continue
        isaret = "+" if s[0] in ILGILI else "-"
        kalan = s[1:].strip(" .:,;")
        if not kalan:
            continue
        if re.fullmatch(r"[\d\s,;]+", kalan):
            out += [(isaret, n) for n in re.findall(r"\d+", kalan)]
        else:
            out.append((isaret, kalan))
    return out

def kalemleri_ayir(govde):
    """Bildirim yorumu -> {no: (baslik, bolum, alan, sayfa)}. ★ kalemler '### N. Baslik', digerleri
    'Diger kalemler' altinda '- **N.** Baslik (s. P)' (eski bicimde numarasiz: sirayla devam)."""
    kal, bolum, diger = {}, None, False
    satirlar = govde.splitlines()
    for i, l in enumerate(satirlar):
        m = re.match(r"^### (\d+)\. (.+)$", l)
        if m:
            m2 = re.match(r"^\*\*(.+?)\*\* · (.+?) · \[s\. (\d+)", satirlar[i + 1]) if i + 1 < len(satirlar) else None
            kal[int(m.group(1))] = (m.group(2).strip(), m2.group(2) if m2 else "?",
                                    m2.group(1) if m2 else "?", m2.group(3) if m2 else "?")
            continue
        if l.startswith("## Diğer kalemler"):
            diger = True
            continue
        if not diger:
            continue
        m = re.match(r"^\*\*(?:\d+\. Mükerrer · )?(.+)\*\*$", l)
        if m:
            bolum = m.group(1)
            continue
        m = re.match(r"^- (?:\*\*(\d+)\.\*\* )?(.+) \(s\. (\d+)\)$", l)
        if m and bolum:
            no = int(m.group(1)) if m.group(1) else len(kal) + 1
            kal[no] = (m.group(2).strip(), bolum, None, m.group(3))
    return kal

def son_bildirim(token, repo, no, once):
    """Yorumdan (once: ISO zaman) onceki son rg-bildirim yorumu -> (ymd, kalemler) ya da (None, {})."""
    since = (datetime.fromisoformat(once.replace("Z", "+00:00")) - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
    yorumlar = bildir.gh(token, "GET", f"/repos/{repo}/issues/{no}/comments", params={"per_page": 100, "since": since})
    for c in reversed(yorumlar):
        m = re.search(r"<!-- rg-bildirim (\d{8}) -->", c.get("body") or "")
        if m and c["created_at"] <= once:
            return m.group(1), kalemleri_ayir(c["body"])
    return None, {}

def coz(hedef, kal):
    """Hedef (numara ya da baslik basi) -> (kalem, hata)."""
    if hedef.isdigit():
        k = kal.get(int(hedef))
        return (k, None) if k else (None, f"{hedef} numaralı kalem yok")
    n = rt.norm(hedef).strip()
    adaylar = [k for k in kal.values() if rt.norm(k[0]).strip().startswith(n)] or \
              [k for k in kal.values() if n in rt.norm(k[0])]
    if len(adaylar) == 1:
        return adaylar[0], None
    return None, f"'{hedef}' eşleşmedi" if not adaylar else f"'{hedef}' {len(adaylar)} kalemle eşleşti, numara verin"

def kaydet(ymd, kayitlar, dosya=DOSYA):
    """[(isaret, kalem)] -> GUNLUK GERI BILDIRIM bolumune yaz (ayni baslik varsa isareti guncelle)."""
    metin = dosya.read_text(encoding="utf-8") if dosya.exists() else ""
    if BOLUM not in metin:
        metin = metin.rstrip("\n") + "\n\n" + BOLUM + "\n"
    satirlar = metin.rstrip("\n").split("\n")
    bas = satirlar.index(BOLUM)
    for isaret, (baslik, bolum, alan, sayfa) in kayitlar:
        yeni = f"[{'★' if isaret == '+' else '-'}] {ymd} | {bolum[:28]} | {('★' + alan) if alan else '-'} | {baslik} (s. {sayfa})"
        anahtar = rt.norm(baslik).strip()
        for i in range(bas + 1, len(satirlar)):
            m = re.match(r"^\[[^\]]*\]\s*\d{8}\s*\|[^|]*\|[^|]*\|\s*(.*)$", satirlar[i])
            if m and rt.norm(m.group(1).split(" (s. ")[0]).strip() == anahtar:
                satirlar[i] = yeni
                break
        else:
            satirlar.append(yeni)
    dosya.write_text("\n".join(satirlar) + "\n", encoding="utf-8")

def git_push(mesaj):
    run = lambda *a: subprocess.run(["git", *a], check=True, capture_output=True, text=True)
    run("add", str(DOSYA))
    run("commit", "-m", mesaj)
    for _ in range(3):
        try:
            run("pull", "--rebase", "--autostash", "origin", "main")
            run("push", "origin", "HEAD:main")
            return True
        except subprocess.CalledProcessError as e:
            print("push denemesi:", (e.stderr or "")[-300:])
    return False

def tetikle(token, repo, dosya, girdiler=None):
    try:
        bildir.gh(token, "POST", f"/repos/{repo}/actions/workflows/{dosya}/dispatches",
                  json={"ref": "main", "inputs": girdiler or {}})
        return dosya
    except Exception as e:
        print(f"{dosya} tetiklenemedi: {e}")
        return None

def main():
    token, repo = os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"]
    olay = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    issue, yorum = olay["issue"], olay["comment"]
    if issue.get("title") != bildir.BASLIK or IMZA in (yorum.get("body") or ""):
        return 0
    isr = isaretler(yorum.get("body"))
    if not isr:
        print("geri bildirim satırı yok")
        return 0
    no = issue["number"]
    ymd, kal = son_bildirim(token, repo, no, yorum["created_at"])
    cevap = []
    if not kal:
        cevap.append("Son 4 günde bildirim yorumu bulunamadı, numaralar çözülemedi.")
    kayit, hata = [], []
    for isaret, hedef in isr:
        k, h = coz(hedef, kal) if kal else (None, None)
        if k:
            kayit.append((isaret, k))
        elif h:
            hata.append(h)
    tarih = f"{ymd[6:]}.{ymd[4:6]}.{ymd[:4]}" if ymd else "?"
    if kayit:
        kaydet(ymd, kayit)
        art, eksi = sum(1 for i, _ in kayit if i == "+"), sum(1 for i, _ in kayit if i == "-")
        if git_push(f"Geri bildirim {tarih}: {art} ilgili, {eksi} ilgisiz"):
            yeni_yildiz = any(i == "+" and not k[2] for i, k in kayit)
            tetiklenen = [t for t in ((tetikle(token, repo, "yz-ozet.yml", {"tarihler": ymd}),
                                       tetikle(token, repo, "pdf-kes.yml", {"tarihler": ymd}))
                                      if yeni_yildiz else (tetikle(token, repo, "site.yml"),)) if t]
            cevap.append(f"Kaydedildi ({tarih}): " + "; ".join(
                f"{'★' if i == '+' else '✗'} {k[0][:70]}" for i, k in kayit) + ".")
            if tetiklenen:
                cevap.append("Tetiklendi: " + ", ".join(tetiklenen) + ".")
        else:
            cevap.append("Dosyaya yazıldı ama main'e gönderilemedi; iş akışı günlüğüne bakın.")
    if hata:
        cevap.append("Anlaşılamadı: " + "; ".join(hata) + ".")
    try:
        bildir.gh(token, "POST", f"/repos/{repo}/issues/comments/{yorum['id']}/reactions",
                  json={"content": "+1" if kayit and not hata else "confused"})
    except Exception as e:
        print("tepki eklenemedi:", e)
    bildir.gh(token, "POST", f"/repos/{repo}/issues/{no}/comments",
              json={"body": "\n".join(cevap) + f"\n\n{IMZA}"})
    print("\n".join(cevap))
    return 0

if __name__ == "__main__":
    sys.exit(main())
