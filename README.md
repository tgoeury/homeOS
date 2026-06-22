# HomeOS

A self-hosted home automation dashboard built with Plotly Dash. Designed to run continuously on a low-power PC on the local network and be accessible from any device on the LAN.

---

## Features

| Tab | What it does |
|---|---|
| **Home** | At-a-glance overview: current temperature/humidity, weather summary, active alerts |
| **Sensors** | Live Zigbee sensor readings (temperature, humidity) with 24 h graphs per room |
| **Weather** | OpenMeteo forecast: hourly + 7-day outlook for the configured location |
| **Music** | Plex Media Server integration: now playing, transport controls, album shelf, yt-dlp downloader |
| **Timers** | Up to 3 concurrent countdown/alarm timers with in-browser audio |
| **Comfort** | 24 h shutter/window planning via an ONNX thermal model; per-room temperature sliders |
| **Energy** | Daily electricity consumption from Enedis (via conso.boris.sh), cost estimate |
| **Network** | LAN device scan (nmap), NextDNS blocked query stats |
| **System** | CPU, RAM, disk, temperature of the host machine |
| **Chatbot** | Synology Chat integration — ask about room temperature, weather, alerts |

---

## Architecture

```
OpenMeteo API ──► weather_service.py (10 min cache)
                          │
Zigbee2MQTT ──► Mosquitto ──► mqtt_client.py ──► sensor_store (in-memory)
                                                        │
                                                 data_cache.log()
                                                 (history → data/cache.db)
                          │
Enedis API ──► conso.boris.sh ──► data/cache.db (table: history)
                          │
                    callbacks.py ──► Dash component tree ──► browser
```

**Persistence** — `data/cache.db` (SQLite, two tables):

- `cache` — key/value store for last-known values and comfort ranges
- `history` — time series `(name, ts, source, value, unit)` with write-on-change + 30 min heartbeat

---

## Hardware
Sensors communicate over Zigbee → Zigbee2MQTT → Mosquitto → homeOS. No cloud dependency for sensor data.

---

## Stack

| Layer | Technology |
|---|---|
| Dashboard | [Plotly Dash](https://dash.plotly.com/) 4.3+ |
| Data store | SQLite 3 (via stdlib `sqlite3`) |
| Zigbee bridge | [Zigbee2MQTT](https://www.zigbee2mqtt.io/) + Mosquitto |
| MQTT client | paho-mqtt |
| Thermal model | ONNX Runtime (`models/limited.onnx`) |
| Weather | [OpenMeteo](https://open-meteo.com/) (free, no API key) |
| Energy | [conso.boris.sh](https://conso.boris.sh/) (Enedis proxy) |
| Music | PlexAPI + yt-dlp |
| Network scan | nmap |
| DNS stats | NextDNS API |

---

## Requirements

- Python 3.10+
- `nmap` installed on the host (for LAN scanning)
- Mosquitto broker running locally (default `localhost:1883`)
- Zigbee2MQTT running and paired devices

---

## Installation

```bash
git clone <repo>
cd homeOS

pip install -r requirements.txt

# Copy and edit the configuration file
cp config.example.py config.py   # or create config.py from scratch
$EDITOR config.py

python app.py
# → http://localhost:8050
```

The server binds to `0.0.0.0:8050` and is reachable from any device on the LAN.

### Recommended startup order

1. Mosquitto
2. Zigbee2MQTT
3. `python app.py`

If homeOS starts before Z2M, the MQTT client reconnects automatically every 15 seconds.

---

## Configuration

All personal data, tokens, and preferences live in `config.py` (excluded from version control).

```python
# Location (OpenMeteo weather)
GEO_LATITUDE, GEO_LONGITUDE = 45.55, 6.22
GEO_TIMEZONE = "Europe/Paris"

# MQTT broker
MQTT_BROKER_HOST = "localhost"
MQTT_BROKER_PORT = 1883

# Rooms and sensors
ROOMS = [
    ("salon", "Living room", "cyan", [
        ("salon-temp",  "Temperature", "--°C", "cyan", "SNZB-02P · Zigbee2MQTT"),
        ("salon-hygro", "Humidity",    "-- %", "cyan", "SNZB-02P · Zigbee2MQTT"),
    ]),
    ...
]

# Zigbee device → room mapping (friendly name as shown in Z2M)
ZIGBEE_DEVICES = {
    "Living_room_TempHygro": {"type": "snzb02p", "room": "salon"},
    "Plant_moisture":        {"type": "sgs01z",  "plant": "plant-id"},
}

# Enedis electricity
ENEDIS_TOKEN = "..."   # from conso.boris.sh
ENEDIS_PRM   = "..."   # PDL identifier (14 digits, on your bill)
ELECTRICITY_PRICE_KWH = 0.18

# Plex
PLEX_HOST  = "192.168.1.x"
PLEX_TOKEN = "..."

# Alert thresholds (global fallback)
ALERT_TEMP_MIN  = 16.0
ALERT_TEMP_MAX  = 25.0
ALERT_HYGRO_MIN = 40
ALERT_HYGRO_MAX = 55
```

> Temperature colour-coding on the Sensors tab uses **per-room comfort ranges** set via the Comfort tab sliders (stored in `data/cache.db`), falling back to the global `ALERT_TEMP_*` values if no per-room range has been set yet.

### Adding a room

1. Add an entry to `ZIGBEE_DEVICES` with the Z2M friendly name.
2. Add an entry to `ROOMS` with the matching `room` id.
3. Restart homeOS.

New devices that send MQTT messages but are not yet mapped appear automatically in the **Sensors → Zigbee discovery** section.

---

## Comfort model (ONNX)

The Comfort tab runs an in-process thermal model via ONNX Runtime (no subprocess, no PyTorch at runtime).

```
models/
  limited.onnx   # weather + solar + shutters/windows → indoor temperature
  full.onnx      # adds outdoor sensor inputs (optional, higher accuracy)
```

Model metadata (feature columns, normalisation stats, hyperparameters) is embedded directly inside the ONNX file under the `home_model_meta` custom property. No separate checkpoint files needed.

`model_status()` returns `"full"` / `"limited"` / `"none"` depending on which files are present.

---

## Docker

### Build

```bash
docker build -t homeos .
```

Multi-architecture (amd64 + arm64, e.g. for Raspberry Pi):

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t homeos .
```

### Run

```bash
docker run -d --name homeos \
  -p 8050:8050 \
  -v $(pwd)/config.py:/app/config.py:ro \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/models:/app/models:ro \
  --cap-add NET_RAW \
  homeos
```

`--cap-add NET_RAW` is required for nmap ICMP ping without root.

### CI/CD

Pushing to `master` automatically builds and pushes a multi-arch image to the GitHub Container Registry via `.github/workflows/docker.yml`.

---

## Tests

```bash
pytest
```

288 tests covering: sensor store, data cache (write-on-change + heartbeat), comfort engine (ONNX pipeline, planning, solar features), weather service, Enedis, timers, network, theme, chatbot.

---

## Data flow notes

- **MQTT retained messages are ignored.** When Z2M restarts, it republishes the last known device states as retained messages. homeOS discards these (`msg.retain == True`) to avoid showing stale values from a previous session.
- **History heartbeat.** The write-on-change logger forces a DB entry every 30 minutes even when the value is stable, keeping graphs continuous during long steady-state periods.
- **Sensor freshness.** SNZB-02P values older than 15 minutes are treated as stale (`--`). SGS01Z soil moisture values are valid for 24 hours (the sensor reports every ~30 minutes).

---

## License

Private — all rights reserved.
