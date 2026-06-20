# HomeOS — Les 10 onglets du dashboard

---

## 🏠 Accueil

Vue d'ensemble de l'appartement en un seul écran.

**Météo résumée** : température extérieure actuelle, ressenti, humidité, vent, pression.
Bande de prévisions 5 jours avec icône météo et min/max.

**Capteurs intérieurs** : température et humidité en direct pour chaque pièce,
horodatage du dernier relevé, tag "OUTDATED" si le capteur n'a pas émis depuis
le délai configuré (`MAX_DELAY` dans `config.py`).

**Lecteur Plex compact** : pochette, titre, artiste, progression, contrôles
lecture/pause/suivant/précédent. Miroir de l'onglet Musique.

**Journal système** : alertes en temps réel (données périmées par catégorie,
arrosage plantes nécessaire, échec fetch Enedis, absence de modèle de confort,
données NAS obsolètes). Affiche "Cache synchronisé" si tout est nominal.

**Appareils réseau actifs** : liste des 15 premières IP détectées par nmap
sur le LAN, avec leur nom/hostname si disponible, et âge du dernier scan.

---

## 🌡 Capteurs

Détail pièce par pièce de toutes les mesures Zigbee.

**Panneaux collapsibles par pièce** : chaque pièce déclarée dans `ROOMS`
(`config.py`) a son propre panneau avec toutes ses mesures (température,
humidité…) affichées sous forme de grandes valeurs colorées. Un tag "OUTDATED"
s'affiche si le capteur est silencieux depuis trop longtemps.

**Plantes** : les capteurs SGS01Z associés à une pièce apparaissent dans
une sous-section dédiée de ce panneau, avec le pourcentage d'humidité sol
et une alerte visuelle si en dessous du seuil `ALERT_PLANT_WATER_MIN`.

**Historique 24 h** : graphe Plotly de l'évolution température/humidité
sur les dernières 24 h, lu depuis la table `history` de `cache.db`.

**Découverte Zigbee** : appareils reçus via MQTT mais non encore mappés
dans `ZIGBEE_DEVICES` — affiche le topic et le payload brut pour faciliter
le paramétrage.

---

## ☁ Météo

Données OpenMeteo en détail.

**Conditions actuelles** : température, ressenti, humidité, pression, vent
(vitesse + rafales), précipitations, code météo avec description.

**Prévisions 7 jours** : tableau jour par jour avec icône, min/max, précipitations.

**Graphe horaire aujourd'hui** : courbe de température heure par heure.

**Comparaison intérieur / extérieur** : température et humidité de chaque
pièce mise en regard de la météo extérieure actuelle.

