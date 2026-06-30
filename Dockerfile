FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    PORT=8099

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-driver \
    xvfb \
    imagemagick x11-apps scrot xdotool \
    fonts-liberation fonts-noto-cjk \
    ca-certificates curl wget \
    libgtk-3-0 libpolkit-gobject-1-0 \
    && rm -rf /var/lib/apt/lists/*

# Install AnyDesk
RUN wget -q -O /tmp/anydesk.deb "https://download.anydesk.com/linux/anydesk_6.4.3-1_amd64.deb" \
    && dpkg -i /tmp/anydesk.deb 2>&1 || true \
    && rm /tmp/anydesk.deb

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install-deps chromium 2>/dev/null || true

COPY . .

COPY start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 8099

CMD ["/start.sh"]
