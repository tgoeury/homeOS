"""
HomeOS — config.example.py
══════════════════════════════════════════════════════════════
Fichier modèle — copier en config.py et remplir les valeurs.
    cp config.example.py config.py
config.py est ignoré par git (.gitignore).
══════════════════════════════════════════════════════════════
"""

# ── Identité du logement ──────────────────────────────────────
HOME_NAME     = "Mon Appartement"
HOME_CITY     = "Paris"

# ── Localisation géographique (météo OpenMeteo) ───────────────
GEO_LATITUDE  = 48.8566
GEO_LONGITUDE = 2.3522
GEO_TIMEZONE  = "Europe/Paris"
GEO_LABEL     = f"{HOME_CITY} // EXT. 48°51'N 2°21'E"

# ── Serveur Dash ──────────────────────────────────────────────
DASH_HOST  = "0.0.0.0"
DASH_PORT  = 8050
DASH_DEBUG = False

# ── Intervalles de rafraîchissement (millisecondes) ───────────
INTERVAL_CLOCK_MS   = 1_000
INTERVAL_SENSORS_MS = 5_000
INTERVAL_SYSTEM_MS  = 8_000
INTERVAL_WEATHER_MS = 600_000

# ── MQTT ──────────────────────────────────────────────────────
MQTT_BROKER_HOST = "localhost"
MQTT_BROKER_PORT = 1883
MQTT_USERNAME    = ""
MQTT_PASSWORD    = ""

# ── InfluxDB ──────────────────────────────────────────────────
INFLUX_URL    = "http://localhost:8086"
INFLUX_TOKEN  = "VOTRE_TOKEN_INFLUXDB"
INFLUX_ORG    = "homeos"
INFLUX_BUCKET = "sensors"

# ── Plex ─────────────────────────────────────────────────────
PLEX_HOST  = "192.168.1.X"
PLEX_PORT  = 32400
PLEX_TOKEN = "VOTRE_TOKEN_PLEX"
PLEX_LABEL = f"PLEX · {PLEX_HOST}:{PLEX_PORT}"

# ── NextDNS ───────────────────────────────────────────────────
NEXTDNS_API_KEY    = "VOTRE_CLE_API_NEXTDNS"
NEXTDNS_PROFILE_ID = "VOTRE_ID_PROFIL"

# ── Synology Chat Bot ─────────────────────────────────────────
# URL du webhook entrant : Intégrations → Webhooks → Créer un webhook entrant → Copier l'URL
SYNOLOGY_CHAT_WEBHOOK_URL = "https://votre-synology/webapi/..."

# Token du bot sortant : Intégrations → Bots → Créer un bot sortant → Copier le token
SYNOLOGY_CHAT_TOKEN       = "VOTRE_TOKEN_BOT"

# ── Chatbot LangGraph ────────────────────────────────────────────────────────
# En production Docker Compose, utiliser le nom du service (ex. "chatbot").
# En dev local, remplacer par "http://localhost:8000".
CHATBOT_API_URL = "http://chatbot:8000"

# ── Seuils d'alerte ───────────────────────────────────────────
ALERT_TEMP_MIN  = 16.0
ALERT_TEMP_MAX  = 27.0
ALERT_HYGRO_MIN = 40
ALERT_HYGRO_MAX = 55
ALERT_LUX_MIN   = 100

# ── Pièces et capteurs ────────────────────────────────────────
ROOMS = [
    ("salon",   "Salon",   "cyan",   [
        ("salon-temp",  "Température", "21.4°C", "cyan",   "DHT22 · RPi Zero W #1"),
        ("salon-hygro", "Hygrométrie", "58 %",   "yellow", "DHT22 · RPi Zero W #1"),
    ]),
    ("bureau",  "Bureau",  "green",  [
        ("bureau-temp",  "Température", "--°C",  "green",  "DHT22 · RPi Zero W #2"),
        ("bureau-lux",   "Luminosité",  "-- lx", "cyan",   "BH1750 · RPi Zero W #2"),
    ]),
]
