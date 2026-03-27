FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg wget unzip curl \
    && rm -rf /var/lib/apt/lists/*

# Install mp4decrypt from Bento4
RUN wget -q "https://www.bok.net/Bento4/binaries/Bento4-SDK-1-6-0-641.x86_64-unknown-linux.zip" \
    -O /tmp/bento4.zip \
    && unzip -q /tmp/bento4.zip -d /tmp/bento4 \
    && cp "/tmp/bento4/Bento4-SDK-1-6-0-641.x86_64-unknown-linux/bin/mp4decrypt" /usr/local/bin/ \
    && chmod +x /usr/local/bin/mp4decrypt \
    && rm -rf /tmp/bento4*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p DOWNLOADS SESSIONS

CMD ["python", "main.py"]
