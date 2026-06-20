FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    PORT=8099

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-driver \
    xvfb \
    fonts-liberation fonts-noto-cjk \
    ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install-deps chromium 2>/dev/null || true

COPY . .

COPY start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 8099

CMD ["/start.sh"]
