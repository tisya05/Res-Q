import re
try:
    import google.generativeai as genai
except Exception:
    genai = None
import os
from dotenv import load_dotenv
from ip_utils import detect_ip_info
import requests
from typing import Optional, Tuple
import threading
import time
# Try to import emergency_info helpers; degrade gracefully if not present.
try:
    from emergency_info import (
        fetch_nws_alerts,
        fetch_news_advisories,
        find_suitable_locations,
        find_nearby_emergency_places,
        recommend_nearby_services,
    )
except Exception:
    fetch_nws_alerts = None
    fetch_news_advisories = None
    find_suitable_locations = None
    find_nearby_emergency_places = None
    recommend_nearby_services = None



# Translation helpers
def _translate_via_gemini(text: str, target_lang: str = "en") -> str:
    """Translate text using Gemini (if available).

    If Gemini isn't available, return the original text unchanged.
    This is used as a replacement for the previous googletrans path.
    """
    if not genai or not text or target_lang == "en":
        return text
    try:
        prompt = (
            f"Translate the following text to {target_lang} and return only the translation without commentary:\n\n" + text
        )
        model = genai.GenerativeModel(model_name)
        resp = model.generate_content(prompt)
        return resp.text.strip()
    except Exception:
        return text


def _translate_outgoing(text: str, target_lang: str) -> str:
    """Translate assistant text (assumed English) back to target_lang.

    Uses Gemini when available; otherwise returns original text.
    """
    if not target_lang or target_lang == "en":
        return text
    return _translate_via_gemini(text, target_lang)

# --- Setup ---
load_dotenv()
# Configure Gemini client only when available (allow module import without the
# dependency so tests / local dev can run without it).
if genai:
    try:
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    except Exception:
        # ignore configuration errors here; runtime calls will handle availability
        pass
model_name = "models/gemini-2.5-flash-lite"

# --- Core memory & conversation ---
conversation = [
    {"role": "system", "content": (
        "You are a calm, concise, safety-first emergency response assistant. "
        "Always prioritize the user's safety and privacy. "
        "Keep responses short and direct."
        "You are still a virtual assistant who can't make real world calls, so never say things like I am sending help for you."
    )}
]

memory = {
    "emergency_type": None,
    "approx_location": None,
    "vulnerability": None,
    "people_involved": None,
    "hazards": None,
    "environment": None,
    "ip_location_hint": None,
    # tuple (lat, lon) when available from IP geolocation
    "approx_coords": None,
    # detected user language (ISO code), e.g. 'en', 'es', 'fr'
    "user_lang": None,
}

MAX_TURNS_KEEP = 8
MEMORY_PROMPT_MAX_LEN = 800


# --- Load IP location automatically ---
def init_ip_location():
    try:
        info = detect_ip_info()
        geo = info.get("geolocation", {})
        loc_str = f"{geo.get('city', 'N/A')}, {geo.get('regionName', 'N/A')}, {geo.get('country', 'N/A')}"
        memory["ip_location_hint"] = loc_str
        # store approximate coordinates for later map queries when possible
        lat = geo.get("lat")
        lon = geo.get("lon")
        if lat is not None and lon is not None:
            memory["approx_coords"] = (float(lat), float(lon))
        print(f"🌍 IP location hint set to: {loc_str}")
    except Exception as e:
        print("⚠️ Failed to fetch IP location:", e)


init_ip_location()


# --- Keyword tables ---
EMERGENCY_KEYWORDS = {
    "fire": ["fire", "smoke", "burning"],
    "flood": ["flood", "flooding", "water rising"],
    "earthquake": ["earthquake", "tremor", "shaking"],
    "medical": ["injured", "bleeding", "unconscious", "heart attack", "collapse"],
    "storm": ["tornado", "hurricane", "storm", "wind"],
}

VULNERABILITY_KEYWORDS = {
    "child": ["child", "kid", "baby"],
    "elderly": ["elderly", "old", "senior"],
    "pregnant": ["pregnant", "expecting"],
}

SITUATION_KEYWORDS = [
    "apartment", "house", "room", "bathroom", "kitchen",
    "garage", "car", "building", "office", "school", "street"
]


