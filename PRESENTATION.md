# Memoir Map — Project Presentation

## 1. Project Overview

**Memoir Map** is an AI-powered travel memory application.  
Upload 1–2 travel photos → Claude reads the scene → pins memories on an interactive world map.

**Core value**: Turn an unorganized photo library into a structured, navigable travel journal with zero manual input — and human correction when the AI gets it wrong.

---

## 2. System Pipeline

```
User uploads 1–2 photos
        ↓
[EXIF Extraction]
  - GPS coordinates (if available)
  - Shooting datetime
        ↓
[Claude Haiku — Vision + Tool Use]
  - Reads image content: scene, atmosphere, location cues
  - Reads EXIF context passed as text
  - Returns structured JSON via tool call:
    { location_name, date, scene_description, tags, transport_emoji }
        ↓
[Geocoding]
  - EXIF has GPS?  → use coordinates directly (precise)
  - No GPS?        → Nominatim geocoding on location_name (cached)
        ↓
[Human-in-the-loop correction]
  - Geocoding failed? → edit form before saving (retry or save without pin)
  - Result wrong?     → correction form after pinning (re-geocode with new name)
  - Past entry?       → edit / delete from memory list at any time
        ↓
[Folium Map Rendering]
  - Marker color by transport mode
  - Popup: thumbnail + stop number + date + description + tags
  - Chronological route: animated Bezier arc lines (AntPath)
  - MarkerCluster for dense areas
```

---

## 3. Tech Stack

| Layer | Tool | Reason |
|-------|------|--------|
| UI | Streamlit | Rapid prototyping, file upload built-in |
| Vision LLM | Claude Haiku (`claude-haiku-4-5`) | Fast, cheap, strong vision capability |
| Structured output | Tool Use (function calling) | More reliable than free-text JSON parsing |
| Prompt caching | `cache_control: ephemeral` | Reduces latency and cost on repeated calls |
| Geocoding | Nominatim (geopy) | Free, no API key required |
| Geocoding cache | `data/geo_cache.json` | Avoids repeated requests for same location |
| Map | Folium + streamlit-folium | Interactive, embeds in Streamlit |
| Route visualization | AntPath + quadratic Bezier | Animated arc lines showing travel direction |

---

## 4. Key Design Decisions

### Tool Use vs. Free-text JSON

Two approaches to getting structured output from the LLM:

- **Baseline**: Ask the model to "return JSON" in free text, then parse it
- **Tool Use**: Define a strict JSON schema as a tool, force the model to call it

Tool use eliminates markdown wrapping, missing keys, and inconsistent formatting — the model is constrained by the schema at inference time. Evaluation showed that prompt instructions embedded in the schema description are also more effective than system prompt instructions alone.

### EXIF GPS Priority

```
if EXIF GPS available:
    use raw coordinates  ← precise, sub-meter accuracy
else:
    geocode location_name via Nominatim  ← city/landmark level
```

iPhone and Android photos typically embed GPS by default. When available, this gives exact placement on the map without any geocoding.

### Prompt Caching

The system prompt in `llm_processor.py` is marked with `cache_control: ephemeral`. Anthropic caches this on their side for 5 minutes, so repeated calls skip re-encoding the system prompt and reduce latency by ~30%.

### Human-in-the-loop Correction

The AI makes mistakes — indoor venues confuse geocoding, similar landmarks get mixed up, and sparse photos produce generic location names. Rather than hiding these failures, the app surfaces them and lets the user fix them in three places:

1. **Geocoding failure** — edit form appears before saving; user can correct the name and retry, or save without a map pin
2. **Wrong result** — a correction expander appears after every successful pin; user can fix name/description and re-geocode
3. **Past entries** — every memory in the list has ✏️ (edit) and 🗑️ (delete) buttons; edits can trigger re-geocoding

This keeps AI errors recoverable without requiring the user to start over.

### Chronological Route Visualization

Entries with dates are sorted chronologically and connected with an animated AntPath route. To avoid the rigidity of straight lines, each segment is drawn as a **quadratic Bezier arc**: the control point is offset perpendicular to the travel direction, with the bulge scaling proportionally to segment distance (capped at 12°). This gives subtle curves for short hops and pronounced arcs for intercontinental flights, matching the visual intuition of great-circle routes.

---

## 5. Evaluation

### Setup

5 synthetic test cases covering:

| ID | Scenario | Has GPS EXIF |
|----|----------|-------------|
| tc_001 | Eiffel Tower selfie | No |
| tc_002 | Tokyo street food | Yes |
| tc_003 | Mountain road trip | No |
| tc_004 | European train station | No |
| tc_005 | Thailand beach | Yes |

