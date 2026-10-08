# WhatsApp Tabir Tarayıcı

Seçtiğiniz WhatsApp sohbet ve gruplarındaki **tüm mesajları ve görselleri** okuyup, yazdığınız
tabire / kritere uyanları **Claude yapay zekâsı** ile ayıklayıp listeleyen Windows masaüstü uygulaması.

- **Claude aboneliğinizle çalışır** (Pro / Max) – Claude Code üzerinden; API anahtarı gerekmez.
- **Görselleri de tarar:** fotoğraf içindeki yazılar (dekont, fiş, belge, ekran görüntüsü, el yazısı)
  okunur, görsel içeriği tanınır ve kritere uyuyorsa listeye eklenir.
- **Anlamsal arama:** "kira ödemesi" diye aradığınızda içinde "kira" geçmeyen
  "ev sahibine bu ayın parasını gönderdim" mesajını da bulur. Yazım hataları, Türkçe karakter
  eksikliği (ş→s, ı→i), eş anlamlılar dahil.
- İsterseniz yapay zekâsız **kelime araması** (virgülle birden çok kelime).
- Sonuçlar tablo olarak listelenir; **Excel (CSV)** veya **görselli HTML rapor** olarak kaydedilir.
- **Modern Windows 11 görünümü:** açık / koyu tema, özet kartları (toplam, metin, görsel, sohbet),
  sonuçlarda anlık arama ve Metin / Görsel filtresi, görsel önizlemeli ayrıntı paneli.

## Mesaj kaynakları

| Sekme | Nasıl çalışır | Ne zaman |
|---|---|---|
| **WhatsApp oturumu (önerilen)** | Uygulama bir Edge penceresi açar, WhatsApp hesabınız buraya *bağlı cihaz* olarak eklenir (WhatsApp Desktop ile aynı hesap, aynı sohbetler). Sohbet listesinden istediğiniz sohbet/grupları seçersiniz; uygulama sohbeti yukarı kaydırarak mesajları ve görselleri okur. | Günlük kullanım |
| **WhatsApp Desktop ekran tarama** | Açık olan WhatsApp Desktop penceresini öne getirir, sohbeti kaydırarak ekran görüntüleri alır; Claude bu görüntülerdeki mesajları ve görselleri okur. | Tarayıcı oturumu istemiyorsanız |
| **Dışa aktarılmış sohbet (.zip)** | Telefonda *Sohbeti dışa aktar → Medya dahil* ile alınan .zip dosyalarını okur. | Tüm geçmiş + tam çözünürlüklü görseller gerektiğinde |

> **Neden doğrudan WhatsApp Desktop'ın dosyaları okunmuyor?** Windows'taki WhatsApp Desktop mesaj
> veritabanını şifreli tutar ve uygulama WhatsApp Web altyapısını kullanır. Bu nedenle aynı hesabı
> "bağlı cihaz" olarak bağlamak, güvenli ve güvenilir tek otomatik yoldur.

## Kurulum (Windows 10 / 11)

1. **Python 3.10+** kurun: <https://www.python.org/downloads/> – kurulumda
   *"Add python.exe to PATH"* kutusunu işaretleyin.
2. **Claude Code**'u kurun ve aboneliğinizle giriş yapın. PowerShell'i açıp:
   ```powershell
   irm https://claude.ai/install.ps1 | iex
   claude
   ```
   Açılan ekranda Claude (Pro/Max) hesabınızla giriş yapın, sonra `/exit` ile çıkın.
   Bu işlem yalnızca bir kez yapılır.
3. Bu klasördeki **`run.bat`** dosyasına çift tıklayın. İlk çalıştırmada gerekli paketleri kurar
   (birkaç dakika), sonra uygulama açılır. Sonraki açılışlar anında olur.

Tarayıcı olarak Windows'ta zaten kurulu olan **Microsoft Edge** kullanılır; ek indirme gerekmez.

İsterseniz `build_exe.bat` ile tek klasörlük bir `.exe` de oluşturabilirsiniz
(`dist\WhatsAppTabirTarayici\WhatsAppTabirTarayici.exe`).

## Kullanım

1. **WhatsApp'a bağlan**'a basın. İlk seferde açılan pencerede QR kod çıkar:
   telefonda *WhatsApp → Ayarlar → Bağlı cihazlar → Cihaz bağla* ile okutun.
   Oturum saklanır, bir daha QR istenmez.
2. Sohbet listesinden taranacak sohbet ve grupları seçin (Ctrl/Shift ile çoklu seçim, üstteki
   arama kutusuyla filtreleme; filtre değişse de seçimleriniz korunur).