# --- Memory updater ---
def update_memory(user_text):
    text_l = user_text.lower()

    # --- Emergency type ---
    for kind, kws in EMERGENCY_KEYWORDS.items():
        # match keywords as whole words to avoid substring false positives (e.g. 'windows' matching 'wind')
        for kw in kws:
            if re.search(r"\b" + re.escape(kw) + r"\b", text_l):
                if memory["emergency_type"] != kind:
                    memory["emergency_type"] = kind
                    print(f"✅ Stored: emergency_type = {kind}")
                break

    # --- Vulnerability ---
    for vuln, kws in VULNERABILITY_KEYWORDS.items():
        for kw in kws:
            if re.search(r"\b" + re.escape(kw) + r"\b", text_l):
                if memory["vulnerability"] != vuln:
                    memory["vulnerability"] = vuln
                    print(f"✅ Stored: vulnerability = {vuln}")
                break

    # --- Explicit location (like “near Boston”, "in Olympia Drive") ---
    # Accept more prepositions (in/on/at/near/around/by) and shorter names.
    # Find all location-like phrases by locating preposition occurrences and
    # taking the text up to the next preposition or punctuation. This avoids
    # greedy matches like 'in a fire in olymp' capturing the whole span.
    prepos_re = re.compile(r"\b(?:at|near|around|by|in|on)\b", flags=re.IGNORECASE)
    preps = list(prepos_re.finditer(user_text))
    loc = None
    if preps:
        candidates = []
        for i, m in enumerate(preps):
            start = m.end()
            end = len(user_text)
            if i + 1 < len(preps):
                end = preps[i + 1].start()
            # Also stop at common sentence punctuation if present before end
            sub = user_text[start:end]
            sub = re.split(r"[\.,;\n\?]\s*", sub)[0]
            sub = sub.strip()
            if sub:
                # normalize whitespace
                sub = re.sub(r"\s+", " ", sub)
                candidates.append(sub)
        if candidates:
            loc = candidates[-1]
            if memory["approx_location"] != loc:
                memory["approx_location"] = loc
                print(f"✅ Stored: approx_location = {loc}")
        if memory["approx_location"] != loc:
            memory["approx_location"] = loc
            print(f"✅ Stored: approx_location = {loc}")
            # Attempt to geocode this free-text location so we can use it for
            # map/news lookups. Use OSM Nominatim as a zero-key fallback.
            try:
                geoc = _geocode_location(loc)
                if geoc:
                    memory["approx_coords"] = geoc
                    print(f"✅ Geocoded approx_location -> approx_coords = {geoc}")
                else:
                    # If geocoding failed and the captured token seems short or
                    # single-token (likely a fragment), mark as ambiguous.
                    tokens = loc.split()
                    if len(loc) < 6 or len(tokens) == 1:
                        memory["approx_location_partial"] = loc
                        print(f"⚠️ Captured ambiguous location '{loc}', asking for clarification")
            except Exception as e:
                print("⚠️ Geocoding failed:", e)

    # --- People involved ---
    if "alone" in text_l and memory["people_involved"] != "alone":
        memory["people_involved"] = "alone"
        print("✅ Stored: people_involved = alone")
    elif re.search(r"\b(\d+)\s+(?:people|persons|others)\b", text_l):
        num = re.search(r"\b(\d+)\s+(?:people|persons|others)\b", text_l).group(1)
        val = f"{num} people"
        if memory["people_involved"] != val:
            memory["people_involved"] = val
            print(f"✅ Stored: people_involved = {val}")

    # --- Hazards ---
    for hazard in ["gas leak", "weapon", "gun", "knife", "electric", "collapsed"]:
        if re.search(r"\b" + re.escape(hazard) + r"\b", text_l) and memory["hazards"] != hazard:
            memory["hazards"] = hazard
            print(f"✅ Stored: hazards = {hazard}")

    # --- Environment / situation ---
    for env in SITUATION_KEYWORDS:
        if re.search(r"\b" + re.escape(env) + r"\b", text_l) and memory["environment"] != env:
            memory["environment"] = env
            print(f"✅ Stored: environment = {env}")
            break


# --- Context builder for Gemini ---
def build_prompt():
    mem_items = [f"{k}: {v}" for k, v in memory.items() if v]
    mem_block = "Pinned context:\n" + ("\n".join(mem_items) if mem_items else "None")
    if len(mem_block) > MEMORY_PROMPT_MAX_LEN:
        mem_block = mem_block[:MEMORY_PROMPT_MAX_LEN] + "…"

    # Collect external data (alerts, news, nearby places) and include a short
    # summary so the Gemini model can make context-aware recommendations.
    external_block = assemble_external_context()

    joined = ""
    for msg in conversation[-MAX_TURNS_KEEP*2:]:
        joined += f"[{msg['role'].upper()}] {msg['content']}\n\n"

    return f"{mem_block}\n\n{external_block}\n\n{joined}"


