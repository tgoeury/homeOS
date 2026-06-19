"""
HomeOS — modules/dashboard_layout.py
Layout Dash principal — tailles ×2, capteurs collapsibles par pièce,
nom machine dynamique, navigation corrigée.
"""

from dash import html, dcc
from modules.theme import CP, FONT_MONO, FONT_HUD, card_style, label_style, value_style, section_title_style, WORLDMAP_HEIGHT
from modules.sysinfo import SYSTEM_LABEL, HOST_INFO
from modules import timer_service
from modules.data_cache import data_cache
import config as CFG

# ROOMS résolu depuis config.py (clés de couleur → valeurs CP)
ROOMS = [
    (rid, rname, CP[accent_key],
     [(sid, lbl, dflt, CP[col_key], unit) for sid, lbl, dflt, col_key, unit in sensors])
    for rid, rname, accent_key, sensors in CFG.ROOMS
]

# IDs des capteurs plantes : tous les "plant" renseignés dans ZIGBEE_DEVICES
_PLANT_IDS = frozenset(
    cfg["plant"] for cfg in CFG.ZIGBEE_DEVICES.values() if "plant" in cfg
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _sec_title(text: str) -> html.Div:
    """Titre de section cyberpunk préfixé par '// ' jaune."""
    return html.Div([
        html.Span("// ", style={"color": CP["yellow"]}),
        text,
    ], style=section_title_style())


def _metric_card(label: str, value_id: str, default: str,
                 accent: str = CP["cyan"], unit: str = "", extra: dict = None) -> html.Div:
    """Carte métrique réutilisable : label + valeur dynamique + unité + tag OUTDATED."""
    _extra = {**(extra or {}), "position": "relative"}
    return html.Div([
        html.Div(label, style=label_style()),
        html.Div(default, id=value_id, style=value_style(color=accent)),
        html.Div(unit, style={
            "fontSize": "13px", "color": "rgba(0,229,255,0.35)",
            "fontFamily": FONT_MONO, "marginTop": "4px",
        }),
        html.Div(id=f"{value_id}-outdated-tag", style={"display": "none"}),
    ], style=card_style(accent=accent, extra=_extra))


def _bar_row(label: str, pct: int, color: str = CP["cyan"],
             val: str = "", bar_id: str = None, val_id: str = None) -> html.Div:
    """Ligne label + barre de progression + valeur textuelle (ex: CPU 42%)."""
    bar_style = {
        "height": "100%", "width": f"{pct}%", "background": color,
        "transition": "width .7s cubic-bezier(.4,0,.2,1)",
    }
    val_style = {
        "fontSize": "14px", "color": CP["text_dim"],
        "fontFamily": FONT_MONO, "minWidth": "52px", "textAlign": "right",
    }
    bar_inner = html.Div(id=bar_id, style=bar_style) if bar_id else html.Div(style=bar_style)
    val_el    = html.Span(val or f"{pct}%", id=val_id, style=val_style) if val_id \
                else html.Span(val or f"{pct}%", style=val_style)
    return html.Div([
        html.Span(label, style={
            "fontSize": "14px", "color": CP["text_dim"],
            "fontFamily": FONT_MONO, "minWidth": "80px",
        }),
        html.Div(bar_inner, style={
            "flex": "1", "height": "6px", "background": "rgba(255,255,255,0.06)",
        }),
        val_el,
    ], style={"display": "flex", "alignItems": "center", "gap": "10px", "marginBottom": "12px"})



# ── Topbar ────────────────────────────────────────────────────────────────────

def _topbar() -> html.Div:
    """Barre supérieure fixe : logo HomeOS, badges de statut des services, horloge."""
    os_label = HOST_INFO["os_name"].upper()
    return html.Div([
        html.Div([
            html.Div([
                html.Span("HOME"),
                html.Span("OS", style={"color": CP["yellow"]}),
            ], style={
                "fontSize": "28px", "fontWeight": "700",
                "letterSpacing": "4px", "color": CP["cyan"], "lineHeight": "1",
            }),
            html.Div(CFG.HOME_NAME.upper(), style={
                "fontSize": "12px", "letterSpacing": "3px",
                "color": CP["text_dim"], "fontFamily": FONT_MONO, "marginTop": "3px",
            }),
        ], style={"padding": "12px 20px", "borderRight": "1px solid rgba(0,229,255,0.1)",
                  "minWidth": "160px"}),

        # Badges statut
        html.Div([
            _status_badge("MQTT",    CP["red"], dot_id="badge-mqtt"),
            _status_badge("SENSORS", CP["red"], dot_id="badge-sensors"),
            _status_badge("METEO",   CP["red"], dot_id="badge-meteo"),
            _status_badge("PLEX",    CP["red"], dot_id="badge-plex"),
            _status_badge("DNS",     CP["red"], dot_id="badge-dns"),
            _status_badge("CHATBOT", CP["red"], dot_id="badge-chatbot"),
            _status_badge("LOGIC",   CP["red"], dot_id="badge-logic"),
            _status_badge("CONFORT", CP["red"], dot_id="badge-confort"),
            _status_badge("ENEDIS",  CP["red"], dot_id="badge-enedis"),
        ], style={"flex": "1", "display": "flex", "alignItems": "center", "gap": "20px",
                  "padding": "0 20px", "borderRight": "1px solid rgba(0,229,255,0.1)"}),

        # OS + Horloge
        html.Div([
            html.Div(id="topbar-clock", style={
                "fontSize": "32px", "fontWeight": "700", "fontFamily": FONT_MONO,
                "color": CP["yellow"], "letterSpacing": "3px", "lineHeight": "1",
            }),
            html.Div(id="topbar-date", style={
                "fontSize": "12px", "letterSpacing": "2px",
                "color": CP["text_dim"], "fontFamily": FONT_MONO, "marginTop": "3px",
            }),
        ], style={"padding": "10px 20px", "textAlign": "right"}),

    ], style={
        "display": "flex", "alignItems": "stretch",
        "borderBottom": "1px solid rgba(0,229,255,0.15)",
        "background": "rgba(6,8,16,0.97)", "flexShrink": "0",
    })


def _status_badge(label: str, color: str, dot_id: str = None) -> html.Div:
    """Pastille de statut colorée (dot + label) pour la topbar."""
    dot_kwargs = {"id": dot_id} if dot_id else {}
    return html.Div([
        html.Div(style={
            "width": "9px", "height": "9px", "borderRadius": "50%",
            "background": color, "flexShrink": "0",
        }, **dot_kwargs),
        html.Span(label, style={
            "fontSize": "13px", "letterSpacing": "2px",
            "fontFamily": FONT_MONO, "color": CP["text_dim"],
        }),
    ], style={"display": "flex", "alignItems": "center", "gap": "7px"})


# ── Navigation ────────────────────────────────────────────────────────────────

NAV_ITEMS = [
    ("accueil",   "🏠", "Accueil"),
    ("capteurs",  "🌡", "Capteurs"),
    ("meteo",     "☁",  "Météo"),
    ("musique",   "♪",  "Musique"),
    ("minuteurs", "⏱",  "Minuteurs"),
    ("confort",   "❄",  "Confort"),
    ("energie",   "⚡", "Énergie"),
    ("reseau",    "⬡",  "Réseau"),
    ("systeme",   "⚙",  "Système"),
    ("chatbot",   "💬", "Chatbot"),
]
PAGE_IDS = [n[0] for n in NAV_ITEMS]


def _nav() -> html.Div:
    """Barre de navigation horizontale avec un bouton par page (NAV_ITEMS)."""
    buttons = []
    for i, (tab_id, icon, label) in enumerate(NAV_ITEMS):
        buttons.append(html.Button(
            [
                html.Span(icon, style={"fontSize": "26px"}),
                html.Span(label, className="nav-label"),
            ],
            id=f"nav-{tab_id}",
            n_clicks=0,
            # Seul Accueil est actif au départ — le callback gère ensuite
            className="nav-btn" + (" nav-btn--active" if i == 0 else ""),
        ))
    return html.Div(buttons, style={
        "display": "flex", "alignItems": "stretch",
        "borderBottom": "2px solid rgba(0,229,255,0.12)",
        "background": "rgba(10,13,21,0.97)",
        "overflowX": "auto", "flexShrink": "0",
    })


# ── Page Accueil ──────────────────────────────────────────────────────────────

def _page_accueil() -> html.Div:
    """Page d'accueil : météo extérieure, métriques intérieures, lecteur miroir, journal, devices."""
    return html.Div([
        html.Div([
            # Colonne gauche : météo + lecteur empilés
            html.Div([
                # Carte météo extérieure
                html.Div([
                    html.Div(CFG.GEO_LABEL, style={
                        "fontSize": "13px", "letterSpacing": "3px",
                        "color": "rgba(255,230,0,0.6)", "fontFamily": FONT_MONO, "marginBottom": "4px",
                    }),
                    html.Div(id="home-temp-ext", style={
                        "fontSize": "64px", "fontWeight": "700",
                        "color": CP["cyan"], "fontFamily": FONT_HUD, "lineHeight": "1",
                    }),
                    html.Div(id="home-cond-ext", style={
                        "fontSize": "16px", "color": CP["yellow"], "letterSpacing": "3px",
                        "fontFamily": FONT_MONO, "margin": "4px 0 10px",
                    }),
                    html.Div([
                        _mini_meta("Ressenti",     "home-feels"),
                        _mini_meta("Vent",         "home-vent"),
                        _mini_meta("Humidité ext.", "home-hygro-ext"),
                        _mini_meta("UV",           None, "MOD."),
                    ], style={"display": "flex", "gap": "24px"}),
                ], style={
                    "background": CP["bg2"],
                    "border": f"1px solid {CP['border']}",
                    "borderTop": f"2px solid {CP['cyan']}",
                    "padding": "14px 20px",
                    "clipPath": "polygon(0 0,calc(100% - 18px) 0,100% 18px,100% 100%,0 100%)",
                }),

                # Barre lecteur (contrôles miroirs de l'onglet Musique)
                _player_card("home-", art_size=48, show_queue=False),

            ], style={"flex": "2", "display": "flex", "flexDirection": "column", "gap": "12px"}),

            # Métriques intérieures
            html.Div([
                _metric_card("Temp. Salon",  "home-temp-int", "--",  CP["cyan"],
                             "SALON · SNZB-02P",
                             extra={"marginBottom": "10px", "clipPath": "polygon(0 0,calc(100% - 12px) 0,100% 12px,100% 100%,0 100%)"}),
                _metric_card("Hygrométrie",  "home-hygro-int", "--",   CP["yellow"],
                             "SALON · SNZB-02P",
                             extra={"marginBottom": "10px", "clipPath": "polygon(0 0,calc(100% - 12px) 0,100% 12px,100% 100%,0 100%)"}),
                _metric_card("Luminosité",   "home-lux",      "--", CP["green"],
                             "BUREAU · pas de capteur lux",
                             extra={"clipPath": "polygon(0 0,calc(100% - 12px) 0,100% 12px,100% 100%,0 100%)"}),
            ], style={"flex": "1", "display": "flex", "flexDirection": "column"}),

        ], style={"display": "flex", "gap": "12px", "marginBottom": "12px"}),

        

        html.Div([
            html.Div([
                _sec_title("Journal système"),
                html.Div(id="home-log", style={
                    "fontFamily": FONT_MONO, "fontSize": "13px",
                    "display": "flex", "flexDirection": "column", "gap": "5px",
                }),
            ], style=card_style(accent=CP["cyan"], extra={"flex": "1"})),
            html.Div([
                _sec_title("Périphériques actifs"),
                html.Div(id="home-devices"),
            ], style=card_style(accent=CP["yellow"], extra={"flex": "1"})),
        ], style={"display": "flex", "gap": "12px"}),

        dcc.Interval(id="interval-main",  interval=CFG.INTERVAL_SENSORS_MS,     n_intervals=0),
        dcc.Interval(id="interval-clock", interval=CFG.INTERVAL_CLOCK_MS,     n_intervals=0),
        dcc.Interval(id="interval-meteo", interval=CFG.INTERVAL_WEATHER_MS,  n_intervals=0),
        dcc.Interval(id="interval-sys",   interval=CFG.INTERVAL_SYSTEM_MS,     n_intervals=0),

    ], id="page-accueil", style={"padding": "16px", "display": "flex",
                                   "flexDirection": "column", "gap": "0",
                                   "overflowY": "auto"})


def _mini_meta(label: str, id_: str | None, default: str = "--") -> html.Div:
    """Petit indicateur label+valeur pour la carte météo de l'accueil (ressenti, vent…)."""
    val = html.Span(id=id_, style={"fontSize": "16px", "color": CP["cyan"], "fontFamily": FONT_MONO}) \
          if id_ else html.Span(default, style={"fontSize": "16px", "color": CP["cyan"], "fontFamily": FONT_MONO})
    return html.Div([
        html.Span(label, style={"fontSize": "11px", "letterSpacing": "2px",
                                 "color": CP["text_dim"], "fontFamily": FONT_MONO, "display": "block"}),
        val,
    ])


# ── Page Capteurs — pièces collapsibles ───────────────────────────────────────

# ROOMS chargé depuis config.py via les imports en tête de fichier


def _plant_subsection(room_id: str, accent: str, plants: list) -> html.Div:
    """
    Sous-section collapsible des plantes imbriquée à l'intérieur d'un panneau de pièce.
    plants — liste de (sid, lbl, dflt, col, unit) filtrée sur les capteurs plantes.
    """
    n = len(plants)
    plant_cards = [_plant_card(sid, lbl, dflt, col) for sid, lbl, dflt, col, _ in plants]
    return html.Div([
        html.Button([
            html.Span("🌿  PLANTES", style={
                "fontSize": "13px", "fontWeight": "600", "letterSpacing": "3px",
                "color": accent, "fontFamily": FONT_MONO,
            }),
            html.Span("▼", id=f"plant-arrow-{room_id}", style={
                "fontSize": "12px", "color": accent, "marginLeft": "8px",
                "transition": "transform .2s",
            }),
            html.Span(f"// {n} plante(s)", style={
                "fontSize": "11px", "letterSpacing": "2px", "color": CP["text_dim"],
                "fontFamily": FONT_MONO, "marginLeft": "12px",
            }),
        ], id=f"plant-toggle-{room_id}", n_clicks=0, style={
            "width": "100%", "background": "transparent",
            "border": "none", "borderTop": f"1px solid {accent}33",
            "padding": "10px 18px", "textAlign": "left", "cursor": "pointer",
            "display": "flex", "alignItems": "center",
            "-webkit-tap-highlight-color": "transparent",
        }),
        html.Div(
            plant_cards,
            id=f"plant-content-{room_id}",
            style={
                "display": "grid",
                "gridTemplateColumns": f"repeat({min(n, 3)}, 1fr)",
                "gap": "10px", "padding": "10px 18px 14px 18px",
            },
        ),
    ])


def _room_panel(room_id: str, room_name: str, accent: str, sensors: list) -> html.Div:
    """
    Panneau collapsible de la pièce `room_id` pour l'onglet Capteurs.
    Les capteurs plantes (IDs dans `_PLANT_IDS`) sont rendus dans une sous-section
    collapsible dédiée, imbriquée en bas du panneau.
    """
    regular = [(sid, lbl, dflt, col, unit) for sid, lbl, dflt, col, unit in sensors
               if sid not in _PLANT_IDS]
    plants  = [(sid, lbl, dflt, col, unit) for sid, lbl, dflt, col, unit in sensors
               if sid in _PLANT_IDS]

    n_total = len(regular) + len(plants)

    sensor_cards = [
        _metric_card(lbl, sid, dflt, col, unit,
                     extra={"marginBottom": "8px",
                            "clipPath": "polygon(0 0,calc(100% - 10px) 0,100% 10px,100% 100%,0 100%)"})
        for sid, lbl, dflt, col, unit in regular
    ]

    content_children = []
    if sensor_cards:
        n_reg = len(sensor_cards)
        content_children.append(html.Div(sensor_cards, style={
            "display": "grid",
            "gridTemplateColumns": f"repeat({min(n_reg, 3)}, 1fr)",
            "gap": "10px", "padding": "14px 18px 4px 18px",
        }))
    if plants:
        content_children.append(_plant_subsection(room_id, accent, plants))

    return html.Div([
        html.Button([
            html.Span(room_name.upper(), style={
                "fontSize": "18px", "fontWeight": "700", "letterSpacing": "4px",
                "color": accent, "fontFamily": FONT_HUD,
            }),
            html.Span("▼", id=f"room-arrow-{room_id}", style={
                "fontSize": "14px", "color": accent, "marginLeft": "10px",
                "transition": "transform .2s",
            }),
            html.Span(f"// {n_total} capteur(s)", style={
                "fontSize": "12px", "letterSpacing": "2px", "color": CP["text_dim"],
                "fontFamily": FONT_MONO, "marginLeft": "16px",
            }),
        ], id=f"room-toggle-{room_id}", n_clicks=0, style={
            "width": "100%", "background": "transparent",
            "border": "none", "borderBottom": f"1px solid {accent}44",
            "padding": "14px 18px", "textAlign": "left", "cursor": "pointer",
            "display": "flex", "alignItems": "center",
            "-webkit-tap-highlight-color": "transparent",
        }),
        html.Div(
            content_children,
            id=f"room-content-{room_id}",
            style={"display": "grid", "gridTemplateColumns": "1fr", "gap": "0"},
        ),
    ], style={
        "background": CP["bg2"],
        "border": "1px solid rgba(0,229,255,0.1)",
        "borderLeft": f"3px solid {accent}",
        "marginBottom": "10px",
    })


def _page_capteurs() -> html.Div:
    """Page Capteurs : panneaux collapsibles par pièce + graphiques historique 24h."""
    return html.Div([
        _sec_title("Capteurs environnement — par pièce"),
        *[_room_panel(rid, rname, accent, sensors)
          for rid, rname, accent, sensors in ROOMS],

        # Graphe historique global
        html.Div([
            _sec_title("Historique 24h — Température"),
            dcc.Graph(id="env-graph", config={"displayModeBar": False},
                      style={"height": "180px"}),
        ], style=card_style(accent=CP["cyan"], extra={"marginBottom": "10px"})),

        html.Div([
            _sec_title("Historique 24h — Humidité"),
            dcc.Graph(id="env-graph-humidity", config={"displayModeBar": False},
                      style={"height": "180px"}),
        ], style=card_style(accent=CP["cyan"], extra={"marginBottom": "10px"})),

        # Découverte Zigbee — appareils non encore mappés dans config.py
        html.Div([
            _sec_title("Appareils Zigbee — découverte"),
            html.Div(
                "// EN ATTENTE DU BROKER MQTT...",
                id="zigbee-discovery",
                style={"fontFamily": FONT_MONO, "fontSize": "13px",
                       "color": CP["text_dim"]},
            ),
        ], style=card_style(accent=CP["yellow"])),

    ], id="page-capteurs", style={"padding": "16px", "display": "none",
                                    "flexDirection": "column", "gap": "0",
                                    "overflowY": "auto"})


# ── Page Plantes — humidité par pièce ────────────────────────────────────────

def _plant_card(plant_id: str, name: str, default: str, accent: str) -> html.Div:
    """
    Carte individuelle pour un capteur d'humidité plante.
    Affiche le nom, la valeur courante et une barre de progression colorée
    selon les seuils ALERT_HUMI_MIN / ALERT_HUMI_MAX de config.py.
    """
    return html.Div([
        html.Div(name, style=label_style()),
        html.Div(default, id=plant_id, style=value_style(color=accent)),
        # Barre d'humidité (0–100 %)
        html.Div(
            html.Div(
                id=f"{plant_id}-bar",
                style={"height": "100%", "width": "0%",
                       "background": accent,
                       "transition": "width .7s cubic-bezier(.4,0,.2,1)"},
            ),
            style={"height": "5px", "background": "rgba(255,255,255,0.06)", "marginTop": "8px"},
        ),
        html.Div([
            html.Span(f"{CFG.ALERT_HUMI_MIN}%", style={
                "fontSize": "11px", "color": "rgba(255,255,255,0.2)",
                "fontFamily": FONT_MONO,
            }),
            html.Span(f"{CFG.ALERT_HUMI_MAX}%", style={
                "fontSize": "11px", "color": "rgba(255,255,255,0.2)",
                "fontFamily": FONT_MONO,
            }),
        ], style={"display": "flex", "justifyContent": "space-between", "marginTop": "3px"}),
        html.Div("SGS01Z · Zigbee2MQTT", style={
            "fontSize": "11px", "color": "rgba(0,229,255,0.3)",
            "fontFamily": FONT_MONO, "marginTop": "4px",
        }),
        html.Div(id=f"{plant_id}-outdated-tag", style={"display": "none"}),
    ], style=card_style(accent=accent, extra={
        "marginBottom": "8px",
        "clipPath": "polygon(0 0,calc(100% - 10px) 0,100% 10px,100% 100%,0 100%)",
        "position": "relative",
    }))


# ── Page Météo ────────────────────────────────────────────────────────────────

def _page_meteo() -> html.Div:
    """Page Météo : métriques courantes, prévisions 7 jours, courbe horaire, comparaison int/ext."""
    return html.Div([
        html.Div([
            _metric_card("Température",  "m-temp",  "--°C",    CP["cyan"]),
            _metric_card("Humidité",     "m-hygro", "--%",     CP["yellow"]),
            _metric_card("Vent",         "m-wind",  "-- km/h", CP["orange"]),
            _metric_card("Pression",     "m-press", "-- hPa",  CP["green"]),
        ], style={"display": "grid", "gridTemplateColumns": "1fr 1fr 1fr 1fr",
                  "gap": "10px", "marginBottom": "12px"}),

        html.Div([
            _sec_title("Prévisions 7 jours"),
            html.Div(id="m-forecast",
                     style={"display": "grid", "gridTemplateColumns": "repeat(7,1fr)", "gap": "8px"}),
        ], style=card_style(accent=CP["cyan"], extra={"marginBottom": "12px"})),

        html.Div([
            _sec_title("Courbe horaire — aujourd'hui"),
            html.Div("ACQUISITION SIGNAL MÉTÉO...", id="m-loading", style={
                "textAlign": "center", "padding": "24px",
                "fontFamily": FONT_MONO, "fontSize": "14px",
                "color": CP["text_dim"], "letterSpacing": "3px",
            }),
            dcc.Graph(id="m-graph", config={"displayModeBar": False},
                      style={"height": "180px", "display": "none"}),
        ], style=card_style(accent=CP["cyan"], extra={"marginBottom": "12px"})),

        html.Div([
            _sec_title("Comparaison intérieur / extérieur"),
            html.Div(id="m-compare"),
        ], style=card_style(accent=CP["green"])),

    ], id="page-meteo", style={"padding": "16px", "display": "none",
                                "flexDirection": "column", "gap": "0",
                                "overflowY": "auto"})


# ── Page Musique ──────────────────────────────────────────────────────────────

def _player_card(prefix: str = "", art_size: int = 96, show_queue: bool = False) -> html.Div:
    """
    Bloc lecteur réutilisable (pochette + titre + barre + boutons).
    prefix=""      → onglet Musique (IDs canoniques : btn-play, mu-prog-fill…)
    prefix="home-" → onglet Accueil (IDs miroirs : home-btn-play…)
    show_queue=True ajoute le carrousel de file d'attente (onglet Musique uniquement).
    """
    pfx = prefix
    art_wrap = {
        "width": f"{art_size}px", "height": f"{art_size}px", "flexShrink": "0",
        "background": CP["bg3"],
        "border": f"1px solid {CP['border']}",
        "display": "flex", "alignItems": "center", "justifyContent": "center",
        "overflow": "hidden",
        "clipPath": "polygon(0 0,calc(100% - 8px) 0,100% 8px,100% 100%,8px 100%,0 calc(100% - 8px))",
    }
    title_size = "20px" if art_size >= 96 else "16px"
    info_children = [
        html.Div(id=f"{pfx}mu-title", children="Aucune lecture", style={
            "fontSize": title_size, "fontWeight": "600", "color": CP["cyan"],
            "overflow": "hidden", "whiteSpace": "nowrap", "textOverflow": "ellipsis",
        }),
        html.Div(id=f"{pfx}mu-artist", children="PLEX · EN ATTENTE", style={
            "fontSize": "13px", "color": CP["text_dim"],
            "fontFamily": FONT_MONO, "marginTop": "3px",
        }),
    ]
    # L'album n'est affiché que dans le lecteur plein format (onglet Musique)
    if not prefix:
        info_children.append(html.Div(id="mu-album", children="", style={
            "fontSize": "11px", "color": "rgba(0,229,255,0.35)",
            "fontFamily": FONT_MONO,
        }))
    info_children += [
        html.Div(
            html.Div(id=f"{pfx}mu-prog-fill",
                     style={"height": "100%", "width": "0%",
                            "background": CP["cyan"], "transition": "width .3s ease"}),
            style={"height": "4px", "background": "rgba(255,255,255,0.08)", "marginTop": "12px"},
        ),
        html.Div([
            html.Span("0:00", id=f"{pfx}mu-pos"),
            html.Span("0:00", id=f"{pfx}mu-dur"),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "fontSize": "11px", "color": CP["text_dim"],
                  "fontFamily": FONT_MONO, "marginTop": "4px"}),
        html.Div([
            html.Button("⏮", id=f"{pfx}btn-prev", n_clicks=0, className="ctrl-btn"),
            html.Button("▶", id=f"{pfx}btn-play", n_clicks=0, className="ctrl-btn ctrl-btn--play"),
            html.Button("⏭", id=f"{pfx}btn-next", n_clicks=0, className="ctrl-btn"),
        ], style={"display": "flex", "gap": "8px", "marginTop": "12px"}),
    ]
    row = html.Div([
        html.Div(
            html.Div("♪", style={"fontSize": f"{art_size // 3}px", "color": "rgba(0,229,255,0.2)"}),
            id=f"{pfx}mu-art",
            style=art_wrap,
        ),
        html.Div(info_children, style={"flex": "1", "minWidth": "0"}),
    ], style={"display": "flex", "alignItems": "center", "gap": "18px"})

    card_children = [row]
    if show_queue:
        card_children.append(
            html.Div(id="mu-queue-carousel", className="plex-carousel",
                     style={"marginTop": "10px", "paddingTop": "10px",
                            "borderTop": f"1px solid {CP['border']}"}),
        )
    return html.Div(card_children, style={
        "background": CP["bg2"], "border": f"1px solid {CP['border']}",
        "borderLeft": f"3px solid {CP['cyan']}", "padding": "16px",
    })


def _ytdlp_section() -> html.Div:
    """Section téléchargement audio yt-dlp intégrée à l'onglet Musique."""
    _input_style = {
        "background": "rgba(0,229,255,0.05)",
        "border":     f"1px solid {CP['border']}",
        "color":      CP["text"],
        "fontFamily": FONT_MONO,
        "fontSize":   "13px",
        "padding":    "7px 10px",
        "outline":    "none",
        "borderRadius": "0",
    }
    _tag_row = {
        "display": "flex", "alignItems": "center",
        "gap": "10px", "marginBottom": "8px",
    }
    _lbl = {**label_style(), "minWidth": "130px"}

    return html.Div([
        _sec_title("Data dump — Téléchargement audio"),

        # ── URL + bouton ──────────────────────────────────────────────────────
        html.Div([
            dcc.Input(
                id="dl-url",
                type="url",
                placeholder="https://youtube.com/…",
                debounce=False,
                className="plex-search-input",
                style={"flex": "1"},
            ),
            html.Button(
                "▼  TÉLÉCHARGER",
                id="dl-start",
                n_clicks=0,
                className="ctrl-btn ctrl-btn--play",
                style={"fontSize": "12px", "letterSpacing": "2px", "flexShrink": "0"},
            ),
        ], style={"display": "flex", "gap": "8px", "alignItems": "center",
                  "marginBottom": "12px"}),

        # ── Paramètres ────────────────────────────────────────────────────────
        html.Div([
            html.Div([
                html.Span("Chapitres :", style=label_style()),
                html.Button("PAS DE CHAPITRES", id="dl-no-chapters", n_clicks=0,
                            className="ctrl-btn ctrl-btn--play",
                            style={"fontSize": "11px", "letterSpacing": "1px"}),
                html.Button("CHAPITRES", id="dl-chapters", n_clicks=0,
                            className="ctrl-btn",
                            style={"fontSize": "11px", "letterSpacing": "1px"}),
            ], style={"display": "flex", "alignItems": "center", "gap": "6px"}),

            html.Div([
                html.Span("Qualité :", style=label_style()),
                dcc.Input(
                    id="dl-quality",
                    type="number",
                    min=1, max=10, step=1, value=8,
                    style={**_input_style, "width": "58px", "textAlign": "center"},
                ),
                html.Span("/ 10", style={
                    "fontSize": "12px", "color": CP["text_dim"], "fontFamily": FONT_MONO,
                }),
            ], style={"display": "flex", "alignItems": "center", "gap": "6px"}),

            html.Div([
                html.Span("Format :", style=label_style()),
                html.Button("MP3", id="dl-fmt-mp3", n_clicks=0,
                            className="ctrl-btn ctrl-btn--play",
                            style={"fontSize": "11px", "letterSpacing": "1px"}),
                html.Button("FLAC", id="dl-fmt-flac", n_clicks=0,
                            className="ctrl-btn",
                            style={"fontSize": "11px", "letterSpacing": "1px"}),
            ], style={"display": "flex", "alignItems": "center", "gap": "6px"}),
        ], style={"display": "flex", "gap": "24px", "alignItems": "center",
                  "flexWrap": "wrap", "marginBottom": "14px"}),

        # ── Barre de progression ──────────────────────────────────────────────
        html.Div([
            html.Div(
                html.Div(id="dl-bar-fill",
                         style={"height": "100%", "width": "0%",
                                "background": CP["cyan"],
                                "transition": "width .3s ease"}),
                style={"height": "6px",
                       "background": "rgba(255,255,255,0.06)",
                       "marginBottom": "6px"},
            ),
            html.Div("Initialisation…", id="dl-bar-label", style={
                "fontSize": "12px", "color": CP["text_dim"],
                "fontFamily": FONT_MONO, "letterSpacing": "1px",
            }),
        ], id="dl-progress-wrap",
           style={"display": "none", "marginBottom": "14px"}),

        # ── Éditeur de tags ID3 ───────────────────────────────────────────────
        html.Div([
            html.Div([
                html.Span("// ", style={"color": CP["yellow"]}),
                html.Span("Tags ID3", style={
                    "color": CP["text_dim"], "letterSpacing": "2px",
                }),
            ], style={"fontFamily": FONT_MONO, "fontSize": "13px",
                      "marginBottom": "12px"}),

            html.Div([
                html.Span("Artiste", style=_lbl),
                dcc.Input(id="dl-tag-artist", type="text",
                          placeholder="ex : Pink Floyd", debounce=False,
                          style={"flex": "1", **_input_style}),
            ], style=_tag_row),

            html.Div([
                html.Span("Artiste album", style=_lbl),
                dcc.Input(id="dl-tag-albumartist", type="text",
                          placeholder="ex : Pink Floyd", debounce=False,
                          style={"flex": "1", **_input_style}),
            ], style=_tag_row),

            html.Div([
                html.Span("Album", style=_lbl),
                dcc.Input(id="dl-tag-album", type="text",
                          placeholder="ex : The Wall", debounce=False,
                          style={"flex": "1", **_input_style}),
            ], style=_tag_row),

            html.Div([
                html.Span("Année", style=_lbl),
                dcc.Input(id="dl-tag-year", type="text",
                          placeholder="ex : 1979", debounce=False,
                          style={"width": "90px", **_input_style}),
            ], style=_tag_row),

            # Titre — masqué si plusieurs fichiers (chapitres)
            html.Div([
                html.Span("Titre (piste)", style=_lbl),
                dcc.Input(id="dl-tag-title", type="text",
                          placeholder="ex : Comfortably Numb", debounce=False,
                          style={"flex": "1", **_input_style}),
            ], id="dl-title-row",
               style={**_tag_row, "display": "flex"}),

            html.Div([
                html.Button("■  ANNULER", id="dl-cancel", n_clicks=0,
                            className="ctrl-btn",
                            style={"fontSize": "12px", "letterSpacing": "2px"}),
                html.Button("✓  ENREGISTRER", id="dl-ok", n_clicks=0,
                            className="ctrl-btn ctrl-btn--play",
                            style={"fontSize": "12px", "letterSpacing": "2px"}),
            ], style={"display": "flex", "gap": "10px",
                      "justifyContent": "flex-end", "marginTop": "12px"}),
        ], id="dl-id3-wrap", style={"display": "none"}),

        # ── Stores et interval ────────────────────────────────────────────────
        dcc.Store(id="dl-params-store",   data={"chapters": False, "format": "mp3"}),
        dcc.Store(id="dl-trigger-store", data={}),
        dcc.Store(id="dl-action-store",  data={}),
        dcc.Store(id="dl-state-store",   data={}),
        dcc.Store(id="dl-files-store",   data=[]),
        dcc.Interval(id="interval-ytdlp", interval=1_000, n_intervals=0),

    ], style=card_style(accent=CP["yellow"]))


def _page_musique() -> html.Div:
    """Page Musique : lecteur Plex avec file d'attente, recherche, artistes récents, playlists."""
    return html.Div([

        # ── 1. Lecteur ──────────────────────────────────────────────────────────
        _player_card("", art_size=96, show_queue=True),

        html.Div(id="mu-ctrl-dummy",  style={"display": "none"}),
        html.Div(id="mu-audio-ctrl", style={"display": "none"}),
        dcc.Store(id="mu-local-track"),
        dcc.Store(id="mu-queue"),
        dcc.Store(id="mu-plex-progress"),
        dcc.Store(id="mu-nav-stack", data=[]),
        html.Audio(id="mu-audio", src="", autoPlay=False,
                   controls=False, preload="auto", style={"display": "none"}),

        # ── 2. Recherche ────────────────────────────────────────────────────────
        html.Div([
            _sec_title("Recherche"),
            html.Div([
                dcc.Input(
                    id="mu-search-input",
                    type="text",
                    placeholder="Titre, artiste, album…",
                    debounce=False,
                    n_submit=0,
                    className="plex-search-input",
                    style={"flex": "1"},
                ),
                html.Button("OK", id="mu-search-btn", n_clicks=0, className="ctrl-btn",
                            style={"fontSize": "13px", "letterSpacing": "2px"}),
            ], style={"display": "flex", "gap": "8px", "alignItems": "center",
                      "marginBottom": "10px"}),
            html.Button(
                "← Retour", id="mu-back-btn", n_clicks=0,
                className="ctrl-btn",
                style={"display": "none", "fontSize": "13px",
                       "letterSpacing": "2px", "marginBottom": "10px"},
            ),
            html.Div(id="mu-search-results"),
        ], style=card_style(accent=CP["yellow"])),

        # ── 3. Artistes récents ─────────────────────────────────────────────────
        html.Div([
            _sec_title("Artistes récents"),
            html.Div(id="mu-artists", className="plex-carousel"),
        ], style=card_style(accent=CP["cyan"])),

        # ── 4. Playlists ────────────────────────────────────────────────────────
        html.Div([
            _sec_title("Playlists"),
            html.Div(id="mu-playlists", className="plex-carousel"),
        ], style=card_style(accent=CP["orange"])),

        dcc.Interval(id="interval-plex-shelf", interval=60_000, n_intervals=0),

        # ── 5. Data dump ────────────────────────────────────────────────────────
        _ytdlp_section(),

    ], id="page-musique", style={"padding": "16px", "display": "none",
                                  "flexDirection": "column", "gap": "10px",
                                  "overflowY": "auto"})


# ── Page Minuteurs ────────────────────────────────────────────────────────────

def _page_minuteurs() -> html.Div:
    """
    Page de gestion des minuteurs.
    Trois zones : saisie H:M:S + nom, carousel de presets, liste des timers actifs.
    Un modal fixe s'affiche quand un timer expire ; l'alarme est un beep WAV généré
    en mémoire (timer_service.ALARM_DATA_URI), joué via clientside callback.
    """
    return html.Div([
        dcc.Interval(id="interval-minuteurs", interval=CFG.INTERVAL_MINUTEURS_MS, n_intervals=0),
        dcc.Store(id="min-action-store", data=0),
        dcc.Store(id="min-expired-store", data=[]),
        html.Div(id="min-alarm-ctrl", style={"display": "none"}),
        html.Audio(
            id="min-alarm",
            src=timer_service.ALARM_DATA_URI,
            autoPlay=False,
            loop=True,
            style={"display": "none"},
        ),

        # ── Modal expiration ─────────────────────────────────────────────────
        html.Div(
            html.Div([
                html.Div("⚠", className="blink", style={
                    "fontSize": "48px", "color": CP["red"],
                    "marginBottom": "8px", "lineHeight": "1",
                }),
                html.Div("// MINUTEUR TERMINÉ //", style={
                    "fontSize": "18px", "fontWeight": "700",
                    "color": CP["yellow"], "letterSpacing": "4px",
                    "fontFamily": FONT_HUD, "marginBottom": "16px",
                }),
                html.Div(id="min-modal-names", style={
                    "fontSize": "20px", "color": CP["cyan"],
                    "fontFamily": FONT_MONO, "letterSpacing": "2px",
                    "marginBottom": "28px",
                }),
                html.Button(
                    "■  OK — COUPER ALARME",
                    id="min-modal-ok",
                    n_clicks=0,
                    className="ctrl-btn ctrl-btn--play",
                    style={
                        "fontSize": "15px", "letterSpacing": "3px",
                        "background": CP["red"], "color": CP["bg0"],
                        "border": f"1px solid {CP['red']}",
                        "padding": "12px 28px",
                    },
                ),
            ], className="timer-modal"),
            id="min-modal-overlay",
            className="timer-modal-overlay",
            style={"display": "none"},
        ),

        # ── Section 1 : Saisie ───────────────────────────────────────────────
        html.Div([
            _sec_title("Nouveau minuteur"),

            # Picker H : M : S
            html.Div([
                html.Div([
                    html.Div("HH", className="timer-picker-label"),
                    dcc.Input(
                        id="min-h", type="number", min=0, max=99, value=0,
                        className="timer-number-input",
                    ),
                ], className="timer-picker-col"),
                html.Div(":", className="timer-separator"),
                html.Div([
                    html.Div("MM", className="timer-picker-label"),
                    dcc.Input(
                        id="min-m", type="number", min=0, max=59, value=0,
                        className="timer-number-input",
                    ),
                ], className="timer-picker-col"),
                html.Div(":", className="timer-separator"),
                html.Div([
                    html.Div("SS", className="timer-picker-label"),
                    dcc.Input(
                        id="min-s", type="number", min=0, max=59, value=0,
                        className="timer-number-input",
                    ),
                ], className="timer-picker-col"),
            ], className="timer-picker-group"),

            # Nom + bouton Lancer
            html.Div([
                dcc.Input(
                    id="min-name", type="text",
                    placeholder=timer_service.next_timer_name(),
                    className="plex-search-input",
                    style={"flex": "1"},
                ),
                html.Button(
                    "▶  LANCER",
                    id="min-start-btn",
                    n_clicks=0,
                    className="ctrl-btn ctrl-btn--play",
                    style={"fontSize": "14px", "letterSpacing": "3px"},
                ),
            ], style={
                "display": "flex", "gap": "10px",
                "alignItems": "center", "marginTop": "16px",
            }),
        ], style=card_style(accent=CP["cyan"])),

        # ── Section 2 : Presets fréquents ────────────────────────────────────
        html.Div([
            _sec_title("Minuteurs fréquents"),
            html.Div(id="min-presets", className="plex-carousel"),
        ], style=card_style(accent=CP["yellow"])),

        # ── Section 3 : Minuteurs en cours ───────────────────────────────────
        html.Div([
            _sec_title("En cours"),
            html.Div(id="min-list"),
        ], style=card_style(accent=CP["cyan"])),

    ], id="page-minuteurs", style={
        "padding": "16px", "display": "none",
        "flexDirection": "column", "gap": "12px", "overflowY": "auto",
    })


# ── Page Confort — optimisation climatique ────────────────────────────────────

def _page_confort() -> html.Div:
    """
    Contrôle/affichage de l'algorithme d'optimisation climatique
    (modules/comfort_engine.py) : plages de confort par pièce + déclenchement
    d'une inférence ("Calculer") affichant les actions recommandées
    (fenêtres, volets, chauffage/climatisation).
    """
    _DEFAULT_RANGE = [18, 24]

    def _slider_value(room_id: str) -> list[int]:
        entry = data_cache.read(f"confort.range.{room_id}")
        if entry and isinstance(entry["value"], list) and len(entry["value"]) == 2:
            return entry["value"]
        return _DEFAULT_RANGE

    room_cards = [
        html.Div([
            html.Div(room_name.upper(), style={
                "fontSize": "16px", "fontWeight": "700", "letterSpacing": "4px",
                "color": accent, "fontFamily": FONT_HUD, "marginBottom": "18px",
            }),
            html.Div("Plage de confort", style=label_style()),
            html.Div(
                dcc.RangeSlider(
                    id=f"confort-range-{room_id}",
                    min=4, max=30, step=2,
                    value=_slider_value(room_id),
                    marks={t: f"{t}°" for t in range(4, 31, 2)},
                    tooltip={"placement": "bottom", "always_visible": True},
                    allowCross=False,
                ),
                className="confort-slider-wrap",
                style={"--accent": accent},
            ),
        ], style=card_style(accent=accent))
        for room_id, room_name, accent, _ in ROOMS
    ]

    return html.Div([
        # En-tête — titre + déclencheur d'inférence
        html.Div([
            html.Div([
                _sec_title("Optimisation du confort"),
                html.Button("CALCULER", id="confort-calc-btn", n_clicks=0,
                             className="ctrl-btn ctrl-btn--play",
                             style={"fontSize": "14px", "letterSpacing": "3px"}),
            ], style={"display": "flex", "justifyContent": "space-between",
                      "alignItems": "center"}),
            html.Div(
                "// EN ATTENTE DE CALCUL — appuyez sur CALCULER pour générer les recommandations //",
                id="confort-instructions",
                style={
                    "fontFamily": FONT_MONO, "fontSize": "13px",
                    "color": CP["text_dim"], "marginTop": "14px",
                    "display": "flex", "flexDirection": "column", "gap": "6px",
                },
            ),
        ], style=card_style(accent=CP["cyan"], extra={"marginBottom": "12px"})),

        # Plages de confort par pièce
        html.Div(room_cards, style={
            "display": "grid", "gridTemplateColumns": "1fr 1fr",
            "gap": "12px",
        }),

        # Store silencieux — sortie fictive du callback de persistance des plages
        dcc.Store(id="confort-persist-store", storage_type="memory"),
        # Store intermédiaire — déclenche l'inférence après avoir affiché "en cours"
        dcc.Store(id="confort-calc-store",   storage_type="memory"),

    ], id="page-confort", style={"padding": "16px", "display": "none",
                                   "flexDirection": "column", "gap": "12px",
                                   "overflowY": "auto"})


# ── Page Énergie ─────────────────────────────────────────────────────────────

def _page_energie() -> html.Div:
    """
    Consommation électrique Enedis (via conso.boris.sh).
    Stat cards : hier / mois en cours / mois précédent.
    Graphique   : barplot 30 jours avec code couleur au-dessus/en-dessous du 75e percentile.
    Toggle      : affichage en kWh ou en €.
    """
    def _stat_card(label: str, comp_id: str, accent: str) -> html.Div:
        return html.Div([
            html.Div(label, style=label_style()),
            html.Div("—", id=comp_id, style={
                "fontSize": "28px", "fontWeight": "700",
                "color": accent, "fontFamily": FONT_HUD,
                "lineHeight": "1.2", "marginTop": "6px",
            }),
        ], style=card_style(accent=accent))

    return html.Div([
        # ── En-tête ──────────────────────────────────────────────────────────
        html.Div([
            html.Div([
                _sec_title("Consommation électrique"),
                html.Div(id="energie-status", style={
                    "fontFamily": FONT_MONO, "fontSize": "12px",
                    "color": CP["text_dim"], "letterSpacing": "1px",
                }),
            ]),
            html.Button(
                "AFFICHER EN €",
                id="energie-unit-btn",
                n_clicks=0,
                className="ctrl-btn",
                style={"fontSize": "13px", "letterSpacing": "2px"},
            ),
        ], style={
            "display": "flex", "justifyContent": "space-between",
            "alignItems": "flex-start",
            **card_style(accent=CP["yellow"], extra={"marginBottom": "0"}),
        }),

        # ── Stat cards : Hier / Ce mois / Mois précédent ──────────────────
        html.Div([
            _stat_card("Consommation hier",            "energie-hier",   CP["cyan"]),
            _stat_card("Ce mois (cumulé)",             "energie-mois",   CP["green"]),
            _stat_card("Mois précédent (total)",       "energie-prev",   CP["yellow"]),
        ], style={"display": "grid", "gridTemplateColumns": "1fr 1fr 1fr", "gap": "12px"}),

        # ── Graphique 30 jours ────────────────────────────────────────────
        html.Div([
            dcc.Graph(
                id="energie-graph",
                config={"displayModeBar": False},
                style={"height": "280px"},
            ),
        ], style=card_style(accent=CP["cyan"])),

    ], id="page-energie", style={
        "padding": "16px", "display": "none",
        "flexDirection": "column", "gap": "12px", "overflowY": "auto",
    })


# ── Page Réseau ───────────────────────────────────────────────────────────────

def _page_reseau() -> html.Div:
    """Page Réseau : devices LAN (nmap), stats DNS NextDNS, NAS Synology, carte mondiale du trafic."""
    return html.Div([
        # ── Métriques ───────────────────────────────────────────────────────────
        html.Div([
            _metric_card("Devices actifs",    "r-devices", "—",    CP["cyan"]),
            _metric_card("DNS bloquées 24 h", "r-blocked", "—",    CP["yellow"], "NextDNS"),
            _metric_card("Taux de blocage",   "r-rate",    "—",    CP["green"]),
        ], style={"display": "grid", "gridTemplateColumns": "1fr 1fr 1fr",
                  "gap": "10px", "marginBottom": "10px"}),

        # ── Devices LAN + NAS côte à côte ──────────────────────────────────────
        html.Div([
            html.Div([
                _sec_title("Périphériques réseau"),
                html.Div(id="r-table"),
            ], style=card_style(accent=CP["cyan"])),

            # ── NAS Synology ──────────────────────────────────────────────────
            html.Div([
                _sec_title("NAS — Stockage"),
                html.Div(id="nas-volumes", style={"marginBottom": "12px"}),
                _sec_title("NAS — Disques"),
                html.Div(id="nas-disks"),
                html.Div(
                    "OUTDATED",
                    id="nas-outdated-tag",
                    style={"display": "none"},
                ),
            ], style={
                **card_style(accent=CP["orange"]),
                "position": "relative",
            }),
        ], style={"display": "grid", "gridTemplateColumns": "3fr 2fr",
                  "gap": "10px", "marginBottom": "10px"}),

        # ── Carte monde ─────────────────────────────────────────────────────────
        html.Div([
            _sec_title("Destination du trafic"),
            html.Div("Pays de destination de votre trafic Internet (24 h).", style={
                "fontSize": "13px", "color": CP["text_dim"],
                "fontFamily": FONT_MONO, "marginBottom": "8px",
            }),
            dcc.Graph(
                id="r-worldmap",
                config={"displayModeBar": False},
                style={"height": f"{WORLDMAP_HEIGHT}px"},
            ),
        ], style=card_style(accent=CP["yellow"])),

        dcc.Interval(id="interval-reseau", interval=60_000, n_intervals=0),

    ], id="page-reseau", style={"padding": "16px", "display": "none",
                                  "flexDirection": "column", "gap": "0",
                                  "overflowY": "auto"})


# ── Page Système ──────────────────────────────────────────────────────────────

def _page_systeme() -> html.Div:
    """Page Système : ressources (CPU/RAM/disque/temp), services systemctl, uptime, alertes ML."""
    host_label = SYSTEM_LABEL
    return html.Div([
        # Nom de la machine en titre
        html.Div([
            html.Span("// ", style={"color": CP["yellow"]}),
            html.Span(f"Machine : ", style={"color": CP["text_dim"]}),
            html.Span(host_label, style={"color": CP["cyan"]}),
        ], style={
            "fontSize": "15px", "letterSpacing": "3px", "fontFamily": FONT_MONO,
            "marginBottom": "14px",
        }),

        html.Div([
            # Ressources système
            html.Div([
                _sec_title(f"Ressources — {HOST_INFO['hostname']}"),
                _bar_row("CPU",   0,  CP["cyan"],   "0%",      "bar-cpu",  "val-cpu"),
                _bar_row("RAM",   0,  CP["yellow"], "0%",      "bar-ram",  "val-ram"),
                _bar_row("DISQUE",0,  CP["green"],  "0%",      "bar-disk", "val-disk"),
                _bar_row("TEMP",  0,  CP["orange"], "-- °C",   "bar-temp", "val-temp"),
                html.Div(id="sys-net", style={
                    "fontSize": "13px", "color": CP["text_dim"],
                    "fontFamily": FONT_MONO, "marginTop": "4px",
                }),
                html.Div(id="sys-uptime-res", style={
                    "fontSize": "13px", "color": CP["text_dim"],
                    "fontFamily": FONT_MONO, "marginTop": "4px",
                }),
            ], style=card_style(accent=CP["cyan"])),

            # Services
            html.Div([
                _sec_title("Services actifs"),
                html.Div(id="sys-services"),
            ], style=card_style(accent=CP["yellow"])),
        ], style={"display": "grid", "gridTemplateColumns": "1fr 1fr",
                  "gap": "10px", "marginBottom": "12px"}),

        # Compteurs
        html.Div([
            _metric_card("Uptime",         "sys-uptime",  "--:--:--", CP["cyan"]),
            _metric_card("Messages MQTT",  "sys-mqtt",    "0",        CP["yellow"]),
            _metric_card("Points InfluxDB","sys-influx",  "0",        CP["green"]),
        ], style={"display": "grid", "gridTemplateColumns": "1fr 1fr 1fr",
                  "gap": "10px", "marginBottom": "12px"}),

        # ML
        html.Div([
            _sec_title("Alertes ML — Isolation Forest"),
            html.Div(id="sys-ml"),
        ], style=card_style(accent=CP["red"])),

    ], id="page-systeme", style={"padding": "16px", "display": "none",
                                   "flexDirection": "column", "gap": "0",
                                   "overflowY": "auto"})


# ── Page Chatbot ───────────────────────────────────────────────────────────────

def _page_chatbot() -> html.Div:
    """Page Chatbot : interface Synology Chat (webhook entrant + saisie manuelle)."""
    return html.Div([
        dcc.Interval(id="interval-chatbot", interval=CFG.INTERVAL_CHATBOT_MS, n_intervals=0),
        dcc.Store(id="chat-store", data=0),

        # En-tête
        html.Div([
            html.Div([
                html.Span("// ", style={"color": CP["yellow"]}),
                html.Span("Synology Chat", style={"color": CP["text_dim"]}),
                html.Span(" // ", style={"color": CP["yellow"]}),
                html.Span("Bot Interface", style={"color": CP["cyan"]}),
            ], style={
                "fontSize": "15px", "letterSpacing": "3px",
                "fontFamily": FONT_MONO,
            }),
            html.Button([
                html.Span("⟳ "),
                "EFFACER",
            ], id="chat-clear-btn", className="ctrl-btn", style={
                "fontSize": "13px", "letterSpacing": "2px",
                "color": CP["text_dim"],
            }),
        ], style={
            "display": "flex", "justifyContent": "space-between",
            "alignItems": "center", "marginBottom": "12px",
            "flexShrink": "0",
        }),

        # Zone messages (scrollable)
        html.Div(
            id="chat-messages",
            style={
                "flex": "1",
                "overflowY": "auto",
                "display": "flex",
                "flexDirection": "column",
                "gap": "10px",
                "padding": "8px 4px",
                "minHeight": "0",   # nécessaire pour flex scroll
            },
        ),

        # Barre de saisie
        html.Div([
            dcc.Input(
                id="chat-input",
                type="text",
                placeholder="// SAISIR MESSAGE...",
                className="chat-input",
                debounce=False,
                style={"flex": "1"},
            ),
            html.Button(
                "▶ ENVOYER",
                id="chat-send-btn",
                className="ctrl-btn",
            ),
        ], className="chat-input-area"),

    ], id="page-chatbot", style={
        "padding": "16px", "display": "none",
        "flexDirection": "column",
        "height": "100%", "overflow": "hidden",
    })


# ── Layout global ─────────────────────────────────────────────────────────────

def build_layout() -> html.Div:
    """Assemble et retourne le layout complet de l'application (appelé une seule fois par app.py)."""
    return html.Div([
        html.Div(className="bg-grid"),
        html.Div(className="bg-corner bg-corner--tl"),
        html.Div(className="bg-corner bg-corner--br"),
        html.Div(className="scanlines"),

        html.Div([
            _topbar(),
            _nav(),

            html.Div([
                _page_accueil(),
                _page_capteurs(),
                _page_meteo(),
                _page_musique(),
                _page_minuteurs(),
                _page_confort(),
                _page_energie(),
                _page_reseau(),
                _page_systeme(),
                _page_chatbot(),
            ], style={"flex": "1", "overflow": "hidden", "position": "relative"}),

            # Footer avec nom machine dynamique
            html.Div([
                html.Span(
                    f"HOMEOS v{CFG.APP_VERSION} // {SYSTEM_LABEL.upper()}",
                    style={"fontSize": "12px", "letterSpacing": "2px",
                           "color": "rgba(0,229,255,0.25)", "fontFamily": FONT_MONO},
                ),
                html.Div([
                    html.Span(id="footer-uptime", style={
                        "fontSize": "12px", "letterSpacing": "2px",
                        "color": "rgba(0,229,255,0.25)", "fontFamily": FONT_MONO,
                    }),
                    html.Span(id="footer-mqtt", style={
                        "fontSize": "12px", "letterSpacing": "2px",
                        "color": "rgba(0,229,255,0.25)", "fontFamily": FONT_MONO,
                    }),
                ], style={"display": "flex", "gap": "16px"}),
            ], style={
                "borderTop": "1px solid rgba(0,229,255,0.1)",
                "background": "rgba(6,8,16,0.97)",
                "padding": "8px 18px", "display": "flex",
                "justifyContent": "space-between", "alignItems": "center",
                "flexShrink": "0",
            }),
        ], style={"position": "relative", "zIndex": "2", "display": "flex",
                  "flexDirection": "column", "height": "100%"}),

    ], style={
        "background": CP["bg0"], "color": CP["text"],
        "fontFamily": FONT_HUD, "minHeight": "100vh",
        "position": "relative", "overflow": "hidden",
    })