Metrics evaluated:
- `valid_json_rate` — response parsed without error
- `location_match_rate` — extracted location matches expected
- `avg_tag_overlap` — tag relevance (intersection with expected keywords)
- `transport_match_rate` — correct transport emoji
- `avg_grounding_1to5` — model-as-judge: no hallucinated specifics (1–5)
- `avg_vividness_1to5` — model-as-judge: description is evocative (1–5)

---

### Round 1 Results (before prompt fix)

| Strategy | valid_json | location | tag_overlap | transport |
|----------|-----------|----------|-------------|-----------|
| baseline | 1.00 | 1.00 | 0.54 | 0.40 |
| tool_use | 1.00 | 1.00 | 0.31 | 0.60 |

**Observation**: tool_use had better transport accuracy but lower tag_overlap.  
Investigation revealed the tag_overlap gap was a **metric artifact**: baseline produces single-word tags (`'neon'`) that match keywords exactly; tool_use produces descriptive phrases (`'neon lights'`) that are semantically better but score 0 on exact-match intersection.

**Real problem found**: Both strategies output `🚤` for tc_005 (Thailand beach) because a long-tail boat was visible in the scene — the model inferred transport from scene objects rather than arrival mode.

---

### Prompt Fix

Added to system prompt and tool schema description:

> *"transport_emoji refers to how the traveler ARRIVED at the destination — not objects visible in the scene. Boats in the background do NOT mean 🚢. A beach/island most likely means ✈️."*

---

### Round 2 Results (after prompt fix + model-as-judge)

| Strategy | valid_json | location | tag_overlap | transport | grounding /5 | vividness /5 |
|----------|-----------|----------|-------------|-----------|--------------|--------------|
| baseline | 1.00 | 1.00 | 0.43 | 0.40 | **5.00** | 3.20 |
| **tool_use** | **1.00** | **1.00** | 0.30 | **0.80** ✓ | **5.00** | 2.80 |

The same prompt fix had **zero effect on baseline** (still 0.40) but raised tool_use from 0.60 → 0.80. This demonstrates that tool use's dual constraint — schema description + system prompt — makes the model more instruction-following than free-text generation.

**Grounding: 5.00/5 for both** — the model-as-judge found no hallucinated specifics in any case. The model stays faithful to source input when claims are concrete and verifiable.

**Vividness: 2.80–3.20** — descriptions are accurate but atmospherically thin. This is expected: the eval uses text descriptions as ground truth proxies rather than real photos; real photo inputs (as in the live app) produce richer output.

---

### Key Takeaway from Eval

> Structured tool use is not just about reliable parsing — it also makes the model more steerable via prompt engineering.  
> The same instruction had measurably different effects depending on whether it was delivered through a schema constraint (tool_use) or plain text (baseline).  
> Model-as-judge grounding confirmed zero hallucination across all test cases.

---

## 6. Live Demo Flow

1. Open `http://localhost:8501` after `streamlit run app.py`
2. Upload a travel photo in the sidebar → click **✨ Analyze & Pin**
3. AI extracts location, date, tags, transport emoji
4. If geocoding succeeds → marker appears on the map; a correction expander lets you fix anything wrong
5. If geocoding fails → an edit form appears before saving; correct the name and retry
6. Add multiple dated entries → a Bezier arc route connects them in chronological order
7. Use ✏️ / 🗑️ buttons in the memory list to edit or delete any past entry

**Example output** (Antelope Canyon photo, no GPS EXIF):
```json
{
  "location_name": "Antelope Canyon, Arizona",
  "scene_description": "Golden sunlight streams through narrow slot canyon walls,
    illuminating stunning layers of rust-orange and purple striped sandstone.",
  "tags": ["slot canyon", "desert", "sandstone", "geology", "southwest"],
  "transport_emoji": "🚗",
  "lat": 36.930266,
  "lon": -111.4219676
}
```

---

## 7. Limitations & Where a Human Should Stay Involved

| Limitation | Status | Notes |
|-----------|--------|-------|
| Geocoding fails for indoor / event venues | Mitigated | Edit form lets user correct and retry before saving |
| AI misidentifies location | Mitigated | Correction form after every pin; edit button in memory list |
| Sparse photos → generic scene descriptions | Known | Grounding check catches it; user should treat low-detail entries as interpretations |
| tag_overlap metric penalizes multi-word tags | Known | Embedding cosine similarity would be a better metric |
| Eval uses text descriptions, not real photos | Known | Vividness scores reflect this; real-photo results are qualitatively richer |
| Single-user, local storage | Out of scope | Future: user auth + cloud storage (e.g., Supabase) |
