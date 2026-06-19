"""
HomeOS — modules/weather_service.py
Récupère et met en cache les données OpenMeteo.
Aucune clé API requise.
Localisation lue depuis config.py.
"""

import time
import logging
import requests
from dataclasses import dataclass, field
from datetime import datetime, date as date_cls
from typing import Optional

from config import GEO_LATITUDE, GEO_LONGITUDE, GEO_TIMEZONE, INTERVAL_WEATHER_MS

logger = logging.getLogger(__name__)

# ── Configuration (depuis config.py) ──────────────────────────────────────────
LATITUDE  = GEO_LATITUDE
LONGITUDE = GEO_LONGITUDE
TIMEZONE  = GEO_TIMEZONE
CACHE_TTL = INTERVAL_WEATHER_MS // 1000   # ms → secondes

OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"


WMO_DESCRIPTIONS = {
    0: "Ciel dégagé",       1: "Principalement dégagé", 2: "Partiellement nuageux",
    3: "Couvert",           45: "Brouillard",            48: "Brouillard givrant",
    51: "Bruine légère",    53: "Bruine modérée",        55: "Bruine forte",
    61: "Pluie légère",     63: "Pluie modérée",         65: "Pluie forte",
    71: "Neige légère",     73: "Neige modérée",         75: "Neige forte",
    80: "Averses légères",  81: "Averses modérées",      82: "Averses fortes",
    95: "Orage",            96: "Orage avec grêle",      99: "Orage violent",
}

WMO_ICONS = {
    0: "☀", 1: "🌤", 2: "⛅", 3: "☁", 45: "🌫", 48: "🌫",
    51: "🌦", 53: "🌦", 55: "🌧", 61: "🌧", 63: "🌧", 65: "🌧",
    71: "🌨", 73: "🌨", 75: "❄", 80: "🌦", 81: "🌧", 82: "⛈",
    95: "⛈", 96: "⛈", 99: "⛈",
}

DAY_NAMES_FR = ["Dim", "Lun", "Mar", "Mer", "Jeu", "Ven", "Sam"]


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class CurrentWeather:
    temperature: float
    feels_like: float
    humidity: int
    weather_code: int
    wind_speed: float
    wind_gusts: float
    precipitation: float
    pressure: float

    @property
    def description(self) -> str:
        return WMO_DESCRIPTIONS.get(self.weather_code, f"Code {self.weather_code}")

    @property
    def icon(self) -> str:
        return WMO_ICONS.get(self.weather_code, "?")


@dataclass
class DailyForecast:
    date: str
    day_name: str
    weather_code: int
    temp_max: float
    temp_min: float
    precipitation: float

    @property
    def icon(self) -> str:
        return WMO_ICONS.get(self.weather_code, "?")


@dataclass
class HourlyTemperature:
    times: list
    temperatures: list


@dataclass
class WeatherData:
    current: CurrentWeather
    daily: list
    hourly_today: HourlyTemperature
    fetched_at: float = field(default_factory=time.time)


# ── Service ───────────────────────────────────────────────────────────────────

class WeatherService:
    """
    Wrapper autour de l'API OpenMeteo avec cache en mémoire.
    Usage :
        svc = WeatherService()
        data = svc.get()            # depuis cache si frais
        data = svc.get(force=True)  # force un appel API
    """

    def __init__(
        self,
        latitude: float = LATITUDE,
        longitude: float = LONGITUDE,
        timezone: str = TIMEZONE,
        cache_ttl: int = CACHE_TTL,
    ):
        self.lat = latitude
        self.lon = longitude
        self.tz  = timezone
        self.ttl = cache_ttl
        self._cache: Optional[WeatherData] = None

    def get(self, force: bool = False) -> Optional[WeatherData]:
        """Retourne les données météo depuis le cache si valide, sinon effectue un appel API."""
        if not force and self._cache_valid():
            return self._cache
        return self._fetch()

    def is_stale(self) -> bool:
        """Retourne True si le cache est absent ou a dépassé son TTL. Utilisé par update_badges()."""
        return not self._cache_valid()

    def _cache_valid(self) -> bool:
        """Retourne True si le cache est présent et non expiré (< TTL secondes)."""
        if self._cache is None:
            return False
        return (time.time() - self._cache.fetched_at) < self.ttl

    def _fetch(self) -> Optional[WeatherData]:
        """Effectue l'appel API OpenMeteo, met à jour le cache et retourne le résultat."""
        params = {
            "latitude": self.lat, "longitude": self.lon,
            "timezone": self.tz, "wind_speed_unit": "kmh", "forecast_days": 7,
            "current": [
                "temperature_2m", "relative_humidity_2m", "apparent_temperature",
                "weather_code", "wind_speed_10m", "wind_gusts_10m",
                "precipitation", "surface_pressure",
            ],
            "hourly": ["temperature_2m"],
            "daily": ["weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_sum"],
        }
        try:
            resp = requests.get(OPENMETEO_URL, params=params, timeout=10)
            resp.raise_for_status()
            self._cache = self._parse(resp.json())
            logger.info("Météo mise à jour — %.1f°C %s",
                        self._cache.current.temperature, self._cache.current.description)
        except requests.RequestException as e:
            logger.error("OpenMeteo fetch failed: %s", e)
        return self._cache

    def _parse(self, raw: dict) -> WeatherData:
        """Parse la réponse JSON OpenMeteo en WeatherData."""
        c = raw["current"]
        current = CurrentWeather(
            temperature  = round(c["temperature_2m"], 1),
            feels_like   = round(c["apparent_temperature"], 1),
            humidity     = int(c["relative_humidity_2m"]),
            weather_code = int(c["weather_code"]),
            wind_speed   = round(c["wind_speed_10m"], 1),
            wind_gusts   = round(c["wind_gusts_10m"], 1),
            precipitation= round(c["precipitation"], 1),
            pressure     = round(c["surface_pressure"]),
        )
        daily = []
        for i, d in enumerate(raw["daily"]["time"]):
            dt = datetime.strptime(d, "%Y-%m-%d")
            daily.append(DailyForecast(
                date=d,
                day_name="Auj" if i == 0 else DAY_NAMES_FR[dt.weekday()],
                weather_code=int(raw["daily"]["weather_code"][i]),
                temp_max=round(raw["daily"]["temperature_2m_max"][i], 1),
                temp_min=round(raw["daily"]["temperature_2m_min"][i], 1),
                precipitation=round(raw["daily"]["precipitation_sum"][i], 1),
            ))
        today_str = date_cls.today().isoformat()
        h_times = raw["hourly"]["time"]
        h_temps = raw["hourly"]["temperature_2m"]
        idxs = [i for i, t in enumerate(h_times) if t.startswith(today_str)]
        hourly = HourlyTemperature(
            times=[h_times[i] for i in idxs],
            temperatures=[round(h_temps[i], 1) for i in idxs],
        )
        return WeatherData(current=current, daily=daily, hourly_today=hourly)


# Singleton partagé par toute l'application
weather_service = WeatherService()


if __name__ == "__main__":
    import logging as _l
    _l.basicConfig(level=_l.INFO)
    data = weather_service.get()
    if data:
        c = data.current
        print(f"\n{c.icon}  {c.temperature}°C — {c.description}")
        print(f"   Ressenti : {c.feels_like}°C | Vent : {c.wind_speed} km/h | Humidité : {c.humidity}%")
        print("\nPrévisions :")
        for d in data.daily:
            print(f"  {d.day_name:4}  {d.icon}  {d.temp_max}° / {d.temp_min}°  {d.precipitation} mm")
