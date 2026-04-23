"""GPS anomaly detection and correction."""

import math
from datetime import datetime

from config import logger
from .helpers import _safe_float

# GPS anomaly detection constants
MIN_JUMP_DISTANCE_KM = 0.01
MEDIAN_ROUTE_DISTANCE_THRESHOLD_KM_DEFAULT = 5.0
NEIGHBOR_EXPAND_RANGE = 2
CLUSTER_MAX_GAP_ROWS = 20

def _detect_and_fix_gps(rows: list, workout_type: str = None) -> dict:
    """Detect GPS anomalies and null out bad lat/lon/elevation.

    Args:
        rows: List of workout data rows
        workout_type: Workout type for discipline-specific thresholds (optional)

    Returns dict with:
      - anomalies: list of {row_idx, timestamp, jump_km, speed_kmh, elevation}
      - corrected_count: number of rows corrected
      - original_gps_distance_km: total GPS distance including bad points
      - corrected_gps_distance_km: total GPS distance excluding bad points
      - original_elevation_m: total elevation gain including bad points
      - corrected_elevation_m: total elevation gain excluding bad points
    """
    def _haversine(lat1, lon1, lat2, lon2):
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        dlat, dlon = lat2 - lat1, lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 6371 * 2 * math.asin(math.sqrt(a))  # km

    # Discipline-specific speed thresholds (km/h)
    MAX_SPEED_CYCLING_KMH = 120
    MAX_SPEED_SWIMMING_KMH = 15
    MAX_SPEED_RUNNING_KMH = 50
    MAX_SPEED_DEFAULT_KMH = 80

    wtype = (workout_type or "").lower()
    if "cycling" in wtype:
        MAX_SPEED_KMH = MAX_SPEED_CYCLING_KMH
    elif "swimming" in wtype:
        MAX_SPEED_KMH = MAX_SPEED_SWIMMING_KMH
    elif "running" in wtype or "walking" in wtype:
        MAX_SPEED_KMH = MAX_SPEED_RUNNING_KMH
    else:
        MAX_SPEED_KMH = MAX_SPEED_DEFAULT_KMH

    MAX_ELEVATION_M = 4000  # covers Alps, Colorado, etc. while catching GPS anomalies
    MIN_ELEVATION_M = -500  # Dead Sea is ~-430m

    # Collect GPS-bearing rows
    gps_rows = []
    for i, r in enumerate(rows):
        lat_s, lon_s = r.get("lat", ""), r.get("lon", "")
        if not lat_s or not lon_s:
            continue
        try:
            lat, lon = float(lat_s), float(lon_s)
        except (ValueError, TypeError):
            continue
        elev = None
        try:
            elev = float(r.get("elevation_m", ""))
        except (ValueError, TypeError):
            pass
        ts_str = r.get("timestamp", "")
        gps_rows.append({"idx": i, "lat": lat, "lon": lon, "elev": elev, "ts": ts_str})

    if len(gps_rows) < 2:
        return {"anomalies": [], "corrected_count": 0}

    # Pass 1: flag rows with impossible speed or elevation
    bad_indices = set()
    anomalies = []
    for j in range(1, len(gps_rows)):
        prev, cur = gps_rows[j - 1], gps_rows[j]
        dist_km = _haversine(prev["lat"], prev["lon"], cur["lat"], cur["lon"])
        # Parse timestamps for speed calc
        dt = 0
        try:
            t1 = datetime.fromisoformat(prev["ts"].replace("Z", "+00:00").replace(" +", "+").rstrip())
            t2 = datetime.fromisoformat(cur["ts"].replace("Z", "+00:00").replace(" +", "+").rstrip())
            dt = abs((t2 - t1).total_seconds())
        except Exception:
            pass
        speed_kmh = (dist_km / (dt / 3600)) if dt > 0 else 0

        # Flag: impossible speed
        if speed_kmh > MAX_SPEED_KMH and dist_km > MIN_JUMP_DISTANCE_KM:
            bad_indices.add(cur["idx"])
            anomalies.append({
                "row_idx": cur["idx"], "timestamp": cur["ts"][:19],
                "jump_km": round(dist_km, 2), "speed_kmh": round(speed_kmh, 0),
                "elevation": cur["elev"],
            })

        # Flag: impossible elevation
        if cur["elev"] is not None and (cur["elev"] > MAX_ELEVATION_M or cur["elev"] < MIN_ELEVATION_M):
            if cur["idx"] not in bad_indices:
                bad_indices.add(cur["idx"])
                anomalies.append({
                    "row_idx": cur["idx"], "timestamp": cur["ts"][:19],
                    "jump_km": 0, "speed_kmh": 0,
                    "elevation": cur["elev"],
                })

    # Pass 1.5: flood-fill between jump pairs.
    # If we detect a jump OUT (bad speed), all points until the jump BACK should also be bad.
    # Sort bad indices in GPS order and fill gaps between pairs.
    if bad_indices:
        gps_idx_to_j = {g["idx"]: j for j, g in enumerate(gps_rows)}
        bad_j = sorted(gps_idx_to_j[idx] for idx in bad_indices if idx in gps_idx_to_j)
        # Fill between consecutive bad-j pairs: if gap between two bad-j entries
        # contains points that are far from the route median, fill them in
        if len(bad_j) >= 2 and len(gps_rows) > 10:
            import statistics
            good_lats = [g["lat"] for g in gps_rows if g["idx"] not in bad_indices]
            good_lons = [g["lon"] for g in gps_rows if g["idx"] not in bad_indices]
            if good_lats:
                med_lat = statistics.median(good_lats)
                med_lon = statistics.median(good_lons)
                # Scale threshold based on total good route distance (long routes need larger threshold)
                total_good_dist_km = 0.0
                good_rows = [g for g in gps_rows if g["idx"] not in bad_indices]
                for gi in range(1, len(good_rows)):
                    total_good_dist_km += _haversine(
                        good_rows[gi - 1]["lat"], good_rows[gi - 1]["lon"],
                        good_rows[gi]["lat"], good_rows[gi]["lon"])
                median_threshold_km = max(MEDIAN_ROUTE_DISTANCE_THRESHOLD_KM_DEFAULT,
                                          total_good_dist_km * 0.15)
                # Mark all points far from median between bad-j entries
                for k in range(len(bad_j) - 1):
                    j_start, j_end = bad_j[k], bad_j[k + 1]
                    if j_end - j_start < 2:
                        continue  # already adjacent
                    for jj in range(j_start, j_end + 1):
                        g = gps_rows[jj]
                        dist_from_med = _haversine(g["lat"], g["lon"], med_lat, med_lon)
                        if dist_from_med > median_threshold_km:
                            bad_indices.add(g["idx"])
                # Also flag all points AFTER the last bad point that are far from median
                last_bad_j = max(bad_j)
                for jj in range(last_bad_j + 1, len(gps_rows)):
                    g = gps_rows[jj]
                    dist_from_med = _haversine(g["lat"], g["lon"], med_lat, med_lon)
                    if dist_from_med > median_threshold_km:
                        bad_indices.add(g["idx"])

    # Pass 2: also flag neighbors of bad points (GPS often drifts for several seconds)
    expanded = set()
    for idx in bad_indices:
        for offset in range(-NEIGHBOR_EXPAND_RANGE, NEIGHBOR_EXPAND_RANGE + 1):
            expanded.add(idx + offset)
    bad_indices = expanded & {g["idx"] for g in gps_rows}

    # Compute original vs corrected GPS distance & elevation
    orig_dist = corr_dist = 0.0
    orig_elev = corr_elev = 0.0
    for j in range(1, len(gps_rows)):
        prev, cur = gps_rows[j - 1], gps_rows[j]
        seg = _haversine(prev["lat"], prev["lon"], cur["lat"], cur["lon"])
        orig_dist += seg
        if prev["elev"] is not None and cur["elev"] is not None:
            gain = cur["elev"] - prev["elev"]
            if gain > 0:
                orig_elev += gain
        if cur["idx"] not in bad_indices and prev["idx"] not in bad_indices:
            corr_dist += seg
            if prev["elev"] is not None and cur["elev"] is not None:
                gain = cur["elev"] - prev["elev"]
                if gain > 0:
                    corr_elev += gain

    # Pass 3: save bad GPS coords then null them out
    bad_gps_points = []
    for idx in sorted(bad_indices):
        if 0 <= idx < len(rows):
            lat_s, lon_s = rows[idx].get("lat", ""), rows[idx].get("lon", "")
            if lat_s and lon_s:
                try:
                    bad_gps_points.append({"idx": idx, "lat": float(lat_s), "lon": float(lon_s)})
                except (ValueError, TypeError):
                    pass
            rows[idx]["lat"] = ""
            rows[idx]["lon"] = ""
            rows[idx]["elevation_m"] = ""
            rows[idx]["speed_mps"] = ""
            rows[idx]["course_deg"] = ""
            rows[idx]["h_accuracy"] = ""
            rows[idx]["v_accuracy"] = ""
            rows[idx]["_gps_corrected"] = "1"

    # Group bad GPS points into clusters (consecutive indices within 20 rows)
    # Position each cluster at the nearest good GPS point (on the real route)
    bad_clusters = []
    # Build a lookup of good GPS rows (not in bad_indices) for anchoring clusters
    good_gps_lookup = []
    for g in gps_rows:
        if g["idx"] not in bad_indices:
            good_gps_lookup.append(g)

    def _cluster_anchor(cluster):
        """Find nearest good GPS point before the cluster start (fallback: after)."""
        first_idx = cluster[0]["idx"]
        last_idx = cluster[-1]["idx"]
        # Search backward for nearest good point before cluster
        for g in reversed(good_gps_lookup):
            if g["idx"] < first_idx:
                return g["lat"], g["lon"]
        # Fallback: nearest good point after cluster
        for g in good_gps_lookup:
            if g["idx"] > last_idx:
                return g["lat"], g["lon"]
        # Last resort: centroid of bad points
        return (sum(p["lat"] for p in cluster) / len(cluster),
                sum(p["lon"] for p in cluster) / len(cluster))

    if bad_gps_points:
        cluster = [bad_gps_points[0]]
        for pt in bad_gps_points[1:]:
            if pt["idx"] - cluster[-1]["idx"] <= CLUSTER_MAX_GAP_ROWS:
                cluster.append(pt)
            else:
                clat, clon = _cluster_anchor(cluster)
                bad_clusters.append({"lat": round(clat, 6), "lon": round(clon, 6), "count": len(cluster)})
                cluster = [pt]
        clat, clon = _cluster_anchor(cluster)
        bad_clusters.append({"lat": round(clat, 6), "lon": round(clon, 6), "count": len(cluster)})

    return {
        "anomalies": anomalies,
        "corrected_count": len(bad_indices),
        "original_gps_distance_km": round(orig_dist, 3),
        "corrected_gps_distance_km": round(corr_dist, 3),
        "original_elevation_m": round(orig_elev, 1),
        "corrected_elevation_m": round(corr_elev, 1),
        "bad_clusters": bad_clusters,
    }
