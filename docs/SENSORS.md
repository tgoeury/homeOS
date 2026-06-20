# HomeOS — Capteurs Zigbee

HomeOS utilise des capteurs **Zigbee** pilotés par **Zigbee2MQTT**.
Aucun code Python ne tourne sur les capteurs — ils communiquent via le
coordinateur Zigbee (clé USB) branché sur la machine qui héberge Zigbee2MQTT.

---

## Matériel utilisé

### SONOFF SNZB-02P — Température & Humidité

- Protocole : Zigbee 3.0
- Mesures : température (±0,1°C), humidité (±1%)
- Alimentation : CR2477 (autonomie ~1 an)
- Fréquence de publication : sur changement (environ toutes les 30–60 s en conditions normales)
- Payload Zigbee2MQTT : `{"temperature": 21.4, "humidity": 58, "battery": 97}`

**Placement recommandé** : à l'abri du soleil direct et des courants d'air, à mi-hauteur du mur.

### Vish-tec SGS01Z — Humidité sol (plantes)

- Protocole : Zigbee 3.0
- Mesure : humidité volumétrique du sol (0–100%)
- Alimentation : AAA × 2
- Payload Zigbee2MQTT : `{"soil_moisture": 42, "battery": 98}`

**Seuil d'alerte** : configurable via `ALERT_PLANT_WATER_MIN` dans `config.py`
(défaut : 50% — en dessous, une alerte apparaît dans le journal Accueil).

---

## Infrastructure Zigbee2MQTT

```
[Capteurs Zigbee] ──► [Coordinateur USB] ──► [Zigbee2MQTT] ──► [Mosquitto] ──► [HomeOS]
  SNZB-02P / SGS01Z      ConBee II / CC2652      sur RPi           MQTT broker    dashboard
```

Zigbee2MQTT expose une interface web (port 8080 par défaut) pour visualiser
les appareils, modifier les friendly_names et surveiller le réseau maillé.

---

## Déclarer un nouveau capteur

1. Appairer le capteur via l'interface Zigbee2MQTT (ou bouton d'appairage si activé)
2. Relever son `friendly_name` (visible dans Zigbee2MQTT ou via `mosquitto_sub -t "zigbee2mqtt/#" -v`)
3. L'ajouter dans `config.py` :

```python
# Capteur température/humidité dans une pièce
ZIGBEE_DEVICES["Mon_Capteur_Salon"] = {"type": "snzb02p", "room": "salon"}

# Capteur sol pour une plante
ZIGBEE_DEVICES["Ficus_moisture"] = {"type": "sgs01z", "plant": "ficus"}
```

4. Ajouter la pièce dans `ROOMS` si elle n'existe pas encore, ou ajouter
   l'ID de la plante dans les sensors de la pièce correspondante.
5. Redémarrer le dashboard.

Les appareils Zigbee détectés mais non encore mappés dans `ZIGBEE_DEVICES`
apparaissent automatiquement dans la section **"Appareils Zigbee — découverte"**
de l'onglet Capteurs, avec leur topic et leur dernier payload.

---

## Données stockées

Chaque valeur est enregistrée **write-on-change** dans `data/cache.db` (table `history`) :

| Série | Déclencheur |
|-------|-------------|
| `sensor_<room>_temperature` | Nouveau message MQTT avec température différente |
| `sensor_<room>_humidity` | Nouveau message MQTT avec humidité différente |
| `plant_<id>_soil_moisture` | Nouveau message MQTT avec humidité sol différente |

L'historique alimente les graphes 24 h de l'onglet Capteurs.
