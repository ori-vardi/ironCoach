"""Weather data extraction and external API integration."""

import asyncio
import csv
import json
import time
import urllib.request
import urllib.error
import ssl
from datetime import date as dt_date
from pathlib import Path

from config import TRAINING_DATA, logger
from data_processing.helpers import _safe_float, _classify_type


# External weather cache
_weather_cache: dict[str, dict] = {}  # key: "lat,lon,date" -> {"wind_kmh": float, "rain_mm": float, ...}

# Circuit breaker state
_weather_failures = 0
_weather_circuit_open_until = 0.0
_WEATHER_FAILURE_THRESHOLD = 3
_WEATHER_CIRCUIT_TIMEOUT = 300  # 5 minutes


def _check_weather_circuit() -> bool:
    global _weather_failures, _weather_circuit_open_until
    if _weather_circuit_open_until > time.time():
        return False
    return True


def _record_weather_failure():
    global _weather_failures, _weather_circuit_open_until
    _weather_failures += 1
    if _weather_failures >= _WEATHER_FAILURE_THRESHOLD:
        _weather_circuit_open_until = time.time() + _WEATHER_CIRCUIT_TIMEOUT
        logger.warning(f"Weather API circuit breaker OPEN for {_WEATHER_CIRCUIT_TIMEOUT}s")


def _record_weather_success():
    global _weather_failures, _weather_circuit_open_until
    _weather_failures = 0
    _weather_circuit_open_until = 0


def _format_weather(w: dict) -> str:
    """Extract and format weather/environment data from workout summary.

    Includes: outdoor temp, humidity, water temp, indoor/outdoor, swim location type.
    """
    parts = []
    disc = _classify_type(w.get("type", ""))

    # Indoor/outdoor detection
    indoor_raw = str(w.get("meta_IndoorWorkout", "")).strip()
    has_gps = w.get("gps_corrected", "") not in ("", "none")
    if indoor_raw == "1":
        parts.append("Indoor")
    elif indoor_raw == "0":
        parts.append("Outdoor")
    elif not has_gps and disc in ("run", "bike"):
        # No indoor flag + no GPS → likely indoor (treadmill/trainer)
        parts.append("Likely indoor (no GPS)")

    # Swim location type: 1=Pool, 2=Open Water
    if disc == "swim":
        loc_type = str(w.get("meta_SwimmingLocationType", "")).strip()
        if loc_type == "1":
            parts.append("Pool")
        elif loc_type == "2":
            parts.append("Open Water")

    # Water temperature (swim)
    water_temp_raw = w.get("WaterTemperature_average", "")
    if water_temp_raw:
        try:
            wt = float(str(water_temp_raw).split()[0])
            if wt > 0:
                parts.append(f"Water: {wt:.1f}°C")
        except (ValueError, IndexError):
            pass

    # Outdoor temperature
    temp_raw = w.get("meta_WeatherTemperature", "")
    if temp_raw:
        try:
            temp_f = float(str(temp_raw).split()[0])
            temp_c = (temp_f - 32) * 5 / 9
            label = "Air" if water_temp_raw else ""
            if label:
                parts.append(f"Air: {temp_c:.0f}°C")
            else:
                parts.append(f"{temp_c:.0f}°C")
        except (ValueError, IndexError):
            pass

    # Humidity
    humidity_raw = w.get("meta_WeatherHumidity", "")
    if humidity_raw:
        try:
            h = float(str(humidity_raw).split()[0])
            if h > 100:
                h = h / 100
            parts.append(f"{h:.0f}% humidity")
        except (ValueError, IndexError):
            pass

    return ", ".join(parts)


def _get_first_gps(wnum: int, data_dir: Path = None) -> tuple[float, float] | None:
    """Extract first valid lat/lon from a workout CSV."""
    csv_dir = data_dir or TRAINING_DATA
    pattern = f"workout_{int(wnum):03d}_*"
    matches = sorted(csv_dir.glob(pattern + ".csv"))
    if not matches:
        return None
    try:
        with open(matches[0]) as f:
            reader = csv.DictReader(f)
            for row in reader:
                lat_s = row.get("lat", "")
                lon_s = row.get("lon", "")
                if lat_s and lon_s:
                    lat, lon = float(lat_s), float(lon_s)
                    if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat != 0 or lon != 0):
                        return (lat, lon)
    except (OSError, ValueError):
        pass
    return None


