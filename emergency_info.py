"""emergency_info.py

Utilities to fetch local advisories (news / government alerts) and find nearby
emergency services (hospitals, police, fire stations) using Google Maps.

Design goals:
- Use authoritative APIs where available (NWS for US alerts).
- Query news sources (NewsAPI if API key present, otherwise Google News RSS) for
  recent advisories mentioning the location.
- Use Google Places API (if API key present) to find nearby emergency services.

Note: This module performs network calls. It expects the following environment
variables for richer behavior:
- NEWS_API_KEY (optional) - for NewsAPI.org queries
- GOOGLE_MAPS_API_KEY (optional) - for Google Places API

If API keys are missing the functions will fall back to lighter-weight options
or return empty results.

"""
from __future__ import annotations

import os
import logging
import requests
from typing import Dict, List, Optional, Any
from urllib.parse import quote_plus
# Delay importing BeautifulSoup until needed so the module can be imported
# even if bs4 is not installed (news parsing fallback will be skipped).
BeautifulSoup = None
import time

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Simple in-memory cache to avoid repeated API calls for the same location/query.
# Keys are strings; values are tuples (timestamp, data).
_CACHE: Dict[str, Any] = {}
CACHE_TTL = 60 * 10  # 10 minutes


def _cache_get(key: str):
    entry = _CACHE.get(key)
    if not entry:
        return None
    ts, val = entry
    if time.time() - ts > CACHE_TTL:
        try:
            del _CACHE[key]
        except KeyError:
            pass
        return None
    return val


def _cache_set(key: str, val: Any):
    _CACHE[key] = (time.time(), val)


def fetch_nws_alerts(lat: float, lon: float, timeout: int = 6) -> List[Dict[str, Any]]:
    """Fetch active National Weather Service alerts for the provided point.

    Returns a list of alert dicts. If the location is outside the US or the
    service fails, returns an empty list.
    """
    # caching key based on lat/lon
    key = f"nws:{lat:.4f},{lon:.4f}"
    cached = _cache_get(key)
    if cached is not None:
        logger.debug("Using cached NWS alerts for %s", key)
        return cached

    try:
        url = f"https://api.weather.gov/alerts/active?point={lat},{lon}"
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        features = data.get("features", [])
        alerts = []
        for f in features:
            props = f.get("properties", {})
            alerts.append({
                "id": props.get("id"),
                "event": props.get("event"),
                "severity": props.get("severity"),
                "headline": props.get("headline"),
                "description": props.get("description"),
                "instruction": props.get("instruction"),
                "effective": props.get("effective"),
                "expires": props.get("expires"),
                "url": props.get("uri"),
            })
        _cache_set(key, alerts)
        return alerts
    except Exception as e:
        logger.debug("NWS alerts fetch failed: %s", e)
        return []


