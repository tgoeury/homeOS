"""
HomeOS — modules/sensor_store.py
Store thread-safe des dernières valeurs Zigbee2MQTT.

Alimentation : mqtt_client appelle sensor_store.update(topic, payload).
Lecture      : callbacks appellent get_room_value() / get_plant_value().
Découverte   : unmapped_devices() retourne les appareils non encore configurés.

Historique   : toute valeur reçue (température, humidité, luminosité, soil_moisture)
               est immédiatement persistée dans data/cache.db via data_cache.write()
               (dernière valeur connue) et data_cache.log() (série temporelle).
               Force=True : bypass write-on-change, chaque message MQTT est enregistré.
"""

import threading
import time

import config as CFG
from modules.data_cache import data_cache

_PLANT_MAX_AGE = 24 * 3600   # 24 h — les capteurs plantes envoient rarement


def _safe_id(name: str) -> str:
    """Transforme un nom de device en identifiant sûr pour les noms de séries SQLite."""
    return "".join(c if c.isalnum() or c == "-" else "_" for c in name.lower())


class SensorStore:
    # Sous-topics Zigbee2MQTT à ignorer (bridge, availability, etc.)
    _SKIP_DEVICES = {"bridge", "log", "logging", "ota"}

    def __init__(self):
        self._lock = threading.Lock()
        # { friendly_name: { champ: valeur, ..., "_ts": float } }
        self._data: dict[str, dict] = {}
        # Cache du mapping device_name → plant_id pour éviter de re-parcourir CFG à chaque message
        self._device_to_plant: dict[str, str | None] = {}

    def _plant_id_for(self, device: str) -> str:
        """Retourne le plant_id configuré pour ce device, ou l'identifiant sanitisé du device."""
        if device not in self._device_to_plant:
            plant_id = None
            for dev_name, dev_cfg in CFG.ZIGBEE_DEVICES.items():
                if dev_name == device and dev_cfg.get("plant"):
                    plant_id = dev_cfg["plant"]
                    break
            self._device_to_plant[device] = plant_id
        return self._device_to_plant[device] or _safe_id(device)

    # ── Alimentation ──────────────────────────────────────────────────────────

    def update(self, topic: str, payload: dict) -> None:
        """
        Stocke le payload d'un topic MQTT.
        Seulement les topics de la forme zigbee2mqtt/<device> (longueur 2).
        Les sous-topics (availability, set, get…) et bridge/ sont ignorés.
        Tout message contenant soil_moisture est automatiquement archivé en CSV.
        """
        parts = topic.split("/")
        if len(parts) != 2:
            return
        device = parts[1]
        if device in self._SKIP_DEVICES:
            return
        with self._lock:
            if device not in self._data:
                self._data[device] = {}
            self._data[device].update(payload)
            self._data[device]["_ts"] = time.time()

        # Persistance immédiate temp/humidity/luminosité → DB (force=True : toute valeur reçue)
        dev_cfg = CFG.ZIGBEE_DEVICES.get(device, {})
        room_id = dev_cfg.get("room")
        if room_id:
            dev_type = dev_cfg.get("type", "snzb02p").upper()
            src = f"{dev_type} · Zigbee2MQTT · {device}"
            for field, unit in (("temperature", "°C"), ("humidity", "%"), ("luminosity", "lux")):
                if field in payload:
                    try:
                        val = round(float(payload[field]), 1)
                        data_cache.write(f"sensor.{room_id}.{field}", val, unit, src)
                        data_cache.log(f"sensor_{room_id}_{field}", val, unit, src, force=True)
                    except (TypeError, ValueError):
                        pass

        # Historique soil_moisture — dynamique pour tout device détecté (force=True)
        if "soil_moisture" in payload:
            try:
                val = round(float(payload["soil_moisture"]), 1)
                plant_id = self._plant_id_for(device)
                src_plant = f"SGS01Z · Zigbee2MQTT · {device}"
                data_cache.write(f"plant.{plant_id}.soil_moisture", val, "%", src_plant)
                data_cache.log(f"plant_{plant_id}_soil_moisture", val, "%", src_plant, force=True)
            except (TypeError, ValueError):
                pass

    # ── Lecture par champ ─────────────────────────────────────────────────────

    def get_field(self, device_name: str, field: str, max_age: float = 900) -> float | None:
        """Retourne la valeur d'un champ pour un device, None si absent ou périmé.
        max_age=900 : 3× l'intervalle max du SNZB-02P (300 s), tolère les retards Zigbee."""
        with self._lock:
            d = self._data.get(device_name)
            if not d:
                return None
            if time.time() - d.get("_ts", 0) > max_age:
                return None
            val = d.get(field)
            try:
                return float(val) if val is not None else None
            except (TypeError, ValueError):
                return None

    # ── Lookup par pièce / plante (via CFG.ZIGBEE_DEVICES) ───────────────────

    def get_room_value(self, room_id: str, field: str, max_age: float = 900) -> float | None:
        """
        Retourne la valeur d'un champ pour la pièce donnée.
        Cherche dans CFG.ZIGBEE_DEVICES le(s) device(s) avec "room" == room_id.
        """
        for dev_name, dev_cfg in CFG.ZIGBEE_DEVICES.items():
            if dev_cfg.get("room") == room_id:
                val = self.get_field(dev_name, field, max_age)
                if val is not None:
                    return val
        return None

    def get_plant_value(self, plant_id: str, field: str = "soil_moisture",
                        max_age: float = _PLANT_MAX_AGE) -> float | None:
        """
        Retourne la valeur sol pour un capteur plante.
        Cherche dans CFG.ZIGBEE_DEVICES le device avec "plant" == plant_id.
        max_age étendu à 24h car les SGS01Z envoient bien moins fréquemment
        que les capteurs température/humidité.
        """
        for dev_name, dev_cfg in CFG.ZIGBEE_DEVICES.items():
            if dev_cfg.get("plant") == plant_id:
                val = self.get_field(dev_name, field, max_age)
                if val is not None:
                    return val
        return None

    # ── Découverte ────────────────────────────────────────────────────────────

    def known_devices(self) -> list[str]:
        """Tous les noms de devices ayant envoyé au moins un message."""
        with self._lock:
            return list(self._data.keys())

    def unmapped_devices(self) -> dict:
        """
        Devices présents dans le store mais absents de CFG.ZIGBEE_DEVICES.
        Sert à la section de découverte de l'onglet Capteurs.
        """
        mapped = set(CFG.ZIGBEE_DEVICES.keys())
        with self._lock:
            return {
                name: dict(data)
                for name, data in self._data.items()
                if name not in mapped
            }

    def get_device_snapshot(self, device_name: str) -> dict | None:
        """Copie des dernières données d'un device, None si inconnu."""
        with self._lock:
            d = self._data.get(device_name)
            return dict(d) if d else None

    def mapped_active_count(self, max_age: float = 900) -> int:
        """Nombre de devices configurés ayant envoyé des données récentes."""
        now = time.time()
        count = 0
        with self._lock:
            for dev_name in CFG.ZIGBEE_DEVICES:
                d = self._data.get(dev_name)
                if d and (now - d.get("_ts", 0)) <= max_age:
                    count += 1
        return count


sensor_store = SensorStore()
