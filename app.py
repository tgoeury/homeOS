"""
HomeOS — app.py
Point d'entrée principal Dash.
Lancement : python app.py
Accès     : http://<ip-machine>:<DASH_PORT>
"""

import logging
import threading
from dash import Dash, html
from flask import request as flask_request

import config as CFG
from config import DASH_HOST, DASH_PORT, DASH_DEBUG, HOME_NAME

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

app = Dash(
    __name__,
    title=f"HomeOS v{CFG.APP_VERSION} — {HOME_NAME}",
    update_title=None,
    suppress_callback_exceptions=True,
)

from modules.dashboard_layout import build_layout
app.layout = build_layout()

import modules.callbacks  # noqa: F401 — enregistre les @callback

server = app.server  # WSGI entry point pour gunicorn (app:server)


# ── Pré-fetch au démarrage ─────────────────────────────────────────────────────
# Lance un thread daemon qui pré-chauffe les caches météo et réseau dès le
# démarrage du serveur, avant que le premier navigateur se connecte.
# La météo (~1 s) est récupérée en premier ; le scan réseau (nmap, ~30-60 s)
# tourne ensuite sans bloquer le serveur.

def _prefetch() -> None:
    """Pré-chauffe les caches MQTT, météo et réseau en arrière-plan au démarrage."""
    log = logging.getLogger("startup")

    log.info("Démarrage MQTT…")
    try:
        from modules.mqtt_client import mqtt_client
        from modules.sensor_store import sensor_store
        mqtt_client.register(sensor_store.update)
        mqtt_client.start()
        log.info("MQTT démarré (connexion en arrière-plan)")
    except Exception as exc:
        log.warning("MQTT démarrage échoué : %s", exc)

    log.info("Pré-fetch météo…")
    try:
        from modules.weather_service import weather_service
        weather_service.get()
        log.info("Pré-fetch météo OK")
    except Exception as exc:
        log.warning("Pré-fetch météo échoué : %s", exc)

    log.info("Scan réseau (nmap)…")
    try:
        from modules.network_scanner import get_local_devices
        get_local_devices()
        log.info("Scan réseau OK")
    except Exception as exc:
        log.warning("Scan réseau échoué : %s", exc)


threading.Thread(target=_prefetch, daemon=True, name="prefetch").start()


# ── Webhook entrant Synology Chat ──────────────────────────────────────────────
# Synology Chat (bot sortant) envoie un POST ici quand un utilisateur écrit au bot.
# Configurer l'URL dans Synology Chat : Intégrations → Bots → URL sortante
# → http://<IP_HOMEOS>:8050/webhook/chat

@app.server.route('/webhook/chat', methods=['POST'])
def receive_chat_webhook():
    token = flask_request.form.get('token', '')
    if token != CFG.SYNOLOGY_CHAT_TOKEN:
        logging.warning("[webhook] Token invalide reçu")
        return 'Unauthorized', 401
    text     = flask_request.form.get('text', '').strip()
    username = flask_request.form.get('username', 'Synology')
    if text:
        from modules import chatbot_engine
        chatbot_engine.add_incoming_message(text, username)
        logging.info("[webhook] Message reçu de %s : %r", username, text)
    return 'OK', 200

from dash import Output, Input, State

app.clientside_callback(
    """
    function(n, plex_progress) {
        function fmt(s) {
            s = Math.floor(s);
            return Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2);
        }
        var fill = {height: '100%', background: '#00e5ff', transition: 'width .3s ease'};
        var audio = document.getElementById('mu-audio');
        var prog, pos, dur, btn;
        var hasAudio = audio && !isNaN(audio.duration) && audio.duration > 0;
        if (hasAudio) {
            var pct = audio.currentTime / audio.duration * 100;
            btn  = audio.paused ? '▶' : '⏸';
            prog = Object.assign({}, fill, {width: pct + '%'});
            pos  = fmt(audio.currentTime);
            dur  = fmt(audio.duration);
        } else if (plex_progress && plex_progress.pct !== undefined) {
            btn  = plex_progress.state === 'playing' ? '⏸' : '▶';
            prog = Object.assign({}, fill, {width: plex_progress.pct + '%'});
            pos  = plex_progress.pos || '0:00';
            dur  = plex_progress.dur || '0:00';
        } else {
            prog = Object.assign({}, fill, {width: '0%'});
            pos = '0:00'; dur = '0:00'; btn = '▶';
        }
        /* Retourne les 4 valeurs pour l'onglet Musique ET les 4 miroirs pour l'Accueil */
        return [prog, pos, dur, btn, prog, pos, dur, btn];
    }
    """,
    Output("mu-prog-fill",      "style"),
    Output("mu-pos",            "children"),
    Output("mu-dur",            "children"),
    Output("btn-play",          "children"),
    Output("home-mu-prog-fill", "style"),
    Output("home-mu-pos",       "children"),
    Output("home-mu-dur",       "children"),
    Output("home-btn-play",     "children"),
    Input("interval-clock",    "n_intervals"),
    State("mu-plex-progress",  "data"),
)

app.clientside_callback(
    """
    function(n, n_home) {
        var audio = document.getElementById('mu-audio');
        if (audio && audio.src && audio.src !== window.location.href) {
            if (audio.paused) { audio.play(); }
            else              { audio.pause(); }
        }
        return '';
    }
    """,
    Output("mu-audio-ctrl",  "children"),
    Input("btn-play",        "n_clicks"),
    Input("home-btn-play",   "n_clicks"),
    prevent_initial_call=True,
)

app.clientside_callback(
    """
    function(expired_ids) {
        var a = document.getElementById('min-alarm');
        if (!a) return '';
        if (expired_ids && expired_ids.length > 0) {
            a.play().catch(function() {});
        } else {
            a.pause();
            a.currentTime = 0;
        }
        return '';
    }
    """,
    Output("min-alarm-ctrl", "children"),
    Input("min-expired-store", "data"),
)

if __name__ == "__main__":
    logging.info("HomeOS démarrage — http://%s:%s", DASH_HOST, DASH_PORT)
    app.run(
        host=DASH_HOST,
        port=DASH_PORT,
        debug=DASH_DEBUG,
    )
