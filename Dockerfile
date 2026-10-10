FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    ffmpeg \
    libopus0 \
    unzip \
    && curl -fsSL https://deno.land/install.sh -o /tmp/deno-install.sh \
    && DENO_INSTALL=/usr/local sh /tmp/deno-install.sh \
    && rm /tmp/deno-install.sh \
    && apt-get purge -y --auto-remove curl unzip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
