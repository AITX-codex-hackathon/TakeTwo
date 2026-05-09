"""
Stage 2: Analyze the anchor frame using GPT-4o.
Cached per anchor frame so restarts skip the API call.
"""
import json
import base64
import os
from ..models.schemas import SceneContext
from .. import config


PROMPT = """You are a professional video editor's AI assistant analyzing a frame flagged for quality issues.

The detected issues are: {issues}

Return JSON with:
  description: one-sentence description of what's happening in this scene
  issues_detail: explain specifically what quality problems you see
  mood: short phrase for the mood/tone (e.g. "warm sunset interview", "dark moody b-roll")
  replacement_prompts: 2-3 prompts for generating a BETTER cinematic replacement clip.
    Each should be photorealistic and cinematic. Under 30 words each.
  recommendation: "replace" if worth keeping with better quality, "cut" if it adds nothing.

Return ONLY valid JSON."""


def _cache_path(anchor_path: str) -> str:
    name = os.path.splitext(os.path.basename(anchor_path))[0]
    return str(config.CACHE / f"analyze_{name}.json")


def analyze_anchor(anchor_path: str, issues: list) -> SceneContext:
    cache_file = _cache_path(anchor_path)
    if os.path.exists(cache_file):
        print(f"[analyze] using cached analysis", flush=True)
        with open(cache_file) as f:
            d = json.load(f)
        return SceneContext.from_dict(d)

    print(f"[analyze] calling GPT-4o, issues={issues}", flush=True)
    from openai import OpenAI
    try:
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        with open(anchor_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        resp = client.chat.completions.create(
            model=config.VLM_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT.format(issues=", ".join(issues))},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}},
                ],
            }],
            max_tokens=600,
            temperature=0,
        )
        text = resp.choices[0].message.content.replace("```json", "").replace("```", "").strip()
        d = json.loads(text)
        print(f"[analyze] GPT-4o: {d.get('description')} → {d.get('recommendation')}", flush=True)
        with open(cache_file, "w") as f:
            json.dump(d, f)
    except Exception as e:
        print(f"[analyze] ERROR: {e}", flush=True)
        d = {}

    return SceneContext(
        description=d.get("description", "scene from video"),
        issues_detail=d.get("issues_detail", ", ".join(issues)),
        mood=d.get("mood", "neutral"),
        replacement_prompts=d.get("replacement_prompts", ["high quality cinematic version of this scene"]),
        recommendation=d.get("recommendation", "replace"),
    )
