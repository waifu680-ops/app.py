# Temel Python imajını alıyoruz
FROM python:3.9-slim

# Gerekli grafik ve indirme araçlarını kuruyoruz
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

# 1. MOTOR: Ren'Py 7.5.3 (Python 2 - Eski ve Yaygın Oyunlar İçin)
RUN wget https://www.renpy.org/dl/7.5.3/renpy-7.5.3-sdk.tar.bz2 && \
    tar -xf renpy-7.5.3-sdk.tar.bz2 && \
    rm renpy-7.5.3-sdk.tar.bz2

# 2. MOTOR: Ren'Py 8.1.3 (Python 3 - Yeni Nesil Oyunlar İçin)
RUN wget https://www.renpy.org/dl/8.1.3/renpy-8.1.3-sdk.tar.bz2 && \
    tar -xf renpy-8.1.3-sdk.tar.bz2 && \
    rm renpy-8.1.3-sdk.tar.bz2

# Çalışma klasörümüzü ayarlıyoruz
WORKDIR /app

# Python kütüphanelerini yüklüyoruz
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Dosyalarımızı kopyalıyoruz
COPY . .

# Her iki motora da çalışma izni veriyoruz
RUN chmod +x /renpy-7.5.3-sdk/renpy.sh
RUN chmod +x /renpy-8.1.3-sdk/renpy.sh

# Web sunucusu portunu açıyoruz
EXPOSE 5000

# Uygulamayı başlatıyoruz
CMD ["python", "app.py"]
