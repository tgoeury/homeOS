# HomeOS — Architecture

## Structure des fichiers

```
homeos_v0.1.0/
├── app.py                      Point d'entrée : Dash, route Flask /webhook/chat, thread prefetch
├── config.py                   Source unique de vérité pour tous les réglages (ignoré par git)
├── config.example.py           Modèle vierge à copier → config.py
├── requirements.txt
│
├── modules/
│   │
│   │── Présentation & thème ──────────────────────────────────────────────────
│   ├── theme.py                Palette CP (Cyberpunk 2077), helpers de style,
│   │                           thème Plotly — source unique de vérité visuelle
│   ├── dashboard_layout.py     Layout statique : topbar, nav 10 onglets, 10 pages
│   │
│   │── Données temps réel ────────────────────────────────────────────────────
│   ├── callbacks.py            Tous les @callback Dash (clock, nav, capteurs, météo…)
│   ├── weather_service.py      OpenMeteo — cache TTL 10 min (singleton weather_service)
│   ├── sysinfo.py              CPU / RAM / disque / température (psutil ou /proc)
│   ├── plex_client.py          PlexAPI : lecture en cours, file, recherche, historique
│   ├── nextdns_client.py       Stats DNS NextDNS (blocages, pays du trafic)
│   ├── network_scanner.py      nmap -sn sur le subnet LAN (cache TTL 120 s)
│   ├── synology_client.py      API DSM File Station + SYNO.DSM.Info (cache TTL 1 h)
│   │
│   │── Pipeline MQTT / capteurs ──────────────────────────────────────────────
│   ├── mqtt_client.py          Paho-MQTT : abonné zigbee2mqtt/# et homeos/#
│   │                           Auto-reconnexion 15 s — singleton mqtt_client
│   ├── sensor_store.py         Store mémoire thread-safe alimenté par MQTT
│   │                           API : get_room_value(room, field) / get_plant_value(plant)
│   │
│   │── Persistance ───────────────────────────────────────────────────────────
│   ├── data_cache.py           SQLite data/cache.db — table cache (clé/valeur JSON)
│   │                           et table history (séries temporelles)
│   ├── data_logger.py          Façade sur data_cache.log() (write-on-change)
│   │
│   │── Services métier ───────────────────────────────────────────────────────
│   ├── comfort_engine.py       Inférence GRU + planification volets/fenêtres 24 h
│   │                           Checkpoints dans ./models/  (limited.pt / full.pt)
│   ├── enedis_service.py       Conso électrique quotidienne via conso.boris.sh
│   │                           Fetch à 08h42, retry/h en cas d'échec
│   ├── timer_service.py        Minuteurs en mémoire, son d'alarme data-URI WAV
│   ├── ytdlp_service.py        Téléchargement audio via yt-dlp (lectures VLC)
│   │
│   │── Chatbot ───────────────────────────────────────────────────────────────
│   ├── chatbot_engine.py       Synology Chat — webhooks entrant/sortant, store messages
│   └── logic_engine.py         Traitement des messages (FORWARD / ML / CLAUDE)
│
├── assets/
│   └── cyberpunk.css           Thème CP2077 chargé automatiquement par Dash
│
├── models/                     Checkpoints PyTorch (non versionné)
│   ├── limited.pt              Modèle GRU météo+solaire → température intérieure
│   └── full.pt                 Modèle étendu (avec capteurs extérieurs de façade)
│
└── data/                       Données locales — non versionné
    ├── cache.db                SQLite (tables cache + history)
    └── downloads/              Fichiers audio téléchargés par yt-dlp
```

---

## Flux de données

```
OpenMeteo API ──► weather_service (TTL 10 min) ──────────────────────────┐
                                                                          │
Zigbee2MQTT ──► Mosquitto ──► mqtt_client ──► sensor_store (mémoire) ──►│
                                                    │                     │
                                              data_cache.log()           │
                                         (history SQLite write-on-change)│
                                                                          │
EnedisService ──► conso.boris.sh ──► data/cache.db (history enedis_daily)│
                                                                          │
SynologyClient ──► DSM API ──────────────────────────────────────────────│
                                                                          │
sysinfo ──► /proc / psutil ──────────────────────────────────────────────┤
                                                                          │
NextDNS API ──► nextdns_client ──────────────────────────────────────────┤
                                                                          │
nmap -sn ──► network_scanner (TTL 120 s) ────────────────────────────────┤
                                                                          ▼
                                                              callbacks.py
                                                                  │
                                                          Dash / navigateur (LAN)

[Synology Chat]
    POST /webhook/chat ──► chatbot_engine ──► logic_engine
                       ◄── POST webhook entrant ◄────────
```

