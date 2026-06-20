# HomeOS — MQTT & Zigbee2MQTT

HomeOS s'abonne à un broker **Mosquitto** qui reçoit les données de **Zigbee2MQTT**.
Chaque capteur Zigbee publie sur `zigbee2mqtt/<friendly_name>` avec un payload JSON.

---

## Installation Mosquitto

```bash
sudo apt install mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto
```

Pour l'écoute LAN (nécessaire si Mosquitto et le dashboard sont sur des machines différentes),
créer `/etc/mosquitto/conf.d/homeos.conf` :

```
listener 1883 0.0.0.0
allow_anonymous true
```

Si auth souhaitée :
```
listener 1883 0.0.0.0
password_file /etc/mosquitto/passwd
```

Puis dans `config.py` :
```python
MQTT_BROKER_HOST = "192.168.1.X"
MQTT_USERNAME    = "user"
MQTT_PASSWORD    = "pass"
```

---

## Topics souscrits par HomeOS

HomeOS s'abonne à deux wildcards :

| Wildcard | Usage |
|----------|-------|
| `zigbee2mqtt/#` | Données capteurs Zigbee (SNZB-02P, SGS01Z…) |
| `homeos/#` | Réservé à de futurs actionneurs/scripts HomeOS |

`mqtt_client.py` ignore les topics avec plus de deux segments (`bridge/`, `availability`…).
Seuls les topics `zigbee2mqtt/<friendly_name>` (exactement deux composants) sont traités.

---

## Format des payloads Zigbee2MQTT

### SONOFF SNZB-02P — capteur température / humidité

```json
{
  "temperature": 21.4,
  "humidity": 58,
  "battery": 97,
  "voltage": 3000,
  "linkquality": 255
}
```

### Vish-tec SGS01Z — capteur humidité sol

```json
{
  "soil_moisture": 42,
  "battery": 98,
  "linkquality": 200
}
```

HomeOS ne lit que `temperature`, `humidity` et `soil_moisture` ; les autres champs sont ignorés.

---

## Mapping friendly_name → HomeOS

Le mapping est défini dans `config.py` sous `ZIGBEE_DEVICES` :

```python
ZIGBEE_DEVICES = {
    "1_Bureau_TempHygro":   {"type": "snzb02p", "room": "bureau"},
    "4_Salon_TempHygro":    {"type": "snzb02p", "room": "salon"},
    "Coleus_mere_moisture": {"type": "sgs01z",  "plant": "coleus-mere"},
}
```

- `"room"` → l'id doit exister dans `ROOMS` (onglet Capteurs)
- `"plant"` → l'id doit correspondre à un sensor_id dans `ROOMS` (sous-section Plantes)

Les appareils reçus mais **non mappés** apparaissent dans la section
"Appareils Zigbee — découverte" de l'onglet Capteurs.

---

## Vérification en ligne de commande

```bash
# Voir tous les messages Zigbee2MQTT en temps réel
mosquitto_sub -h 192.168.1.X -t "zigbee2mqtt/#" -v

# Simuler un capteur salon (pour tester sans matériel)
mosquitto_pub -h 192.168.1.X \
  -t "zigbee2mqtt/4_Salon_TempHygro" \
  -m '{"temperature": 21.4, "humidity": 58}'
```
