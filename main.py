import sys
import sqlite3
import requests
import os
import datetime
import qrcode
import urllib.parse
from PyQt5 import QtWidgets, uic, QtCore, QtGui
from PyQt5.QtChart import QChart, QChartView, QPieSeries, QPieSlice
from PyQt5.QtGui import QColor
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QMainWindow, QApplication, QFrame,
    QVBoxLayout, QLabel, QPushButton
)

# ------------------------- Style Dosyasını Okuma -------------------------
def qss_oku(dosya_yolu):
    try:
        with open(dosya_yolu, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"Hata: {dosya_yolu} dosyası bulunamadı!")
        return ""

# ------------------------- Veritabanı Hazırlığı -------------------------
def veritabani_hazirla():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kullanicilar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kullanici_adi TEXT NOT NULL UNIQUE,
            email TEXT,
            sifre TEXT NOT NULL
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kaydedilen_etkinlikler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kullanici_adi TEXT NOT NULL,
            etkinlik_id TEXT,
            etkinlik_adi TEXT NOT NULL,
            tarih TEXT,
            sehir TEXT,
            fiyat TEXT,
            onaylandi INTEGER DEFAULT 0
        )
    """)
    
    cursor.execute("PRAGMA table_info(kaydedilen_etkinlikler)")
    sutunlar = [sutun[1] for sutun in cursor.fetchall()]
    
    if "fiyat" not in sutunlar:
        cursor.execute("ALTER TABLE kaydedilen_etkinlikler ADD COLUMN fiyat TEXT")
    if "onaylandi" not in sutunlar:
        cursor.execute("ALTER TABLE kaydedilen_etkinlikler ADD COLUMN onaylandi INTEGER DEFAULT 0")

    conn.commit()
    conn.close()

veritabani_hazirla()

# ------------------------- Grafik Veri Çekme İşçisi (Thread) -------------------------
class ChartDataWorker(QThread):
    data_fetched = pyqtSignal(dict)

    def __init__(self, api_key, query):
        super().__init__()
        self.api_key = api_key
        self.query = query

    def run(self):
        from collections import Counter
        import datetime # Burada datetime kütüphanesinin çağrıldığından emin oluyoruz
        kategori_sayac = Counter()
        # Sadece 1 adet istek atıyoruz ve o şehirdeki ilk 100 güncel etkinliği tek seferde çekiyoruz
        bugun = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        
        try:
            url = (
                f"https://app.ticketmaster.com/discovery/v2/events.json"
                f"?apikey={self.api_key}&city={self.query}&startDateTime={bugun}"
                f"&size=100" # Tek seferde geniş bir veri kümesi istiyoruz
            )
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if "_embedded" in data:
                    events = data["_embedded"]["events"]
                    for event in events:
                        # Etkinliğin kategorisini (classifications) doğrudan çekiyoruz
                        classifications = event.get("classifications", [])
                        if classifications:
                            segment_name = classifications[0].get("segment", {}).get("name")
                            if segment_name:
                                # API'den dönen isimleri kendi sözlüğümüzle eşleştiriyoruz
                                kategori_sayac[segment_name] += 1
        except Exception as e:
            print(f"Hızlı grafik veri çekme hatası: {e}")
            pass

        self.data_fetched.emit(dict(kategori_sayac))

def pdf_olustur(isim, tarih, mekan_yer, sehir, fiyat, kullanici_adi):
    try:
        import qrcode
        import urllib.parse
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        # 1. Google Takvim URL'si İçin Dinamik Saat Ayrıştırma
        try:
            # Veritabanından gelen veri "2026-05-18 21:00" formatında olacağı için boşluktan ayırıyoruz
            if " " in tarih:
                gun_kismi, saat_kismi = tarih.strip().split(" ", 1)
            else:
                gun_kismi = tarih.strip()
                saat_kismi = "21:00" # Ekranda eski kayıtlardan kalma saat yoksa güvenlik önlemi
            
            # Google biçimi için temizleme: "2026-05-18" -> "20260518"
            tarih_temiz = gun_kismi.replace("-", "")
            # Saat biçimi için temizleme: "21:00" -> "210000"
            baslangic_saati = saat_kismi.replace(":", "") + "00"
            saat_int = int(saat_kismi.split(":")[0])
            bitis_saati = f"{saat_int + 2:02d}" + saat_kismi.split(":")[1] + "00"
            
            # HATA ÇÖZÜMÜ: Saatlerin sonundaki 'Z' harflerini sildik. 
            # Böylece Google Takvim saat dilimi dönüşümü yapmadan doğrudan biletteki saati yazacak.
            tarih_param = f"{tarih_temiz}T{baslangic_saati}/{tarih_temiz}T{bitis_saati}"
        except Exception as e:
            print(f"Dinamik QR saat ayrıştırma hatası: {e}")
            tarih_param = "20260518T210000Z/20260518T230000Z"

        detaylar = f"EventWise tarafından {kullanici_adi} için oluşturulmuştur."
        
        google_calendar_url = (
            f"https://www.google.com/calendar/render?action=TEMPLATE"
            f"&text={urllib.parse.quote(isim)}"
            f"&dates={tarih_param}" # Dinamik saat başarıyla gömüldü
            f"&details={urllib.parse.quote(detaylar)}"
            f"&location={urllib.parse.quote(mekan_yer)}"
            f"&sf=true&output=xml"
        )

        qr = qrcode.QRCode(version=1, box_size=10, border=2)
        qr.add_data(google_calendar_url)
        qr.make(fit=True)
        qr_resim_yolu = "gecici_qr.png"
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(qr_resim_yolu)

        # 2. Türkçe Font Ayarları
        font_yolu = os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts', 'arial.ttf')
        font_bold_yolu = os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts', 'arialbd.ttf')
        
        if os.path.exists(font_yolu) and os.path.exists(font_bold_yolu):
            pdfmetrics.registerFont(TTFont('Arial-TR', font_yolu))
            pdfmetrics.registerFont(TTFont('Arial-TR-Bold', font_bold_yolu))
            font_normal = 'Arial-TR'
            font_bold = 'Arial-TR-Bold'
        else:
            font_normal = 'Helvetica'
            font_bold = 'Helvetica-Bold'

        # 3. PDF Çizim İşlemleri
        klasor_adi = "pdf"
        if not os.path.exists(klasor_adi):
            os.makedirs(klasor_adi)

        # PDF dosyasının adını artık "pdf/" klasör yoluyla birleştirerek tanımlıyoruz
        saf_dosya_adi = f"etkinlik_{isim[:20].replace(' ', '_')}.pdf"
        dosya_adi = os.path.join(klasor_adi, saf_dosya_adi)
        
        c = canvas.Canvas(dosya_adi, pagesize=A4)
        w, h = A4

        c.setFillColor(colors.HexColor("#0a0a0a"))
        c.rect(0, 0, w, h, fill=1, stroke=0)

        c.setFillColor(colors.HexColor("#5d5df8"))
        c.rect(0, h - 80, w, 80, fill=1, stroke=0)

        c.setFillColor(colors.white)
        c.setFont(font_bold, 22)
        c.drawCentredString(w / 2, h - 52, "EventWise - Etkinlik Onay Belgesi")

        c.setFillColor(colors.HexColor("#1e1e1e"))
        c.roundRect(50, h - 400, w - 100, 290, 10, fill=1, stroke=0)

        c.setStrokeColor(colors.HexColor("#5d5df8"))
        c.setLineWidth(2)
        c.roundRect(50, h - 400, w - 100, 290, 10, fill=0, stroke=1)

        c.setFont(font_bold, 14)
        c.setFillColor(colors.HexColor("#5d5df8"))
        c.drawString(80, h - 140, "Etkinlik Bilgileri")

        c.setLineWidth(1)
        c.setStrokeColor(colors.HexColor("#5d5df8"))
        c.line(80, h - 148, w - 80, h - 148)

        satirlar = [
            ("Etkinlik Adı :", isim),
            ("Tarih / Saat :", tarih), # Arayüzde artık tarih ve saati bir arada şıkça basacak
            ("Mekan Yer    :", mekan_yer),
            ("Fiyat        :", fiyat if fiyat and fiyat != "Belirtilmemiş" else "Belirtilmemiş"),
            ("Katılımcı    :", kullanici_adi),
        ]

        y = h - 180
        for baslik, deger in satirlar:
            c.setFont(font_bold, 11)
            c.setFillColor(colors.HexColor("#aaaaaa"))
            c.drawString(80, y, baslik)
            c.setFont(font_normal, 11)
            c.setFillColor(colors.white)
            c.drawString(210, y, str(deger)[:60])
            y -= 35

        c.setFillColor(colors.HexColor("#1a3a1a"))
        c.roundRect(50, h - 470, w - 100, 55, 8, fill=1, stroke=0)
        c.setStrokeColor(colors.HexColor("#4caf50"))
        c.setLineWidth(2)
        c.roundRect(50, h - 470, w - 100, 55, 8, fill=0, stroke=1)
        c.setFillColor(colors.HexColor("#4caf50"))
        c.setFont(font_bold, 14)
        c.drawCentredString(w / 2, h - 448, "Bu etkinliğe katılım onaylanmıştır.")

        # 4. QR KODU SAYFAYA EKLEME (Büyütüldü ve Ortalandı)
        qr_boyut = 180
        qr_x_konumu = (w / 2) - (qr_boyut / 2)
        qr_y_konumu = 135
        
        c.drawImage(qr_resim_yolu, qr_x_konumu, qr_y_konumu, width=qr_boyut, height=qr_boyut)
        
        c.setFillColor(colors.HexColor("#aaaaaa"))
        c.setFont(font_normal, 9)
        c.drawCentredString(w / 2, qr_y_konumu - 15, "Takviminize eklemek için yukarıdaki QR kodu telefonunuzdan okutun.")

        c.setFillColor(colors.HexColor("#555555"))
        c.setFont(font_normal, 9)
        c.drawCentredString(w / 2, 40, "EventWise tarafından oluşturulmuştur.")

        c.save()

        if os.path.exists(qr_resim_yolu):
            os.remove(qr_resim_yolu)

        return dosya_adi
    except Exception as e:
        print(f"PDF hatası: {e}")
        return None
# ------------------------- Kaydedilenler Ekranı -------------------------
class EventWiseSaved(QtWidgets.QMainWindow):
    def __init__(self, kullanici_adi):
        super(EventWiseSaved, self).__init__()
        uic.loadUi('mySavedEvents.ui', self)
        self.setWindowTitle("EventWise - Saved Events")
        self.setFixedSize(self.size())
        self.kullanici_adi = kullanici_adi

        self.tbl_etkinlikler.setColumnCount(5)

        self.tbl_etkinlikler.setHorizontalHeaderLabels(
            ["Etkinlik Adı", "Tarih", "Mekan Yer", "Fiyat", "İşlemler"]
        )
        self.tbl_etkinlikler.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.Stretch
        )
        self.tbl_etkinlikler.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_etkinlikler.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_etkinlikler.verticalHeader().setVisible(False)

        self.btn_clear_all.clicked.connect(self.hepsini_sil)
        self.btn_geri.clicked.connect(self.geri_don)
        self.btn_onayla.clicked.connect(self.secili_etkinligi_onayla)
        
        self.etkinlikleri_yukle()

    def etkinlikleri_yukle(self):
        self.tbl_etkinlikler.setRowCount(0)
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, etkinlik_adi, tarih, sehir, fiyat, onaylandi FROM kaydedilen_etkinlikler WHERE kullanici_adi = ?",
            (self.kullanici_adi,)
        )
        rows = cursor.fetchall()
        conn.close()

        for row_idx, (db_id, isim, tarih, sehir, fiyat, onaylandi) in enumerate(rows):
            self.tbl_etkinlikler.insertRow(row_idx)
            
            item_isim = QtWidgets.QTableWidgetItem(isim)
            item_isim.setData(QtCore.Qt.UserRole, db_id)
            
            self.tbl_etkinlikler.setItem(row_idx, 0, item_isim)
            self.tbl_etkinlikler.setItem(row_idx, 1, QtWidgets.QTableWidgetItem(tarih or ""))
            self.tbl_etkinlikler.setItem(row_idx, 2, QtWidgets.QTableWidgetItem(sehir or ""))
            self.tbl_etkinlikler.setItem(row_idx, 3, QtWidgets.QTableWidgetItem(fiyat or "Belirtilmemiş"))

            durum = "✅ Onaylandı" if onaylandi else "⏳ Bekliyor"
            durum_item = QtWidgets.QTableWidgetItem(durum)
            durum_item.setForeground(
                QtGui.QBrush(QtGui.QColor("#4caf50" if onaylandi else "#ffaa00"))
            )
            self.tbl_etkinlikler.setItem(row_idx, 4, durum_item)

            actions_widget = QtWidgets.QWidget()
            actions_layout = QtWidgets.QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(4, 2, 4, 2)
            actions_layout.setSpacing(4)

            btn_sil = QPushButton("🗑")
            btn_sil.setFixedSize(32, 32)
            btn_sil.setObjectName("btn_sil")
            btn_sil.setCursor(QtCore.Qt.PointingHandCursor)
            btn_sil.clicked.connect(lambda checked, did=db_id: self.etkinlik_sil(did))

            actions_layout.addStretch()
            actions_layout.addWidget(btn_sil)
            actions_layout.addStretch()

            self.tbl_etkinlikler.setCellWidget(row_idx, 5, actions_widget)
            self.tbl_etkinlikler.setRowHeight(row_idx, 48)
    def secili_etkinligi_onayla(self):
        current_row = self.tbl_etkinlikler.currentRow()
        if current_row == -1:
            QtWidgets.QMessageBox.warning(self, "Uyarı", "Lütfen katılımını onaylamak istediğiniz etkinliği tablodan seçin!")
            return

        # 0. İndeksteki hücreden gizli DB ID'sini ve Etkinlik Adını alıyoruz
        ilk_hucre = self.tbl_etkinlikler.item(current_row, 0)
        db_id = ilk_hucre.data(QtCore.Qt.UserRole)
        isim = ilk_hucre.text()
        
        tarih = self.tbl_etkinlikler.item(current_row, 1).text() if self.tbl_etkinlikler.item(current_row, 1) else ""
        fiyat = self.tbl_etkinlikler.item(current_row, 3).text() if self.tbl_etkinlikler.item(current_row, 3) else "Belirtilmemiş"

        cevap = QtWidgets.QMessageBox.question(
            self, "Katılımı Onayla",
            f"'{isim}' etkinliğine katılımınızı onaylıyor musunuz?\n\nOnay belgesi PDF olarak indirilecektir.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if cevap != QtWidgets.QMessageBox.Yes:
            return

        try:
            # Veritabanı bağlantısı açılıyor
            conn = sqlite3.connect("database.db")
            cursor = conn.cursor()
            
            # --- SENİN İSTEDİĞİN ÇÖZÜM BURASI ---
            # Tablodan (UI) çekmek yerine, veritabanında bu etkinliğe ait kayıtlı olan 
            # en uzun/tam mekan metnini (sehir kolonunda tutulan tam veriyi) sorguluyoruz.
            cursor.execute("SELECT sehir FROM kaydedilen_etkinlikler WHERE id = ?", (db_id,))
            db_row = cursor.fetchone()
            
            # Eğer veritabanında kayıt bulunduysa tam mekan ismini alıyoruz, yoksa boşa düşürüyoruz
            uzun_mekan_ismi = db_row[0] if db_row else ""

            # Veritabanında onay durumunu güncelle
            cursor.execute("UPDATE kaydedilen_etkinlikler SET onaylandi = 1 WHERE id = ?", (db_id,))
            conn.commit()
            conn.close()

            # PDF Oluşturucuya temiz ve uzun mekan verisini paslıyoruz
            pdf_dosya = pdf_olustur(isim, tarih, uzun_mekan_ismi, " ", fiyat, self.kullanici_adi)
            
            if pdf_dosya:
                QtWidgets.QMessageBox.information(
                    self, "Onaylandı! ✅",
                    f"Katılımınız başarıyla onaylandı!\n\nBelge İndirildi:\n📄 {pdf_dosya}"
                )
                os.startfile(pdf_dosya) if sys.platform == "win32" else os.system(f"xdg-open '{pdf_dosya}'")
            
            # Tabloyu yeniliyoruz
            self.etkinlikleri_yukle()

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Hata", f"Onaylama işlemi sırasında bir hata oluştu:\n{e}")

    def etkinlik_sil(self, db_id):
        cevap = QtWidgets.QMessageBox.question(
            self, "Sil", "Bu etkinliği kayıtlardan kaldırmak istiyor musun?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if cevap == QtWidgets.QMessageBox.Yes:
            conn = sqlite3.connect("database.db")
            cursor = conn.cursor()
            cursor.execute("DELETE FROM kaydedilen_etkinlikler WHERE id = ?", (db_id,))
            conn.commit()
            conn.close()
            self.etkinlikleri_yukle()

    def hepsini_sil(self):
        cevap = QtWidgets.QMessageBox.question(
            self, "Tümünü Sil", "Tüm kaydedilen etkinlikleri silmek istiyor musun?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if cevap == QtWidgets.QMessageBox.Yes:
            conn = sqlite3.connect("database.db")
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM kaydedilen_etkinlikler WHERE kullanici_adi = ?",
                (self.kullanici_adi,)
            )
            conn.commit()
            conn.close()
            self.etkinlikleri_yukle()

    def geri_don(self):
        self.discovery = EventWiseDiscovery(self.kullanici_adi)
        self.discovery.show()
        self.close()

# ------------------------- Kayıt Sayfası -------------------------
class EventWiseSignUp(QtWidgets.QMainWindow):
    def __init__(self):
        super(EventWiseSignUp, self).__init__()
        uic.loadUi('sign_up.ui', self)
        self.setWindowTitle("EventWise - Kayıt Ol")
        self.setFixedSize(self.size())
        self.btn_kayitol.clicked.connect(self.kayit_yap)
        self.btn_geri.clicked.connect(self.geri_don)

    def kayit_yap(self):
        ad    = self.txt_kullaniciadi.text().strip()
        mail  = self.txt_mail.text().strip()
        sifre = self.txt_sifre.text().strip()
        if not ad or not mail or not sifre:
            QtWidgets.QMessageBox.warning(self, "Hata", "Lütfen tüm alanları doldurun!")
            return
        try:
            conn = sqlite3.connect("database.db")
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO kullanicilar (kullanici_adi, email, sifre) VALUES (?, ?, ?)",
                (ad, mail, sifre)
            )
            conn.commit()
            conn.close()
            QtWidgets.QMessageBox.information(self, "Başarılı", "Kaydınız oluşturuldu!")
            self.geri_don()
        except sqlite3.IntegrityError:
            QtWidgets.QMessageBox.warning(self, "Hata", "Bu kullanıcı adı zaten alınmış!")

    def geri_don(self):
        self.login_penceresi = EventWiseLogin()
        self.login_penceresi.show()
        self.close()

# ------------------------- Keşif Paneli -------------------------
class EventWiseDiscovery(QtWidgets.QMainWindow):
    son_arama = "İstanbul"

    def __init__(self, kullanici_adi=""):
        super(EventWiseDiscovery, self).__init__()
        uic.loadUi('Discovery_panel.ui', self)
        self.setWindowTitle("EventWise - Keşfet")
        self.setFixedSize(self.size())
        self.scrollArea.setStyleSheet("""
            QScrollArea { background-color: #0a0a0a; border: none; }
            QScrollArea > QWidget > QWidget { background-color: #0a0a0a; }
        """)
        self.kullanici_adi = kullanici_adi
        from config import API_KEY
        self.api_key = API_KEY

        self.txt_search.setPlaceholderText("🔍  Sanatçı, şehir veya etkinlik ara...")
        self.txt_search.setText(EventWiseDiscovery.son_arama)

        self.btn_ara.clicked.connect(self.etkinlik_getir)
        self.btn_cikis.clicked.connect(self.cikis_yap)
        self.btn_my_events.clicked.connect(self.kaydedilenler_ac)

        QtCore.QTimer.singleShot(200, self.varsayilan_yukle)

    def varsayilan_yukle(self):
            self._etkinlik_getir_query(EventWiseDiscovery.son_arama)
            # Gecikme süresini 1200'den 50 milisaniyeye indiriyoruz, yani anında çalışacak
            QtCore.QTimer.singleShot(50, lambda: self.pasta_grafigi_sehir_getir(EventWiseDiscovery.son_arama))
    def detay_ac(self, event_data):
        self.detay = EventWiseDetail(event_data, self.kullanici_adi)
        self.detay.show()
        self.close()

    def etkinlik_getir(self):
        query = self.txt_search.text().strip()
        if not query:
            QtWidgets.QMessageBox.warning(self, "Uyarı", "Lütfen bir arama terimi girin!")
            return
        EventWiseDiscovery.son_arama = query
        self._etkinlik_getir_query(query)

    def _etkinlik_getir_query(self, query):
        self.temizle_layout()

        # Python 3.13 Uyarısı Düzeltildi: UTC zamanı yeni standartla alınıyor
        bugun = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

        kategori_map = {
            "Konser":  "Music",
            "Tiyatro": "Arts & Theatre",
            "Spor":    "Sports",
            "Sergi":   "Arts & Theatre",
        }
        secili_kategori = self.cmb_category.currentText()
        classification  = kategori_map.get(secili_kategori, "")

        url = (
            f"https://app.ticketmaster.com/discovery/v2/events.json"
            f"?apikey={self.api_key}&city={query}&startDateTime={bugun}"
            f"&classificationName={classification}&sort=date,asc"
        )

        try:
            response = requests.get(url, timeout=10)
            data = response.json()

            if "_embedded" in data:
                events = data["_embedded"]["events"]

                self.scroll_widget = QtWidgets.QWidget()
                self.scroll_widget.setObjectName("scrollContent")
                self.grid_layout = QtWidgets.QGridLayout(self.scroll_widget)
                self.grid_layout.setSpacing(12)
                self.grid_layout.setContentsMargins(10, 10, 10, 10)

                for i, event in enumerate(events):
                    self.kart_olustur(event, i // 2, i % 2, self.grid_layout)

                self.grid_layout.setRowStretch(self.grid_layout.rowCount(), 1)
                self.scrollArea.setWidget(self.scroll_widget)

                QtCore.QTimer.singleShot(100, lambda: self.pasta_grafigi_sehir_getir(query))
            else:
                QtWidgets.QMessageBox.warning(
                    self, "Sonuç Yok",
                    f"'{query}' için Türkiye'de etkinlik bulunamadı."
                )
        except requests.exceptions.Timeout:
            QtWidgets.QMessageBox.critical(self, "Zaman Aşımı", "Sunucuya bağlanılamadı, tekrar deneyin.")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Hata", f"Bir hata oluştu:\n{e}")

    def pasta_grafigi_guncelle(self, kategori_sayac):
        """PyQtChart ile pasta grafigi - Dilim icinde sadece sayi, alttaki legend kısmında isim yazar."""
        if not hasattr(self, 'lbl_chart'):
            return
        if not kategori_sayac:
            return

        kat_tanim = {
            "Music":          ("Konser / Müzik",  "#5d5df8"),
            "Sports":         ("Spor",             "#f85d5d"),
            "Arts & Theatre": ("Sanat / Tiyatro",  "#f8d05d"),
            "Film":           ("Film",             "#c85df8"),
            "Miscellaneous":  ("Diğer",            "#5dc8f8"),
        }

        if hasattr(self, '_chart_view') and self._chart_view:
            self._chart_view.setParent(None)
            self._chart_view.deleteLater()

        series = QPieSeries()
        
        for kat, sayi in kategori_sayac.items():
            tanim = kat_tanim.get(kat, (kat, "#aaaaaa"))
            
            dilim = series.append(str(sayi), sayi) 
            dilim.setBrush(QColor(tanim[1]))
            dilim.setBorderColor(QColor("#0a0a0a"))
            
            dilim.setLabelVisible(True)
            dilim.setLabelColor(QColor("white"))
            dilim.setLabelFont(QtGui.QFont("Arial", 10, QtGui.QFont.Bold))
            dilim.setLabelPosition(QPieSlice.LabelInsideNormal)

        chart = QChart()
        chart.addSeries(series)
        chart.setTitle("")
        chart.setBackgroundBrush(QtGui.QBrush(QColor("#1e1e1e")))
        chart.setBackgroundRoundness(10)
        
        chart.legend().setVisible(True)
        chart.legend().setAlignment(QtCore.Qt.AlignBottom)
        chart.legend().setColor(QColor("#dddddd"))
        chart.legend().setFont(QtGui.QFont("Arial", 9))
        chart.legend().setBackgroundVisible(False)
        chart.setMargins(QtCore.QMargins(5, 5, 5, 5))
        chart.setAnimationOptions(QChart.SeriesAnimations)

        w = self.lbl_chart.width() or 260
        h = w + 120

        self._chart_view = QChartView(chart, self.lbl_chart.parent())
        self._chart_view.setGeometry(
            self.lbl_chart.x(),
            self.lbl_chart.y(),
            w,
            h
        )
        self._chart_view.setRenderHint(QtGui.QPainter.Antialiasing)
        self._chart_view.setStyleSheet("background: transparent; border: none;")
        self._chart_view.show()
        self.lbl_chart.hide()

        # CRITICAL FIX: Çökmeye sebep olan 'str' nesnesi hatasını gidermek için marker etiket yönetimi düzeltildi
        markers = chart.legend().markers(series)
        for i, (kat, sayi) in enumerate(kategori_sayac.items()):
            if i < len(markers):
                tanim = kat_tanim.get(kat, (kat, "#aaaaaa"))
                markers[i].setLabel(f"{tanim[0]} ({sayi})")

    def pasta_grafigi_sehir_getir(self, query):
        if hasattr(self, 'chart_worker') and self.chart_worker.isRunning():
            self.chart_worker.terminate()
            self.chart_worker.wait()

        self.chart_worker = ChartDataWorker(self.api_key, query)
        self.chart_worker.data_fetched.connect(self.pasta_grafigi_guncelle)
        self.chart_worker.start()

    def kart_olustur(self, event_data, r, c, ana_layout):
        kart = QFrame()
        kart.setMinimumSize(260, 220)
        kart.setMaximumWidth(310)
        kart.setObjectName("eventCard")

        v_layout = QVBoxLayout(kart)
        v_layout.setContentsMargins(15, 15, 15, 15)
        v_layout.setSpacing(10)

        isim        = event_data.get('name', 'Etkinlik')
        tarih       = event_data.get('dates', {}).get('start', {}).get('localDate', 'Bilinmiyor')
        venues      = event_data.get('_embedded', {}).get('venues', [{}])
        sehir       = venues[0].get('city', {}).get('name', 'Bilinmiyor') if venues else 'Bilinmiyor'

        fiyat_str = "Fiyat belirtilmemiş"
        price_ranges = event_data.get('priceRanges')
        if not price_ranges and 'sales' in event_data:
            price_ranges = event_data.get('sales', {}).get('public', {}).get('priceRanges', [])

        if price_ranges and len(price_ranges) > 0:
            pr = price_ranges[0]
            para = pr.get('currency', 'TRY')
            min_f = pr.get('min')
            max_f = pr.get('max')
            if min_f is not None and max_f is not None:
                fiyat_str = f"💰 {min_f:.0f} - {max_f:.0f} {para}"
            elif min_f is not None:
                fiyat_str = f"💰 {min_f:.0f} {para}"

        lbl_title = QLabel(f"<b>{isim[:50]}</b>")
        lbl_title.setWordWrap(True)
        lbl_title.setObjectName("cardTitle")

        lbl_date = QLabel(f"📅  {tarih}")
        lbl_date.setObjectName("cardDetail")

        lbl_city = QLabel(f"📍  {sehir}")
        lbl_city.setObjectName("cardDetail")

        lbl_price = QLabel(fiyat_str)
        lbl_price.setObjectName("cardPrice")

        btn_save = QPushButton("💾  Kaydet")
        btn_save.setMinimumHeight(36)
        btn_save.setObjectName("btn_kaydet_kart")
        btn_save.setCursor(QtCore.Qt.PointingHandCursor)
        btn_save.clicked.connect(
            lambda checked, e=event_data: self.etkinlik_kaydet(e)
        )

        v_layout.addWidget(lbl_title)
        v_layout.addWidget(lbl_date)
        v_layout.addWidget(lbl_city)
        v_layout.addWidget(lbl_price)
        v_layout.addStretch()
        v_layout.addWidget(btn_save)

        kart.mousePressEvent = lambda event, e=event_data: self.detay_ac(e)
        kart.setCursor(QtCore.Qt.PointingHandCursor)
        ana_layout.addWidget(kart, r, c)

    def etkinlik_kaydet(self, event_data):
            isim        = event_data.get('name', 'Bilinmiyor')
            etkinlik_id = event_data.get('id', '')

            # --- DİNAMİK TARİH VE SAAT YAKALAMA ---
            tarih       = event_data.get('dates', {}).get('start', {}).get('localDate', 'Bilinmiyor')
            saat        = event_data.get('dates', {}).get('start', {}).get('localTime', '')
            
            # Eğer saat bilgisi API'den geldiyse tarihin yanına ekliyoruz (Örn: "2026-05-18 21:00")
            # Saat gelmediyse varsayılan olarak "20:00" ekliyoruz
            tam_tarih_saat = f"{tarih} {saat[:5]}" if saat else f"{tarih} 20:00"

            venues      = event_data.get('_embedded', {}).get('venues', [{}])
            mekan_adi   = venues[0].get('name', 'Bilinmiyor') if venues else 'Bilinmiyor'
            sehir_adi   = venues[0].get('city', {}).get('name', '') if venues else ''
            tam_mekan   = f"{mekan_adi}, {sehir_adi}" if sehir_adi else mekan_adi

            price_ranges = event_data.get('priceRanges')
            if not price_ranges and 'sales' in event_data:
                price_ranges = event_data.get('sales', {}).get('public', {}).get('priceRanges', [])

            fiyat_str = "Belirtilmemiş"
            if price_ranges and len(price_ranges) > 0:
                pr = price_ranges[0]
                para = pr.get('currency', 'TRY')
                min_f = pr.get('min')
                max_f = pr.get('max')
                if min_f is not None and max_f is not None:
                    fiyat_str = f"{min_f:.0f} - {max_f:.0f} {para}"
                elif min_f is not None:
                    fiyat_str = f"{min_f:.0f} {para}"

            try:
                conn = sqlite3.connect("database.db")
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id FROM kaydedilen_etkinlikler WHERE kullanici_adi = ? AND etkinlik_id = ?",
                    (self.kullanici_adi, etkinlik_id)
                )
                if cursor.fetchone():
                    QtWidgets.QMessageBox.information(self, "Bilgi", "Bu etkinlik zaten kaydedilmiş!")
                    conn.close()
                    return
                
                # Değişiklik: Artık veritabanına sadece gün değil, "tam_tarih_saat" değişkenini gönderiyoruz
                cursor.execute(
                    "INSERT INTO kaydedilen_etkinlikler "
                    "(kullanici_adi, etkinlik_id, etkinlik_adi, tarih, sehir, fiyat, onaylandi) "
                    "VALUES (?, ?, ?, ?, ?, ?, 0)",
                    (self.kullanici_adi, etkinlik_id, isim, tam_tarih_saat, tam_mekan, fiyat_str)
                )
                conn.commit()
                conn.close()
                QtWidgets.QMessageBox.information(self, "Kaydedildi", f"✅ '{isim}' kaydedildi!")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Hata", f"Kayıt sırasında hata:\n{e}")

    def temizle_layout(self):
        bos = QtWidgets.QWidget()
        bos.setObjectName("scrollContent")
        bos.setStyleSheet("background-color: #0a0a0a;")
        self.scrollArea.setWidget(bos)

    def kaydedilenler_ac(self):
        self.saved = EventWiseSaved(self.kullanici_adi)
        self.saved.show()
        self.close()

    def cikis_yap(self):
        self.login = EventWiseLogin()
        self.login.show()
        self.close()

# ------------------------- Giriş Sayfası -------------------------
class EventWiseLogin(QtWidgets.QMainWindow):
    def __init__(self):
        super(EventWiseLogin, self).__init__()
        uic.loadUi('login.ui', self)
        self.setWindowTitle("EventWise")
        self.setFixedSize(self.size())
        self.btn_login.clicked.connect(self.giris_kontrol)
        self.btn_signup.clicked.connect(self.kayit_ekranini_ac)
        self.txt_password.setEchoMode(QtWidgets.QLineEdit.Password)

    def giris_kontrol(self):
        ad    = self.txt_username.text().strip()
        sifre = self.txt_password.text().strip()
        if not ad or not sifre:
            QtWidgets.QMessageBox.warning(self, "Hata", "Kullanıcı adı ve şifre boş bırakılamaz!")
            return
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM kullanicilar WHERE kullanici_adi = ? AND sifre = ?",
            (ad, sifre)
        )
        kullanici = cursor.fetchone()
        conn.close()
        if kullanici:
            self.discovery_ekrani = EventWiseDiscovery(kullanici_adi=ad)
            self.discovery_ekrani.show()
            self.close()
        else:
            QtWidgets.QMessageBox.warning(self, "Hata", "Kullanıcı adı veya şifre hatalı!")

    def kayit_ekranini_ac(self):
        self.kayit_penceresi = EventWiseSignUp()
        self.kayit_penceresi.show()
        self.hide()

# ------------------------- Etkinlik Detay Ekranı -------------------------
class EventWiseDetail(QtWidgets.QMainWindow):
    def __init__(self, event_data, kullanici_adi):
        super(EventWiseDetail, self).__init__()
        uic.loadUi('eventDetail.ui', self)
        self.setWindowTitle("EventWise - Etkinlik Detayı")
        self.setFixedSize(self.size())

        self.event_data    = event_data
        self.kullanici_adi = kullanici_adi

        isim   = event_data.get('name', 'Bilinmiyor')
        tarih  = event_data.get('dates', {}).get('start', {}).get('localDate', 'Bilinmiyor')
        saat   = event_data.get('dates', {}).get('start', {}).get('localTime', '')
        venues = event_data.get('_embedded', {}).get('venues', [{}])
        mekan  = venues[0].get('name', 'Bilinmiyor') if venues else 'Bilinmiyor'
        sehir = venues[0].get('city', {}).get('name', 'Bilinmiyor') if venues else 'Bilinmiyor'
        

        price_ranges = event_data.get('priceRanges')
        if not price_ranges and 'sales' in event_data:
            price_ranges = event_data.get('sales', {}).get('public', {}).get('priceRanges', [])

        fiyat_str = "Belirtilmemiş"
        if price_ranges and len(price_ranges) > 0:
            pr = price_ranges[0]
            para = pr.get('currency', 'TRY')
            min_f = pr.get('min')
            max_f = pr.get('max')
            if min_f is not None and max_f is not None:
                fiyat_str = f"{min_f:.0f} - {max_f:.0f} {para}"
            elif min_f is not None:
                fiyat_str = f"{min_f:.0f} {para}"

        self.mekan_str = f"{mekan}, {sehir}" if sehir else mekan
        self.fiyat_str = fiyat_str

        saat_str = f" - {saat[:5]}" if saat else ""
        self.lbl_event_name.setText(isim)
        self.lbl_datetime.setText(f"📅  {tarih}{saat_str}")
        self.lbl_venue.setText(f"📍  {self.mekan_str}")
        self.lbl_price.setText(f"💰  {fiyat_str}")

        self.resim_yukle(event_data)

        self.btn_save_event.clicked.connect(self.kaydet)
        self.btn_back.clicked.connect(self.geri_don)

    def resim_yukle(self, event_data):
        try:
            images = event_data.get('images', [])
            images_sorted = sorted(images, key=lambda x: x.get('width', 0), reverse=True)
            if images_sorted:
                url = images_sorted[0]['url']
                response = requests.get(url, timeout=8)
                pixmap = QtGui.QPixmap()
                pixmap.loadFromData(response.content)
                pixmap = pixmap.scaled(
                    self.lbl_image.width(),
                    self.lbl_image.height(),
                    QtCore.Qt.KeepAspectRatioByExpanding,
                    QtCore.Qt.SmoothTransformation
                )
                self.lbl_image.setPixmap(pixmap)
            else:
                self.lbl_image.setText("🖼️  Resim bulunamadı")
        except Exception:
            self.lbl_image.setText("🖼️  Resim yüklenemedi")

    def kaydet(self):
        try:
            isim        = self.event_data.get('name', 'Bilinmiyor')
            etkinlik_id = self.event_data.get('id', '')

            # Detay ekranındaki tarihi ve saati birleştirip veritabanına yazıyoruz
            tarih       = self.event_data.get('dates', {}).get('start', {}).get('localDate', 'Bilinmiyor')
            saat        = self.event_data.get('dates', {}).get('start', {}).get('localTime', '')
            tam_tarih_saat = f"{tarih} {saat[:5]}" if saat else f"{tarih} 20:00"

            venues      = self.event_data.get('_embedded', {}).get('venues', [{}])
            mekan_adi   = venues[0].get('name', 'Bilinmiyor') if venues else 'Bilinmiyor'
            sehir_adi   = venues[0].get('city', {}).get('name', '') if venues else ''
            tam_mekan   = f"{mekan_adi}, {sehir_adi}" if sehir_adi else mekan_adi

            conn = sqlite3.connect("database.db")
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM kaydedilen_etkinlikler WHERE kullanici_adi = ? AND etkinlik_id = ?",
                (self.kullanici_adi, etkinlik_id)
            )
            if cursor.fetchone():
                QtWidgets.QMessageBox.information(self, "Bilgi", "Bu etkinlik zaten kaydedilmiş!")
                conn.close()
                return
                
            cursor.execute(
                "INSERT INTO kaydedilen_etkinlikler "
                "(kullanici_adi, etkinlik_id, etkinlik_adi, tarih, sehir, fiyat, onaylandi) "
                "VALUES (?, ?, ?, ?, ?, ?, 0)",
                (self.kullanici_adi, etkinlik_id, isim, tam_tarih_saat, tam_mekan, self.fiyat_str)
            )
            conn.commit()
            conn.close()
            QtWidgets.QMessageBox.information(self, "Kaydedildi", f"✅ '{isim}' kaydedildi!")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Hata", f"Kayıt hatası:\n{e}")
    def geri_don(self):
        self.discovery = EventWiseDiscovery(self.kullanici_adi)
        self.discovery.show()
        self.close()
# ------------------------- Uygulama Başlangıcı -------------------------
if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    stil = qss_oku("style.qss")
    app.setStyleSheet(stil)
    pencereler = EventWiseLogin()
    pencereler.show()
    sys.exit(app.exec_())