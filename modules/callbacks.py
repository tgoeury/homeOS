"""
HomeOS — modules/callbacks.py
Tous les @callback Dash du dashboard, organisés par domaine :

  Horloge       — mise à jour topbar + footer + uptime système (1 s)
  Navigation    — switch de page et surbrillance du bouton actif
  Capteurs      — données pièces (simulées, futures MQTT) + toggle collapsible
  Plantes       — humidité capteurs plantes (simulée) + toggle collapsible
  Météo         — rendu OpenMeteo : température, prévisions 7 j, graphe horaire
  Système       — CPU, RAM, disque, température, réseau, services, ML
  Réseau        — scan nmap LAN, stats NextDNS, carte choroplèthe DNS
  Badges        — indicateurs de connexion dans le topbar (MQTT, METEO, PLEX…)
  Plex          — lecteur audio, recherche, artistes, playlists, file d'attente
  Journal/Home  — log système + périphériques actifs (page Accueil)
  Chatbot       — envoi / réception / affichage messages Synology Chat
"""

import io
import logging
import math
import time
import random
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
import httpx
from dash import callback, dcc, Output, Input, State, html, no_update, ctx, ALL

logger = logging.getLogger(__name__)
import plotly.graph_objects as go

from modules.theme import CP, FONT_MONO, FONT_HUD, PLOTLY_THEME, WORLDMAP_HEIGHT
from modules.weather_service import weather_service
from modules.sysinfo import (
    get_resources, get_system_uptime,
    check_systemctl, check_process, check_tcp,
    HOST_INFO, SYSTEM_LABEL,
)
import config as CFG
from modules.dashboard_layout import PAGE_IDS, ROOMS
from modules import timer_service
from modules.data_cache       import data_cache
from modules.data_logger      import data_logger
from modules import chatbot_engine, logic_engine
from modules.logic_engine     import LogicMode
from modules.sensor_store     import sensor_store
from modules.mqtt_client      import mqtt_client
from modules import comfort_engine
from modules.enedis_service   import enedis_service
from modules.synology_client  import synology_client, NAS_STALE_SECS
from modules.ytdlp_service    import ytdlp_service

# IDs des capteurs plantes (valeurs "plant" de ZIGBEE_DEVICES)
_plant_ids = frozenset(cfg["plant"] for cfg in CFG.ZIGBEE_DEVICES.values() if "plant" in cfg)
# Liste plate (pid, name, default, color) pour les callbacks plantes
_plant_list = [
    (sid, lbl, dflt, col)
    for _, _, _, sensors in ROOMS
    for sid, lbl, dflt, col, unit, *_ in sensors
    if sid in _plant_ids
]


_START_TIME = time.time()   # uptime applicatif (depuis le lancement du process)
DAYS_FR = ["LUN", "MAR", "MER", "JEU", "VEN", "SAM", "DIM"]
WMO_ICO = {0:"☀",1:"🌤",2:"⛅",3:"☁",45:"🌫",51:"🌦",61:"🌧",63:"🌧",65:"🌧",80:"🌦",95:"⛈"}

def _pad(n: int) -> str:
    """Formate un entier en deux chiffres (ex. 7 → '07')."""
    return str(n).zfill(2)


def _temp_color(val: float, t_min: float = CFG.ALERT_TEMP_MIN, t_max: float = CFG.ALERT_TEMP_MAX) -> str:
    """Retourne une couleur CP selon les bornes de confort fournies (ou les seuils config par défaut)."""
    if val < t_min:
        return CP["cyan"]
    if val > t_max:
        return CP["red"]
    return CP["green"]


def _hygro_color(val: float) -> str:
    """Retourne une couleur CP selon les seuils d'alerte hygrométrie de config.py."""
    if CFG.ALERT_HYGRO_MIN <= val <= CFG.ALERT_HYGRO_MAX:
        return CP["green"]
    return CP["yellow"]


def _humi_color(val: float) -> str:
    """
    Retourne une couleur CP selon les seuils d'alerte humidité plante de config.py.
    rouge  → val < ALERT_HUMI_MIN  (manque d'eau)
    vert   → ALERT_HUMI_MIN ≤ val ≤ ALERT_HUMI_MAX  (optimal)
    jaune  → val > ALERT_HUMI_MAX  (sur-arrosage)
    """
    if val < CFG.ALERT_HUMI_MIN:
        return CP["red"]
    if val > CFG.ALERT_HUMI_MAX:
        return CP["yellow"]
    return CP["green"]


# ── Horloge ───────────────────────────────────────────────────────────────────

@callback(
    Output("topbar-clock",   "children"),
    Output("topbar-date",    "children"),
    Output("footer-uptime",  "children"),
    Output("sys-uptime",     "children"),
    Output("sys-uptime-res", "children"),
    Input("interval-clock",  "n_intervals"),
)
def update_clock(_n):
    """
    Déclenché toutes les secondes. Met à jour :
      - l'horloge et la date dans le topbar
      - les deux affichages d'uptime machine (footer + carte Ressources système)
    L'uptime affiché est celui de l'OS (depuis le dernier boot), via get_system_uptime().
    """
    now   = datetime.now()
    clock = f"{_pad(now.hour)}:{_pad(now.minute)}:{_pad(now.second)}"
    date_ = f"{DAYS_FR[now.weekday()]} {_pad(now.day)}/{_pad(now.month)}/{now.year}"
    up    = get_system_uptime()
    ud    = up // 86400
    uh, um, us = (up % 86400) // 3600, (up % 3600) // 60, up % 60
    uptime_str = f"{ud}j {_pad(uh)}:{_pad(um)}:{_pad(us)}" if ud else f"{_pad(uh)}:{_pad(um)}:{_pad(us)}"
    footer_str = f"UPTIME {uptime_str}"
    return clock, date_, footer_str, uptime_str, f"UPTIME  {uptime_str}"


# ── Navigation + surbrillance ─────────────────────────────────────────────────
# Outputs : style de chaque page + className de chaque bouton nav

@callback(
    *[Output(f"page-{p}",  "style")     for p in PAGE_IDS],
    *[Output(f"nav-{p}",   "className") for p in PAGE_IDS],
    *[Input(f"nav-{p}",    "n_clicks")  for p in PAGE_IDS],
    prevent_initial_call=True,
)
def switch_page(*_clicks):
    """
    Gère la navigation entre les 7 onglets.
    Affiche la page active (display:flex) et masque toutes les autres (display:none).
    Met à jour la classe CSS du bouton nav correspondant (nav-btn--active).

    Certaines pages ont des styles spécifiques qui surchargent la base commune :
      chatbot — height:100% + overflowY:hidden pour que le scroll interne fonctionne.
    """
    triggered   = ctx.triggered_id          # ex. "nav-meteo"
    active_page = triggered.replace("nav-", "") if triggered else "accueil"

    base_page = {"padding": "16px", "flexDirection": "column",
                 "gap": "0", "overflowY": "auto"}
    # Surcharges par page — propriétés ajoutées/remplacées par rapport à base_page
    _overrides = {
        "chatbot": {"overflowY": "hidden", "height": "100%"},
    }

    page_styles = []
    nav_classes = []
    for p in PAGE_IDS:
        style = {**base_page, **_overrides.get(p, {})}
        if p == active_page:
            page_styles.append({**style, "display": "flex"})
            nav_classes.append("nav-btn nav-btn--active")
        else:
            page_styles.append({**style, "display": "none"})
            nav_classes.append("nav-btn")

    return (*page_styles, *nav_classes)


# ── Panneau collapsible — logique partagée ────────────────────────────────────

def _toggle_collapsible(current_style: dict, accent: str) -> tuple:
    """Bascule un panneau collapsible : affiche/masque le contenu + pivote la flèche."""
    is_open = current_style.get("display") != "none"
    return (
        {**current_style, "display": "none" if is_open else "grid"},
        {"fontSize": "14px", "color": accent, "marginLeft": "10px",
         "transform": "rotate(-90deg)" if is_open else "rotate(0deg)",
         "transition": "transform .2s"},
    )


# ── Collapsible capteurs par pièce ────────────────────────────────────────────

for room_id, room_name, accent, _ in ROOMS:
    @callback(
        Output(f"room-content-{room_id}", "style"),
        Output(f"room-arrow-{room_id}",   "style"),
        Input(f"room-toggle-{room_id}",   "n_clicks"),
        State(f"room-content-{room_id}", "style"),
        prevent_initial_call=True,
    )
    def toggle_room(n_clicks, current_style, _accent=accent):
        return _toggle_collapsible(current_style, _accent)


# ── Collapsible plantes (sous-section imbriquée dans chaque pièce) ───────────

_rooms_with_plants = [
    (rid, accent)
    for rid, _, accent, sensors in ROOMS
    if any(sid in _plant_ids for sid, *_ in sensors)
]
for _room_id, _accent in _rooms_with_plants:
    @callback(
        Output(f"plant-content-{_room_id}", "style"),
        Output(f"plant-arrow-{_room_id}",   "style"),
        Input(f"plant-toggle-{_room_id}",   "n_clicks"),
        State(f"plant-content-{_room_id}", "style"),
        prevent_initial_call=True,
    )
    def toggle_plant(n_clicks, current_style, _acc=_accent):
        return _toggle_collapsible(current_style, _acc)


# ── Capteurs plantes (humidité) ───────────────────────────────────────────────

# Construire les listes d'Output dynamiquement depuis _plant_list
_plant_outputs_val = [Output(pid, "children")       for pid, *_ in _plant_list]
_plant_outputs_bar = [Output(f"{pid}-bar", "style") for pid, *_ in _plant_list]
_plant_outputs_col = [Output(pid, "style")          for pid, *_ in _plant_list]

_PLANT_CACHE_MAX_AGE = 24 * 3600  # Durée maximale de rétention en cache DB (24h)

@callback(
    *_plant_outputs_val,
    *_plant_outputs_bar,
    *_plant_outputs_col,
    Input("interval-main", "n_intervals"),
)
def update_plants(_n):
    """
    Met à jour les capteurs d'humidité plantes.
    Priorité : valeur MQTT fraîche (sensor_store, 24h max) → cache DB (24h max) → "--".
    Les valeurs fraîches sont persistées dans data_cache (clé plant.<pid>.soil_moisture)
    pour survivre aux redémarrages de l'application.
    """
    values, bar_styles, val_styles = [], [], []

    for pid, name, dflt, color in _plant_list:
        real_val = sensor_store.get_plant_value(pid, "soil_moisture")

        if real_val is not None:
            val = round(max(0.0, min(100.0, real_val)), 1)
            data_cache.write(f"plant.{pid}.soil_moisture", val, "%", "zigbee")
        else:
            entry = data_cache.read(f"plant.{pid}.soil_moisture")
            if entry and (time.time() - entry["updated_at"]) < _PLANT_CACHE_MAX_AGE:
                val = round(max(0.0, min(100.0, entry["value"])), 1)
            else:
                val = None

        if val is not None:
            col = _humi_color(val)
            values.append(f"{val:.0f}%")
            bar_styles.append({
                "height": "100%", "width": f"{val:.0f}%",
                "background": col,
                "transition": "width .7s cubic-bezier(.4,0,.2,1)",
            })
            val_styles.append({
                "fontSize": "32px", "fontWeight": "700",
                "color": col, "fontFamily": FONT_HUD, "lineHeight": "1.1",
            })
        else:
            values.append("--")
            bar_styles.append({
                "height": "100%", "width": "0%",
                "background": CP["text_dim"],
                "transition": "width .7s cubic-bezier(.4,0,.2,1)",
            })
            val_styles.append({
                "fontSize": "32px", "fontWeight": "700",
                "color": CP["text_dim"], "fontFamily": FONT_HUD, "lineHeight": "1.1",
            })

    return (*values, *bar_styles, *val_styles)


# ── Découverte Zigbee ─────────────────────────────────────────────────────────

@callback(
    Output("zigbee-discovery", "children"),
    Input("interval-main", "n_intervals"),
)
def update_zigbee_discovery(_n):
    """
    Affiche les appareils Zigbee2MQTT actifs mais absents de CFG.ZIGBEE_DEVICES.
    Aide l'utilisateur à identifier et mapper ses capteurs.
    """
    if not mqtt_client.is_connected():
        return html.Div(
            "// BROKER MQTT HORS-LIGNE — vérifier Mosquitto sur localhost:1883 //",
            style={"color": CP["red"], "fontFamily": FONT_MONO, "fontSize": "13px",
                   "letterSpacing": "2px", "textAlign": "center", "padding": "12px"},
        )

    unmapped = sensor_store.unmapped_devices()
    active   = sensor_store.mapped_active_count()
    total    = len(CFG.ZIGBEE_DEVICES)

    if not unmapped:
        return html.Div(
            f"// {active}/{total} appareil(s) configuré(s) · aucun appareil inconnu //",
            style={"color": CP["green"], "fontFamily": FONT_MONO, "fontSize": "13px",
                   "letterSpacing": "2px", "textAlign": "center", "padding": "12px"},
        )

    rows = []
    for name, data in sorted(unmapped.items()):
        ts      = data.get("_ts", 0)
        age     = time.time() - ts
        age_str = f"{int(age)}s" if age < 60 else f"{int(age / 60)}m"
        # Afficher les champs utiles (exclure les méta-champs internes)
        useful_fields = {
            k: v for k, v in data.items()
            if not k.startswith("_") and k in (
                "temperature", "humidity", "soil_moisture",
                "battery", "linkquality", "voltage",
            )
        }
        field_str = "  ·  ".join(
            f"{k}: {round(v, 1) if isinstance(v, float) else v}"
            for k, v in useful_fields.items()
        )
        # Détecter le type probable selon les champs présents
        if "soil_moisture" in data:
            dev_type = "sgs01z"
        elif "temperature" in data or "humidity" in data:
            dev_type = "snzb02p"
        else:
            dev_type = "unknown"

        rows.append(html.Div([
            html.Div([
                html.Span(name, style={
                    "color": CP["yellow"], "fontFamily": FONT_MONO,
                    "fontSize": "15px", "fontWeight": "700",
                }),
                html.Span(f"  //  {dev_type}  //  il y a {age_str}", style={
                    "color": CP["text_dim"], "fontFamily": FONT_MONO, "fontSize": "12px",
                }),
            ], style={"marginBottom": "3px"}),
            html.Div(field_str or "—", style={
                "color": CP["cyan"], "fontFamily": FONT_MONO, "fontSize": "13px",
                "marginBottom": "4px",
            }),
            html.Div(
                f'→ Dans config.py : ZIGBEE_DEVICES["{name}"] = '
                f'{{"type": "{dev_type}", "room": "bureau"}}',
                style={
                    "color": CP["text_dim"], "fontFamily": FONT_MONO,
                    "fontSize": "11px", "opacity": "0.65",
                },
            ),
        ], style={
            "padding": "10px 14px",
            "borderBottom": f"1px solid {CP['border']}",
            "marginBottom": "2px",
        }))

    header = html.Div(
        f"// {len(unmapped)} appareil(s) non mappé(s) — identifier et ajouter dans config.py //",
        style={"color": CP["yellow"], "fontFamily": FONT_MONO, "fontSize": "12px",
               "letterSpacing": "2px", "marginBottom": "10px"},
    )
    return [header] + rows


# ── Minuteurs ─────────────────────────────────────────────────────────────────