def fetch_news_advisories(query: str, page_size: int = 5, timeout: int = 6) -> List[Dict[str, str]]:
    """Fetch recent news items related to `query`.

    Prefers NewsAPI.org if `NEWS_API_KEY` present. Otherwise falls back to
    Google News RSS search and parses titles/links.
    Returns a list of dicts {title, source, url, publishedAt (optional)}.
    """
    api_key = os.getenv("NEWS_API_KEY")
    results: List[Dict[str, str]] = []

    if api_key:
        try:
            url = (
                f"https://newsapi.org/v2/everything?q={quote_plus(query)}&pageSize={page_size}&sortBy=publishedAt"
            )
            headers = {"Authorization": api_key}
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            j = resp.json()
            for a in j.get("articles", [])[:page_size]:
                results.append({
                    "title": a.get("title"),
                    "source": a.get("source", {}).get("name"),
                    "url": a.get("url"),
                    "publishedAt": a.get("publishedAt"),
                })
            return results
        except Exception as e:
            logger.debug("NewsAPI fetch failed: %s", e)

    # Fallback: Google News RSS. Try to use BeautifulSoup if available, but
    # otherwise parse the RSS using the stdlib XML parser so the fallback works
    # even when `bs4` isn't installed.
    rss_url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        resp = requests.get(rss_url, timeout=timeout)
        resp.raise_for_status()
        content = resp.content

        # Prefer BeautifulSoup when available for forgiving parsing
        global BeautifulSoup
        if BeautifulSoup is None:
            try:
                import importlib
                _BS = importlib.import_module('bs4').BeautifulSoup
                BeautifulSoup = _BS
            except Exception:
                BeautifulSoup = None

        if BeautifulSoup:
            try:
                soup = BeautifulSoup(content, features="xml")
                items = soup.find_all("item")[:page_size]
                for it in items:
                    title = it.title.text if it.title else None
                    link = it.link.text if it.link else None
                    source = None
                    source_tag = it.find("source")
                    if source_tag:
                        source = source_tag.text
                    results.append({"title": title, "source": source, "url": link})
                return results
            except Exception:
                # fall through to stdlib XML parser
                pass

        # Stdlib XML parsing fallback (works without external deps)
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(content)
            # RSS structure: /rss/channel/item
            channel = root.find('channel')
            if channel is not None:
                items = channel.findall('item')[:page_size]
                for it in items:
                    title = it.findtext('title')
                    link = it.findtext('link')
                    source = it.findtext('source')
                    results.append({"title": title, "source": source, "url": link})
            return results
        except Exception as e:
            logger.debug("XML RSS parse failed: %s", e)
            return results
    except Exception as e:
        logger.debug("Google News RSS fetch failed: %s", e)
        return results


