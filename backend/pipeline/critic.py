"""
Stage 4: Quality critic using Gemini.
Compares original bad frame vs generated replacement — checks if it's actually better.
"""
import json
import cv2
import os
from ..models.schemas import Insert
from .. import config


PROMPT = """
You are a video quality critic. You'll see two images:
  IMAGE A: a frame from the ORIGINAL clip that was flagged as bad quality
  IMAGE B: first frame of an AI-GENERATED replacement clip

The original was flagged for: {issues}

Evaluate whether the replacement is an improvement. Return JSON:
{{
  "better_quality": true/false,
  "maintains_scene": true/false,
  "artifacts": true/false,
  "natural_looking": true/false,
  "pass": true/false,
  "notes": "short explanation"
}}

"pass" = true means IMAGE B is a clear improvement over IMAGE A and safe to use.
"""


def _first_frame(clip_path: str, out_path: str) -> str:
    cap = cv2.VideoCapture(clip_path)
    ok, f = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"Cannot read clip: {clip_path}")
    cv2.imwrite(out_path, f)
    return out_path


def review(insert: Insert, anchor_path: str, issues: list) -> Insert:
    first_path = os.path.join(config.FRAMES, f"{insert.id}_gen_first.png")
    _first_frame(insert.clip_path, first_path)

    issues_str = ", ".join(issues) if issues else "general quality"
    print(f"[critic] calling Gemini to compare original vs replacement, issues={issues_str}", flush=True)

    from google import genai
    from google.genai import types
    try:
        client = genai.Client(api_key=config.GOOGLE_API_KEY)
        with open(anchor_path, "rb") as f:
            anchor_bytes = f.read()
        with open(first_path, "rb") as f:
            gen_bytes = f.read()
        resp = client.models.generate_content(
            model=config.VLM_MODEL,
            contents=[
                PROMPT.format(issues=issues_str),
                "Image A (original bad frame):",
                types.Part.from_bytes(data=anchor_bytes, mime_type="image/png"),
                "Image B (generated replacement):",
                types.Part.from_bytes(data=gen_bytes, mime_type="image/png"),
            ],
        )
        text = resp.text.replace("```json", "").replace("```", "").strip()
        d = json.loads(text)
        print(f"[critic] Gemini: pass={d.get('pass')} notes={d.get('notes')}", flush=True)
    except Exception as e:
        print(f"[critic] ERROR: Gemini call failed: {e}", flush=True)
        d = {"pass": False, "notes": f"critic error: {e}"}

    insert.critic_pass = bool(d.get("pass", False))
    insert.critic_notes = d.get("notes", "")
    return insert