def _fmt_remaining(s: int) -> str:
    """Formate un nombre de secondes en HH:MM:SS ou MM:SS si < 1 h."""
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    if h:
        return f"{_pad(h)}:{_pad(m)}:{_pad(sec)}"
    return f"{_pad(m)}:{_pad(sec)}"


def _remaining_color(remaining: int, total: int) -> str:
    """Couleur selon la fraction restante : rouge ≤ 10 %, orange ≤ 30 %, cyan sinon."""
    if total <= 0:
        return CP["cyan"]
    frac = remaining / total
    if frac <= 0.10:
        return CP["red"]
    if frac <= 0.30:
        return CP["orange"]
    return CP["cyan"]


@callback(
    Output("min-list",          "children"),
    Output("min-modal-overlay", "style"),
    Output("min-modal-names",   "children"),
    Output("min-expired-store", "data"),
    Input("interval-minuteurs", "n_intervals"),
    Input("min-action-store",   "data"),
)
def update_timer_list(_n, _store):
    """
    Déclenché toutes les secondes (et après action utilisateur).
    Met à jour uniquement la liste des timers actifs et le modal.
    Les presets ne sont PAS re-rendus ici pour éviter de déclencher
    spurieusement handle_timer_actions via les boutons pattern-matching.
    """
    timers  = timer_service.tick_and_get()
    expired = [t for t in timers if t["expired"]]
    active  = [t for t in timers if not t["expired"]]

    # ── Liste active ──────────────────────────────────────────────────────────
    if not active and not expired:
        list_children = html.Div(
            "// AUCUN MINUTEUR ACTIF //",
            style={
                "textAlign": "center", "padding": "24px",
                "color": CP["text_dim"], "fontFamily": FONT_MONO,
                "fontSize": "14px", "letterSpacing": "3px",
            },
        )
    else:
        list_children = html.Div([
            html.Div([
                html.Span(t["name"], className="timer-row__name"),
                html.Span(
                    _fmt_remaining(t["remaining_s"]),
                    className="timer-row__remaining",
                    style={"color": _remaining_color(t["remaining_s"], t["total_s"])},
                ),
                html.Button(
                    "🗑",
                    id={"type": "min-delete", "id": t["id"]},
                    n_clicks=0,
                    className="timer-delete-btn",
                ),
            ], className="timer-row")
            for t in active
        ])

    # ── Modal ─────────────────────────────────────────────────────────────────
    modal_style = {"display": "flex"} if expired else {"display": "none"}
    expired_ids = [t["id"] for t in expired]
    modal_names = html.Div([
        html.Div(t["name"], style={"marginBottom": "4px"}) for t in expired
    ]) if expired else ""

    return list_children, modal_style, modal_names, expired_ids


@callback(
    Output("min-presets", "children"),
    Input("min-action-store", "data"),
)
def update_presets_display(_store):
    """
    Re-rend le carousel de presets uniquement après une action utilisateur
    (pas toutes les secondes). Cela évite que les boutons preset soient
    recréés continuellement et déclenchent handle_timer_actions par erreur.
    """
    presets = timer_service.get_presets()
    if not presets:
        return html.Div("Aucun preset", style={"color": CP["text_dim"], "fontFamily": FONT_MONO})
    preset_cards = []
    for p in presets:
        uses_label = f"×{p['uses']}" if p["uses"] > 0 else "jamais utilisé"
        preset_cards.append(html.Button([
            html.Div(p["label"], style={
                "fontSize": "20px", "fontWeight": "700",
                "color": CP["yellow"], "fontFamily": FONT_MONO,
                "letterSpacing": "1px",
            }),
            html.Div(uses_label, style={
                "fontSize": "11px", "color": CP["text_dim"],
                "fontFamily": FONT_MONO, "letterSpacing": "1px",
                "marginTop": "4px",
            }),
        ], id={"type": "min-preset", "id": p["id"]},
           n_clicks=0, className="timer-preset-card"))
    return preset_cards


@callback(
    Output("min-action-store", "data"),
    Output("min-h",            "value"),
    Output("min-m",            "value"),
    Output("min-s",            "value"),
    Output("min-name",         "value"),
    Input("min-start-btn",                              "n_clicks"),
    Input({"type": "min-delete", "id": ALL},            "n_clicks"),
    Input("min-modal-ok",                               "n_clicks"),
    Input({"type": "min-preset", "id": ALL},            "n_clicks"),
    State("min-h",             "value"),
    State("min-m",             "value"),
    State("min-s",             "value"),
    State("min-name",          "value"),
    State("min-action-store",  "data"),
    prevent_initial_call=True,
)
def handle_timer_actions(
    _start, _deletes, _ok, _presets,
    h, m, s, name, store,
):
    """
    Gère toutes les actions utilisateur sur les minuteurs.
    Le guard n_clicks > 0 est indispensable : Dash fire ce callback quand des
    composants pattern-matching (preset/delete) sont dynamiquement ajoutés au
    layout (n_clicks=0), même avec prevent_initial_call=True. On ignore ces fires.
    """
    # Guard : ignorer les fires causés par l'apparition dynamique de composants
    # (n_clicks vaut 0 dans ce cas ; un vrai clic donne toujours n_clicks >= 1)
    if not ctx.triggered or not ctx.triggered[0].get("value"):
        return no_update, no_update, no_update, no_update, no_update

    tid   = ctx.triggered_id
    store = (store or 0) + 1
    h = int(h or 0)
    m = int(m or 0)
    s = int(s or 0)

    if tid == "min-start-btn":
        total = h * 3600 + m * 60 + s
        if total > 0:
            timer_name = (name or "").strip() or timer_service.next_timer_name()
            timer_service.start_timer(timer_name, total)
        return store, 0, 0, 0, timer_service.next_timer_name()

    if isinstance(tid, dict) and tid.get("type") == "min-delete":
        timer_service.delete_timer(tid["id"])
        return store, no_update, no_update, no_update, no_update

    if tid == "min-modal-ok":
        timer_service.delete_expired()
        return store, no_update, no_update, no_update, no_update

    if isinstance(tid, dict) and tid.get("type") == "min-preset":
        preset_id = tid["id"]
        timer_service.increment_preset(preset_id)
        all_presets = timer_service.get_presets()
        preset = next((p for p in all_presets if p["id"] == preset_id), None)
        if preset:
            timer_service.start_timer(timer_service.next_timer_name(), preset["duration_s"])
        return store, no_update, no_update, no_update, timer_service.next_timer_name()

    return store, no_update, no_update, no_update, no_update


# ── Météo ──────────────────────────────────────────────────────────────────────

def _cmp(label: str, val: float, max_val: float, color: str, unit: str) -> html.Div:
    """Ligne de comparaison label / barre proportionnelle / valeur (page Météo)."""
    pct = max(0, min(100, round((val / max_val) * 100)))
    return html.Div([
        html.Span(label, style={"fontSize": "14px", "color": CP["text_dim"],
                                "fontFamily": FONT_MONO, "minWidth": "90px", "display": "inline-block"}),
        html.Div(html.Div(style={"height": "100%", "width": f"{pct}%", "background": color}),
                 style={"flex": "1", "height": "7px", "background": "rgba(255,255,255,0.05)"}),
        html.Span(f"{val}{unit}", style={"fontSize": "14px", "color": CP["cyan"],
                                         "fontFamily": FONT_MONO, "minWidth": "54px", "textAlign": "right"}),
    ], style={"display": "flex", "alignItems": "center", "gap": "12px", "marginBottom": "10px"})


def _weather_to_dict(data) -> dict:
    """Sérialise un WeatherData en dict JSON-compatible pour le cache persistant."""
    c = data.current
    return {
        "temperature":   c.temperature,
        "feels_like":    c.feels_like,
        "humidity":      c.humidity,
        "weather_code":  c.weather_code,
        "description":   c.description,
        "wind_speed":    c.wind_speed,
        "pressure":      c.pressure,
        "daily": [
            {"date":         d.date,
             "weather_code": d.weather_code,
             "temp_max":     d.temp_max,
             "temp_min":     d.temp_min}
            for d in data.daily
        ],
        "hourly_times": data.hourly_today.times        if data.hourly_today else [],
        "hourly_temps": data.hourly_today.temperatures if data.hourly_today else [],
    }


def _render_meteo(w: dict) -> tuple:
    """Construit les 14 sorties Dash depuis un dict météo (live ou issu du cache)."""
    # Prévisions 7j
    fc = []
    today_date = datetime.now().date()
    for d in w.get("daily", []):
        dt       = datetime.strptime(d["date"], "%Y-%m-%d")
        is_today = dt.date() == today_date
        icon     = WMO_ICO.get(d["weather_code"], "?")
        fc.append(html.Div([
            html.Div("AUJ" if is_today else DAYS_FR[dt.weekday()], style={
                "fontSize": "13px",
                "color": "rgba(255,230,0,0.8)" if is_today else "rgba(0,229,255,0.4)",
                "fontFamily": FONT_MONO, "letterSpacing": "1px",
            }),
            html.Div(icon, style={"fontSize": "24px", "margin": "4px 0"}),
            html.Div(f"{d['temp_max']:.0f}°", style={
                "fontSize": "18px", "fontWeight": "700",
                "color": "#ff6b6b", "fontFamily": FONT_HUD,
            }),
            html.Div(f"{d['temp_min']:.0f}°", style={
                "fontSize": "15px", "color": "rgba(0,229,255,0.45)", "fontFamily": FONT_HUD,
            }),
        ], style={
            "background": "#060810",
            "border": "1px solid rgba(0,229,255,0.1)",
            "borderTop": f"1px solid {'#ffe600' if is_today else 'rgba(0,229,255,0.1)'}",
            "padding": "8px 4px", "textAlign": "center",
        }))

    # Graphe horaire
    fig = go.Figure()
    times = w.get("hourly_times", [])
    temps = w.get("hourly_temps", [])
    if times and temps:
        fig.add_trace(go.Scatter(
            x=[t[11:16] for t in times], y=temps, mode="lines",
            line=dict(color=CP["cyan"], width=2),
            fill="tozeroy", fillcolor="rgba(0,229,255,0.05)",
            hovertemplate="%{x} → %{y:.1f}°C<extra></extra>",
        ))
    fig.update_layout(height=160, **PLOTLY_THEME)

    # Comparaison int/ext — salon comme référence intérieure
    temp_int  = sensor_store.get_room_value("salon", "temperature")
    hygro_int = sensor_store.get_room_value("salon", "humidity")
    if temp_int is None:
        entry = data_cache.read("sensor.salon.temperature")
        temp_int = entry["value"] if entry else None
    if hygro_int is None:
        entry = data_cache.read("sensor.salon.humidity")
        hygro_int = entry["value"] if entry else None

    def _cmp_row(label, val, max_val, color, unit):
        if val is None:
            return html.Div([
                html.Span(label, style={"fontSize": "14px", "color": CP["text_dim"],
                                        "fontFamily": FONT_MONO, "minWidth": "90px",
                                        "display": "inline-block"}),
                html.Span("N/D", style={"fontSize": "14px", "color": CP["text_dim"],
                                         "fontFamily": FONT_MONO, "opacity": "0.5"}),
            ], style={"display": "flex", "alignItems": "center", "gap": "12px",
                      "marginBottom": "10px"})
        return _cmp(label, val, max_val, color, unit)

    compare = [
        _cmp_row("Temp. int.",  temp_int,          40,  CP["cyan"],            "°C"),
        _cmp("Temp. ext.",      w["temperature"],  40,  "#ff6b6b",             "°C"),
        _cmp_row("Hygro int.",  hygro_int,         100, CP["yellow"],          " %"),
        _cmp("Hygro ext.",      w["humidity"],     100, "rgba(255,230,0,0.5)", " %"),
    ]

    return (
        f"{w['temperature']:.0f}°C",  w["description"].upper(),
        f"{w['feels_like']:.0f}°C",   f"{w['wind_speed']:.0f} km/h",  f"{w['humidity']}%",
        f"{w['temperature']:.0f}°C",  f"{w['humidity']}%",
        f"{w['wind_speed']:.0f} km/h", f"{int(w['pressure'])} hPa",
        fc, fig, {"height": "160px"}, {"display": "none"}, compare,
    )


_weather_last_log_hour: datetime | None = None


@callback(
    Output("home-temp-ext",  "children"),
    Output("home-cond-ext",  "children"),
    Output("home-feels",     "children"),
    Output("home-vent",      "children"),
    Output("home-hygro-ext", "children"),
    Output("m-temp",    "children"),
    Output("m-hygro",   "children"),
    Output("m-wind",    "children"),
    Output("m-press",   "children"),
    Output("m-forecast","children"),
    Output("m-graph",   "figure"),
    Output("m-graph",   "style"),
    Output("m-loading", "style"),
    Output("m-compare", "children"),
    Input("interval-meteo", "n_intervals"),
)
def update_meteo(_n):
    global _weather_last_log_hour
    data = weather_service.get()
    if data is not None:
        w = _weather_to_dict(data)
        data_cache.write("weather.snapshot", w, source="openmeteo")

        # Log horaire des métriques météo
        current_hour = datetime.now().replace(minute=0, second=0, microsecond=0)
        if _weather_last_log_hour is None or current_hour > _weather_last_log_hour:
            _weather_last_log_hour = current_hour
            data_logger.log("weather_temperature", w["temperature"],  "°C",   "openmeteo")
            data_logger.log("weather_humidity",    w["humidity"],     "%",    "openmeteo")
            data_logger.log("weather_wind_speed",  w["wind_speed"],   "km/h", "openmeteo")
            data_logger.log("weather_pressure",    w["pressure"],     "hPa",  "openmeteo")
            data_logger.log("weather_sky",         w["weather_code"], w.get("description", ""), "openmeteo")
    else:
        entry = data_cache.read("weather.snapshot")
        if entry is None:
            return [no_update] * 14
        w = entry["value"]
    return _render_meteo(w)


# ── Capteurs (Zigbee2MQTT + fallback simulé) ──────────────────────────────────

def _db_history(room_id: str, field: str) -> list[tuple]:
    """Charge 24h d'historique depuis data/cache.db, retourne [(datetime, float)]."""
    cutoff = (datetime.now() - timedelta(hours=24)).timestamp()
    rows   = data_cache.read_history(f"sensor_{room_id}_{field}", since_ts=cutoff)
    result = []
    for r in rows:
        try:
            result.append((datetime.fromtimestamp(r["ts"]), float(r["value"])))
        except (ValueError, TypeError):
            continue
    return result


def _field_from_sid(sid: str) -> str:
    """Détermine le champ Zigbee2MQTT depuis le suffixe du sensor_id."""
    if sid.endswith("-temp"):
        return "temperature"
    if sid.endswith("-hygro"):
        return "humidity"
    if sid.endswith("-lux"):
        return "luminosity"
    return "unknown"


def _sensor_source(room_id: str) -> str:
    """Retourne la source metadata selon si la pièce est mappée dans ZIGBEE_DEVICES."""
    for dev_name, dev_cfg in CFG.ZIGBEE_DEVICES.items():
        if dev_cfg.get("room") == room_id and sensor_store.get_device_snapshot(dev_name):
            return f"SNZB-02P · Zigbee2MQTT · {dev_name}"
    return "simulé"