async def _fetch_external_weather(lat: float, lon: float, date_str: str, hour: int = 12) -> dict:
    """Fetch wind + rain from Open-Meteo historical/forecast API.

    Returns dict with wind_kmh, wind_dir, rain_mm (for the workout hour), or empty dict on failure.
    Uses in-memory cache to avoid duplicate requests.
    """
    cache_key = f"{lat:.2f},{lon:.2f},{date_str}"
    if cache_key in _weather_cache:
        return _weather_cache[cache_key]

    if not _check_weather_circuit():
        return {}

    result = {}
    try:
        today = dt_date.today()
        target = dt_date.fromisoformat(date_str)
        days_ago = (today - target).days

        # Open-Meteo: archive API for older data, forecast API for recent (≤7 days)
        # Archive supports start_date/end_date; forecast uses past_days (no date filter)
        if days_ago > 7:
            url = (
                f"https://archive-api.open-meteo.com/v1/archive"
                f"?latitude={lat:.4f}&longitude={lon:.4f}"
                f"&start_date={date_str}&end_date={date_str}"
                f"&hourly=wind_speed_10m,wind_direction_10m,precipitation,rain"
                f"&timezone=auto"
            )
        else:
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat:.4f}&longitude={lon:.4f}"
                f"&past_days={min(days_ago + 1, 16)}"
                f"&hourly=wind_speed_10m,wind_direction_10m,precipitation,rain"
                f"&timezone=auto"
            )

        # macOS Python may lack SSL certs — use certifi if available, else unverified
        try:
            import certifi
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

        loop = asyncio.get_event_loop()
        req = urllib.request.Request(url, headers={"User-Agent": "IronCoach/1.0"})
        resp_bytes = await loop.run_in_executor(
            None, lambda: urllib.request.urlopen(req, timeout=10, context=ssl_ctx).read()
        )
        data = json.loads(resp_bytes)
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        winds = hourly.get("wind_speed_10m", [])
        wind_dirs = hourly.get("wind_direction_10m", [])
        precips = hourly.get("precipitation", [])
        rains = hourly.get("rain", [])

        # Find the target hour by matching timestamp (forecast returns multiple days)
        target_time = f"{date_str}T{hour:02d}:00"
        target_idx = -1
        for i, t in enumerate(times):
            if t == target_time:
                target_idx = i
                break
        # Fallback: find any entry on the target date
        if target_idx < 0:
            for i, t in enumerate(times):
                if t.startswith(date_str):
                    target_idx = i
                    break

        if target_idx >= 0:
            result["wind_kmh"] = winds[target_idx] if target_idx < len(winds) else 0
            result["wind_dir"] = wind_dirs[target_idx] if target_idx < len(wind_dirs) else 0
            result["rain_mm"] = rains[target_idx] if target_idx < len(rains) else 0
            result["precip_mm"] = precips[target_idx] if target_idx < len(precips) else 0
            # Day stats: filter to just the target date
            day_indices = [i for i, t in enumerate(times) if t.startswith(date_str)]
            day_winds = [winds[i] for i in day_indices if i < len(winds)]
            day_rains = [rains[i] for i in day_indices if i < len(rains)]
            result["wind_max_kmh"] = max(day_winds) if day_winds else 0
            result["rain_total_mm"] = sum(r for r in day_rains if r) if day_rains else 0

        if result:
            _record_weather_success()
    except Exception as e:
        _record_weather_failure()
        logger.debug("External weather fetch failed for %s: %s", cache_key, e)

    _weather_cache[cache_key] = result
    return result


def _format_external_weather(ext: dict) -> str:
    """Format external weather data (wind, rain) into a string."""
    parts = []
    wind = ext.get("wind_kmh", 0)
    if wind:
        # Wind direction to compass
        deg = ext.get("wind_dir", 0)
        dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        compass = dirs[round(deg / 45) % 8] if deg else ""
        parts.append(f"Wind: {wind:.0f} km/h {compass}".strip())
    rain = ext.get("rain_mm", 0)
    rain_total = ext.get("rain_total_mm", 0)
    if rain > 0:
        parts.append(f"Rain: {rain:.1f}mm/h")
    elif rain_total > 0:
        parts.append(f"Rain that day: {rain_total:.1f}mm")
    elif rain == 0 and ext:
        parts.append("No rain")
    return ", ".join(parts)
