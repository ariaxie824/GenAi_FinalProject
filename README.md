# Memoir Map

Upload travel photos → AI reads scene & EXIF → pins memories on an interactive world map.

## Context

**User**: Travel and photography enthusiasts who want to preserve trip memories 
but find manual journaling too tedious.

**Problem**: After a trip, photos sit scattered across camera rolls with no 
structure. Building a proper travel log manually — finding coordinates, writing 
descriptions, organizing by date — takes hours most people never invest.

**Solution**: Upload a photo; Memoir Map does the rest. Claude reads the scene 
and EXIF metadata, infers location and atmosphere, and pins the memory on an 
interactive map automatically.

**Why GenAI**: A traditional form-based app could display data, but it cannot 
interpret a photo, generate a scene description, or infer location from visual 
cues. Language + vision models make zero-input logging possible.


## Pipeline

```
User uploads 1-2 photos
       ↓
Claude Haiku (vision) reads:
  - Image content (scene, atmosphere, location cues)
  - EXIF metadata (datetime, GPS coordinates)
       ↓
Structured JSON output (tool use):
  { location_name, date, scene_description, tags, transport_emoji }
       ↓
If EXIF has GPS  → use coordinates directly
If no GPS        → Nominatim geocoding on location_name
       ↓
Render to Folium map with rich popups
```

## Setup

```bash
cd memoir-map
pip install -r requirements.txt
```

Create a `.env` file from the example and add your Anthropic API key:

```bash
cp .env.example .env
# then open .env and replace the placeholder with your actual key
```

Your `.env` should look like:

```
ANTHROPIC_API_KEY=sk-ant-...
```

## Run

```bash
streamlit run app.py
```

## Evaluate

Compare `baseline` (plain JSON parse) vs `tool_use` (structured tool call) strategies:

```bash
python eval/eval_runner.py --strategy both
```

Metrics reported per strategy:
- `valid_json_rate` — how often the response parsed cleanly
- `location_match_rate` — location name accuracy
- `avg_tag_overlap` — tag relevance (intersection with expected tags)
- `transport_match_rate` — correct transport emoji
- `avg_grounding_1to5` — model-as-judge: are all claims in the scene description traceable to the source? (1=hallucinated, 5=fully grounded)
- `avg_vividness_1to5` — model-as-judge: is the description evocative and atmospheric? (1=bland, 5=vivid)

The grounding score is the primary hallucination check: a separate Claude instance reads the source image description and the generated scene description, then flags any specific claims — landmarks, activities, sensory details — that cannot be traced back to the input.

## Project Structure

```
memoir-map/
├── app.py              # Streamlit UI + pipeline orchestration
├── llm_processor.py    # Claude Haiku vision + tool-use structured output
├── geocoder.py         # Nominatim geocoding with JSON cache
├── map_renderer.py     # Folium map with thumbnail popups
├── data/
│   ├── entries.json    # Persisted memory entries
│   ├── geo_cache.json  # Geocoding cache (avoid repeat API calls)
│   └── uploads/        # Saved user photos
├── eval/
│   ├── test_cases.json # 5 synthetic test scenarios
│   └── eval_runner.py  # Baseline vs tool_use comparison
└── requirements.txt
```

## Design Notes

- **Prompt caching**: system prompt in `llm_processor.py` uses `cache_control: ephemeral` to reduce latency and cost on repeated calls.
- **Tool use for structured output**: more reliable than asking the model to return JSON in free text — eliminates markdown wrapping and parse failures.
- **EXIF GPS priority**: raw GPS coordinates from EXIF are more accurate than geocoding; Nominatim is only called when GPS is absent.
- **Nominatim rate limit**: 1 request/second enforced in `geocoder.py`; results cached to `data/geo_cache.json`.

## Evaluation

5 synthetic test cases: Eiffel Tower selfie, Tokyo street food, Rocky Mountain road trip, European train station, Thailand beach.

Metrics: `valid_json` (parses cleanly), `location` (name accuracy), `tag_overlap` (keyword intersection), `transport` (correct emoji), `grounding` (model-as-judge, 1–5: no invented facts), `vividness` (model-as-judge, 1–5: atmospheric quality).

### Round 1 — before prompt fix

Both strategies output 🚤 for the Thailand beach case because a long-tail boat was visible in the scene — the model inferred transport from scene objects rather than arrival mode.

| Strategy | valid_json | location | tag_overlap | transport | grounding /5 | vividness /5 |
|----------|-----------|----------|-------------|-----------|--------------|--------------|
| baseline | 1.00 | 1.00 | 0.54 | 0.40 | — | — |
| tool_use | 1.00 | 1.00 | 0.31 | 0.60 | — | — |

**Tag overlap artifact**: baseline's higher tag_overlap was not a real quality difference. Baseline produces single-word tags (`neon`) that match expected keywords exactly; tool_use produces descriptive phrases (`neon lights`) that score 0 on exact-match intersection but are semantically richer.

### Prompt fix

Added to system prompt and tool schema description: *"transport_emoji refers to how the traveler ARRIVED — not objects visible in the scene. Boats in the background do NOT mean 🚢."*

### Round 2 — after prompt fix, with model-as-judge

| Strategy | valid_json | location | tag_overlap | transport | grounding /5 | vividness /5 |
|----------|-----------|----------|-------------|-----------|--------------|--------------|
| baseline | 1.00 | 1.00 | 0.43 | 0.40 | **5.00** | 3.20 |
| tool_use | 1.00 | 1.00 | 0.30 | **0.80** | **5.00** | 2.80 |

**Transport**: the same instruction had no effect on baseline (still 0.40) but raised tool_use from 0.60 → 0.80. This shows that tool use's dual constraint — schema description + system prompt — makes the model more instruction-following than free-text generation. (Remaining tool_use miss: tc_004 train station returned 🚆 instead of 🚂 — a different train emoji, schema-valid but not matching the expected value.)

**Grounding**: both strategies scored 5.00/5 — the judge found no hallucinated specifics in any scene description. The model stays faithful to the source when inputs are concrete and image-like.

**Vividness**: both strategies scored low (2.80–3.20). Scene descriptions are accurate but generic. This is a known limitation: without actual visual input the model produces factually safe but atmospherically thin text. With real photo uploads (the production path in `app.py`), Claude Haiku vision produces richer descriptions — see the Antelope Canyon example in the artifact snapshot below.

### Where the system fails and where a human should stay involved

- **Vague photos or sparse notes** produce generic location names ("a restaurant") that Nominatim cannot geocode — a human should verify or correct the pin
- **Low-grounding entries**: if a user's note is extremely vague ("nice place"), the model may generate plausible-sounding but unverifiable descriptions; users should treat scene descriptions as AI interpretations, not factual ground truth
- **tag_overlap** penalizes multi-word descriptive tags — embedding cosine similarity would be a better metric
- **Eval uses text descriptions**, not real photos; grounding scores on actual image inputs may differ