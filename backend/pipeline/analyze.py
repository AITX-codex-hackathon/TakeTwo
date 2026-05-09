"""
Stage 2: Analyze the anchor frame using Gemini.
Cached per anchor frame path so restarts skip the API call.
"""
import json
import os
from ..models.schemas import SceneContext
from .. import config


PROMPT = """
You are a professional video editor's AI assistant analyzing a frame from a video clip
that has been flagged for quality issues.

The detected issues are: {issues}

Analyze this frame and return JSON with:
  description: one-sentence description of what's happening in this scene
  issues_detail: explain specifically what quality problems you see
  mood: short phrase for the mood/tone (e.g. "warm sunset interview", "dark moody b-roll")
  replacement_prompts: 2-3 prompts for generating a BETTER cinematic replacement clip of the
    same scene/subject. Each prompt should be photorealistic and cinematic. Under 30 words each.
  recommendation: "replace" if the scene is worth keeping with better quality,
    or "cut" if it adds nothing and should be removed entirely.

Return ONLY valid JSON.
"""


def _cache_path(anchor_path: str) -> str:
    name = os.path.splitext(os.path.basename(anchor_path))[0]
    return str(config.CACHE / f"analyze_{name}.json")


def analyze_anchor(anchor_path: str, issues: list) -> SceneContext:
    cache_file = _cache_path(anchor_path)
    if os.path.exists(cache_file):
        print(f"[analyze] using cached analysis (skipping Gemini call)", flush=True)
        with open(cache_file) as f:
            d = json.load(f)
        return SceneContext.from_dict(d)

    print(f"[analyze] calling Gemini to analyze frame, issues={issues}", flush=True)
    from google import genai
    from google.genai import types
    try:
        client = genai.Client(api_key=config.GOOGLE_API_KEY)
        with open(anchor_path, "rb") as f:
            img_bytes = f.read()
        resp = client.models.generate_content(
            model=config.VLM_MODEL,
            contents=[
                PROMPT.format(issues=", ".join(issues)),
                types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
            ],
        )
        text = resp.text.replace("```json", "").replace("```", "").strip()
        d = json.loads(text)
        print(f"[analyze] Gemini: description={d.get('description')} recommendation={d.get('recommendation')}", flush=True)
        with open(cache_file, "w") as f:
            json.dump(d, f)
    except Exception as e:
        print(f"[analyze] ERROR: Gemini call failed: {e}", flush=True)
        d = {}

    return SceneContext(
        description=d.get("description", "scene from video"),
        issues_detail=d.get("issues_detail", ", ".join(issues)),
        mood=d.get("mood", "neutral"),
        replacement_prompts=d.get("replacement_prompts", ["high quality cinematic version of this scene"]),
        recommendation=d.get("recommendation", "replace"),
    )
