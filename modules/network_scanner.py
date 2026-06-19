"""
HomeOS — modules/network_scanner.py
Découverte des devices LAN via nmap -sn (ping scan).
Le scan est lancé en arrière-plan ; get_local_devices() retourne toujours
le cache immédiatement pour ne pas bloquer le callback Dash.
"""

import re
import logging
import subprocess
import threading
import time

import config

logger   = logging.getLogger(__name__)
_SUBNET  = getattr(config, "NETWORK_SUBNET", "192.168.1.0/24")
_TTL     = 120   # secondes entre deux scans complets
_cache   = {"devices": [], "ts": 0.0, "scanning": False}
_lock    = threading.Lock()


def _run_nmap() -> list[dict]:
    """Lance nmap -sn et retourne [{ip, name}, …]. Prend 3–20 s."""
    try:
        out = subprocess.check_output(
            ["nmap", "-sn", _SUBNET],
            text=True, timeout=60, stderr=subprocess.DEVNULL,
        )
        devices = []
        for line in out.splitlines():
            # "Nmap scan report for hostname (192.168.x.x)"
            m = re.match(r"Nmap scan report for (.+?) \((\d+\.\d+\.\d+\.\d+)\)", line)
            if m:
                devices.append({"name": m.group(1), "ip": m.group(2)})
                continue
            # "Nmap scan report for 192.168.x.x"
            m = re.match(r"Nmap scan report for (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})$", line)
            if m:
                devices.append({"name": "—", "ip": m.group(1)})
        logger.info("nmap : %d devices trouvés sur %s", len(devices), _SUBNET)
        return devices
    except FileNotFoundError:
        logger.error("nmap introuvable — installer avec : sudo apt install nmap")
        return []
    except subprocess.TimeoutExpired:
        logger.error("nmap timeout sur %s", _SUBNET)
        return []
    except Exception as e:
        logger.error("nmap : %s", e)
        return []


def _scan_async():
    """Lance _run_nmap() dans un thread dédié et met à jour le cache (thread-safe)."""
    with _lock:
        if _cache["scanning"]:
            return
        _cache["scanning"] = True
    try:
        devices = _run_nmap()
        with _lock:
            _cache["devices"] = devices
            _cache["ts"]      = time.time()
    finally:
        with _lock:
            _cache["scanning"] = False


def get_local_devices() -> list[dict]:
    """
    Retourne les devices LAN [{ip, name}, …] depuis le cache (TTL 120 s).
    Lance un scan nmap en arrière-plan si le cache est périmé.
    """
    with _lock:
        age      = time.time() - _cache["ts"]
        scanning = _cache["scanning"]
        cached   = list(_cache["devices"])
    
    if age > _TTL and not scanning:
        threading.Thread(target=_scan_async, daemon=True).start()

    return cached