def assemble_external_context() -> str:
    """Gather brief external facts (alerts, news headlines, and nearby places).

    This function is intentionally conservative: it will not raise if APIs
    are unavailable and will return a short human-readable summary that is
    safe to append to the model prompt.
    """
    parts = ["External data:"]

    # Location hint / coords
    coords = memory.get("approx_coords")
    if coords:
        parts.append(f"- Approx coords: {coords[0]:.4f},{coords[1]:.4f} (from IP)")
    elif memory.get("approx_location"):
        parts.append(f"- Approx location: {memory.get('approx_location')}")
    else:
        parts.append("- Approx location: unknown")

    # NWS/weather alerts (if available and coords present)
    try:
        if fetch_nws_alerts and coords:
            alerts = fetch_nws_alerts(coords[0], coords[1])
            if alerts:
                a_s = []
                for a in alerts[:3]:
                    ev = a.get("event") or a.get("headline") or a.get("id")
                    sev = a.get("severity") or ""
                    a_s.append(f"{ev} ({sev})".strip())
                parts.append(f"- Active alerts: {', '.join(a_s)}")
            else:
                parts.append("- Active alerts: none")
    except Exception:
        parts.append("- Active alerts: unavailable")

    # Recent news items for the location or emergency type
    try:
        if fetch_news_advisories:
            loc = memory.get("approx_location")
            et = memory.get("emergency_type")
            city = None
            ip_hint = memory.get("ip_location_hint")
            if ip_hint:
                city = ip_hint.split(",")[0].strip()
            queries = []
            if loc and et:
                queries.extend([
                    f"{loc} {et}",
                    f"{loc} {et} last night",
                    f"{loc} {et}\n",
                ])
                if city:
                    queries.extend([
                        f"{loc} {city} {et}",
                        f"{loc} {city} {et} last night",
                    ])
            if loc:
                queries.append(f"{loc} {et or ''}".strip())
                queries.append(loc)
            if et:
                queries.append(et)
            queries.append("emergency")

            seen = set()
            collected = []
            for q in queries:
                try:
                    news = fetch_news_advisories(q, page_size=3)
                except Exception:
                    news = []
                if news and os.getenv("DEBUG_NEWS_QUERIES"):
                    print(f"[DEBUG] news query '{q}' returned {len(news)} items")
                for n in news:
                    title = n.get("title") or "(no title)"
                    if title in seen:
                        continue
                    seen.add(title)
                    collected.append(title)
                    if len(collected) >= 3:
                        break
                if len(collected) >= 3:
                    break

            if collected:
                parts.append(f"- Recent related news: { ' | '.join(collected) }")
            else:
                parts.append("- Recent related news: none")
    except Exception:
        parts.append("- Recent related news: unavailable")

    # Nearby suitable places (prioritized recommendations)
    try:
        if coords:
            serp_key = os.getenv("SERPAPI_API_KEY")
            if find_suitable_locations:
                fav = find_suitable_locations(memory.get("emergency_type") or "", coords[0], coords[1], serpapi_key=serp_key)
                if not fav and find_nearby_emergency_places:
                    fav = find_nearby_emergency_places(coords[0], coords[1])
                if fav:
                    items = []
                    for p in fav[:5]:
                        name = p.get("name") or p.get("title") or "(unknown)"
                        addr = p.get("address") or p.get("vicinity") or ""
                        items.append(f"{name}{(' - '+addr) if addr else ''}")
                    parts.append(f"- Nearby places: { ' ; '.join(items[:5]) }")
                else:
                    parts.append("- Nearby places: none found")
        else:
            parts.append("- Nearby places: unknown (no coords)")
    except Exception:
        parts.append("- Nearby places: unavailable")

    # Short safety hint to help the model (non-prescriptive, safety-first)
    if memory.get("emergency_type"):
        et = memory.get("emergency_type")
        hint_map = {
            "flood": "If outdoors, seek higher ground or enter sturdy multi-storey building; avoid driving through water.",
            "earthquake": "If outdoors, move to open space away from buildings, trees, and power lines.",
            "fire": "If inside, evacuate and call emergency services; if outside, move away from smoke and fire.",
            "medical": "Prioritize calling emergency services and getting to nearest hospital if critical.",
        }
        parts.append(f"- Safety hint: {hint_map.get(et, 'Follow general emergency procedures; ask user for exact location if unclear.')}")

    return "\n".join(parts)


def _geocode_location(q: str, timeout: int = 5) -> Optional[Tuple[float, float]]:
    """Geocode a free-text location using OpenStreetMap Nominatim.

    Returns (lat, lon) tuple on success, or None on failure.
    """
    try:
        url = "https://nominatim.openstreetmap.org/search"
        headers = {"User-Agent": "hackumass-voice-hack/1.0 (+https://example.com)"}
        params = {"q": q, "format": "json", "limit": 1}
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        item = data[0]
        lat = float(item.get("lat"))
        lon = float(item.get("lon"))
        return (lat, lon)
    except Exception:
        return None


# --- Gemini model call ---
def gemini_reply_with_context():
    # If the Gemini client isn't available (local dev/test), return a short
    # fallback summary so the rest of the system can continue to function.
    joined = build_prompt()
    if not genai:
        reply_text = (
            "[Gemini unavailable] The assistant cannot access the Gemini API in this environment. "
            "Use the following context to respond to the user: \n\n" + joined[:2000]
        )
        print("🤖 Gemini (fallback):", reply_text)
        conversation.append({"role": "assistant", "content": reply_text})
        return reply_text

    model = genai.GenerativeModel(model_name)
    response = model.generate_content(joined)
    reply_text = response.text.strip()
    print("🤖 Gemini:", reply_text)
    conversation.append({"role": "assistant", "content": reply_text})
    return reply_text