3. İsterseniz sohbet başına mesaj sınırı ve başlangıç tarihi girin.
4. **Ne aranacak?** kutusuna tabiri yazın. Örnekler:
   - `fatura`
   - `kira ödemesi ile ilgili mesajlar ve dekontlar`
   - `içinde araç plakası görünen fotoğraflar`
   - `toplantı saati veya yeri değişikliği`
5. **Görselleri de tara** işaretliyse fotoğraflar da Claude'a gösterilir.
6. **Taramayı başlat**. Sonuçlar geldikçe tabloya eklenir. Üstteki kutuyla sonuçlarda arayabilir,
   Tümü / Metin / Görsel ile süzebilir, sütun başlığına tıklayarak sıralayabilirsiniz. Bir satıra
   tıklayınca altta tam metin ve görsel önizlemesi görünür; çift tıklayınca görsel büyük açılır.
7. **Excel (CSV) kaydet** / **HTML rapor kaydet** ile sonuçları dışa aktarın.

### Yapay zekâ ayarları

- **Bağlantı:** *Claude aboneliği (Claude Code)* varsayılandır; kullanım aboneliğinizin limitinden
  düşer. Alternatif olarak *Anthropic API anahtarı* seçilebilir (kullandıkça ücretlendirilir).
- **Model:** varsayılan `claude-opus-5-5` (en iyi doğruluk). Çok sayıda mesaj/görselde abonelik
  limitini daha yavaş tüketmek için `claude-sonnet-5-5` veya `claude-haiku-5-5` seçebilirsiniz.
- **Bağlantıyı test et** ile Claude'un hazır olduğunu kontrol edin.

Ortamda `ANTHROPIC_API_KEY` tanımlı olsa bile abonelik modunda bu anahtar Claude Code'a
aktarılmaz; böylece yanlışlıkla API'den ücretlendirilmezsiniz.

## Komut satırı (dışa aktarımlar için)

```bat
.venv\Scripts\python -m wpfilter.cli "WhatsApp Sohbeti - Aile.zip" -q "kira ödemesi" -o rapor.html
```

## Notlar ve sınırlamalar

- **Hız:** metin mesajları 60'lık gruplar halinde, görseller 4'erli gruplar halinde Claude'a
  gönderilir. Binlerce görsel içeren sohbetlerde tarama uzun sürebilir ve abonelik limitinize
  takılabilirsiniz; tarih/mesaj sınırı kullanın.
- **WhatsApp oturumu modunda görseller:** telefonda/WhatsApp'ta henüz indirilmemiş eski görseller
  yalnızca bulanık küçük önizleme olarak gelebilir. Eksiksiz arşiv için dışa aktarım sekmesini
  kullanın.
- **Ekran tarama modu** tarama süresince fareyi/klavyeyi kullanmanızı gerektirmez ama pencereye
  dokunmamalısınız. Otomatik sohbet açma `Ctrl+F` aramasını kullanır; çalışmazsa
  *"Sohbetleri kendim açacağım"* seçeneğini işaretleyin.
- WhatsApp arayüzü değiştiğinde oturum modundaki seçiciler güncelleme gerektirebilir.
- Uygulama yalnızca **okuma** yapar; mesaj göndermez, silmez. WhatsApp'ın otomasyon kullanımına
  dair koşullarını göz önünde bulundurun ve yalnızca kendi hesabınızda, makul hızda kullanın.
- Mesaj ve görseller yalnızca Claude'a (Anthropic) analiz için gönderilir; indirilen görseller
  `%LOCALAPPDATA%\WhatsAppTabirTarayici` altında saklanır, istediğiniz zaman silebilirsiniz.

## Geliştirme

```bash
pip install -r requirements.txt pytest
python -m pytest tests
```

Proje yapısı:

```
app.py                       # GUI başlatıcı
wpfilter/gui.py              # Tkinter + sv-ttk (Windows 11) arayüz
wpfilter/claude_backend.py   # Claude Code (abonelik) ve API arka uçları
wpfilter/matcher.py          # Metin + görsel eşleştirme motoru
wpfilter/report.py           # CSV / HTML rapor
wpfilter/sources/whatsapp_web.py    # WhatsApp oturumu (Playwright + Edge)
wpfilter/sources/desktop_screen.py  # WhatsApp Desktop ekran tarama (pywinauto + mss)
wpfilter/sources/export_zip.py      # Dışa aktarılmış sohbet okuyucu
```
