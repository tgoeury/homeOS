# syntax=docker/dockerfile:1
#
# Multi-arch : linux/amd64 (PC Ubuntu 22) et linux/arm64 (RPi 3 avec OS 64-bit)
#
# Build image locale :
#   docker build -t homeos .
#
# Build multi-arch avec buildx (depuis le PC) :
#   docker buildx build --platform linux/amd64,linux/arm64 -t homeos .
#   Note : la cross-compilation de torch via QEMU est très lente (~1 h sur arm64).
#   Préférer un build natif sur chaque machine, ou utiliser --load sur la cible.
#
# Lancement :
#   docker run -d --name homeos \
#     -p 8050:8050 \
#     -v $(pwd)/config.py:/app/config.py:ro \
#     -v $(pwd)/data:/app/data \
#     -v $(pwd)/models:/app/models:ro \
#     --cap-add NET_RAW \
#     homeos

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# --- Dépendances système ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Scanner réseau (nmap -sn pour la découverte LAN)
    nmap \
    libcap2-bin \
    # Bindings VLC requis par python-vlc (ytdlp_service)
    libvlc5 \
    libvlc-dev \
    && rm -rf /var/lib/apt/lists/*

# Permet à nmap de faire des pings ICMP sans root via cap_net_raw
# (le flag --cap-add NET_RAW au runtime est aussi nécessaire)
RUN setcap cap_net_raw+ep /usr/bin/nmap

# --- Dépendances Python ---
# Layer séparé : si le code change mais pas requirements.txt, ce layer est réutilisé
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Code applicatif ---
COPY . .

# --- Utilisateur non-root ---
# data/ et models/ sont créés ici pour le cas sans volume ;
# quand les volumes sont montés, ces dirs sont remplacées à runtime.
RUN useradd --create-home appuser \
    && mkdir -p data models \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8050

CMD ["python", "app.py"]