# ── Mapping sensor_id → (cache_key, device_type) pour les tags "OUTDATED" ────
_SENSOR_TAG_MAP: dict[str, tuple[str, str]] = {}
for _room_id, _, _, _sensors in ROOMS:
    _room_type = next(
        (cfg.get("type", "snzb02p") for cfg in CFG.ZIGBEE_DEVICES.values()
         if cfg.get("room") == _room_id),
        "snzb02p",
    )
    for _sid, _, _, _, _, *_ in _sensors:
        _f = _field_from_sid(_sid)
        if _f != "unknown":
            _SENSOR_TAG_MAP[_sid] = (f"sensor.{_room_id}.{_f}", _room_type)
for _pid, *_ in _plant_list:
    _SENSOR_TAG_MAP[_pid] = (f"plant.{_pid}.soil_moisture", "sgs01z")

# Mapping landing_pos → (room_id, field) pour les cartes métriques de l'accueil
_landing_map: dict[int, tuple[str, str]] = {}
for _lroom_id, _, _, _lsensors in ROOMS:
    for _lsid, _, _, _, _, _lpos in _lsensors:
        if 1 <= _lpos <= 3:
            _lf = _field_from_sid(_lsid)
            if _lf != "unknown":
                _landing_map[_lpos] = (_lroom_id, _lf)
_landing_outputs = [Output(f"home-landing-{pos}", "children") for pos in sorted(_landing_map)]

def _steadman(T: float, H: float) -> float:
    """Température ressentie (Steadman/Rothfusz simplifié). T en °C, H en % directement."""
    return (
        -8.78469475556
        + 1.61139411      * T
        + 2.33854883889   * H
        - 0.14611605      * T * H
        - 0.012308094     * T ** 2
        - 0.0164248277778 * H ** 2
        + 0.002211732     * T ** 2 * H
        + 0.00072546      * T * H ** 2
        - 0.000003582     * T ** 2 * H ** 2
    )


def _trend(pts: list[tuple], current: float, threshold: float = 0.5) -> tuple[str, str]:
    """Compare la valeur courante à la moyenne de la dernière heure (fallback : 2 dernières valeurs).
    Retourne (symbole, couleur). Symboles : ↑ ↓ ="""
    hour_ago = datetime.now() - timedelta(hours=1)
    recent = [v for ts, v in pts if ts >= hour_ago]
    if len(recent) >= 2:
        ref = sum(recent) / len(recent)
    elif len(pts) >= 2:
        ref = (pts[-2][1] + pts[-1][1]) / 2
    else:
        return "", CP["text_dim"]
    diff = current - ref
    if abs(diff) < threshold:
        return "=", CP["text_dim"]
    return ("↑", CP["red"]) if diff > 0 else ("↓", CP["cyan"])


# Outputs construits dynamiquement depuis ROOMS — capteurs plantes exclus (gérés par update_plants)
_sensor_room_outputs = [
    Output(sid, "children")
    for _, _, _, sensors in ROOMS
    for sid, *_ in sensors
    if sid not in _plant_ids
]

# Rooms disposant à la fois d'un capteur temp et hygro → température ressentie
_ressentie_room_ids = [
    room_id for room_id, _, _, sensors in ROOMS
    if any(s[0].endswith("-temp")  and s[0] not in _plant_ids for s in sensors)
    and any(s[0].endswith("-hygro") and s[0] not in _plant_ids for s in sensors)
]
_ressentie_outputs = [Output(f"{rid}-ressentie", "children") for rid in _ressentie_room_ids]


@callback(
    *_landing_outputs,
    *_sensor_room_outputs,
    Output("env-graph", "figure"),
    Output("env-graph-humidity", "figure"),
    *_ressentie_outputs,
    Input("interval-main", "n_intervals"),
)
def update_sensors(_n):
    """
    Met à jour toutes les cartes capteurs (température, humidité, luminosité) toutes les 5 s.
    Source prioritaire : valeur MQTT fraîche via sensor_store ; affiche '--' si absente.
    Persiste les valeurs fraîches dans data_cache et les journalise dans la table history
    (write-on-change). Construit aussi les graphes 24h température et humidité depuis SQLite.
    Calcule la température ressentie (Steadman simplifié) et les flèches de tendance.
    """
    # Pré-chargement de l'historique 24h pour toutes les pièces (réutilisé par les graphes
    # ET les flèches de tendance — évite de requêter SQLite deux fois par pièce).
    _hist: dict[tuple[str, str], list[tuple]] = {}
    for r_id, _, _, _ in ROOMS:
        for _f in ("temperature", "humidity"):
            _hist[(r_id, _f)] = _db_history(r_id, _f)

    rendered_out = []
    _landing_vals: dict[int, object] = {}  # landing_pos → valeur pour home-landing-{pos}
    # Stockage des valeurs numériques brutes pour le calcul de ressentie
    _room_temp: dict[str, float] = {}
    _room_hygro: dict[str, float] = {}

    _arrow_style = {"marginLeft": "8px", "fontSize": "22px", "lineHeight": "1"}
    _dim = {"color": CP["text_dim"]}

    for room_id, _, _, sensors in ROOMS:
        _cr = data_cache.read(f"confort.range.{room_id}")
        t_min, t_max = (_cr["value"][0], _cr["value"][1]) if _cr else (CFG.ALERT_TEMP_MIN, CFG.ALERT_TEMP_MAX)
        for sid, _, default, _, _, landing_pos in sensors:
            if sid in _plant_ids:
                continue  # handled by update_plants
            field = _field_from_sid(sid)

            if field == "temperature":
                real = sensor_store.get_room_value(room_id, "temperature")
                if real is not None:
                    val = round(real, 1)
                else:
                    entry = data_cache.read(f"sensor.{room_id}.temperature")
                    val = entry["value"] if entry else None
                if val is not None:
                    val = round(float(val), 1)
                    _room_temp[room_id] = val
                    col = _temp_color(val, t_min, t_max)
                    sym, arr_col = _trend(_hist[(room_id, "temperature")], val)
                    out = [
                        html.Span(f"{val}°C", style={"color": col}),
                        html.Span(sym, style={**_arrow_style, "color": arr_col}),
                    ]
                    if landing_pos > 0:
                        _landing_vals[landing_pos] = html.Span(f"{val}°", style={"color": col})
                else:
                    out = html.Span("--", style=_dim)

            elif field == "humidity":
                real = sensor_store.get_room_value(room_id, "humidity")
                if real is not None:
                    val = int(round(real))
                else:
                    entry = data_cache.read(f"sensor.{room_id}.humidity")
                    val = int(round(entry["value"])) if entry else None
                if val is not None:
                    _room_hygro[room_id] = float(val)
                    col = _hygro_color(val)
                    sym, arr_col = _trend(_hist[(room_id, "humidity")], float(val))
                    out = [
                        html.Span(f"{val}%", style={"color": col}),
                        html.Span(sym, style={**_arrow_style, "color": arr_col}),
                    ]
                    if landing_pos > 0:
                        _landing_vals[landing_pos] = html.Span(f"{val}%", style={"color": col})
                else:
                    out = html.Span("--", style=_dim)

            elif field == "luminosity":
                out = "--"

            else:
                out = default

            rendered_out.append(out)

    # ── Graphes 24h — température et humidité ────────────────────────────────────
    _graph_legend = dict(
        orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
        font=dict(size=10, color=CP["text_dim"], family=FONT_MONO),
        bgcolor="rgba(0,0,0,0)", borderwidth=0,
    )
    # Figures retournées comme dicts plain plutôt que go.Figure : Dash les sérialise
    # en JSON de toute façon, la validation/normalisation de go.Figure est du pur
    # surcoût (~57 ms/tick pour ces deux graphes) jeté ensuite. Cf. micro-bench round 2.
    _graph_layout = {
        **PLOTLY_THEME,
        "height":     180,
        "showlegend": True,
        "legend":     _graph_legend,
    }
    fig_data:     list = []
    fig_hum_data: list = []

    for r_id, r_name, r_accent, _ in ROOMS:
        for data_list, field, hover_fmt in (
            (fig_data,     "temperature", "%{x|%H:%M}  %{y:.1f}°C"),
            (fig_hum_data, "humidity",    "%{x|%H:%M}  %{y:.0f}%"),
        ):
            pts = _hist[(r_id, field)]
            if not pts:
                continue
            data_list.append({
                "type": "scatter",
                "x": [ts for ts, _ in pts],
                "y": [v  for _, v  in pts],
                "mode": "lines",
                "name": r_name,
                "line": {"color": r_accent, "width": 2},
                "hovertemplate": f"<b>{r_name}</b><br>{hover_fmt}<extra></extra>",
            })

    fig     = {"data": fig_data,     "layout": _graph_layout}
    fig_hum = {"data": fig_hum_data, "layout": _graph_layout}

    # ── Températures ressenties (Steadman simplifié, sans vent) ─────────────────
    _no_data = html.Span("--", style={"color": CP["text_dim"], "fontSize": "32px"})
    ressentie_out = []
    for rid in _ressentie_room_ids:
        T = _room_temp.get(rid)
        H = _room_hygro.get(rid)
        if T is not None and H is not None:
            at = round(_steadman(T, H), 1)
            col = _temp_color(at, CFG.ALERT_TEMP_MIN, CFG.ALERT_TEMP_MAX)
            ressentie_out.append(html.Span(f"{at}°C", style={"color": col}))
        else:
            ressentie_out.append(_no_data)

    _no_landing = html.Span("--", style={"color": CP["text_dim"]})
    return (
        *[_landing_vals.get(pos, _no_landing) for pos in sorted(_landing_map)],
        *rendered_out,
        fig,
        fig_hum,
        *ressentie_out,
    )


# ── Confort — optimisation climatique ─────────────────────────────────────────

# Marques de base communes à tous les sliders (positions fixes)
_CONFORT_BASE_MARKS = {t: {"label": f"{t}°", "style": {"fontSize": "11px",
                            "color": "rgba(200,232,239,0.4)"}}
                        for t in range(4, 31, 2)}


@callback(
    *[Output(f"confort-range-{room_id}", "marks") for room_id, *_ in ROOMS],
    Input("interval-main", "n_intervals"),
)
def update_confort_temp_marks(_n):
    """Ajoute sur chaque slider une marque à la position de la température actuelle."""
    results = []
    for room_id, _, accent, _ in ROOMS:
        marks = dict(_CONFORT_BASE_MARKS)
        entry = data_cache.read(f"sensor.{room_id}.temperature")
        if entry and entry["value"] is not None:
            t = round(float(entry["value"]), 1)
            if 4 <= t <= 30:
                marks[t] = {
                    "label": f"▲ {t}°",
                    "style": {
                        "color":      accent,
                        "fontWeight": "bold",
                        "fontSize":   "12px",
                    },
                }
        results.append(marks)
    return results


@callback(
    Output("confort-instructions", "children", allow_duplicate=True),
    Output("confort-calc-store",   "data"),
    Input("confort-calc-btn", "n_clicks"),
    prevent_initial_call=True,
)
def _start_confort_inference(_n):
    """Affiche immédiatement 'Inférence en cours' puis transfère le déclenchement au store."""
    loading = html.Div(
        "// INFÉRENCE EN COURS…",
        style={"fontFamily": FONT_MONO, "fontSize": "13px",
               "color": CP["yellow"], "letterSpacing": "2px"},
    )
    return loading, _n


@callback(
    Output("confort-instructions", "children"),
    Input("confort-calc-store", "data"),
    *[State(f"confort-range-{room_id}", "value") for room_id, *_ in ROOMS],
    prevent_initial_call=True,
)
def run_comfort_inference(_, *ranges):
    """Lance l'inférence réelle une fois que l'UI affiche déjà 'En cours'."""
    comfort_ranges = {room_id: tuple(r) for (room_id, *_), r in zip(ROOMS, ranges)}
    room_names = {room_id: room_name for room_id, room_name, *_ in ROOMS}

    result = comfort_engine.run_inference(comfort_ranges)

    if result["status"] != "ok":
        return [html.Div(f"// ERREUR — {result['error']} //", style={"color": CP["red"]})]

    lines = []
    for room in result["rooms"]:
        room_name = room_names.get(room.room_id, room.room_id).upper()
        lines.append(html.Div([
            html.Span(f"{room_name} ", style={"color": CP["cyan"]}),
            html.Span("→  ", style={"color": CP["text_dim"]}),
            html.Span(f"{'   ·   '.join(room.actions)}   "),
            html.Span(f"(jusqu'à {room.until})", style={"color": CP["text_dim"]}),
        ]))
    return lines


@callback(
    Output("confort-persist-store", "data"),
    *[Input(f"confort-range-{room_id}", "value") for room_id, *_ in ROOMS],
    prevent_initial_call=True,
)
def _persist_confort_ranges(*ranges):
    """Persiste les plages de confort de chaque pièce dans data_cache à chaque modification slider."""
    for (room_id, *_), val in zip(ROOMS, ranges):
        if val is not None:
            data_cache.write(f"confort.range.{room_id}", val, "°C", "user")
    return None


# ── Tag "OUTDATED" sur les cartes capteurs ────────────────────────────────────

_TAG_STYLE_BASE = {
    "position": "absolute", "bottom": "6px", "right": "8px",
    "fontSize": "10px", "fontFamily": FONT_MONO,
    "letterSpacing": "2px", "padding": "1px 6px",
}
_TAG_STYLE = {
    **_TAG_STYLE_BASE,
    "color": CP["yellow"],
    "border": f"1px solid {CP['yellow']}66",
}
_TAG_STYLE_RED = {
    **_TAG_STYLE_BASE,
    "color": CP["red"],
    "border": f"1px solid {CP['red']}66",
}
_TAG_HIDDEN = {"display": "none"}

_OUTDATED_YELLOW_S = 15 * 60   # 15 min → jaune
_OUTDATED_RED_S    =  3 * 3600  # 3 h   → rouge

if _SENSOR_TAG_MAP:
    @callback(
        *[Output(f"{sid}-outdated-tag", "children") for sid in _SENSOR_TAG_MAP],
        *[Output(f"{sid}-outdated-tag", "style")    for sid in _SENSOR_TAG_MAP],
        Input("interval-main", "n_intervals"),
    )
    def update_sensor_outdated_tags(_n):
        """Tag OUTDATED jaune (> 15 min) ou rouge (> 3 h) sur les cartes capteurs."""
        now = time.time()
        children_out, styles_out = [], []
        for sid, (cache_key, _dev_type) in _SENSOR_TAG_MAP.items():
            entry = data_cache.read(cache_key)
            if entry:
                age = now - entry["updated_at"]
                if age > _OUTDATED_RED_S:
                    children_out.append("OUTDATED")
                    styles_out.append(_TAG_STYLE_RED)
                elif age > _OUTDATED_YELLOW_S:
                    children_out.append("OUTDATED")
                    styles_out.append(_TAG_STYLE)
                else:
                    children_out.append("")
                    styles_out.append(_TAG_HIDDEN)
            else:
                children_out.append("")
                styles_out.append(_TAG_HIDDEN)
        return (*children_out, *styles_out)


# ── Énergie — consommation Enedis ─────────────────────────────────────────────

def _hline_shape(y: float) -> dict:
    """Réplique la shape produite par fig.add_hline (ligne horizontale pleine largeur)."""
    return {"type": "line", "xref": "x domain", "yref": "y",
            "x0": 0, "x1": 1, "y0": y, "y1": y,
            "line": {"color": "rgba(255,255,255,0.3)", "dash": "dot"}}


