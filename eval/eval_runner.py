"""
Evaluation runner: tests LLM structured output quality against synthetic test cases.
Compares two strategies:
  - baseline: plain text prompt, parse JSON from response
  - tool_use: structured tool call (production approach)

Each result is also scored by a model-as-judge on two dimensions:
  - grounding (1-5): are all claims in scene_description traceable to the source?
  - vividness (1-5): is the description evocative and atmospheric?

Usage:
    python eval/eval_runner.py [--strategy baseline|tool_use|both]
"""

import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import anthropic

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

TEST_CASES_PATH = Path(__file__).parent / "test_cases.json"


# ---------- baseline strategy (no tool use, plain JSON parse) ----------

BASELINE_SYSTEM = "You are a travel memory analyst. Respond ONLY with valid JSON, no markdown."

BASELINE_USER_TMPL = """Given this travel photo description and EXIF data, extract memory info.

Photo description: {image_description}
EXIF data: {exif_data}

Return JSON with keys: location_name, date (YYYY-MM-DD or null), scene_description, tags (list), transport_emoji"""


def run_baseline(case: dict) -> dict | None:
    client = anthropic.Anthropic()
    prompt = BASELINE_USER_TMPL.format(
        image_description=case["image_description"],
        exif_data=json.dumps(case["exif_data"])
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=BASELINE_SYSTEM,
            messages=[{"role": "user", "content": prompt}]
        )
        text = resp.content[0].text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        print(f"  [baseline] parse error: {e}")
        return None


# ---------- tool_use strategy ----------

TOOL_USE_SYSTEM = [
    {
        "type": "text",
        "text": (
            "You are a travel memory analyst. Extract structured information from the provided travel photo description.\n"
            "For transport_emoji, infer how the traveler ARRIVED at the destination — not what is visible in the scene.\n"
            "Boats in the background do NOT mean 🚢. A beach/island most likely means ✈️.\n"
            "Default to ✈️ for international destinations, 🚗 for domestic/local ones."
        ),
        "cache_control": {"type": "ephemeral"}
    }
]

EXTRACT_TOOL = {
    "name": "extract_memory",
    "description": "Extract structured travel memory from photo description",
    "input_schema": {
        "type": "object",
        "properties": {
            "location_name": {"type": "string"},
            "date": {"type": ["string", "null"]},
            "scene_description": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "transport_emoji": {"type": "string"}
        },
        "required": ["location_name", "scene_description", "tags", "transport_emoji"]
    }
}


def run_tool_use(case: dict) -> dict | None:
    client = anthropic.Anthropic()
    prompt = (
        f"Photo: {case['image_description']}\n"
        f"EXIF: {json.dumps(case['exif_data'])}\n"
        "Call extract_memory with this information."
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=TOOL_USE_SYSTEM,
            tools=[EXTRACT_TOOL],
            tool_choice={"type": "tool", "name": "extract_memory"},
            messages=[{"role": "user", "content": prompt}]
        )
        for block in resp.content:
            if block.type == "tool_use":
                return block.input
    except Exception as e:
        print(f"  [tool_use] error: {e}")
    return None


# ---------- model-as-judge ----------

JUDGE_TOOL = {
    "name": "score_scene",
    "description": "Score a generated travel scene description against the source image description",
    "input_schema": {
        "type": "object",
        "properties": {
            "grounding": {
                "type": "integer",
                "description": (
                    "1-5: Are all specific claims in the scene description traceable to the source? "
                    "5=every detail matches the source with no invented specifics; "
                    "3=minor elaboration but no outright invention; "
                    "1=specific facts (landmarks, activities, sensory details) not present in the source"
                )
            },
            "vividness": {
                "type": "integer",
                "description": (
                    "1-5: Is the description vivid and atmospheric? "
                    "5=evocative, captures mood and setting well; "
                    "3=adequate but generic; "
                    "1=bland or inaccurate to the scene"
                )
            },
            "note": {
                "type": "string",
                "description": "One sentence explaining the grounding score — what was grounded or what was invented"
            }
        },
        "required": ["grounding", "vividness", "note"]
    }
}

JUDGE_SYSTEM = [
    {
        "type": "text",
        "text": (
            "You are evaluating a travel memory app's scene description quality. "
            "Your job is to check whether the generated scene description faithfully reflects "
            "the source, or whether it invents specific details (landmarks, activities, sensory "
            "details, weather, people) that cannot be traced to the source description. "
            "Stylistic elaboration and vivid language are acceptable. "
            "Invented specific facts are not."
        ),
        "cache_control": {"type": "ephemeral"}
    }
]


