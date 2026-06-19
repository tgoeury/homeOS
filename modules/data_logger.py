"""
HomeOS — modules/data_logger.py
Façade légère vers DataCache.log() — conservée pour compatibilité des appelants.

Tout l'historique est stocké dans data/cache.db (table history).
Le dossier data/history/ n'est plus utilisé.
"""

from modules.data_cache import data_cache


class DataLogger:
    def log(self, name: str, value, unit: str, source: str) -> None:
        data_cache.log(name, value, unit, source)


data_logger = DataLogger()