# --- Core pipeline (translated) ---
def process_user_message(user_text, detected_lang: Optional[str] = None):
    """Process a user message.

    If detected_lang is provided (e.g., from ElevenLabs STT), use it. If
    detected_lang != 'en', try to translate the incoming text to English via
    Gemini for internal processing. If Gemini is unavailable, keep original.
    """
    # Determine detected language
    if not detected_lang:
        # No STT-provided language available — assume English for now
        detected_lang = "en"

    memory["user_lang"] = detected_lang
    proc_text = user_text
    if detected_lang and detected_lang != "en":
        # Translate inbound text to English for internal processing if possible
        proc_text = _translate_via_gemini(user_text, "en") or user_text
    # Store the translated (English) text in conversation so the model sees English
    conversation.append({"role": "user", "content": proc_text})
    update_memory(proc_text)

    # If the user asks "where" but we don't have an explicit location, ask
    # a concise clarifying question instead of calling the model.
    text_l = proc_text.lower()
    needs_location = False
    if not memory.get("approx_coords") and (not memory.get("approx_location") or memory.get("approx_location_partial")):
        needs_location = True

    if ("where" in text_l or re.search(r"\bwhere\b", text_l)) and needs_location:
        clar = (
            "I need the exact location (street/address/city) to look up nearby incident reports. "
            "Can you tell me the street or nearest landmark? If you are in immediate danger, call emergency services now."
        )
        conversation.append({"role": "assistant", "content": clar})
        print("🤖 Gemini (clarify):", clar)
        return _translate_outgoing(clar, detected_lang)

    if memory.get("approx_location_partial") and not memory.get("approx_coords"):
        p = memory.get("approx_location_partial")
        clar = (
            f"I heard '{p}' — can you confirm the full street name or nearest cross-street? "
            "If you're unsure, give any nearby city or landmark. If in immediate danger, call emergency services now."
        )
        conversation.append({"role": "assistant", "content": clar})
        print("🤖 Gemini (clarify):", clar)
        return _translate_outgoing(clar, detected_lang)

    # --- LOCATION HELP (map-based) ---
    # If the user asks where to go, prefer using map APIs / local places
    # rather than searching external news. This returns prioritized nearby
    # emergency locations (hospitals, police, fire stations, parks) using
    # geocoded coordinates when available.
    if ("where" in text_l or re.search(r"\bwhere\b", text_l)):
        # Prefer precise coordinates when available; try to geocode free-text location otherwise
        coords = memory.get("approx_coords")
        loc = memory.get("approx_location")
        if not coords and loc:
            try:
                g = _geocode_location(loc)
                if g:
                    coords = g
                    memory["approx_coords"] = g
                    print(f"✅ Geocoded approx_location -> approx_coords = {g}")
            except Exception:
                coords = None

        if coords:
            lat, lon = coords[0], coords[1]
            try:
                serp_key = os.getenv("SERPAPI_API_KEY")
                places = []
                # Try higher-level "suitable" places first (parks, hospitals per emergency type)
                if find_suitable_locations:
                    places = find_suitable_locations(memory.get("emergency_type") or "", lat, lon, serpapi_key=serp_key)
                # If that returned nothing, fall back to nearby emergency places
                if not places and find_nearby_emergency_places:
                    places = find_nearby_emergency_places(lat, lon)

                if places:
                    # Optionally prioritize/recommend based on emergency type
                    recs = recommend_nearby_services(memory.get("emergency_type"), places) if recommend_nearby_services else places
                    parts = ["Based on your location, here are nearby places you can go:"]
                    def _primary_type(types_list):
                        if not types_list:
                            return "other"
                        tset = set(t.lower() for t in types_list)
                        if any(x in tset for x in ("hospital", "health", "clinic", "urgent")):
                            return "hospital/clinic"
                        if any(x in tset for x in ("police", "law")):
                            return "police"
                        if any(x in tset for x in ("fire_station", "fire")):
                            return "fire station"
                        if any(x in tset for x in ("park", "open_ground", "stadium", "field")):
                            return "park/open space"
                        return list(types_list)[0] if types_list else "other"

                    for p in recs[:5]:
                        name = p.get("name") or p.get("title") or "(unknown)"
                        addr = p.get("address") or p.get("vicinity") or ""
                        types_list = p.get("types") or []
                        primary = _primary_type(types_list)
                        # Short format: name — street address — primary type
                        line = f"- {name}"
                        if addr:
                            line += f" — {addr}"
                        line += f" — {primary}"
                        parts.append(line)
                    parts.append("If you want directions or turn-by-turn navigation, reply with your exact address or confirm which place above to get more details.")
                    reply = "\n".join(parts)
                    conversation.append({"role": "assistant", "content": reply})
                    print("🤖 Location (map):", reply)
                    return _translate_outgoing(reply, detected_lang)
                else:
                    clar2 = (
                        "I couldn't find nearby emergency facilities automatically. "
                        "Please tell me your street address or nearest landmark so I can show nearby hospitals, police, or fire stations. "
                        "If you're in immediate danger, call emergency services now."
                    )
                    conversation.append({"role": "assistant", "content": clar2})
                    print("🤖 Location (clarify):", clar2)
                    return _translate_outgoing(clar2, detected_lang)
            except Exception as e:
                print("⚠️ Location lookup failed:", e)
                clar2 = (
                    "I couldn't look up nearby places right now. Please tell me your exact address or nearest landmark."
                )
                conversation.append({"role": "assistant", "content": clar2})
                return _translate_outgoing(clar2, detected_lang)
        # If no coords and no approx_location, fall through to existing logic (it will ask for clarification)

    # Immediate safety-first + incident-search flow
    try:
        et = memory.get("emergency_type")
        do_emergency_flow = False
        if et:
            kws = EMERGENCY_KEYWORDS.get(et, [])
            for kw in kws:
                if re.search(r"\b" + re.escape(kw) + r"\b", proc_text, flags=re.IGNORECASE):
                    do_emergency_flow = True
                    break

        if do_emergency_flow:
            hint_map = {
                "flood": "If outdoors, move to higher ground or into a sturdy building; avoid walking or driving through floodwater. Call emergency services if anyone is injured.",
                "earthquake": "If indoors, drop, cover, and hold on; if outdoors, move to open space away from buildings and power lines. Check for injuries and call emergency services if needed.",
                "fire": "If inside, evacuate immediately, stay low to avoid smoke, and call emergency services. If outside, move away from smoke and burning structures.",
                "medical": "If someone is unresponsive or not breathing, call emergency services immediately and begin CPR if trained. Prioritize getting professional medical help."
            }

            immediate = hint_map.get(et, "Follow general emergency procedures and call emergency services if the situation is life-threatening.")
            reply = immediate
            conversation.append({"role": "assistant", "content": reply})
            print("🤖 Gemini (immediate):", reply)

            # background search (same logic as earlier) — keep English in conversation
            def _background_search_and_notify(et, loc):
                try:
                    time.sleep(0.5)
                    if not fetch_news_advisories:
                        return
                    coords = memory.get("approx_coords")
                    ip_hint = memory.get("ip_location_hint")
                    city = ip_hint.split(",")[0].strip() if ip_hint else None

                    queries = []
                    if loc:
                        queries.extend([f'"{loc}" {et}', f"{loc} {et}", f"{loc} {et} last night"])
                        if city:
                            queries.extend([f'"{loc} {city}" {et}', f"{loc} {city} {et}"])
                    if coords and not loc:
                        queries.append(f"{et} near {coords[0]:.4f},{coords[1]:.4f}")
                    if not queries:
                        queries.append(et if not city else f"{city} {et}")

                    seen = set()
                    found = []
                    for q in queries:
                        try:
                            news = fetch_news_advisories(q, page_size=5)
                        except Exception:
                            news = []
                        if os.getenv("DEBUG_NEWS_QUERIES"):
                            print(f"[DEBUG background search] query='{q}' returned {len(news)} items")

                        loc_tokens = [t.lower() for t in re.split(r"\W+", loc) if t] if loc else []
                        event_token = et.lower() if et else None
                        ranked = []
                        for n in news:
                            title = (n.get("title") or n.get("headline") or "").strip()
                            desc = (n.get("description") or n.get("content") or "").strip()
                            if not title:
                                continue
                            text_blob = (title + " " + desc).lower()
                            score = 0
                            if event_token and event_token in text_blob:
                                score += 2
                            if city and city.lower() in text_blob:
                                score += 1
                            for tk in loc_tokens:
                                if tk and tk in text_blob:
                                    score += 3
                            ranked.append((score, title, n.get("source") or n.get("source_name") or "", n.get("url") or n.get("link") or ""))

                        ranked.sort(key=lambda x: x[0], reverse=True)
                        for score, title, src, url in ranked:
                            if title in seen:
                                continue
                            seen.add(title)
                            found.append((title, src, url, score))
                            if len(found) >= 3:
                                break
                        if len(found) >= 3:
                            break

                        high_conf = [f for f in found if f[3] >= 3]
                        if not high_conf and os.getenv("SERPAPI_API_KEY"):
                            serp_key = os.getenv("SERPAPI_API_KEY")
                            web_items = []
                            for q in queries:
                                try:
                                    resp = requests.get("https://serpapi.com/search.json", params={"engine": "google", "q": q, "api_key": serp_key}, timeout=6)
                                    resp.raise_for_status()
                                    j = resp.json()
                                except Exception:
                                    j = {}
                                for k in ("news_results", "organic_results", "local_results", "news"):
                                    for item in j.get(k, []) if isinstance(j.get(k, []), list) else []:
                                        title = item.get("title") or item.get("headline") or ""
                                        snippet = item.get("snippet") or item.get("snippet_text") or item.get("description") or ""
                                        text_blob = (title + " " + snippet).lower()
                                        score = 0
                                        if event_token and event_token in text_blob:
                                            score += 2
                                        if city and city.lower() in text_blob:
                                            score += 1
                                        for tk in loc_tokens:
                                            if tk and tk in text_blob:
                                                score += 3
                                        web_items.append((score, title, item.get("source", ""), item.get("link") or item.get("url") or ""))
                            web_items.sort(key=lambda x: x[0], reverse=True)
                            for score, title, src, url in web_items:
                                if title in seen:
                                    continue
                                seen.add(title)
                                found.append((title, src, url, score))
                                if len(found) >= 3:
                                    break
                            high_conf = [f for f in found if f[3] >= 3]

                        if high_conf:
                            parts = ["I found recent reports that may match your incident:"]
                            for t, s, u, score in high_conf[:2]:
                                if s:
                                    parts.append(f"- {t} ({s}){(' - ' + u) if u else ''}")
                                else:
                                    parts.append(f"- {t}{(' - ' + u) if u else ''}")
                            parts.append("Is any of these the incident you're experiencing? Reply 'yes' to confirm or give the exact address.")
                            follow = "\n".join(parts)
                            conversation.append({"role": "assistant", "content": follow})
                            print("🤖 Gemini (follow-up):", _translate_outgoing(follow, memory.get("user_lang") or "en"))
                except Exception as e:
                    if os.getenv("DEBUG_NEWS_QUERIES"):
                        print("[DEBUG background search] error:", e)

            t = threading.Thread(target=_background_search_and_notify, args=(et, memory.get("approx_location")), daemon=True)
            t.start()

            return _translate_outgoing(reply, detected_lang)
    except Exception:
        pass

    if ("where" in text_l or re.search(r"\bwhere\b", text_l)) and memory.get("approx_location"):
        try:
            if fetch_news_advisories:
                loc = memory.get("approx_location")
                et = memory.get("emergency_type")
                ip_hint = memory.get("ip_location_hint")
                city = ip_hint.split(",")[0].strip() if ip_hint else None

                queries = []
                if loc and et:
                    queries.extend([f"{loc} {et}", f"{loc} {et} last night"])
                    if city:
                        queries.extend([f"{loc} {city} {et}", f"{loc} {city} {et} last night"])
                if loc:
                    queries.append(loc)
                if et:
                    queries.append(et)

                seen = set()
                collected = []
                for q in queries:
                    try:
                        news = fetch_news_advisories(q, page_size=3)
                    except Exception:
                        news = []
                    for n in news:
                        title = n.get("title") or "(no title)"
                        source = n.get("source") or n.get("source_name") or ""
                        url = n.get("url") or n.get("link") or ""
                        key = title
                        if key in seen:
                            continue
                        seen.add(key)
                        collected.append((title, source, url))
                        if len(collected) >= 3:
                            break
                    if len(collected) >= 3:
                        break

                if collected:
                    parts = ["I found recent reports mentioning this location:"]
                    for t, s, u in collected:
                        if s:
                            parts.append(f"- {t} ({s}){ ' - ' + u if u else '' }")
                        else:
                            parts.append(f"- {t}{ ' - ' + u if u else '' }")
                    parts.append("If you are in immediate danger, call emergency services now.\nIf you want, confirm your exact address and I can show nearest hospitals/fire/police.")
                    reply = "\n".join(parts)
                    conversation.append({"role": "assistant", "content": reply})
                    print("🤖 Gemini (news):", reply)
                    return _translate_outgoing(reply, detected_lang)
        except Exception:
            pass

    # Default path: call Gemini (or fallback) and translate result back to user's language
    eng = gemini_reply_with_context()
    return _translate_outgoing(eng, memory.get("user_lang") or "en")
    needs_location = False
    # If we don't have a geocoded coord, treat missing/partial approx_location as needing clarification
    if not memory.get("approx_coords") and (not memory.get("approx_location") or memory.get("approx_location_partial")):
        needs_location = True

    if ("where" in text_l or re.search(r"\bwhere\b", text_l)) and needs_location:
        clar = (
            "I need the exact location (street/address/city) to look up nearby incident reports. "
            "Can you tell me the street or nearest landmark? If you are in immediate danger, call emergency services now."
        )
        conversation.append({"role": "assistant", "content": clar})
        print("🤖 Gemini (clarify):", clar)
        return clar

    # If we captured an ambiguous short location, ask for clarification too
    if memory.get("approx_location_partial") and not memory.get("approx_coords"):
        p = memory.get("approx_location_partial")
        clar = (
            f"I heard '{p}' — can you confirm the full street name or nearest cross-street? "
            "If you're unsure, give any nearby city or landmark. If in immediate danger, call emergency services now."
        )
        conversation.append({"role": "assistant", "content": clar})
        print("🤖 Gemini (clarify):", clar)
        return clar

    # Immediate safety-first + incident-search flow:
    # If an emergency is detected in the user's message (e.g., 'fire') then
    # immediately return concise life-saving advice and also try to search
    # news for matching local incidents (location + emergency type). If we
    # find matching reports, include them and ask the user to confirm whether
    # any of those are the incident they're involved in.
    # This ensures the user receives urgent instructions without waiting for
    # lengthy searches.
    try:
        # detect whether the current message contains the emergency keyword
        et = memory.get("emergency_type")
        do_emergency_flow = False
        if et:
            kws = EMERGENCY_KEYWORDS.get(et, [])
            for kw in kws:
                if re.search(r"\b" + re.escape(kw) + r"\b", user_text, flags=re.IGNORECASE):
                    do_emergency_flow = True
                    break

        if do_emergency_flow:
            # Immediate life-saving advice (non-prescriptive, safety-first)
            hint_map = {
                "flood": "If outdoors, move to higher ground or into a sturdy building; avoid walking or driving through floodwater. Call emergency services if anyone is injured.",
                "earthquake": "If indoors, drop, cover, and hold on; if outdoors, move to open space away from buildings and power lines. Check for injuries and call emergency services if needed.",
                "fire": "If inside, evacuate immediately, stay low to avoid smoke, and call emergency services. If outside, move away from smoke and burning structures.",
                "medical": "If someone is unresponsive or not breathing, call emergency services immediately and begin CPR if trained. Prioritize getting professional medical help."
            }

            immediate = hint_map.get(et, "Follow general emergency procedures and call emergency services if the situation is life-threatening.")

            # Start building a concise reply containing the immediate advice
            # Do NOT tell the user we're searching — keep them focused on safety.
            reply_lines = [immediate]

            reply = "\n".join(reply_lines)
            conversation.append({"role": "assistant", "content": reply})
            print("🤖 Gemini (immediate):", reply)

            # Launch a background thread to perform news lookup and notify in-chat
            def _background_search_and_notify(et, loc):
                try:
                    time.sleep(0.5)  # brief pause to allow immediate reply to be processed
                    if not fetch_news_advisories:
                        return
                    coords = memory.get("approx_coords")
                    ip_hint = memory.get("ip_location_hint")
                    city = ip_hint.split(",")[0].strip() if ip_hint else None

                    # build queries
                    queries = []
                    if loc:
                        queries.append(f'"{loc}" {et}')
                        queries.append(f"{loc} {et}")
                        queries.append(f"{loc} {et} last night")
                        if city:
                            queries.append(f'"{loc} {city}" {et}')
                            queries.append(f"{loc} {city} {et}")
                    if coords and not loc:
                        queries.append(f"{et} near {coords[0]:.4f},{coords[1]:.4f}")
                    if not queries:
                        if city:
                            queries.append(f"{city} {et}")
                        else:
                            queries.append(et)

                    seen = set()
                    found = []
                    for q in queries:
                        try:
                            news = fetch_news_advisories(q, page_size=5)
                        except Exception:
                            news = []
                        if os.getenv("DEBUG_NEWS_QUERIES"):
                            print(f"[DEBUG background search] query='{q}' returned {len(news)} items")

                        # Filter and rank results by token matches (street/city + event)
                        loc_tokens = []
                        if loc:
                            loc_tokens = [t.lower() for t in re.split(r"\W+", loc) if t]
                        event_token = et.lower() if et else None
                        ranked = []
                        for n in news:
                            title = (n.get("title") or n.get("headline") or "").strip()
                            desc = (n.get("description") or n.get("content") or "").strip()
                            src = n.get("source") or n.get("source_name") or ""
                            url = n.get("url") or n.get("link") or ""
                            if not title:
                                continue
                            text_blob = (title + " " + desc).lower()
                            score = 0
                            if event_token and event_token in text_blob:
                                score += 2
                            if city and city.lower() in text_blob:
                                score += 1
                            for tk in loc_tokens:
                                if tk and tk in text_blob:
                                    score += 3
                            ranked.append((score, title, src, url))

                        ranked.sort(key=lambda x: x[0], reverse=True)
                        for score, title, src, url in ranked:
                            if title in seen:
                                continue
                            seen.add(title)
                            found.append((title, src, url, score))
                            if len(found) >= 3:
                                break
                        if len(found) >= 3:
                            break

                        # Only notify the user if we have high-confidence matches (score >=3)
                        high_conf = [f for f in found if f[3] >= 3]
                        # If we don't have high-confidence matches from NewsAPI/RSS,
                        # try SerpApi web search (if key available) to find local articles.
                        if not high_conf and os.getenv("SERPAPI_API_KEY"):
                            serp_key = os.getenv("SERPAPI_API_KEY")
                            web_items = []
                            for q in queries:
                                try:
                                    resp = requests.get("https://serpapi.com/search.json", params={"engine": "google", "q": q, "api_key": serp_key}, timeout=6)
                                    resp.raise_for_status()
                                    j = resp.json()
                                except Exception:
                                    j = {}
                                candidates = []
                                # collect various result buckets
                                for k in ("news_results", "organic_results", "local_results", "news"):
                                    for item in j.get(k, []) if isinstance(j.get(k, []), list) else []:
                                        title = item.get("title") or item.get("headline") or item.get("position") or ""
                                        link = item.get("link") or item.get("url") or item.get("source") or ""
                                        snippet = item.get("snippet") or item.get("snippet_text") or item.get("description") or ""
                                        if title:
                                            candidates.append({"title": title, "snippet": snippet, "source": item.get("source" , ""), "url": link})
                                # score candidates
                                for it in candidates:
                                    title = it["title"].strip()
                                    text_blob = (title + " " + it.get("snippet", "")).lower()
                                    score = 0
                                    if event_token and event_token in text_blob:
                                        score += 2
                                    if city and city.lower() in text_blob:
                                        score += 1
                                    for tk in loc_tokens:
                                        if tk and tk in text_blob:
                                            score += 3
                                    web_items.append((score, title, it.get("source", ""), it.get("url", "")))
                            web_items.sort(key=lambda x: x[0], reverse=True)
                            for score, title, src, url in web_items:
                                if title in seen:
                                    continue
                                seen.add(title)
                                found.append((title, src, url, score))
                                if len(found) >= 3:
                                    break
                            high_conf = [f for f in found if f[3] >= 3]

                        if high_conf:
                            parts = ["I found recent reports that may match your incident:"]
                            for t, s, u, score in high_conf[:2]:
                                if s:
                                    parts.append(f"- {t} ({s}){(' - ' + u) if u else ''}")
                                else:
                                    parts.append(f"- {t}{(' - ' + u) if u else ''}")
                            parts.append("Is any of these the incident you're experiencing? Reply 'yes' to confirm or give the exact address.")
                            follow = "\n".join(parts)
                            conversation.append({"role": "assistant", "content": follow})
                            print("🤖 Gemini (follow-up):", follow)
                except Exception as e:
                    if os.getenv("DEBUG_NEWS_QUERIES"):
                        print("[DEBUG background search] error:", e)

            t = threading.Thread(target=_background_search_and_notify, args=(et, memory.get("approx_location")), daemon=True)
            t.start()

            return reply
    except Exception:
        # Fall through to normal flow on unexpected errors
        pass
    if ("where" in text_l or re.search(r"\bwhere\b", text_l)) and memory.get("approx_location"):
        try:
            if fetch_news_advisories:
                loc = memory.get("approx_location")
                et = memory.get("emergency_type")
                ip_hint = memory.get("ip_location_hint")
                city = ip_hint.split(",")[0].strip() if ip_hint else None

                queries = []
                if loc and et:
                    queries.extend([f"{loc} {et}", f"{loc} {et} last night"])
                    if city:
                        queries.extend([f"{loc} {city} {et}", f"{loc} {city} {et} last night"])
                if loc:
                    queries.append(loc)
                if et:
                    queries.append(et)

                seen = set()
                collected = []
                for q in queries:
                    try:
                        news = fetch_news_advisories(q, page_size=3)
                    except Exception:
                        news = []
                    for n in news:
                        title = n.get("title") or "(no title)"
                        source = n.get("source") or n.get("source_name") or ""
                        url = n.get("url") or n.get("link") or ""
                        key = title
                        if key in seen:
                            continue
                        seen.add(key)
                        collected.append((title, source, url))
                        if len(collected) >= 3:
                            break
                    if len(collected) >= 3:
                        break

                if collected:
                    parts = ["I found recent reports mentioning this location:"]
                    for t, s, u in collected:
                        if s:
                            parts.append(f"- {t} ({s}){ ' - ' + u if u else '' }")
                        else:
                            parts.append(f"- {t}{ ' - ' + u if u else '' }")
                    parts.append("If you are in immediate danger, call emergency services now.\nIf you want, confirm your exact address and I can show nearest hospitals/fire/police.")
                    reply = "\n".join(parts)
                    conversation.append({"role": "assistant", "content": reply})
                    print("🤖 Gemini (news):", reply)
                    return reply
        except Exception:
            pass

    return gemini_reply_with_context()



# --- Debugging / inspection ---
def show_current_context():
    print("\n📝=== CURRENT CONTEXT ===")
    for msg in conversation[-MAX_TURNS_KEEP*2:]:
        print(f"[{msg['role'].upper()}] {msg['content']}")
    print("🗂=== MEMORY ===")
    for k, v in memory.items():
        if v:
            print(f"{k}: {v}")
    print("=======================\n")


def clear_session():
    global conversation, memory
    conversation = conversation[:1]
    for key in memory:
        memory[key] = None
    init_ip_location()
    print("🧹 Session cleared.")