def judge_scene(image_description: str, scene_description: str) -> dict:
    """Use Claude as a judge to score grounding and vividness of a scene description."""
    client = anthropic.Anthropic()
    prompt = (
        f"Source image description (ground truth):\n{image_description}\n\n"
        f"Generated scene description to evaluate:\n{scene_description}\n\n"
        "Call score_scene to evaluate."
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=JUDGE_SYSTEM,
            tools=[JUDGE_TOOL],
            tool_choice={"type": "tool", "name": "score_scene"},
            messages=[{"role": "user", "content": prompt}]
        )
        for block in resp.content:
            if block.type == "tool_use" and block.name == "score_scene":
                return block.input
    except Exception as e:
        print(f"  [judge] error: {e}")
    return {"grounding": None, "vividness": None, "note": "judge error"}


# ---------- scoring ----------

def score(result: dict | None, expected: dict) -> dict:
    if result is None:
        return {"valid_json": False, "location_match": False, "tag_overlap": 0.0, "transport_match": False, "date_match": None}

    # location match
    loc = result.get("location_name", "").lower()
    if "location_name" in expected:
        loc_match = expected["location_name"].lower() in loc or loc in expected["location_name"].lower()
    elif "location_name_contains_any" in expected:
        loc_match = any(kw.lower() in loc for kw in expected["location_name_contains_any"])
    else:
        loc_match = True

    # tag overlap
    result_tags = {t.lower() for t in result.get("tags", [])}
    if "tags_must_include_any" in expected:
        required = {t.lower() for t in expected["tags_must_include_any"]}
        tag_overlap = len(result_tags & required) / max(len(required), 1)
    else:
        tag_overlap = 1.0

    # transport emoji
    emoji = result.get("transport_emoji", "")
    if "transport_emoji" in expected:
        transport_match = emoji == expected["transport_emoji"]
    elif "transport_emoji_any" in expected:
        transport_match = emoji in expected["transport_emoji_any"]
    else:
        transport_match = True

    # date match
    date_match = None
    if expected.get("date"):
        date_match = result.get("date") == expected["date"]

    return {
        "valid_json": True,
        "location_match": loc_match,
        "tag_overlap": round(tag_overlap, 2),
        "transport_match": transport_match,
        "date_match": date_match,
    }


def aggregate(scores: list[dict]) -> dict:
    n = len(scores)
    if n == 0:
        return {}

    grounding_vals = [s["grounding"] for s in scores if s.get("grounding") is not None]
    vividness_vals = [s["vividness"] for s in scores if s.get("vividness") is not None]

    result = {
        "valid_json_rate": sum(s["valid_json"] for s in scores) / n,
        "location_match_rate": sum(s["location_match"] for s in scores) / n,
        "avg_tag_overlap": sum(s["tag_overlap"] for s in scores) / n,
        "transport_match_rate": sum(s["transport_match"] for s in scores) / n,
        "n": n,
    }
    if grounding_vals:
        result["avg_grounding_1to5"] = round(sum(grounding_vals) / len(grounding_vals), 2)
    if vividness_vals:
        result["avg_vividness_1to5"] = round(sum(vividness_vals) / len(vividness_vals), 2)
    return result


# ---------- main ----------

def run_eval(strategy: str) -> None:
    with open(TEST_CASES_PATH, encoding="utf-8") as f:
        cases = json.load(f)

    strategies = ["baseline", "tool_use"] if strategy == "both" else [strategy]

    for strat in strategies:
        print(f"\n{'='*50}")
        print(f"Strategy: {strat}")
        print("=" * 50)
        all_scores = []

        for case in cases:
            print(f"\n[{case['id']}] {case['description']}")
            time.sleep(0.5)

            result = run_baseline(case) if strat == "baseline" else run_tool_use(case)
            s = score(result, case["expected"])

            # model-as-judge: grounding + vividness
            if result and result.get("scene_description"):
                j = judge_scene(case["image_description"], result["scene_description"])
                s["grounding"] = j.get("grounding")
                s["vividness"] = j.get("vividness")
                s["judge_note"] = j.get("note", "")
            else:
                s["grounding"] = None
                s["vividness"] = None
                s["judge_note"] = ""

            all_scores.append(s)

            print(f"  location_match={s['location_match']}  tag_overlap={s['tag_overlap']}  "
                  f"transport_match={s['transport_match']}  date_match={s['date_match']}")
            print(f"  grounding={s['grounding']}/5  vividness={s['vividness']}/5  judge: {s['judge_note']}")
            if result:
                print(f"  → {result.get('location_name')} | {result.get('transport_emoji')} | {result.get('tags')}")

        agg = aggregate(all_scores)
        print(f"\n--- {strat} aggregate ---")
        for k, v in agg.items():
            print(f"  {k}: {v:.2f}" if isinstance(v, float) else f"  {k}: {v}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", choices=["baseline", "tool_use", "both"], default="both")
    args = parser.parse_args()
    run_eval(args.strategy)