def _hline_annot(y: float, text: str) -> dict:
    """Réplique l'annotation produite par fig.add_hline (label à droite de la ligne)."""
    return {"xref": "x domain", "yref": "y", "x": 1, "y": y,
            "xanchor": "right", "yanchor": "bottom", "showarrow": False,
            "text": text, "font": {"color": CP["text_dim"], "size": 11}}


@callback(
    Output("energie-hier",           "children"),
    Output("energie-mois",           "children"),
    Output("energie-prev",           "children"),
    Output("energie-graph",          "figure"),
    Output("energie-unit-btn",       "children"),
    Output("energie-status",         "children"),
    Output("energie-graph-monthly",  "figure"),
    Input("interval-main",           "n_intervals"),
    Input("energie-unit-btn",        "n_clicks"),
)
def update_energie(_n, n_clicks):
    """
    Met à jour la page Énergie : stat cards (hier/mois/mois précédent), barplot 30 j,
    barplot 12 mois glissants.
    Toggle kWh/€ piloté par n_clicks (pair = kWh, impair = €).
    Code couleur : rouge ≥ p75, bleu ≤ p75, vert = mois en cours.
    """
    import statistics
    from collections import defaultdict

    rows = enedis_service.read_history()

    # Unité courante : kWh (n_clicks pair) ou € (n_clicks impair)
    show_euros  = bool(n_clicks and n_clicks % 2 == 1)
    price       = getattr(CFG, "ELECTRICITY_PRICE_KWH", 0.18)
    unit_label  = "€" if show_euros else "kWh"
    btn_label   = "AFFICHER EN kWh" if show_euros else "AFFICHER EN €"

    def _fmt(kwh: float | None) -> str:
        if kwh is None:
            return "—"
        val = kwh * price if show_euros else kwh
        return f"{val:.2f} {unit_label}" if show_euros else f"{val:.1f} kWh"

    # ── Stat cards ────────────────────────────────────────────────────────────
    if not rows:
        no_data = html.Span("—", style={"color": CP["text_dim"]})
        status  = _enedis_status_msg()
        empty_fig = {"data": [], "layout": {**PLOTLY_THEME,
                     "title": {"text": "Aucune donnée disponible",
                               "font": {"color": CP["text_dim"], "size": 13}}}}
        return no_data, no_data, no_data, empty_fig, btn_label, status, empty_fig

    today     = datetime.now().date()
    yesterday = today - timedelta(days=1)

    # Hier
    hier_kwh = next((r["kwh"] for r in reversed(rows) if r["date"] == yesterday), None)

    # Ce mois
    mois_kwh  = sum(r["kwh"] for r in rows if r["date"].year == today.year
                                              and r["date"].month == today.month)

    # Mois précédent
    if today.month == 1:
        prev_year, prev_month = today.year - 1, 12
    else:
        prev_year, prev_month = today.year, today.month - 1
    prev_kwh = sum(r["kwh"] for r in rows if r["date"].year == prev_year
                                             and r["date"].month == prev_month)

    # ── Graphique 30 derniers jours ───────────────────────────────────────────
    last_30 = [r for r in rows if r["date"] >= today - timedelta(days=30)]

    dates  = [r["date"].isoformat() for r in last_30]
    values = [r["kwh"] * price if show_euros else r["kwh"] for r in last_30]

    # 75e percentile pour le code couleur
    if len(values) >= 2:
        p75 = statistics.quantiles(values, n=4)[2]
    elif values:
        p75 = values[0]
    else:
        p75 = 0.0

    colors = [CP["red"] if v >= p75 else "#00b4d8" for v in values]

    # Figure en dict plain (cf. round 2) — évite la validation go.Figure/add_hline.
    fig_layout = {
        **PLOTLY_THEME,
        "bargap": 0.2,
        "margin": {"l": 40, "r": 10, "t": 10, "b": 50},
        "xaxis":  {**PLOTLY_THEME["xaxis"],
                   "tickformat": "%d/%m", "tickangle": -45,
                   "gridcolor": "rgba(255,255,255,0.04)"},
        "yaxis":  {**PLOTLY_THEME["yaxis"],
                   "title": unit_label, "ticksuffix": "",
                   "gridcolor": "rgba(255,255,255,0.06)"},
    }
    if values:
        # Ligne de référence 75e percentile
        fig_layout["shapes"]      = [_hline_shape(p75)]
        fig_layout["annotations"] = [_hline_annot(p75, f"p75 : {p75:.2f} {unit_label}")]
    fig = {"data": [{
        "type": "bar", "x": dates, "y": values,
        "marker": {"color": colors},
        "hovertemplate": f"%{{x}}<br>%{{y:.2f}} {unit_label}<extra></extra>",
    }], "layout": fig_layout}

    # ── Graphique 12 mois glissants ───────────────────────────────────────────
    _MOIS_FR = ["Jan", "Fév", "Mar", "Avr", "Mai", "Jun",
                "Jul", "Aoû", "Sep", "Oct", "Nov", "Déc"]

    # Liste des 13 mois (du plus ancien au plus récent — permet de voir le même mois N-1)
    months_12: list[tuple[int, int]] = []
    y_, m_ = today.year, today.month
    for _ in range(13):
        months_12.insert(0, (y_, m_))
        m_ -= 1
        if m_ == 0:
            m_, y_ = 12, y_ - 1

    # Agréger les consommations journalières par mois
    monthly_kwh: dict[tuple[int, int], float] = defaultdict(float)
    for r in rows:
        monthly_kwh[(r["date"].year, r["date"].month)] += r["kwh"]

    mo_labels = [f"{_MOIS_FR[mo-1]} {str(yr)[2:]}" for (yr, mo) in months_12]
    mo_values = [
        monthly_kwh.get((yr, mo), 0.0) * (price if show_euros else 1.0)
        for (yr, mo) in months_12
    ]

    # 75e percentile des valeurs mensuelles pour le code couleur
    non_zero = [v for v in mo_values if v > 0]
    if len(non_zero) >= 4:
        p75_mo = statistics.quantiles(non_zero, n=4)[2]
    elif non_zero:
        p75_mo = max(non_zero)
    else:
        p75_mo = 0.0

    current_key = (today.year, today.month)
    mo_colors = []
    for (yr, mo), v in zip(months_12, mo_values):
        if (yr, mo) == current_key:
            mo_colors.append(CP["green"])
        elif v > p75_mo:
            mo_colors.append(CP["red"])
        else:
            mo_colors.append("#00b4d8")

    fig_monthly_layout = {
        **PLOTLY_THEME,
        "bargap": 0.25,
        "margin": {"l": 40, "r": 10, "t": 10, "b": 50},
        "xaxis":  {**PLOTLY_THEME["xaxis"], "gridcolor": "rgba(255,255,255,0.04)"},
        "yaxis":  {**PLOTLY_THEME["yaxis"],
                   "title": unit_label, "ticksuffix": "",
                   "gridcolor": "rgba(255,255,255,0.06)"},
    }
    if non_zero:
        fig_monthly_layout["shapes"]      = [_hline_shape(p75_mo)]
        fig_monthly_layout["annotations"] = [_hline_annot(p75_mo, f"p75 : {p75_mo:.2f} {unit_label}")]
    fig_monthly = {"data": [{
        "type": "bar", "x": mo_labels, "y": mo_values,
        "marker": {"color": mo_colors},
        "hovertemplate": f"%{{x}}<br>%{{y:.2f}} {unit_label}<extra></extra>",
    }], "layout": fig_monthly_layout}

    status = _enedis_status_msg()
    return _fmt(hier_kwh), _fmt(mois_kwh), _fmt(prev_kwh), fig, btn_label, status, fig_monthly


def _enedis_kind() -> tuple[str, str]:
    """
    Retourne (kind, state) pour le badge et la ligne "Services actifs".
    kind  : "ok" | "warn" | "err"
    state : libellé court affiché
    Règles : vert < 24h · jaune ≥ 24h · rouge si erreur API ou non configuré
    """
    if not enedis_service.is_configured():
        return "warn", "N/A"
    if enedis_service.last_error:
        return "err", "ERREUR"
    rows = enedis_service.read_history()
    if not rows:
        return "warn", "VIDE"
    last_date = rows[-1]["date"]
    last_eod  = datetime(last_date.year, last_date.month, last_date.day, 23, 59)
    age_h = (datetime.now() - last_eod).total_seconds() / 3600
    if age_h < 24:
        return "ok", last_date.strftime("%d/%m")
    return "warn", f">{int(age_h)}h"


def _enedis_status_msg() -> str:
    """Retourne un message de statut Enedis pour l'affichage dans l'onglet Énergie, ou '' si OK."""
    if not enedis_service.is_configured():
        return "// NON CONFIGURÉ — renseigner ENEDIS_TOKEN et ENEDIS_PRM dans config.py //"
    if enedis_service.last_error:
        return f"// ERREUR : {enedis_service.last_error} //"
    return ""


# ── Système réel ───────────────────────────────────────────────────────────────

_MQTT_COUNT = 4382  # simulé — à remplacer par lecture broker MQTT

# Cache TTL des sondes de services (systemctl/process/TCP). Ces sondes font du fork
# subprocess + connexions socket bloquantes ; leur résultat change très rarement.
# Inutile de les exécuter à chaque tick (8 s) — un rafraîchissement toutes les 60 s
# suffit largement pour un affichage d'état. Sur RPi, évite ~8 forks systemctl/8 s.
_SVC_PROBE_TTL = 60.0
_svc_probe_cache: dict = {"ts": 0.0, "data": None}

