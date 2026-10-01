/**
 * Resmî Gazete günlük e-postasını Gmail'den başka adreslere gizli (BCC) iletir.
 * Google Apps Script — kendi Google hesabında, ücretsiz çalışır; GitHub'a şifre/anahtar gerekmez.
 *
 * KURULUM (bir kez, ~5 dk):
 *  1. https://script.google.com → "Yeni proje" → bu dosyanın tamamını yapıştır.
 *  2. Aşağıdaki ALICILAR listesine adresleri yaz. (Bu liste yalnız senin Google hesabında durur;
 *     GitHub'a yazma.)
 *  3. Üstten "ilet" fonksiyonunu seç → "Çalıştır" → Google izin ister (Gmail okuma/gönderme) → izin ver.
 *     İlk çalıştırma son 3 günün bildirimini hemen iletir (deneme). Bir şey iletilmezse "tani"yi çalıştır.
 *  4. Soldaki saat simgesi (Tetikleyiciler) → "Tetikleyici ekle" → fonksiyon: ilet,
 *     olay kaynağı: "Zamana dayalı", "Saat zamanlayıcısı" → "Her saat" → Kaydet.
 *
 * Ne yapar: GitHub'dan gelen "📰 Günlük bildirim" e-postasını bulur; GitHub'ın "yanıtla / aboneliği
 * bırak" alt bilgisini ve @anmayı temizler; "Resmî Gazete GG.AA.YYYY" konulu e-postayı kendine
 * gönderir, ALICILAR'ı gizli alıcı (BCC) yapar — alıcılar birbirini görmez, yanıtlasalar sana gelir
 * (GitHub'a gitmez). Özet PDF'i varsa ek olarak koyar. Gönderdiği günleri hatırlar, aynı günü ikinci
 * kez göndermez (GitHub her günün bildirimini Gmail'de aynı konuşmaya ekler; bu yüzden etiket değil
 * gün listesi tutulur).
 */
var ALICILAR = [
  // "ornek1@gmail.com",
  // "ornek2@sirket.com.tr",
];
var ARAMA = 'from:notifications@github.com resmi-gazete-arsiv newer_than:3d';
var DEPO = "uguryetgin/resmi-gazete-arsiv";
var DESEN = /Resm[iîÎ]\s*Gazete\s+(\d{2}\.\d{2}\.\d{4})\s*\**\s*haz[ıi]r/i;

function ilet() {
  if (!ALICILAR.length) { Logger.log("ALICILAR listesi boş."); return; }
  var hafiza = PropertiesService.getUserProperties();
  var gonderilen = JSON.parse(hafiza.getProperty("gonderilen") || "[]");
  var ben = Session.getActiveUser().getEmail();
  var sinir = new Date(Date.now() - 3 * 24 * 3600 * 1000);
  GmailApp.search(ARAMA).forEach(function (konu) {
    konu.getMessages().forEach(function (m) {
      if (m.getDate() < sinir) return;
      var html = m.getBody();
      var t = (m.getPlainBody() + " " + m.getBody().replace(/<[^>]+>/g, " ")).match(DESEN);
      if (!t || gonderilen.indexOf(t[1]) >= 0) return;  // bildirim degil ya da zaten gonderildi
      // GitHub alt bilgisi ("Reply to this email directly, view it on GitHub, or unsubscribe")
      html = html.replace(/<p[^>]*>\s*(&mdash;|—)?\s*<br\s*\/?>\s*Reply to this email directly[\s\S]*$/i, "");
      html = html.replace(/<div itemscope[\s\S]*?<\/div>/gi, "");
      html = html.replace(/<a[^>]*class="user-mention[^"]*"[^>]*>@[^<]*<\/a>\s*/gi, "");
      html = html.replace(/@\w[\w-]*\s+(<strong>Resmî Gazete)/, "$1");
      // Tercih: GitHub'in her gun hazirladigi e-posta sayfasi (Outlook/Hotmail'de de duzgun gorunur)
      var g = t[1].split(".");
      var ymd = g[2] + g[1] + g[0];
      try {
        var sayfa = UrlFetchApp.fetch("https://github.com/" + DEPO + "/releases/download/rg-" + ymd + "/Eposta-" + ymd + ".html",
                                      { followRedirects: true, muteHttpExceptions: true });
        if (sayfa.getResponseCode() === 200) html = sayfa.getContentText("UTF-8");
      } catch (e) { Logger.log("E-posta sayfası alınamadı, GitHub e-postası kullanılıyor: " + e); }
      var secenek = { htmlBody: html, bcc: ALICILAR.join(","), name: "Resmî Gazete Arşivi" };
      var pdf = html.match(/href="(https:\/\/github\.com\/[^"]+\/Ozet-\d{8}\.pdf)"/);
      if (pdf) {
        try {
          var r = UrlFetchApp.fetch(pdf[1], { followRedirects: true, muteHttpExceptions: true });
          if (r.getResponseCode() === 200) secenek.attachments = [r.getBlob().setName(pdf[1].split("/").pop())];
        } catch (e) { Logger.log("PDF eklenemedi: " + e); }
      }
      GmailApp.sendEmail(ben, "Resmî Gazete " + t[1], m.getPlainBody().split(/Reply to this email directly/i)[0], secenek);
      Logger.log("İletildi: " + t[1] + " → " + ALICILAR.length + " alıcı");
      gonderilen.push(t[1]);
      hafiza.setProperty("gonderilen", JSON.stringify(gonderilen.slice(-60)));
    });
  });
}

/** Teşhis: "tani"yi çalıştır; Gmail'de bulunan GitHub e-postalarını ve tanınıp tanınmadığını yazar. */
function tani() {
  var konular = GmailApp.search(ARAMA);
  Logger.log("Arama: " + ARAMA + " → " + konular.length + " konuşma");
  konular.forEach(function (k) {
    k.getMessages().forEach(function (m) {
      var t = (m.getPlainBody() + " " + m.getBody().replace(/<[^>]+>/g, " ")).match(DESEN);
      Logger.log(m.getDate() + " | " + m.getFrom() + " | " + m.getSubject() + " | tanındı: " + (t ? t[1] : "HAYIR") +
                 " | " + m.getPlainBody().slice(0, 120).replace(/\s+/g, " "));
    });
  });
  Logger.log("Gönderilmiş günler: " + (PropertiesService.getUserProperties().getProperty("gonderilen") || "[]"));
}

/** Gönderilmiş günler listesini siler (aynı günü yeniden göndermek/denemek için). */
function sifirla() {
  PropertiesService.getUserProperties().deleteProperty("gonderilen");
  Logger.log("Gönderilmiş günler sıfırlandı.");
}
