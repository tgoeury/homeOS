"""
HomeOS — modules/comfort_engine.py
Interface du module d'optimisation climatique (fenêtres / volets / chauffage-clim).

L'algorithme prédictif vit dans modules/home_model/ (projet PyTorch
indépendant, avec son propre config.py). Pour éviter toute collision entre
ce module "config" et le config.py de home_model dans sys.modules, on
invoque `home_model.py plan` en sous-processus (cwd=modules/home_model/) et
on parse le CSV de planning généré.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

HOME_MODEL_DIR = Path(__file__).parent / "home_model"
CHECKPOINT_DIR = HOME_MODEL_DIR / "checkpoints"

STATUS_FULL = "full"
STATUS_LIMITED = "limited"
STATUS_NONE = "none"

# Raisons renvoyées par strategy/comfort.py -> libellés affichés
_REASON_LABELS = {
    "refroidir": "REFROIDIR",
    "rechauffer": "CHAUFFER",
    "maintenir": "MAINTENIR",
}


@dataclass
class RoomPlan:
    room_id: str
    room_name: str
    actions: list[str]   # libellés d'actions en cours, ex: ["VOLET OUVERT", "FENÊTRE FERMÉE", "REFROIDIR"]
    until: str           # heure HH:MM jusqu'à laquelle ces actions restent valables


def model_status() -> str:
    """Modèle prédictif disponible : 'full', 'limited' ou 'none'."""
    if (CHECKPOINT_DIR / "full.pt").exists():
        return STATUS_FULL
    if (CHECKPOINT_DIR / "limited.pt").exists():
        return STATUS_LIMITED
    return STATUS_NONE


def _latest_strategy_csv() -> Path | None:
    """Retourne le fichier *_strategy24.csv le plus récent produit par home_model.py, ou None."""
    csvs = sorted(HOME_MODEL_DIR.glob("*_strategy24.csv"))
    return csvs[-1] if csvs else None


def run_inference(comfort_ranges: dict[str, tuple[float, float]]) -> dict:
    """Lance `home_model.py plan` avec les plages de confort fournies, puis
    parse le dernier CSV `*_strategy24.csv` produit.

    Retourne :
      { "status": "ok" | "error", "error": str | None, "rooms": [RoomPlan, ...] }
    """
    status = model_status()
    if status == STATUS_NONE:
        return {"status": "error", "error": "Aucun modèle prédictif disponible.", "rooms": []}

    payload = json.dumps({room: list(bounds) for room, bounds in comfort_ranges.items()})

    try:
        proc = subprocess.run(
            [sys.executable, "home_model.py", "plan", "--comfort-ranges", payload],
            cwd=HOME_MODEL_DIR,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "Le calcul a dépassé le délai imparti (300s).", "rooms": []}

    if proc.returncode != 0:
        err = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "erreur inconnue"
        return {"status": "error", "error": f"Échec du calcul : {err}", "rooms": []}

    csv_path = _latest_strategy_csv()
    if csv_path is None:
        return {"status": "error", "error": "Aucun fichier de planning (*_strategy24.csv) trouvé.", "rooms": []}

    rooms = _parse_strategy_csv(csv_path)
    return {"status": "ok", "error": None, "rooms": rooms}


def _parse_strategy_csv(csv_path: Path) -> list[RoomPlan]:
    """
    Parse le CSV de planning et regroupe les créneaux consécutifs identiques par pièce.
    Colonnes attendues : room, from, to, shutter (open/closed), window (open/closed), reason.
    """
    df = pd.read_csv(csv_path, parse_dates=["from", "to"])

    room_plans = []
    for room_id, group in df.groupby("room", sort=False):
        group = group.sort_values("from").reset_index(drop=True)
        first = group.iloc[0]

        actions = []
        actions.append("VOLET OUVERT" if first["shutter"] == "open" else "VOLET FERMÉ")
        actions.append("FENÊTRE OUVERTE" if first["window"] == "open" else "FENÊTRE FERMÉE")
        actions.append(_REASON_LABELS.get(first["reason"], str(first["reason"]).upper()))

        # Durée pendant laquelle ce triplet (volet, fenêtre, raison) reste
        # inchangé : on étend tant que les créneaux suivants se suivent et
        # partagent les mêmes valeurs.
        until = first["to"]
        for _, row in group.iloc[1:].iterrows():
            if row["from"] != until:
                break
            if (row["shutter"], row["window"], row["reason"]) != (first["shutter"], first["window"], first["reason"]):
                break
            until = row["to"]

        room_plans.append(
            RoomPlan(
                room_id=room_id,
                room_name=room_id,
                actions=actions,
                until=until.strftime("%H:%M"),
            )
        )

    return room_plans