**Pré-fetch au démarrage** : `app.py` lance un thread daemon (`_prefetch`) qui démarre MQTT, appelle `weather_service.get()` et `get_local_devices()` avant la première connexion navigateur.

---

## Persistance — `data/cache.db`

### Table `cache` (clé/valeur)

| Clé | Contenu |
|-----|---------|
| `weather.snapshot` | Objet WeatherData sérialisé (météo actuelle + prévisions) |
| `weather.inference` | Données OpenMeteo horaires pour le moteur de confort (TTL 10 min) |
| `sensor.<room>.<field>` | Dernière valeur live MQTT par capteur |
| `plant.<id>.soil_moisture` | Dernière humidité sol |
| `network.devices` | Liste des appareils LAN (nmap) |
| `network.dns.*` | Stats NextDNS (blocked, rate, total, countries) |
| `confort.range.<room>` | Plages de confort saisies par l'utilisateur |

### Table `history` (séries temporelles)

| Série (`name`) | Unité | Source |
|----------------|-------|--------|
| `sensor_<room>_temperature` | °C | SNZB-02P via MQTT |
| `sensor_<room>_humidity` | % | SNZB-02P via MQTT |
| `plant_<id>_soil_moisture` | % | SGS01Z via MQTT |
| `weather_temperature` | °C | OpenMeteo |
| `weather_humidity` | % | OpenMeteo |
| `weather_wind_speed` | km/h | OpenMeteo |
| `network_devices_count` | devices | nmap |
| `network_dns_blocked` | requests | NextDNS |
| `network_dns_rate` | % | NextDNS |
| `enedis_daily` | kWh | conso.boris.sh |

---

## Les 10 onglets du dashboard

| ID | Icône | Contenu |
|----|-------|---------|
| `accueil` | 🏠 | Météo résumée, capteurs live, Plex compact, journal système, appareils LAN |
| `capteurs` | 🌡 | Panneaux collapsibles par pièce + plantes, historique 24 h |
| `meteo` | ☁ | Météo détaillée, prévisions 7 j, graphes horaires |
| `musique` | ♪ | Lecteur Plex complet, file d'attente, recherche, artistes récents |
| `minuteurs` | ⏱ | Création / suivi de minuteurs, alarme sonore navigateur |
| `confort` | ❄ | Plages de confort par pièce, bouton CALCULER (moteur GRU) |
| `energie` | ⚡ | Consommation Enedis quotidienne, coût €, graphe mensuel |
| `reseau` | ⬡ | Appareils LAN (nmap), stats NextDNS, NAS Synology, carte choroplèthe |
| `systeme` | ⚙ | CPU / RAM / disque / température, services, anomalies ML |
| `chatbot` | 💬 | Interface de chat Synology Chat (bulles, saisie, effacement) |

→ Voir `docs/PAGES.md` pour le détail de chaque onglet.

---

## Intervalles de rafraîchissement

| Composant Dash | Période | Déclenche |
|----------------|---------|-----------|
| `interval-clock` | 1 s | Horloge, barre de progression Plex |
| `interval-main` | 5 s | Capteurs, journal/accueil, lecteur Plex, minuteurs, badges |
| `interval-sys` | 8 s | Métriques système (CPU, RAM, disque, temp.) |
| `interval-meteo` | 10 min | Météo OpenMeteo |
| `interval-reseau` | 60 s | nmap, NextDNS, NAS Synology |
| `interval-plex-shelf` | 60 s | Artistes récents, playlists |
| `interval-chatbot` | 2 s | Bulles de chat, badges CHATBOT / LOGIC |

---

## Démarrage

```bash
cp config.example.py config.py
# Remplir config.py (MQTT, Plex, OpenMeteo, NextDNS, Enedis, Synology…)

pip install -r requirements.txt
python app.py
# Dashboard : http://<ip-machine>:8050

# Test isolation weather_service :
python -m modules.weather_service

# Test webhook chatbot :
curl -X POST http://localhost:8050/webhook/chat \
  -d "token=<SYNOLOGY_CHAT_TOKEN>" \
  -d "text=Hello" -d "username=Test"
```
