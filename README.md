python -m venv .venv
.\.venv\Scripts\activate
---------------------------------AYRI BÖLMEDE AÇMAK İÇİN


BAĞIMLILIKLAR İÇİN ÖNCE BUNU ÇALIŞTIR
----------------------------------------------
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -c "import PySide6; print(PySide6.__version__)"





# TextMultiReplacer Pro

Windows üzerinde çoklu metin dosyalarında, birden fazla dönüşüm kuralını tek tıklama ile uygulayan profesyonel masaüstü uygulaması.

## Özellikler

- Aynı anda bir veya çok sayıda dosya seçimi
- Klasörden toplu dosya ekleme (uzantı filtresi ile)
- Sınırsız sayıda **Bul → Değiştir** kuralı
- Kural bazında:
  - Regex aç/kapat
  - Büyük/küçük harf duyarlılığı
- Boş değiştirme alanı ile silme desteği
- Kural setlerini JSON olarak export/import
- Son oturumun (dosyalar, kurallar, ayarlar) otomatik hatırlanması
- İşlem ilerleme çubuğu ve ayrıntılı log paneli
- İsteğe bağlı yedek alma (`.bak`) ve geri yükleme
- İsteğe bağlı simülasyon modu (dry-run)
- Seçili dosya için hızlı diff önizlemesi

## Kurulum

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Çalıştırma

```powershell
python app.py
```

## Notlar

- Uygulama, metin kodlamasını otomatik algılamayı dener (`utf-8-sig`, `utf-8`, `cp1254`, `cp1252`, `latin-1`).
- Yedek alma açıksa, değişen her dosya için aynı klasörde `dosyaadı.ext.bak` oluşturulur.
- Geri yükleme, yalnızca mevcut `.bak` dosyaları için yapılır.