def find_nearby_emergency_places(lat: float, lon: float, radius: int = 5000, timeout: int = 6) -> List[Dict[str, Any]]:
    """Use Google Places Nearby Search to find nearby emergency services.

    Requires `GOOGLE_MAPS_API_KEY` in env. Will query for several `type`s and
    combine results. Returns list of places with name, type, rating, address,
    location, and place_id.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        logger.info("GOOGLE_MAPS_API_KEY not set; find_nearby_emergency_places will return [].")
        return []

    place_types = ["hospital", "police", "fire_station", "doctor"]
    base = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    aggregated: Dict[str, Dict[str, Any]] = {}

    for ptype in place_types:
        params = {
            "location": f"{lat},{lon}",
            "radius": radius,
            "type": ptype,
            "key": api_key,
        }
        try:
            resp = requests.get(base, params=params, timeout=timeout)
            resp.raise_for_status()
            j = resp.json()
            for r in j.get("results", []):
                pid = r.get("place_id")
                if not pid:
                    continue
                if pid not in aggregated:
                    aggregated[pid] = {
                        "name": r.get("name"),
                        "types": r.get("types", []),
                        "rating": r.get("rating"),
                        "address": r.get("vicinity"),
                        "location": r.get("geometry", {}).get("location"),
                        "place_id": pid,
                    }
                else:
                    # extend types if new
                    aggregated[pid]["types"] = list(set(aggregated[pid]["types"]) | set(r.get("types", [])))
        except Exception as e:
            logger.debug("Google Places query failed for type %s: %s", ptype, e)

    # Return as a list sorted by rating (desc) when available
    out = list(aggregated.values())
    out.sort(key=lambda x: (x.get("rating") is not None, x.get("rating") or 0), reverse=True)
    return out


def serpapi_search_local(query: str, lat: float, lon: float, serpapi_key: str, zoom: float = 15.1, start: int = 0, timeout: int = 6) -> Dict[str, Any]:
    """Search Google Maps local results through SerpApi for a query near lat/lon.

    Returns the parsed JSON response from SerpApi or an empty dict on failure.
    """
    base = "https://serpapi.com/search.json"
    ll = f"@{lat},{lon},{zoom}z"
    params = {
        "engine": "google_maps",
        "q": query,
        "ll": ll,
        "type": "search",
        "api_key": serpapi_key,
    }
    if start:
        params["start"] = start
    # caching per query/ll/start
    key = f"serp:{query}:{lat:.4f},{lon:.4f}:{start}"
    cached = _cache_get(key)
    if cached is not None:
        logger.debug("Using cached SerpApi result for %s", key)
        return cached
    try:
        resp = requests.get(base, params=params, timeout=timeout)
        resp.raise_for_status()
        j = resp.json()
        _cache_set(key, j)
        return j
    except Exception as e:
        logger.debug("SerpApi search failed: %s", e)
        return {}


def serpapi_find_emergency_places(lat: float, lon: float, serpapi_key: str, zoom: float = 15.1, timeout: int = 6) -> List[Dict[str, Any]]:
    """Use SerpApi to find nearby emergency services (police/hospital/fire).

    Returns a normalized list of place dicts similar to the Google Places
    results used elsewhere in this module.
    """
    queries = ["hospital", "police station", "fire station", "emergency room"]
    aggregated: Dict[str, Dict[str, Any]] = {}

    for q in queries:
        # stop early if we already collected enough distinct places
        if len(aggregated) >= 10:
            break
        data = serpapi_search_local(q, lat, lon, serpapi_key, zoom=zoom, timeout=timeout)
        local = data.get("local_results") or []
        for item in local:
            pid = item.get("place_id") or item.get("data_id")
            if not pid:
                continue
            rec = {
                "name": item.get("title"),
                "place_id": item.get("place_id"),
                "data_id": item.get("data_id"),
                "data_cid": item.get("data_cid"),
                "gps": item.get("gps_coordinates"),
                "rating": item.get("rating"),
                "reviews": item.get("reviews"),
                "types": item.get("types") or ([item.get("type")] if item.get("type") else []),
                "address": item.get("address"),
                "open_state": item.get("open_state"),
                "phone": item.get("phone"),
                "website": item.get("website"),
                "description": item.get("description"),
                "thumbnail": item.get("thumbnail"),
                "position": item.get("position"),
                "place_link": item.get("place_id_search"),
            }
            aggregated[pid] = {**aggregated.get(pid, {}), **rec}
    
    out = list(aggregated.values())
    out.sort(key=lambda x: (x.get("rating") is not None, x.get("rating") or 0), reverse=True)
    return out


def find_suitable_locations(emergency_type: str, lat: float, lon: float, serpapi_key: Optional[str] = None, radius: int = 5000, max_results: int = 3, timeout: int = 6) -> List[Dict[str, Any]]:
    """Find specific locations suitable for the given emergency type.

    This function attempts to locate concrete nearby places the user could go
    to (parks, open grounds, tall buildings, hospitals, police stations,
    etc.) depending on the emergency. When `serpapi_key` is provided (or
    SERPAPI_API_KEY set), SerpApi searches are used for richer results. If
    SerpApi is not available, the function will filter `find_nearby_emergency_places` results.

    Returns a list of normalized place dicts (same shape as serpapi_find_emergency_places output).
    """
    serpapi_key = serpapi_key or os.getenv("SERPAPI_API_KEY")

    # Define search intents per emergency type
    if emergency_type == "flood":
        # Prefer parks, open grounds, stadiums, parking garages, high points
        queries = ["park", "open ground", "stadium", "parking garage", "high-rise building"]
    elif emergency_type == "earthquake":
        # Prefer parks, open squares, fields, sports grounds
        queries = ["park", "open ground", "sports field", "public square"]
    elif emergency_type == "fire":
        queries = ["fire station", "hospital", "police station", "public exit"]
    elif emergency_type == "medical":
        queries = ["hospital", "emergency room", "urgent care", "clinic"]
    else:
        queries = ["hospital", "police station", "fire station", "park"]

    aggregated: Dict[str, Dict[str, Any]] = {}

    # If we have SerpApi, run targeted queries for each intent
    if serpapi_key:
        for q in queries:
            data = serpapi_search_local(q, lat, lon, serpapi_key, timeout=timeout)
            local = data.get("local_results") or []
            for item in local:
                pid = item.get("place_id") or item.get("data_id")
                if not pid:
                    continue
                rec = {
                    "name": item.get("title"),
                    "place_id": item.get("place_id"),
                    "data_id": item.get("data_id"),
                    "data_cid": item.get("data_cid"),
                    "gps": item.get("gps_coordinates"),
                    "rating": item.get("rating"),
                    "types": item.get("types") or ([item.get("type")] if item.get("type") else []),
                    "address": item.get("address"),
                    "phone": item.get("phone"),
                    "place_link": item.get("place_id_search"),
                }
                aggregated[pid] = {**aggregated.get(pid, {}), **rec}
            time.sleep(0.1)
    else:
        # Fallback: use Google Places (via our existing helper) and filter by types/keywords
        places = find_nearby_emergency_places(lat, lon, radius=radius, timeout=timeout)
        keywords_map = {
            "flood": ["park", "stadium", "parking", "high", "tower", "building"],
            "earthquake": ["park", "field", "square", "ground", "stadium"],
            "fire": ["fire_station", "hospital", "police"],
            "medical": ["hospital", "doctor", "clinic", "urgent"]
        }
        kw = keywords_map.get(emergency_type, [])
        for p in places:
            types = [t.lower() for t in (p.get("types") or [])]
            name = (p.get("name") or "").lower()
            match = any(k in " ".join(types) or k in name for k in kw)
            if match:
                pid = p.get("place_id") or p.get("data_id") or p.get("name")
                aggregated[pid] = p

    out = list(aggregated.values())
    # sort by some heuristic: rating and proximity if gps available
    def sort_key(x):
        r = x.get("rating") or 0
        return (r,)

    out.sort(key=sort_key, reverse=True)
    return out[:max_results]


def recommend_nearby_services(emergency_type: Optional[str], places: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Given an emergency_type (e.g., 'fire', 'medical', 'break_in') and a list
    of nearby places, return prioritized recommendations (subset of places).
    """
    if not places:
        return []

    if emergency_type == "fire":
        # prioritize fire_station then hospitals
        def score(p):
            t = p.get("types", [])
            if "fire_station" in t:
                return 100
            if "hospital" in t or "doctor" in t:
                return 80
            return 50
    elif emergency_type == "medical":
        def score(p):
            t = p.get("types", [])
            if "hospital" in t:
                return 100
            if "doctor" in t:
                return 80
            return 40
    elif emergency_type in ("break_in", "robbery", "intruder"):
        def score(p):
            t = p.get("types", [])
            if "police" in t:
                return 100
            return 40
    else:
        def score(p):
            # generic: prefer hospitals and police
            t = p.get("types", [])
            if "hospital" in t:
                return 90
            if "police" in t:
                return 80
            if "fire_station" in t:
                return 70
            return 30

    scored = sorted(places, key=lambda p: (score(p), p.get("rating") or 0), reverse=True)
    # return top 5
    return scored[:5]


