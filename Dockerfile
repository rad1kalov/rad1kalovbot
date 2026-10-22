FROM python:3.11-slim

WORKDIR /app

# Установка шрифтов для историй, ffmpeg и chromium для HTML-рендера карточек
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    fonts-liberation \
    fonts-freefont-ttf \
    ca-certificates \
    ffmpeg \
    chromium \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

VOLUME ["/app/data"]

EXPOSE 8080

CMD ["python", "main.py"]
