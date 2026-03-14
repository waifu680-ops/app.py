# Temel Python imajını alıyoruz
FROM python:3.9-slim

# Gerekli indirme araçlarını VE Ren'Py'ın ihtiyaç duyduğu grafik kütüphanelerini kuruyoruz
RUN apt-get update && apt-get install -y \
    wget \
    bzip2 \
    unzip \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Ren'Py 8.1.3 Resmi SDK'sını indirip sunucuya kuruyoruz
RUN wget https://www.renpy.org/dl/8.1.3/renpy-8.1.3-sdk.tar.bz2
RUN tar -xf renpy-8.1.3-sdk.tar.bz2
RUN rm renpy-8.1.3-sdk.tar.bz2
ENV RENPY_DIR=/renpy-8.1.3-sdk

# Çalışma klasörümüzü ayarlıyoruz
WORKDIR /app

# Gerekli Python kütüphanelerini yüklüyoruz
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Senin app.py vb. dosyalarını sunucuya kopyalıyoruz
COPY . .

# Ren'Py motoruna çalışma izni veriyoruz
RUN chmod +x $RENPY_DIR/renpy.sh

# Web sunucusu portunu açıyoruz
EXPOSE 5000

# Uygulamayı başlatıyoruz
CMD ["python", "app.py"]