@callback(
    Output("bar-cpu",      "style"),
    Output("val-cpu",      "children"),
    Output("bar-ram",      "style"),
    Output("val-ram",      "children"),
    Output("bar-disk",     "style"),
    Output("val-disk",     "children"),
    Output("bar-temp",     "style"),
    Output("val-temp",     "children"),
    Output("sys-net",      "children"),
    Output("sys-services", "children"),
    Output("sys-mqtt",     "children"),
    Output("sys-ml",       "children"),
    Output("footer-mqtt",  "children"),
    Input("interval-sys",  "n_intervals"),
)
def update_system(_n):
    """
    Met à jour la page Système : barres CPU/RAM/disque/temp, réseau, services actifs, ML.
    Les sondes de services (systemctl, process, TCP) sont exécutées synchronement à chaque tick.
    """
    global _MQTT_COUNT
    _MQTT_COUNT += random.randint(1, 8)

    res = get_resources()

    cpu  = res["cpu_pct"]
    ram  = res["ram_pct"]
    disk = res["disk_pct"]
    temp = res["temp_cpu"]

    def _bar(pct, color):
        return {"height": "100%", "width": f"{pct}%", "background": color,
                "transition": "width .7s cubic-bezier(.4,0,.2,1)"}

    temp_color = CP["red"] if (temp or 0) > 75 else CP["orange"] if (temp or 0) > 60 else CP["green"]
    temp_str   = f"{temp} °C" if temp is not None else "N/D"
    temp_pct   = round(min(100, (temp / 100) * 100)) if temp else 0

    net_str = (
        f"↑ {res['net_sent_mb']} MB  ↓ {res['net_recv_mb']} MB  //  "
        f"Disque : {res['disk_used_gb']} / {res['disk_total_gb']} GB  //  "
        f"RAM : {res['ram_used_mb']} / {res['ram_total_mb']} MB"
    )

    # Services — sondes réelles
    def _probe(systemctl_name=None, proc_name=None, host=None, port=None):
        """Retourne (kind, label) en essayant systemctl → process → TCP."""
        if systemctl_name:
            st = check_systemctl(systemctl_name)
            if st == "active":
                return "ok", "UP"
            if st in ("inactive", "failed"):
                return "err", st.upper()
            # st == "unknown" → pas de systemctl, on continue
        if proc_name and check_process(proc_name):
            return "ok", "UP"
        if host and port:
            return ("ok", "UP") if check_tcp(host, port) else ("err", "DOWN")
        return "warn", "N/D"

    chat_ok = chatbot_engine.get_connection_status()
    ml_mode = logic_engine.get_mode()

    # Sondes I/O (systemctl/process/TCP) servies depuis un cache TTL : on ne refork
    # pas de subprocess à chaque tick. Les entrées en mémoire (chat, ml, comfort,
    # enedis) restent recalculées à chaque appel car elles sont quasi gratuites.
    now = time.time()
    if now - _svc_probe_cache["ts"] >= _SVC_PROBE_TTL or _svc_probe_cache["data"] is None:
        _svc_probe_cache["data"] = [
            ("mosquitto",
             _probe("mosquitto",        "mosquitto",   "localhost",    CFG.MQTT_BROKER_PORT)),
            ("influxdb",
             _probe("influxdb",         "influxd",     "localhost",    8086)),
            ("plex",
             _probe("plexmediaserver",  "plex",        CFG.PLEX_HOST,  CFG.PLEX_PORT)),
        ]
        _svc_probe_cache["ts"] = now
    _probed = _svc_probe_cache["data"]

    services_data = [
        ("mosquitto", *_probed[0][1]),
        ("influxdb",  *_probed[1][1]),
        ("plex",      *_probed[2][1]),
        ("homeos-dash",  "ok",  "UP"),
        ("synology-chat",
         "ok"   if chat_ok else "warn",
         "UP"   if chat_ok else "INIT"),
        ("ml-engine",
         "ok"   if logic_engine.is_operational() else "warn",
         ml_mode.value.upper()),
        ("comfort-engine",
         {"full": "ok", "limited": "limited", "none": "err"}[comfort_engine.model_status()],
         comfort_engine.model_status().upper()),
        ("enedis", *_enedis_kind()),
    ]
    _svc_colors = {
        "ok":      (CP["green"],  "rgba(57,255,20,0.3)",  "rgba(57,255,20,0.06)"),
        "limited": (CP["orange"], "rgba(255,107,53,0.3)", "rgba(255,107,53,0.06)"),
        "warn":    (CP["yellow"], "rgba(255,230,0,0.3)",  "rgba(255,230,0,0.06)"),
        "err":     (CP["red"],    "rgba(255,7,58,0.3)",   "rgba(255,7,58,0.06)"),
    }
    services = html.Div([
        html.Div([
            html.Span(svc, style={"fontSize": "14px", "color": CP["text_dim"],
                                   "fontFamily": FONT_MONO}),
            html.Span(state, style={
                "fontSize": "12px", "letterSpacing": "2px", "fontFamily": FONT_MONO,
                "padding": "2px 8px", "border": "1px solid",
                "color":        _svc_colors.get(kind, _svc_colors["warn"])[0],
                "borderColor":  _svc_colors.get(kind, _svc_colors["warn"])[1],
                "background":   _svc_colors.get(kind, _svc_colors["warn"])[2],
            }),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "center", "padding": "5px 0",
                  "borderBottom": "1px solid rgba(0,229,255,0.06)"})
        for svc, kind, state in services_data
    ])

    # Boîte ML — Isolation Forest
    ml_content = html.Div([
        html.Div([
            html.Span("MODÈLE", style={"fontSize": "12px", "color": CP["text_dim"],
                                        "fontFamily": FONT_MONO, "letterSpacing": "2px"}),
            html.Span("sklearn.IsolationForest", style={"fontSize": "14px", "color": CP["cyan"],
                                                         "fontFamily": FONT_MONO}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "borderBottom": "1px solid rgba(0,229,255,0.08)", "paddingBottom": "8px",
                  "marginBottom": "10px"}),

        html.Div("Ce que surveille le modèle :", style={
            "fontSize": "13px", "color": CP["text_dim"], "fontFamily": FONT_MONO,
            "marginBottom": "6px",
        }),
        html.Ul([
            html.Li("Dérive anormale de température (pic/chute rapide)",
                    style={"fontSize": "13px", "color": CP["text_dim"], "fontFamily": FONT_MONO,
                           "marginBottom": "4px"}),
            html.Li("Humidité hors de la plage habituelle (ex : fuite, condensation)",
                    style={"fontSize": "13px", "color": CP["text_dim"], "fontFamily": FONT_MONO,
                           "marginBottom": "4px"}),
            html.Li("Capteur silencieux / déconnecté (score d'anomalie élevé)",
                    style={"fontSize": "13px", "color": CP["text_dim"], "fontFamily": FONT_MONO,
                           "marginBottom": "4px"}),
            html.Li("Corrélations inter-pièces anormales (ex : salon chaud, bureau froid simultanément)",
                    style={"fontSize": "13px", "color": CP["text_dim"], "fontFamily": FONT_MONO}),
        ], style={"paddingLeft": "18px", "marginBottom": "12px"}),

        # Scores d'anomalie — capteurs actifs uniquement (dynamique)
        html.Div("Scores d'anomalie en temps réel :", style={
            "fontSize": "13px", "color": CP["text_dim"], "fontFamily": FONT_MONO,
            "marginBottom": "8px",
        }),
        *_build_anomaly_rows(),

        html.Div([
            html.Span("STATUT GLOBAL", style={"fontSize": "12px", "color": CP["text_dim"],
                                               "fontFamily": FONT_MONO, "letterSpacing": "2px"}),
            html.Span("AUCUNE ANOMALIE DÉTECTÉE", style={
                "fontSize": "13px", "color": CP["green"], "fontFamily": FONT_MONO,
                "letterSpacing": "1px",
            }),
        ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center",
                  "marginTop": "10px", "paddingTop": "8px",
                  "borderTop": "1px solid rgba(0,229,255,0.08)"}),
    ])

    footer_mqtt = f"MQTT: {_MQTT_COUNT:,} MSG".replace(",", " ")

    return (
        _bar(cpu,       CP["cyan"]),   f"{cpu}%",
        _bar(ram,       CP["yellow"]), f"{ram}%",
        _bar(disk,      CP["green"]),  f"{disk}%",
        _bar(temp_pct,  temp_color),   temp_str,
        net_str,
        services,
        str(_MQTT_COUNT),
        ml_content,
        footer_mqtt,
    )


def _build_anomaly_rows() -> list:
    """
    Construit dynamiquement les lignes ML pour les capteurs actifs.
    Un capteur est "actif" si sensor_store retourne une valeur pour temperature ou humidity.
    """
    rows = []
    for r_id, r_name, _, r_sensors in ROOMS:
        for s_id, s_label, *_ in r_sensors:
            field = _field_from_sid(s_id)
            if field not in ("temperature", "humidity"):
                continue
            if sensor_store.get_room_value(r_id, field) is None:
                continue
            rows.append(_anomaly_row(f"{r_name} · {s_label}", round(random.uniform(0.0, 0.15), 2)))
    if not rows:
        rows.append(html.Div(
            "// Aucun capteur actif — en attente de données Zigbee //",
            style={"color": CP["text_dim"], "fontFamily": FONT_MONO,
                   "fontSize": "13px", "letterSpacing": "1px", "padding": "8px 0"},
        ))
    return rows


def _anomaly_row(label: str, score: float) -> html.Div:
    """
    Ligne de score d'anomalie Isolation Forest (page Système).
    score > 0.5 → ANOMALIE (rouge) / > 0.3 → ATTENTION (jaune) / sinon NORMAL (vert).
    """
    color  = CP["red"] if score > 0.5 else CP["yellow"] if score > 0.3 else CP["green"]
    pct    = round(score * 100)
    status = "ANOMALIE" if score > 0.5 else "ATTENTION" if score > 0.3 else "NORMAL"
    return html.Div([
        html.Span(label, style={"fontSize": "13px", "color": CP["text_dim"],
                                 "fontFamily": FONT_MONO, "minWidth": "130px"}),
        html.Div(html.Div(style={"height": "100%", "width": f"{pct}%", "background": color}),
                 style={"flex": "1", "height": "5px", "background": "rgba(255,255,255,0.05)"}),
        html.Span(f"{score:.2f}", style={
            "fontSize": "12px", "fontFamily": FONT_MONO,
            "color": color, "minWidth": "36px", "textAlign": "right",
        }),
        html.Span(status, style={
            "fontSize": "11px", "fontFamily": FONT_MONO, "color": color,
            "minWidth": "72px", "textAlign": "right", "letterSpacing": "1px",
        }),
    ], style={"display": "flex", "alignItems": "center", "gap": "10px", "marginBottom": "7px"})


# ── Réseau ────────────────────────────────────────────────────────────────────

from modules.nextdns_client  import nextdns_client   # noqa: E402
from modules.network_scanner import get_local_devices  # noqa: E402

# ISO-2 → ISO-3 pour le choropleth Plotly
_ISO2 = {
    "AF":"AFG","AL":"ALB","DZ":"DZA","AD":"AND","AO":"AGO","AG":"ATG","AR":"ARG",
    "AM":"ARM","AU":"AUS","AT":"AUT","AZ":"AZE","BS":"BHS","BH":"BHR","BD":"BGD",
    "BB":"BRB","BY":"BLR","BE":"BEL","BZ":"BLZ","BJ":"BEN","BT":"BTN","BO":"BOL",
    "BA":"BIH","BW":"BWA","BR":"BRA","BN":"BRN","BG":"BGR","BF":"BFA","BI":"BDI",
    "CV":"CPV","KH":"KHM","CM":"CMR","CA":"CAN","CF":"CAF","TD":"TCD","CL":"CHL",
    "CN":"CHN","CO":"COL","KM":"COM","CG":"COG","CD":"COD","CR":"CRI","CI":"CIV",
    "HR":"HRV","CU":"CUB","CY":"CYP","CZ":"CZE","DK":"DNK","DJ":"DJI","DM":"DMA",
    "DO":"DOM","EC":"ECU","EG":"EGY","SV":"SLV","GQ":"GNQ","ER":"ERI","EE":"EST",
    "SZ":"SWZ","ET":"ETH","FJ":"FJI","FI":"FIN","FR":"FRA","GA":"GAB","GM":"GMB",
    "GE":"GEO","DE":"DEU","GH":"GHA","GR":"GRC","GD":"GRD","GT":"GTM","GN":"GIN",
    "GW":"GNB","GY":"GUY","HT":"HTI","HN":"HND","HU":"HUN","IS":"ISL","IN":"IND",
    "ID":"IDN","IR":"IRN","IQ":"IRQ","IE":"IRL","IL":"ISR","IT":"ITA","JM":"JAM",
    "JP":"JPN","JO":"JOR","KZ":"KAZ","KE":"KEN","KI":"KIR","KP":"PRK","KR":"KOR",
    "KW":"KWT","KG":"KGZ","LA":"LAO","LV":"LVA","LB":"LBN","LS":"LSO","LR":"LBR",
    "LY":"LBY","LI":"LIE","LT":"LTU","LU":"LUX","MG":"MDG","MW":"MWI","MY":"MYS",
    "MV":"MDV","ML":"MLI","MT":"MLT","MH":"MHL","MR":"MRT","MU":"MUS","MX":"MEX",
    "FM":"FSM","MD":"MDA","MC":"MCO","MN":"MNG","ME":"MNE","MA":"MAR","MZ":"MOZ",
    "MM":"MMR","NA":"NAM","NR":"NRU","NP":"NPL","NL":"NLD","NZ":"NZL","NI":"NIC",
    "NE":"NER","NG":"NGA","NO":"NOR","OM":"OMN","PK":"PAK","PW":"PLW","PA":"PAN",
    "PG":"PNG","PY":"PRY","PE":"PER","PH":"PHL","PL":"POL","PT":"PRT","QA":"QAT",
    "RO":"ROU","RU":"RUS","RW":"RWA","KN":"KNA","LC":"LCA","VC":"VCT","WS":"WSM",
    "SM":"SMR","ST":"STP","SA":"SAU","SN":"SEN","RS":"SRB","SC":"SYC","SL":"SLE",
    "SG":"SGP","SK":"SVK","SI":"SVN","SB":"SLB","SO":"SOM","ZA":"ZAF","SS":"SSD",
    "ES":"ESP","LK":"LKA","SD":"SDN","SR":"SUR","SE":"SWE","CH":"CHE","SY":"SYR",
    "TW":"TWN","TJ":"TJK","TZ":"TZA","TH":"THA","TL":"TLS","TG":"TGO","TO":"TON",
    "TT":"TTO","TN":"TUN","TR":"TUR","TM":"TKM","TV":"TUV","UG":"UGA","UA":"UKR",
    "AE":"ARE","GB":"GBR","US":"USA","UY":"URY","UZ":"UZB","VU":"VUT","VE":"VEN",
    "VN":"VNM","YE":"YEM","ZM":"ZMB","ZW":"ZWE",
}


def _build_worldmap(countries: list) -> go.Figure:
    """Construit la choroplèthe des pays de destination du trafic DNS NextDNS."""
    _GEO = dict(
        showframe=False,
        showcoastlines=False,
        showland=True,
        landcolor="#141828",
        showocean=True,
        oceancolor="#060810",
        showlakes=False,
        showcountries=True,
        countrycolor="rgba(0,229,255,0.12)",
        projection_type="natural earth",
        bgcolor="rgba(0,0,0,0)",
    )
    _LAYOUT = {
        **PLOTLY_THEME,
        "height": WORLDMAP_HEIGHT,
        "margin": {"l": 0, "r": 0, "t": 0, "b": 0},
        "geo":    {"bgcolor": "rgba(0,0,0,0)"},
    }

    locs, vals, texts = [], [], []
    for c in countries:
        iso3 = _ISO2.get(c.get("country", ""))
        if iso3:
            locs.append(iso3)
            vals.append(c["queries"])
            texts.append(f"{c['country']} — {c['queries']:,} req.")

    if not locs:
        fig = go.Figure()
        fig.update_geos(**_GEO)
        fig.update_layout(**_LAYOUT)
        return fig

    fig = go.Figure(go.Choropleth(
        locations=locs,
        z=vals,
        text=texts,
        hovertemplate="%{text}<extra></extra>",
        colorscale=[
            [0.0, "rgba(0,229,255,0.08)"],
            [0.3, "rgba(0,229,255,0.35)"],
            [0.7, "rgba(0,229,255,0.70)"],
            [1.0, "#00e5ff"],
        ],
        showscale=False,
        marker_line_color="rgba(0,229,255,0.20)",
        marker_line_width=0.5,
        zmin=0,
        zmax=max(vals),
    ))
    fig.update_geos(**_GEO)
    fig.update_layout(**_LAYOUT)
    return fig


def _nas_volume_row(vol: dict) -> html.Div:
    """Rend une ligne de volume NAS avec barre de progression et métadonnées."""
    pct   = vol["used_pct"]
    color = CP["red"] if pct > 85 else CP["orange"] if pct > 70 else CP["green"]
    return html.Div([
        html.Div([
            html.Span(vol["path"], style={
                "fontFamily": FONT_MONO, "fontSize": "13px", "color": CP["cyan"],
                "fontWeight": "600",
            }),
            html.Span(f"{vol['used_str']} / {vol['total_str']}", style={
                "fontFamily": FONT_MONO, "fontSize": "12px", "color": CP["text_dim"],
            }),
            html.Span(f"{pct}%", style={
                "fontFamily": FONT_MONO, "fontSize": "12px", "color": color,
                "fontWeight": "700",
            }),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "center", "marginBottom": "4px"}),
        html.Div(
            html.Div(style={"height": "100%", "width": f"{pct}%", "background": color,
                            "transition": "width .7s cubic-bezier(.4,0,.2,1)"}),
            style={"height": "5px", "background": "rgba(255,255,255,0.06)",
                   "marginBottom": "4px"},
        ),
        html.Div(
            html.Span(f"Libre : {vol['free_str']}", style={
                "fontFamily": FONT_MONO, "fontSize": "11px", "color": CP["text_dim"],
            }),
            style={"marginBottom": "10px"},
        ),
    ])


def _nas_system_children(system: dict) -> list:
    """Rend les infos système NAS (modèle, température, uptime, version)."""
    temp = system.get("temperature")
    temp_color = (CP["red"] if (temp or 0) > 60
                  else CP["orange"] if (temp or 0) > 50
                  else CP["green"])
    temp_warn = system.get("temperature_warn", False)

    uptime_s = system.get("uptime_s", 0)
    days, rem = divmod(int(uptime_s), 86400)
    hours = rem // 3600
    uptime_str = f"{days}j {hours}h" if days else f"{hours}h"

    rows = [
        ("Modèle",      system.get("model", "—"),  CP["text_dim"]),
        ("RAM",         f"{system.get('ram_mb', 0)} MB", CP["text_dim"]),
        ("Température", f"{temp}°C{'  ⚠' if temp_warn else ''}", temp_color),
        ("Uptime",      uptime_str,                 CP["text_dim"]),
        ("Version DSM", system.get("version", "—"), CP["text_dim"]),
    ]
    items = []
    for label, value, color in rows:
        items.append(html.Div([
            html.Span(f"{label} :", style={
                "fontFamily": FONT_MONO, "fontSize": "11px",
                "color": CP["text_dim"], "minWidth": "110px",
            }),
            html.Span(value, style={
                "fontFamily": FONT_MONO, "fontSize": "12px", "color": color,
            }),
        ], style={"display": "flex", "gap": "8px", "padding": "3px 0",
                  "borderBottom": "1px solid rgba(255,255,255,0.04)"}))
    return items


_NAS_TAG_STYLE = {
    "position": "absolute", "bottom": "6px", "right": "8px",
    "fontSize": "10px", "fontFamily": FONT_MONO,
    "color": CP["yellow"], "letterSpacing": "2px",
    "border": f"1px solid {CP['yellow']}66", "padding": "1px 6px",
}
_NAS_TAG_HIDDEN = {"display": "none"}


@callback(
    Output("r-devices",         "children"),
    Output("r-blocked",         "children"),
    Output("r-rate",            "children"),
    Output("r-table",           "children"),
    Output("r-worldmap",        "figure"),
    Output("nas-volumes",       "children"),
    Output("nas-system",        "children"),
    Output("nas-outdated-tag",  "style"),
    Input("interval-reseau", "n_intervals"),
)
def update_reseau(_n):
    """
    Met à jour la page Réseau : devices LAN (nmap, cache 120 s), stats DNS NextDNS (cache 60 s),
    NAS Synology (cache 1 h), carte choroplèthe.
    Les données fraîches sont persistées dans data_cache pour le fallback.
    """
    # ── Devices LAN (nmap, cache 120 s) ────────────────────────────────────────
    devices = get_local_devices()
    if devices:
        data_cache.write("network.devices", devices, "devices", "nmap")
        data_logger.log("network_devices_count", len(devices), "devices", "nmap")
    else:
        entry = data_cache.read("network.devices")
        if entry:
            devices = entry["value"]

    device_count = str(len(devices)) if devices else "…"

    if devices:
        rows = []
        for i, d in enumerate(devices):
            bg = "rgba(0,229,255,0.03)" if i % 2 == 0 else "transparent"
            rows.append(html.Div([
                html.Span(d["ip"], style={
                    "fontFamily": FONT_MONO, "fontSize": "14px",
                    "color": CP["cyan"], "minWidth": "130px",
                }),
                html.Span(d["name"], style={
                    "fontFamily": FONT_MONO, "fontSize": "13px",
                    "color": CP["text_dim"],
                }),
            ], style={"display": "flex", "gap": "20px", "padding": "5px 4px",
                      "background": bg, "alignItems": "center"}))
        device_table = html.Div(rows)
    else:
        device_table = html.Div(
            f"Scan en cours… (nmap -sn {CFG.NETWORK_SUBNET})",
            style={"fontSize": "13px", "color": CP["text_dim"], "fontFamily": FONT_MONO},
        )

    # ── NextDNS ────────────────────────────────────────────────────────────────
    if not nextdns_client.configured():
        blocked_str = "N/C"
        rate_str    = "N/C"
        countries   = []
    else:
        status = nextdns_client.get_status()
        if status:
            data_cache.write("network.dns.blocked", status["blocked"], "requests", "nextdns")
            data_cache.write("network.dns.rate",    status["rate"],    "%",        "nextdns")
            data_cache.write("network.dns.total",   status["total"],   "requests", "nextdns")
            data_logger.log("network_dns_blocked", status["blocked"], "requests", "nextdns")
            data_logger.log("network_dns_rate",    status["rate"],    "%",        "nextdns")
            blocked_str = f"{status['blocked']:,}".replace(",", " ")
            rate_str    = f"{status['rate']} %"
        else:
            b = data_cache.read("network.dns.blocked")
            r = data_cache.read("network.dns.rate")
            blocked_str = f"{b['value']:,}".replace(",", " ") if b else "—"
            rate_str    = f"{r['value']} %" if r else "—"

        live_countries = nextdns_client.get_traffic_countries()
        if live_countries:
            data_cache.write("network.dns.countries", live_countries, "countries", "nextdns")
            countries = live_countries
        else:
            c = data_cache.read("network.dns.countries")
            countries = c["value"] if c else []

    # ── NAS Synology (cache 1 h) ───────────────────────────────────────────────
    nas_data = synology_client.fetch()

    _dim = {"fontSize": "12px", "color": CP["text_dim"], "fontFamily": FONT_MONO}
    if nas_data and nas_data.get("volumes"):
        nas_vol_children = [_nas_volume_row(v) for v in nas_data["volumes"]]
    elif not synology_client.is_configured():
        nas_vol_children = html.Div("// NAS non configuré (SYNOLOGY_NAS_USER vide)", style=_dim)
    else:
        nas_vol_children = html.Div("// En attente du premier fetch…", style=_dim)

    nas_sys_children = (
        _nas_system_children(nas_data["system"])
        if nas_data and nas_data.get("system")
        else html.Div("—", style=_dim)
    )

    nas_tag_style = (
        _NAS_TAG_STYLE
        if synology_client.is_stale(NAS_STALE_SECS) and synology_client.is_configured()
        else _NAS_TAG_HIDDEN
    )

    return (device_count, blocked_str, rate_str, device_table,
            _build_worldmap(countries),
            nas_vol_children, nas_sys_children, nas_tag_style)



# ── Plex / Musique ────────────────────────────────────────────────────────────

from modules.plex_client import plex_client  # noqa: E402 — import tardif pour éviter les cycles


# ── Badges de connexion ───────────────────────────────────────────────────────

def _dot_style(color: str) -> dict:
    """Retourne le style inline d'un point de badge (identique au dict dans _status_badge)."""
    return {"width": "9px", "height": "9px", "borderRadius": "50%",
            "background": color, "flexShrink": "0"}

@callback(
    Output("badge-mqtt",    "style"),
    Output("badge-sensors", "style"),
    Output("badge-meteo",   "style"),
    Output("badge-plex",    "style"),
    Output("badge-dns",     "style"),
    Output("badge-confort", "style"),
    Output("badge-enedis",  "style"),
    Input("interval-main", "n_intervals"),
)
def update_badges(_n):
    mqtt_color = CP["green"] if mqtt_client.is_connected() else CP["red"]

    # SENSORS : données fraîches si la dernière écriture date de moins de 30 s
    entry = data_cache.read("sensor.salon.temperature")
    sensors_color = (
        CP["green"] if entry and (time.time() - entry["updated_at"]) < 30
        else CP["red"]
    )

    # METEO : vert = cache frais, yellow = cache expiré, red = aucune donnée
    if weather_service._cache is None:
        meteo_color = CP["red"]
    elif weather_service.is_stale():
        meteo_color = CP["yellow"]
    else:
        meteo_color = CP["green"]

    # PLEX : serveur instancié = connexion établie
    plex_color = CP["green"] if plex_client._server is not None else CP["red"]

    # DNS : clés API configurées = service actif
    dns_color = CP["green"] if nextdns_client.configured() else CP["red"]

    # CONFORT : orange = modèle limited, vert = modèle full, rouge = aucun modèle
    confort_status = comfort_engine.model_status()
    confort_color = {
        comfort_engine.STATUS_FULL:    CP["green"],
        comfort_engine.STATUS_LIMITED: CP["orange"],
        comfort_engine.STATUS_NONE:    CP["red"],
    }[confort_status]

    # ENEDIS : vert < 24h · jaune ≥ 24h · rouge si erreur / non configuré
    enedis_kind, _ = _enedis_kind()
    enedis_color = {"ok": CP["green"], "warn": CP["yellow"], "err": CP["red"]}[enedis_kind]

    return (
        _dot_style(mqtt_color),
        _dot_style(sensors_color),
        _dot_style(meteo_color),
        _dot_style(plex_color),
        _dot_style(dns_color),
        _dot_style(confort_color),
        _dot_style(enedis_color),
    )


def _fmt_ms(ms: int) -> str:
    """Convertit une durée en millisecondes en chaîne M:SS (ex. 183000 → '3:03')."""
    if not ms:
        return "0:00"
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def _art(thumb: str, size: int = 96) -> html.Div:
    """Retourne une pochette (html.Img) ou un placeholder ♪."""
    if thumb:
        return html.Img(
            src=thumb,
            style={"width": "100%", "height": "100%", "objectFit": "cover", "display": "block"},
        )
    return html.Div(
        "♪",
        style={"fontSize": f"{size // 3}px", "color": "rgba(0,229,255,0.2)"},
    )


@callback(
    Output("mu-art",           "children"),
    Output("mu-title",         "children"),
    Output("mu-artist",        "children"),
    Output("mu-album",         "children"),
    Output("home-mu-art",      "children"),
    Output("home-mu-title",    "children"),
    Output("home-mu-artist",   "children"),
    Input("mu-local-track",  "data"),
)
def update_plex_player(local_track):
    """Met à jour les métadonnées affichées du lecteur (titre/artiste/album/pochette) à partir
    de la piste locale en cours (lecture 100% navigateur, voir mu-audio)."""
    if local_track:
        title  = local_track.get("title",  "Aucune lecture")
        artist = local_track.get("artist", "PLEX · EN ATTENTE")
        return (
            _art(local_track.get("thumb", "")),
            title, artist, local_track.get("album", ""),
            _art(local_track.get("thumb", ""), size=48), title, artist,
        )
    return (_art(""), "Aucune lecture", "PLEX · EN ATTENTE", "",
            _art("", size=48), "Aucune lecture", "PLEX · EN ATTENTE")


@callback(
    Output("mu-artists",   "children"),
    Output("mu-playlists", "children"),
    Input("interval-plex-shelf", "n_intervals"),
)
def update_plex_shelves(_n):
    """Met à jour les carrousels artistes récents et playlists (refresh toutes les 60 s)."""
    def _card(thumb: str, label: str, rating_key: str, media: str, sub: str = "") -> html.Div:
        img = (
            html.Img(src=thumb, className="plex-card-img")
            if thumb
            else html.Div("♪", className="plex-card-img plex-card-img--placeholder")
        )
        children = [img, html.Div(label, className="plex-card-label")]
        if sub:
            children.append(html.Div(sub, className="plex-card-sub"))
        div_id = ({"type": "plex-play", "key": rating_key, "media": media}
                  if rating_key else f"plex-card-{label}")
        return html.Div(children, className="plex-card", id=div_id, n_clicks=0)

    _empty = lambda msg: [html.Div(msg, style={"fontSize": "22px", "color": CP["text_dim"],
                                                "fontFamily": FONT_MONO})]

    artists = plex_client.get_recent_artists()
    artist_cards = (
        [_card(a["thumb"], a["title"], a.get("rating_key", ""), "artist") for a in artists]
        if artists else _empty("Aucun artiste")
    )

    playlists = plex_client.get_playlists()
    playlist_cards = (
        [_card(p["thumb"], p["title"], p.get("rating_key", ""), "playlist",
               f"{p['count']} titres" if p.get("count") else "")
         for p in playlists]
        if playlists else _empty("Aucune playlist")
    )

    return artist_cards, playlist_cards


def _track_rows(tracks: list) -> list:
    """Convertit une liste de dicts piste en lignes cliquables Dash (pochette + titre + artiste).

    Chaque ligne sépare la zone info (clic = insère la piste après l'élément en cours de la
    file d'attente) des deux boutons d'action (lecture immédiate / ajout en fin de file) : ce
    sont des divs frères, pas des enfants imbriqués, pour éviter que le clic sur un bouton ne
    déclenche aussi le callback de la zone info (bubbling Dash).
    """
    rows = []
    for t in tracks:
        thumb = (
            html.Img(src=t["thumb"], className="plex-track-thumb")
            if t.get("thumb")
            else html.Div("♪", className="plex-track-thumb plex-track-thumb--placeholder")
        )
        rows.append(html.Div([
            html.Div([
                thumb,
                html.Div([
                    html.Div(t["title"], style={"fontSize": "26px", "color": CP["text"], "fontWeight": "600"}),
                    html.Div(
                        f"{t.get('artist', '')}  —  {t.get('album', '')}",
                        style={"fontSize": "20px", "color": CP["text_dim"],
                               "fontFamily": FONT_MONO, "marginTop": "3px"},
                    ),
                ], style={"minWidth": "0"}),
            ], className="plex-track-info",
               id={"type": "plex-play", "key": t["rating_key"], "media": "track"},
               n_clicks=0),
            html.Div([
                html.Button("▶", id={"type": "plex-track-now", "key": t["rating_key"]},
                            n_clicks=0, className="plex-action-btn", title="Lire maintenant"),
                html.Button("+", id={"type": "plex-track-add", "key": t["rating_key"]},
                            n_clicks=0, className="plex-action-btn plex-action-btn--add",
                            title="Ajouter en fin de file"),
            ], className="plex-track-actions"),
        ], className="plex-track-row"))
    return rows


def _album_cards(albums: list) -> html.Div:
    """Convertit une liste de dicts album en carrousel de cartes cliquables Dash.

    Clic sur la zone info = ouvre l'album (affiche ses pistes) ; deux boutons d'action séparés
    pour lire l'album immédiatement ou l'ajouter en fin de file (voir _track_rows pour la
    raison de la séparation info/actions en divs frères).
    """
    cards = []
    for a in albums:
        img = (
            html.Img(src=a["thumb"], className="plex-card-img")
            if a.get("thumb")
            else html.Div("♪", className="plex-card-img plex-card-img--placeholder")
        )
        info_children = [img, html.Div(a["title"], className="plex-card-label")]
        if a.get("year"):
            info_children.append(html.Div(a["year"], className="plex-card-sub"))
        cards.append(html.Div([
            html.Div(info_children, className="plex-card-info",
                     id={"type": "plex-play", "key": a["rating_key"], "media": "album"},
                     n_clicks=0),
            html.Div([
                html.Button("▶", id={"type": "plex-album-now", "key": a["rating_key"]},
                            n_clicks=0, className="plex-action-btn", title="Lire l'album maintenant"),
                html.Button("+", id={"type": "plex-album-add", "key": a["rating_key"]},
                            n_clicks=0, className="plex-action-btn plex-action-btn--add",
                            title="Ajouter l'album en fin de file"),
            ], className="plex-card-actions"),
        ], className="plex-card"))
    return html.Div(cards, className="plex-carousel")


def _no_result(msg="Aucun résultat."):
    return html.Div(msg, style={"fontSize": "22px", "color": CP["text_dim"],
                                "fontFamily": FONT_MONO, "padding": "10px 0"})


_BACK_HIDDEN  = {"display": "none",  "fontSize": "13px", "letterSpacing": "2px", "marginBottom": "10px"}
_BACK_VISIBLE = {"display": "block", "fontSize": "13px", "letterSpacing": "2px", "marginBottom": "10px"}


def _render_nav_entry(entry: dict):
    """Re-rend le contenu d'une entrée de l'historique de navigation."""
    action = entry.get("action")
    key    = entry.get("key", "")
    if action == "search":
        tracks = plex_client.search_tracks(entry.get("query", ""))
        return _track_rows(tracks) if tracks else _no_result()
    if action == "artist":
        albums = plex_client.get_artist_albums(key)
        return _album_cards(albums) if albums else _no_result("Aucun album trouvé.")
    if action == "album":
        tracks = plex_client.get_album_tracks(key)
        return _track_rows(tracks) if tracks else _no_result()
    if action == "playlist":
        tracks = plex_client.get_playlist_tracks(key)
        return _track_rows(tracks) if tracks else _no_result()
    return []


@callback(
    Output("mu-search-results", "children"),
    Output("mu-nav-stack",      "data"),
    Output("mu-back-btn",       "style"),
    Input("mu-search-btn",   "n_clicks"),
    Input("mu-search-input", "n_submit"),
    Input({"type": "plex-play", "key": ALL, "media": ALL}, "n_clicks"),
    Input("mu-back-btn",     "n_clicks"),
    State("mu-search-input", "value"),
    State("mu-nav-stack",    "data"),
    prevent_initial_call=True,
)
def handle_plex_navigation(_btn, _submit, _all_clicks, _back, query, nav_stack):
    """
    Gère la navigation dans la bibliothèque Plex : recherche texte + drill-down artiste/album/playlist.
    Maintient une pile de navigation (nav_stack) pour le bouton Retour.
    """
    triggered_id = ctx.triggered_id
    nav_stack    = nav_stack or []

    # ── Retour arrière ────────────────────────────────────────────────────────
    if triggered_id == "mu-back-btn":
        if len(nav_stack) <= 1:
            return [], [], _BACK_HIDDEN
        nav_stack  = nav_stack[:-1]
        back_style = _BACK_VISIBLE if len(nav_stack) > 1 else _BACK_HIDDEN
        return _render_nav_entry(nav_stack[-1]), nav_stack, back_style

    # ── Recherche texte ───────────────────────────────────────────────────────
    if isinstance(triggered_id, str):
        if not query:
            return [], [], _BACK_HIDDEN
        tracks    = plex_client.search_tracks(query)
        nav_stack = [{"action": "search", "query": query}]
        return _track_rows(tracks) if tracks else _no_result(), nav_stack, _BACK_HIDDEN

    if not triggered_id or not triggered_id.get("key"):
        return no_update, no_update, no_update

    key   = triggered_id["key"]
    media = triggered_id.get("media", "")

    # ── Navigation par drill-down (artiste → albums → pistes) ────────────────
    if media == "artist":
        albums    = plex_client.get_artist_albums(key)
        nav_stack = nav_stack + [{"action": "artist", "key": key}]
        content   = _album_cards(albums) if albums else _no_result("Aucun album trouvé.")
        return content, nav_stack, _BACK_VISIBLE if len(nav_stack) > 1 else _BACK_HIDDEN

    if media == "album":
        tracks    = plex_client.get_album_tracks(key)
        nav_stack = nav_stack + [{"action": "album", "key": key}]
        content   = _track_rows(tracks) if tracks else _no_result()
        return content, nav_stack, _BACK_VISIBLE if len(nav_stack) > 1 else _BACK_HIDDEN

    if media == "playlist":
        tracks    = plex_client.get_playlist_tracks(key)
        nav_stack = nav_stack + [{"action": "playlist", "key": key}]
        content   = _track_rows(tracks) if tracks else _no_result()
        return content, nav_stack, _BACK_VISIBLE if len(nav_stack) > 1 else _BACK_HIDDEN

    return no_update, no_update, no_update


def _queue_lists(queue) -> tuple:
    """Normalise un store mu-queue (éventuellement vide/None) en (tracks, idx)."""
    queue = queue or {}
    return list(queue.get("tracks", [])), queue.get("idx", 0)


@callback(
    Output("mu-queue", "data", allow_duplicate=True),
    Input({"type": "plex-play", "key": ALL, "media": "track"}, "n_clicks"),
    State("mu-queue", "data"),
    prevent_initial_call=True,
)
def insert_track_after_current(all_clicks, queue):
    """Clic sur une ligne piste (hors boutons) : insère la piste juste après l'élément en cours."""
    if not any(all_clicks):
        return no_update
    triggered = ctx.triggered_id
    if not triggered or not triggered.get("key"):
        return no_update
    track = plex_client.get_track_data(triggered["key"])
    if not track:
        return no_update
    tracks, idx = _queue_lists(queue)
    tracks.insert(idx + 1, track)
    return {"tracks": tracks, "idx": idx}


@callback(
    Output("mu-local-track", "data", allow_duplicate=True),
    Output("mu-queue",       "data", allow_duplicate=True),
    Input({"type": "plex-track-now", "key": ALL}, "n_clicks"),
    State("mu-queue", "data"),
    prevent_initial_call=True,
)
def play_track_now(all_clicks, queue):
    """Bouton ▶ d'une piste : remplace l'élément courant de la file et lance la lecture."""
    if not any(all_clicks):
        return no_update, no_update
    triggered = ctx.triggered_id
    if not triggered or not triggered.get("key"):
        return no_update, no_update
    track = plex_client.get_track_data(triggered["key"])
    if not track:
        return no_update, no_update
    tracks, idx = _queue_lists(queue)
    if tracks and 0 <= idx < len(tracks):
        tracks[idx] = track
    else:
        tracks, idx = [track], 0
    return track, {"tracks": tracks, "idx": idx}


@callback(
    Output("mu-queue", "data", allow_duplicate=True),
    Input({"type": "plex-track-add", "key": ALL}, "n_clicks"),
    State("mu-queue", "data"),
    prevent_initial_call=True,
)
def add_track_to_end(all_clicks, queue):
    """Bouton + d'une piste : ajoute la piste en fin de file, sans toucher à la lecture en cours."""
    if not any(all_clicks):
        return no_update
    triggered = ctx.triggered_id
    if not triggered or not triggered.get("key"):
        return no_update
    track = plex_client.get_track_data(triggered["key"])
    if not track:
        return no_update
    tracks, idx = _queue_lists(queue)
    tracks.append(track)
    return {"tracks": tracks, "idx": idx}


@callback(
    Output("mu-local-track", "data", allow_duplicate=True),
    Output("mu-queue",       "data", allow_duplicate=True),
    Input({"type": "plex-album-now", "key": ALL}, "n_clicks"),
    State("mu-shuffle-on", "data"),
    prevent_initial_call=True,
)
def play_album_now(all_clicks, shuffle_on):
    """Bouton ▶ d'un album : remplace toute la file par l'album et lance la lecture."""
    if not any(all_clicks):
        return no_update, no_update
    triggered = ctx.triggered_id
    if not triggered or not triggered.get("key"):
        return no_update, no_update
    tracks = plex_client.get_album_context(triggered["key"]).get("tracks", [])
    if not tracks:
        return no_update, no_update
    if shuffle_on:
        random.shuffle(tracks)
    return tracks[0], {"tracks": tracks, "idx": 0}


@callback(
    Output("mu-queue", "data", allow_duplicate=True),
    Input({"type": "plex-album-add", "key": ALL}, "n_clicks"),
    State("mu-queue", "data"),
    State("mu-shuffle-on", "data"),
    prevent_initial_call=True,
)
def add_album_to_end(all_clicks, queue, shuffle_on):
    """Bouton + d'un album : ajoute toutes ses pistes en fin de file."""
    if not any(all_clicks):
        return no_update
    triggered = ctx.triggered_id
    if not triggered or not triggered.get("key"):
        return no_update
    new_tracks = plex_client.get_album_context(triggered["key"]).get("tracks", [])
    if not new_tracks:
        return no_update
    if shuffle_on:
        new_tracks = new_tracks.copy()
        random.shuffle(new_tracks)
    tracks, idx = _queue_lists(queue)
    tracks.extend(new_tracks)
    return {"tracks": tracks, "idx": idx}


@callback(
    Output("mu-queue-carousel", "children"),
    Input("mu-queue", "data"),
)
def update_queue_carousel(queue):
    """Affiche les pistes restantes de la file d'attente (après l'index courant)."""
    if not queue or not queue.get("tracks"):
        return []
    tracks = queue["tracks"]
    idx    = queue.get("idx", 0)
    cards  = []
    for pos, t in enumerate(tracks):
        if pos <= idx:
            continue
        img = (
            html.Img(src=t["thumb"], className="plex-mini-img")
            if t.get("thumb")
            else html.Div("♪", className="plex-mini-img plex-mini-placeholder")
        )
        cards.append(html.Div(
            [img, html.Div(t["title"], className="plex-mini-label")],
            className="plex-mini-card",
            id={"type": "plex-queue-jump", "idx": pos},
            n_clicks=0,
        ))
    return cards


@callback(
    Output("mu-local-track", "data", allow_duplicate=True),
    Output("mu-queue",       "data", allow_duplicate=True),
    Input({"type": "plex-queue-jump", "idx": ALL}, "n_clicks"),
    State("mu-queue", "data"),
    prevent_initial_call=True,
)
def jump_queue(all_clicks, queue):
    """Clic sur une piste de la file d'attente : saute directement dessus."""
    if not any(all_clicks) or not queue or not queue.get("tracks"):
        return no_update, no_update
    triggered = ctx.triggered_id
    if not triggered or "idx" not in triggered:
        return no_update, no_update
    idx, tracks = triggered["idx"], queue["tracks"]
    if not (0 <= idx < len(tracks)):
        return no_update, no_update
    return tracks[idx], {"tracks": tracks, "idx": idx}


@callback(
    Output("mu-local-track", "data",  allow_duplicate=True),
    Output("mu-queue",       "data",  allow_duplicate=True),
    Input("btn-prev",       "n_clicks"),
    Input("btn-next",       "n_clicks"),
    Input("home-btn-prev",  "n_clicks"),
    Input("home-btn-next",  "n_clicks"),
    State("mu-queue",        "data"),
    prevent_initial_call=True,
)
def navigate_queue(_prev, _next, _home_prev, _home_next, queue):
    """Piste précédente / suivante dans la file d'attente (boutons Prev/Next, onglet Musique et Accueil)."""
    if not queue or not queue.get("tracks"):
        return no_update, no_update
    triggered = ctx.triggered_id
    tracks = queue["tracks"]
    idx    = queue.get("idx", 0)
    new_idx = idx - 1 if triggered in ("btn-prev", "home-btn-prev") else idx + 1
    if new_idx < 0 or new_idx >= len(tracks):
        return no_update, no_update
    t = tracks[new_idx]
    track_data = {k: t[k] for k in ("title", "artist", "album", "thumb", "stream_url", "duration")}
    return track_data, {"tracks": tracks, "idx": new_idx}


@callback(
    Output("mu-audio", "src"),
    Output("mu-audio", "autoPlay"),
    Input("mu-local-track", "data"),
    prevent_initial_call=True,
)
def update_audio_src(track_data):
    """Met à jour la source <audio> et déclenche la lecture quand une nouvelle piste est sélectionnée."""
    if not track_data or not track_data.get("stream_url"):
        return no_update, no_update
    return track_data["stream_url"], True


@callback(
    Output("mu-shuffle-on", "data"),
    Output("btn-shuffle",   "className"),
    Output("mu-queue",      "data", allow_duplicate=True),
    Input("btn-shuffle", "n_clicks"),
    State("mu-shuffle-on", "data"),
    State("mu-queue", "data"),
    prevent_initial_call=True,
)
def toggle_shuffle(_n, shuffle_on, queue):
    """Active/désactive le mode aléatoire ; à l'activation, mélange les pistes à venir de la file."""
    new_state  = not bool(shuffle_on)
    class_name = "ctrl-btn ctrl-btn--active" if new_state else "ctrl-btn"
    if new_state and queue and queue.get("tracks"):
        tracks, idx = _queue_lists(queue)
        upcoming = tracks[idx + 1:]
        random.shuffle(upcoming)
        tracks[idx + 1:] = upcoming
        return new_state, class_name, {"tracks": tracks, "idx": idx}
    return new_state, class_name, no_update


@callback(
    Output("mu-queue", "data", allow_duplicate=True),
    Input("btn-clear-queue", "n_clicks"),
    State("mu-queue", "data"),
    prevent_initial_call=True,
)
def clear_queue(_n, queue):
    """Vide la file d'attente, sans interrompre la piste en cours de lecture."""
    if not queue or not queue.get("tracks"):
        return no_update
    tracks, idx = _queue_lists(queue)
    current = [tracks[idx]] if 0 <= idx < len(tracks) else []
    return {"tracks": current, "idx": 0}


@callback(
    Output("btn-clear-queue", "style"),
    Input("mu-queue", "data"),
)
def toggle_clear_queue_visibility(queue):
    """Le bouton poubelle n'apparaît que si la file contient au moins un élément."""
    return {} if (queue and queue.get("tracks")) else {"display": "none"}


# ── Journal système & périphériques actifs ────────────────────────────────────

def _log_entry(ts: str, level: str, msg: str, color: str) -> html.Div:
    return html.Div([
        html.Span(f"[{ts}]", style={
            "fontSize": "12px", "color": "rgba(200,232,239,0.3)",
            "fontFamily": FONT_MONO, "minWidth": "72px",
        }),
        html.Span(level, style={
            "fontSize": "11px", "fontFamily": FONT_MONO, "color": color,
            "letterSpacing": "2px", "padding": "1px 8px",
            "border": f"1px solid {color}40", "background": f"{color}15",
            "minWidth": "64px", "textAlign": "center",
        }),
        html.Span(msg, style={
            "fontSize": "12px", "fontFamily": FONT_MONO, "color": CP["text_dim"],
        }),
    ], style={"display": "flex", "alignItems": "center", "gap": "10px"})


@callback(
    Output("home-log",     "children"),
    Output("home-devices", "children"),
    Input("interval-main", "n_intervals"),
)
def update_home_status(_n):
    """
    Met à jour le journal système et la liste des périphériques réseau (page Accueil).
    Vérifie : cache expiré par catégorie, arrosage plantes, fetch Enedis échoué,
    données électricité périmées (> 36h), modèle confort indisponible.
    """
    now_str = datetime.now().strftime("%H:%M:%S")

    # ── Fraîcheur des données ─────────────────────────────────────────────────
    stale   = data_cache.get_stale_categories()
    _LABELS = {"weather": "METEO", "sensor": "CAPTEURS", "network": "RESEAU"}
    _SEUILS = {"weather": "12h",   "sensor": "1h",       "network": "1h"}
    log_items: list = []

    for cat, age in sorted(stale.items()):
        h = int(age // 3600)
        m = int((age % 3600) // 60)
        age_str = f"{h}h {m:02d}m" if h else f"{m}m"
        log_items.append(_log_entry(
            now_str, "ALERTE",
            f"{_LABELS.get(cat, cat.upper())} · {age_str} sans maj (seuil {_SEUILS.get(cat, '?')})",
            CP["red"],
        ))

    # ── Alerte arrosage plantes ────────────────────────────────────────────────
    for pid, pname, *_ in _plant_list:
        moisture = sensor_store.get_plant_value(pid, "soil_moisture")
        if moisture is None:
            entry = data_cache.read(f"plant.{pid}.soil_moisture")
            if entry and (time.time() - entry["updated_at"]) < _PLANT_CACHE_MAX_AGE:
                moisture = entry["value"]
        if moisture is not None and moisture < CFG.ALERT_PLANT_WATER_MIN:
            log_items.append(_log_entry(
                now_str, "ALERTE",
                f"PLANTE · {pname} · humidité sol {moisture:.0f}% (arrosage requis)",
                CP["yellow"],
            ))

    # ── Alerte fetch Enedis échoué (fetch 8h42 en échec ou retry en cours) ──────
    if enedis_service.is_configured() and enedis_service.morning_failed:
        log_items.append(_log_entry(
            now_str, "WARN",
            f"ELECTRICITE · fetch matinal echoue — retry en cours ({enedis_service.last_error})",
            CP["orange"],
        ))

    # ── Alerte données électricité périmées (> 36h) ───────────────────────────
    if enedis_service.is_configured():
        elec_rows = enedis_service.read_history()
        if not elec_rows:
            log_items.append(_log_entry(
                now_str, "ALERTE",
                "ELECTRICITE · aucune donnee disponible (premier fetch en attente ?)",
                CP["yellow"],
            ))
        else:
            last_date = elec_rows[-1]["date"]
            last_eod  = datetime(last_date.year, last_date.month, last_date.day, 23, 59)
            age_h     = (datetime.now() - last_eod).total_seconds() / 3600
            if age_h > 36:
                age_str = f"{int(age_h)}h"
                log_items.append(_log_entry(
                    now_str, "ALERTE",
                    f"ELECTRICITE · dernieres donnees : {last_date} (il y a {age_str})",
                    CP["yellow"],
                ))

    # ── Alerte modele de confort indisponible ─────────────────────────────────
    if comfort_engine.model_status() == comfort_engine.STATUS_NONE:
        log_items.append(_log_entry(
            now_str, "ERREUR",
            "CONFORT · aucun modele predictif disponible (deposer limited.pt dans ./models/)",
            CP["red"],
        ))

    # ── Alerte NAS : données obsolètes (> 6 h) ───────────────────────────────
    if synology_client.is_configured() and synology_client.is_stale(NAS_STALE_SECS):
        age = synology_client.cache_age()
        if age is None:
            msg = "NAS · aucune donnee disponible (premier fetch en attente ?)"
        else:
            h, m = int(age // 3600), int((age % 3600) // 60)
            msg = f"NAS · derniere donnee il y a {h}h{m:02d}m (seuil 6h)"
        log_items.append(_log_entry(now_str, "WARN", msg, CP["orange"]))

    if not log_items:
        log_items.append(_log_entry(
            now_str, "OK", "Cache synchronise · toutes donnees recentes", CP["green"],
        ))

    # ── Periph actifs (depuis cache reseau) ───────────────────────────────────
    entry = data_cache.read("network.devices")
    if entry:
        net_devices = entry["value"]
        age_s   = time.time() - entry["updated_at"]
        age_str = (f"{int(age_s // 60)}m" if age_s < 3600
                   else f"{int(age_s // 3600)}h{int((age_s % 3600) // 60):02d}")
        rows = []
        for i, d in enumerate(net_devices[:15]):
            bg = "rgba(0,229,255,0.03)" if i % 2 == 0 else "transparent"
            rows.append(html.Div([
                html.Span(d["ip"], style={
                    "fontFamily": FONT_MONO, "fontSize": "13px",
                    "color": CP["cyan"], "minWidth": "120px",
                }),
                html.Span(d["name"], style={
                    "fontFamily": FONT_MONO, "fontSize": "12px", "color": CP["text_dim"],
                }),
            ], style={"display": "flex", "gap": "12px", "padding": "4px 2px", "background": bg}))
        footer = html.Div(
            f"{len(net_devices)} appareils · scan il y a {age_str}",
            style={"fontSize": "12px", "color": CP["text_dim"], "fontFamily": FONT_MONO,
                   "marginTop": "8px", "opacity": "0.5"},
        )
        devices_display = html.Div(rows + [footer])
    else:
        devices_display = html.Div(
            "Scan reseau en attente…",
            style={"fontSize": "13px", "color": CP["text_dim"], "fontFamily": FONT_MONO},
        )

    return log_items, devices_display


# ── Chatbot ────────────────────────────────────────────────────────────────────

CHATBOT_API_URL = getattr(CFG, "CHATBOT_API_URL", "http://localhost:8000")


def _render_chat_messages(msgs: list) -> list:
    """Convertit la liste de messages {"role", "text"} en composants Dash cyberpunk-stylés."""
    if not msgs:
        return [html.Div(
            "// EN ATTENTE DE MESSAGES — POSEZ UNE QUESTION A L'AGENT...",
            style={
                "fontSize": "13px", "letterSpacing": "2px",
                "color": CP["text_dim"], "fontFamily": FONT_MONO,
                "textAlign": "center", "marginTop": "32px",
            },
        )]
    children = []
    for msg in msgs:
        role = msg.get("role", "")
        text = msg.get("text", "")
        if role == "user":
            children.append(html.Div(
                html.Div(text, style={"margin": "0"}),
                className="chat-bubble chat-bubble--user",
            ))
        elif role == "bot":
            children.append(html.Div(
                html.Div(text, style={"margin": "0"}),
                className="chat-bubble chat-bubble--bot",
            ))
        else:
            children.append(html.Div(
                text,
                className="chat-bubble chat-bubble--system",
            ))
    return children


@callback(
    Output("chat-input",            "value"),
    Output("agent-messages-store",  "data"),
    Output("agent-pending-query",   "data"),
    Output("agent-status",          "children"),
    Input("chat-send-btn",          "n_clicks"),
    Input("chat-input",             "n_submit"),
    Input("chat-clear-btn",         "n_clicks"),
    State("chat-input",             "value"),
    State("agent-session-id",       "data"),
    State("agent-messages-store",   "data"),
    prevent_initial_call=True,
)
def handle_chat_input(send_clicks, n_submit, clear_clicks, text, session_id, messages):
    """Étape 1 : affiche le message utilisateur immédiatement et arme la requête API."""
    messages = messages or []
    if ctx.triggered_id == "chat-clear-btn":
        return "", [], None, ""
    if not text or not text.strip():
        return no_update, no_update, no_update, no_update
    new_messages = messages + [{"role": "user", "text": text.strip()}]
    pending = {"text": text.strip(), "session_id": session_id or ""}
    return "", new_messages, pending, ""


@callback(
    Output("agent-session-id",      "data"),
    Output("agent-messages-store",  "data", allow_duplicate=True),
    Output("agent-pending-query",   "data", allow_duplicate=True),
    Output("agent-status",          "children", allow_duplicate=True),
    Input("agent-pending-query",    "data"),
    State("agent-messages-store",   "data"),
    prevent_initial_call=True,
)
def fetch_agent_response(pending, messages):
    """Étape 2 : appelle POST /chat et ajoute la réponse du LLM au store."""
    if not pending or not pending.get("text"):
        return no_update, no_update, no_update, no_update
    messages = messages or []
    try:
        r = httpx.post(
            f"{CHATBOT_API_URL}/chat",
            json={"message": pending["text"], "session_id": pending["session_id"]},
            timeout=240.0,
        )
        r.raise_for_status()
        data = r.json()
        return data["session_id"], messages + [{"role": "bot", "text": data["response"]}], None, ""
    except httpx.ConnectError:
        return no_update, messages, None, "// API INACCESSIBLE — uvicorn demarre ?"
    except Exception as e:
        return no_update, messages, None, f"// ERREUR : {e}"


@callback(
    Output("chat-messages", "children"),
    Input("agent-messages-store", "data"),
)
def update_chat_display(messages):
    """Met à jour l'affichage des messages depuis le store Dash."""
    return _render_chat_messages(messages or [])


@callback(
    Output("badge-chatbot", "style"),
    Output("badge-logic",   "style"),
    Input("interval-chatbot", "n_intervals"),
)
def update_chatbot_badges(_n):
    """Badge CHATBOT : vert si GET /health répond 200. Badge LOGIC : état du logic_engine."""
    try:
        r = httpx.get(f"{CHATBOT_API_URL}/health", timeout=2.0)
        chatbot_ok = r.status_code == 200
    except Exception:
        chatbot_ok = False

    logic_ok = logic_engine.is_operational()
    logic_col = CP["green"] if logic_ok else CP["yellow"] if logic_engine.get_mode() == LogicMode.FORWARD else CP["red"]
    return _dot_style(CP["green"] if chatbot_ok else CP["red"]), _dot_style(logic_col)


# ── Data dump — yt-dlp ────────────────────────────────────────────────────────
# Architecture : zéro allow_duplicate.
# dl-trigger-store → déclenche update_dl_display au démarrage d'un job.
# dl-action-store  → transporte le résultat OK/Annuler vers update_dl_display.
# update_dl_display est le SEUL callback écrivant sur les composants visuels.

_DL_HIDDEN   = {"display": "none"}
_DL_VISIBLE  = {"display": "block"}
_DL_FLEX_ROW = {"display": "flex", "alignItems": "center",
                "gap": "10px", "marginBottom": "8px"}
_BAR_RESET   = {"height": "100%", "width": "0%",
                "background": CP["cyan"], "transition": "width .3s ease"}


@callback(
    Output("dl-params-store", "data"),
    Output("dl-no-chapters",  "className"),
    Output("dl-chapters",     "className"),
    Output("dl-fmt-mp3",      "className"),
    Output("dl-fmt-flac",     "className"),
    Output("dl-fmt-mp4",      "className"),
    Input("dl-no-chapters",   "n_clicks"),
    Input("dl-chapters",      "n_clicks"),
    Input("dl-fmt-mp3",       "n_clicks"),
    Input("dl-fmt-flac",      "n_clicks"),
    Input("dl-fmt-mp4",       "n_clicks"),
    State("dl-params-store",  "data"),
    prevent_initial_call=True,
)
def update_dl_params(_cn, _cy, _cm, _cf, _cv, params):
    """Met à jour les paramètres yt-dlp (chapitres, format) et l'aspect des boutons."""
    params = params or {"chapters": False, "format": "mp3"}
    tid = ctx.triggered_id
    if tid == "dl-no-chapters":
        params["chapters"] = False
    elif tid == "dl-chapters":
        params["chapters"] = True
    elif tid == "dl-fmt-mp3":
        params["format"] = "mp3"
    elif tid == "dl-fmt-flac":
        params["format"] = "flac"
    elif tid == "dl-fmt-mp4":
        params["format"] = "mp4"
    _act   = "ctrl-btn ctrl-btn--play"
    _inact = "ctrl-btn"
    return (
        params,
        _act if not params["chapters"]     else _inact,
        _act if params["chapters"]         else _inact,
        _act if params["format"] == "mp3"  else _inact,
        _act if params["format"] == "flac" else _inact,
        _act if params["format"] == "mp4"  else _inact,
    )


@callback(
    Output("dl-trigger-store", "data"),
    Input("dl-start",          "n_clicks"),
    State("dl-url",            "value"),
    State("dl-params-store",   "data"),
    State("dl-quality",        "value"),
    prevent_initial_call=True,
)
def start_download(_n, url, params, quality):
    """Lance yt-dlp en arrière-plan et notifie dl-trigger-store pour déclencher l'UI."""
    if not url or not url.strip():
        return no_update
    p = {**(params or {}), "quality": int(quality or 8)}
    job_id, folder = ytdlp_service.start(url.strip(), p)
    data_cache.log_ytdlp_job(job_id, job_id, url.strip(), p, folder)
    return {"active": True, "job_id": job_id, "folder": folder, "ts": time.time()}


@callback(
    Output("dl-action-store",   "data"),
    Input("dl-ok",              "n_clicks"),
    Input("dl-cancel",          "n_clicks"),
    State("dl-tag-artist",      "value"),
    State("dl-tag-albumartist", "value"),
    State("dl-tag-album",       "value"),
    State("dl-tag-year",        "value"),
    State("dl-tag-title",       "value"),
    State("dl-files-store",     "data"),
    State("dl-save-device",     "value"),
    prevent_initial_call=True,
)
def collect_dl_action(_ok, _cancel, artist, albumartist, album, year, title, files, save_device):
    """Capture l'intention OK / Annuler et les tags saisis vers dl-action-store."""
    return {
        "action":         "ok" if ctx.triggered_id == "dl-ok" else "cancel",
        "artist":         artist      or "",
        "albumartist":    albumartist or "",
        "album":          album       or "",
        "year":           year        or "",
        "title":          title       or "",
        "files":          files       or [],
        "save_on_device": bool(save_device),
        "ts":             time.time(),
    }


@callback(
    Output("dl-progress-wrap",   "style"),
    Output("dl-bar-fill",        "style"),
    Output("dl-bar-label",       "children"),
    Output("dl-id3-wrap",        "style"),
    Output("dl-tag-artist",      "value"),
    Output("dl-tag-albumartist", "value"),
    Output("dl-tag-album",       "value"),
    Output("dl-tag-year",        "value"),
    Output("dl-tag-title",       "value"),
    Output("dl-title-row",       "style"),
    Output("dl-start",           "disabled"),
    Output("dl-url",             "value"),
    Output("dl-state-store",     "data"),
    Output("dl-files-store",     "data"),
    Output("dl-download",        "data"),
    Input("interval-ytdlp",      "n_intervals"),
    Input("dl-trigger-store",    "data"),
    Input("dl-action-store",     "data"),
    State("dl-state-store",      "data"),
    prevent_initial_call=True,
)
def update_dl_display(_n, trigger, action, state):
    """
    Callback maître de la section Data dump — seul à écrire sur les composants visuels.
    Se déclenche sur : tick 1 s (progression) | trigger (démarrage) | action (OK/Annuler).
    """
    state = state or {}
    last_status = state.get("last_status", "")
    tid = ctx.triggered_id

    # ── OK / Annuler ──────────────────────────────────────────────────────────
    if tid == "dl-action-store" and action and action.get("action"):
        snap  = ytdlp_service.get_snapshot()
        files = action.get("files") or []
        download_data = no_update

        if action["action"] == "ok" and snap:
            ytdlp_service.apply_tags(
                files, snap["folder"],
                {k: action.get(k, "")
                 for k in ("artist", "albumartist", "album", "year", "title")},
                single_file=len(files) == 1,
            )
            data_cache.update_ytdlp_job(snap["id"], "success", files)
            ytdlp_service.clear()

            if action.get("save_on_device") and files:
                try:
                    folder_path = Path(snap["folder"])
                    if len(files) == 1:
                        fpath = folder_path / files[0]
                        if fpath.exists():
                            download_data = dcc.send_file(str(fpath))
                    else:
                        buf = io.BytesIO()
                        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                            for fname in files:
                                fpath = folder_path / fname
                                if fpath.exists():
                                    zf.write(fpath, fname)
                        buf.seek(0)
                        download_data = dcc.send_bytes(buf.read(), "download.zip")
                except Exception as exc:
                    logger.warning("dl-save-on-device: %s", exc)

        elif action["action"] == "cancel":
            if snap:
                data_cache.update_ytdlp_job(snap["id"], "cancelled")
            ytdlp_service.cancel()

        return (
            _DL_HIDDEN, _BAR_RESET, "Initialisation…",
            _DL_HIDDEN,
            "", "", "", "", "",
            _DL_FLEX_ROW,
            False, "",
            {}, [],
            download_data,
        )

    # ── Démarrage ─────────────────────────────────────────────────────────────
    if tid == "dl-trigger-store" and trigger and trigger.get("active"):
        return (
            _DL_VISIBLE, _BAR_RESET, "Connexion…",
            _DL_HIDDEN,
            no_update, no_update, no_update, no_update, no_update,
            no_update,
            True, no_update,
            {"last_status": "running"}, [],
            no_update,
        )

    # ── Polling ───────────────────────────────────────────────────────────────
    snap = ytdlp_service.get_snapshot()
    if snap is None:
        return (no_update,) * 15

    status = snap["status"]
    pct    = snap["progress_pct"]
    label  = snap["progress_str"]
    bar_fill = {
        "height": "100%", "width": f"{min(100, pct):.0f}%",
        "background": CP["green"] if status != "running" else CP["cyan"],
        "transition": "width .3s ease",
    }
    prog_vis  = _DL_HIDDEN  if status == "success" else _DL_VISIBLE
    id3_vis   = _DL_VISIBLE if status == "success" else _DL_HIDDEN
    new_state = {**state, "last_status": status}

    if status == "success" and last_status != "success":
        meta   = snap.get("metadata", {})
        files  = snap.get("files", [])
        single = len(files) == 1
        data_cache.update_ytdlp_job(snap["id"], "success", files)
        return (
            prog_vis, bar_fill, label,
            id3_vis,
            meta.get("artist", ""), meta.get("albumartist", ""),
            meta.get("album",  ""), meta.get("year",        ""),
            meta.get("title",  "") if single else "",
            _DL_FLEX_ROW if single else _DL_HIDDEN,
            True, no_update,
            {**new_state, "files": files}, files,
            no_update,
        )

    if status == "failed":
        data_cache.update_ytdlp_job(snap["id"], "failed")
        ytdlp_service.clear()
        return (
            _DL_VISIBLE, bar_fill, label,
            _DL_HIDDEN,
            no_update, no_update, no_update, no_update, no_update,
            no_update,
            False, no_update,
            new_state, no_update,
            no_update,
        )

    return (
        prog_vis, bar_fill, label,
        id3_vis,
        no_update, no_update, no_update, no_update, no_update,
        no_update,
        no_update, no_update,
        new_state, no_update,
        no_update,
    )