Source : [open-meteo.com](https://open-meteo.com) — gratuit, sans clé API,
localisation configurée via `GEO_LATITUDE` / `GEO_LONGITUDE` dans `config.py`.
Cache TTL 10 min (`weather_service.py`).

---

## ♪ Musique

Lecteur Plex complet.

**Lecture en cours** : pochette (96 px), titre, artiste, album, progression
(barre + temps écoulé / durée), contrôles lecture/pause/suivant/précédent/volume.

**File d'attente** : liste des pistes à venir, scroll, possibilité de cliquer
pour sauter directement à une piste.

**Recherche** : champ de recherche dans la bibliothèque Plex (artistes,
albums, pistes).

**Historique** : artistes et albums récemment joués (shelf Plex), actualisé
toutes les 60 s.

**Playlists** : liste des playlists Plex avec lancement en un clic.

Connexion configurée via `PLEX_HOST`, `PLEX_PORT`, `PLEX_TOKEN` dans `config.py`.

---

## ⏱ Minuteurs

Gestion de minuteurs dans le navigateur.

**Création** : saisie d'une durée (heures / minutes / secondes) et d'un
label optionnel, démarrage immédiat.

**Suivi** : liste des minuteurs actifs avec barre de progression et compte
à rebours en temps réel (mise à jour 1 s).

**Alarme** : à l'expiration, un son d'alarme est joué directement dans le
navigateur (data-URI WAV embarqué, aucun serveur audio requis). Le minuteur
expiré reste affiché jusqu'à l'acquittement.

Les minuteurs sont stockés en mémoire (`timer_service.py`) et perdus au
redémarrage du dashboard.

---

## ❄ Confort

Recommandations volets / fenêtres sur 24 h via le moteur de confort thermique.

**Plages de confort par pièce** : sliders min/max de température confortable
pour chaque pièce. Les valeurs sont persistées dans `cache.db` entre les sessions.

**Bouton CALCULER** : déclenche l'inférence du modèle GRU (`comfort_engine.py`).
Le modèle prédit les températures intérieures sur 24 h en fonction de la météo,
de la position solaire et de l'état des volets/fenêtres, puis sélectionne
le planning (créneaux 2 h) qui minimise le temps hors plage de confort.

**Résultat** : pour chaque pièce, action recommandée (VOLET OUVERT/FERMÉ,
FENÊTRE OUVERTE/FERMÉE) et raison (REFROIDIR / MAINTENIR / CHAUFFER),
valide jusqu'à une heure précise.

**Prérequis** : checkpoint `./models/limited.pt` (entraîné séparément).
Sans modèle, le bouton renvoie une erreur explicite.

---

## ⚡ Énergie

Consommation électrique Enedis quotidienne.

**Consommation du jour** : kWh consommés hier (J-1, dernière donnée disponible
en J) et coût estimé en € (prix configuré via `ELECTRICITY_PRICE_KWH`).

**Graphe mensuel** : courbe des 30 derniers jours lue depuis la série
`enedis_daily` dans `cache.db`.

**Statistiques** : moyenne, min, max sur la période affichée.

Source : [conso.boris.sh](https://conso.boris.sh) — proxy Enedis.
Fetch quotidien à 08h42, avec retry toutes les 60 min en cas d'échec.
Nécessite un token Bearer et le PDL (numéro de compteur à 14 chiffres)
configurés dans `config.py`.

---

## ⬡ Réseau

Surveillance du réseau local et des services internet.

**Appareils LAN** : liste des IP actives sur le subnet (`NETWORK_SUBNET`
dans `config.py`), obtenue par `nmap -sn`. Scan non-bloquant avec cache
TTL 120 s — le dashboard affiche toujours le dernier résultat connu pendant
le scan suivant.

**DNS NextDNS** : nombre de requêtes bloquées sur 24 h, taux de blocage,
actualisés toutes les 60 s. Nécessite `NEXTDNS_API_KEY` et `NEXTDNS_PROFILE_ID`.

**NAS Synology** : espace volume (utilisé / total / % libre) et informations
système (modèle DS420+, RAM, température boîtier, uptime, version DSM).
Fetch via l'API File Station (compte non-admin suffisant), cache TTL 1 h.

**Carte choroplèthe** : pays de destination du trafic DNS sur 24 h
(données NextDNS), affichée sur un fond de carte Plotly.

---

## ⚙ Système

Métriques de la machine qui héberge le dashboard.

**CPU** : usage global en %, fréquence, nombre de cœurs.

**RAM** : utilisée / totale, pourcentage.

**Disques** : espace utilisé / total pour chaque point de montage.

**Température** : température CPU (psutil si disponible, sinon `/sys/class/thermal`).

**Uptime** : durée depuis le dernier démarrage.

**Services** : statut de connexion de chaque service intégré
(MQTT, Plex, Enedis, NextDNS, NAS, Chatbot, modèle de confort).

**Anomalies** : scores ML Isolation Forest sur les mesures capteurs
(détection d'outliers). Section simulée — à brancher sur données réelles.

---

## 💬 Chatbot

Interface de chat bidirectionnel avec Synology Chat.

**Messages entrants** : les messages envoyés dans le canal Synology Chat
configuré arrivent via un webhook `POST /webhook/chat` (route Flask dans
`app.py`) et s'affichent dans des bulles utilisateur.

**Réponses** : le dashboard peut envoyer des messages vers Synology Chat
via le champ de saisie. Les messages passent par `logic_engine.py` avant
envoi (mode `FORWARD` actuel = echo direct ; mode `CLAUDE` prévu).

**Polling** : l'affichage se rafraîchit toutes les 2 s (`interval-chatbot`).

**Configuration** :
```python
SYNOLOGY_CHAT_WEBHOOK_URL = "https://nas:5001/webapi/entry.cgi?..."  # webhook entrant
SYNOLOGY_CHAT_TOKEN       = "token_du_bot_sortant"
```
