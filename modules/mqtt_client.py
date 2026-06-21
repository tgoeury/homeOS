"""
HomeOS — modules/mqtt_client.py
Subscriber Paho-MQTT : Zigbee2MQTT (zigbee2mqtt/#) + topics legacy (homeos/#).
Reconnexion automatique toutes les 15 s en cas de perte de connexion.
"""

import json
import logging
import threading
import time

import paho.mqtt.client as mqtt

import config as CFG

log = logging.getLogger("mqtt_client")


class MQTTClient:
    def __init__(self):
        self._connected = False
        self._client: mqtt.Client | None = None
        self._callbacks: list = []

    def register(self, callback) -> None:
        """Enregistre un callback(topic: str, payload: dict) appelé à chaque message."""
        self._callbacks.append(callback)

    def start(self) -> None:
        """Démarre la boucle de connexion dans un thread daemon."""
        threading.Thread(target=self._loop, daemon=True, name="mqtt").start()

    def is_connected(self) -> bool:
        return self._connected

    # ── Boucle interne ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while True:
            try:
                self._connect_and_run()
            except Exception as exc:
                log.warning("MQTT connexion échouée : %s — réessai dans 15 s", exc)
            self._connected = False
            time.sleep(15)

    def _connect_and_run(self) -> None:
        # Compatibilité paho-mqtt 1.x et 2.x
        if hasattr(mqtt, "CallbackAPIVersion"):
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        else:
            client = mqtt.Client()

        client.on_connect    = self._on_connect
        client.on_message    = self._on_message
        client.on_disconnect = self._on_disconnect

        if CFG.MQTT_USERNAME:
            client.username_pw_set(CFG.MQTT_USERNAME, CFG.MQTT_PASSWORD)

        client.connect(CFG.MQTT_BROKER_HOST, CFG.MQTT_BROKER_PORT, keepalive=60)
        self._client = client
        client.loop_forever()

    # ── Handlers Paho ─────────────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected = True
            client.subscribe("zigbee2mqtt/#")
            client.subscribe("homeos/#")
            log.info("MQTT connecté sur %s:%s", CFG.MQTT_BROKER_HOST, CFG.MQTT_BROKER_PORT)
        else:
            log.warning("MQTT rc=%s (broker injoignable ?)", rc)

    def _on_message(self, client, userdata, msg):
        # Les messages retained sont les dernières valeurs connues stockées par le broker
        # (republié par Z2M au démarrage). Ils peuvent dater d'une session précédente et
        # doivent être ignorés : sensor_store les horodaterait à now(), les faisant passer
        # pour des données fraîches alors qu'elles sont potentiellement périmées.
        if msg.retain:
            return
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(payload, dict):
            return
        for cb in self._callbacks:
            try:
                cb(msg.topic, payload)
            except Exception as exc:
                log.debug("Callback MQTT erreur : %s", exc)

    def _on_disconnect(self, client, userdata, rc):
        self._connected = False
        log.info("MQTT déconnecté (rc=%s)", rc)


mqtt_client = MQTTClient()