if __name__ == "__main__":
    # small demo: use ip_utils.detect_ip_info if available
    try:
        from ip_utils import detect_ip_info

        info = detect_ip_info()
        geo = info.get("geolocation") or {}
        lat = geo.get("lat")
        lon = geo.get("lon")
        city = f"{geo.get('city', '')}, {geo.get('regionName', '')}, {geo.get('country', '')}".strip(', ')

        print("Detected location:", city)

        if lat and lon:
            print("Checking NWS / official alerts...")
            nws = fetch_nws_alerts(lat, lon)
            if nws:
                print(f"Found {len(nws)} official alerts:")
                for a in nws:
                    print("-", a.get("event"), "->", a.get("headline"))
            else:
                print("No NWS alerts found (or not in US).")

            print("\nSearching news advisories...")
            news = fetch_news_advisories(city or f"{lat},{lon}")
            for n in news:
                print("-", n.get("title"), "(", n.get("source"), ")", n.get("url"))

            print("\nFinding nearby emergency services (requires GOOGLE_MAPS_API_KEY)...")
            places = find_nearby_emergency_places(lat, lon)
            if places:
                print(f"Found {len(places)} places; sample:")
                for p in places[:5]:
                    print("-", p.get("name"), "|", p.get("types"), "|", p.get("address"))
            else:
                print("No places found or GOOGLE_MAPS_API_KEY not configured.")
        else:
            print("No lat/lon available for this IP; consider providing a city name or using a different geolocation source.")
    except Exception as e:
        print("Demo run failed:", e)
