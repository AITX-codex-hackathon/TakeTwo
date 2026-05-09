"""
Stage 2: Director's Cut analysis using GPT-4o.
Sends 3 frames (before → problem → after) for temporal context.
Treats video_meta as a hint, not a constraint — frame content is ground truth.
Injects a motion directive so every replacement is kinetic, not static.
Cached per anchor frame.
"""
import json
import base64
import os
import shutil
import subprocess
import tempfile
from typing import Optional

from ..models.schemas import SceneContext, Slot
from .. import config
from .api_utils import retry_api, parse_json


# Maps video_type → camera movement language injected into prompts.
# Goal: if the original shot was static/dead, the replacement MUST move.
MOTION_BY_TYPE = {
    "vlog":         "slow push-in with cinematic handheld drift",
    "travel":       "slow push-in, parallax depth effect, gentle handheld drift",
    "interview":    "subtle push-in toward subject, dynamic rack focus to background",
    "documentary":  "slow environmental pan, pull-back reveal, observational drift",
    "action":       "tracking shot following subject, dynamic handheld energy",
    "product":      "slow 180° orbit around subject, creeping push-in",
    "tutorial":     "smooth stabilized push-in, clean motivated camera movement",
    "other":        "slow push-in, subtle cinematic camera drift",
}

DEFAULT_MOTION = "slow push-in with subtle cinematic drift"


PROMPT = """You are a film director giving notes on a low-production-value moment in a video.

STYLE HINT (auto-detected — guide only, not a constraint):
- Type: {video_type}
- Visual style: {visual_style}
- Color palette: {color_palette}
- Lighting: {lighting}
- Subject: {subject}
- Summary: {description}

IMPORTANT: The three frames are ground truth. If the frames contradict the hint
(e.g., hint says "anamorphic cinema" but frames look like a phone vlog), trust
your eyes — base every decision on the actual frame content.

FLAGGED ISSUES: {issues}

MOTION DIRECTIVE (apply to every replacement prompt):
"{motion_directive}"
The original shot scored low on cinematic value — possibly dead air, flat light,
or weak framing. Every replacement MUST include this camera movement. A static
replacement is a failed replacement. Even "still" shots must have micro-motion:
leaves moving, light shifting, subtle environmental life.

Frames in order:
  FRAME A — scene just BEFORE the problem (temporal context)
  FRAME B — the PROBLEM FRAME (what needs replacing)
  FRAME C — scene just AFTER the problem (temporal context)

Return ONLY valid JSON:
{{
  "description": "one sentence: what is literally happening in frame B",
  "issues_detail": "specific visual/technical problems in frame B",
  "mood": "precise mood phrase from what you SEE + the required camera movement, e.g. 'golden hour street with slow push-in through traffic'",
  "replacement_prompts": [
    "Prompt 1 — ground truth: actual subject/setting visible in B, {color_palette} tones, {lighting} lighting, MUST include {motion_directive}. Describe the motion explicitly. Under 40 words.",
    "Prompt 2 — same subject and location as frames, different cinematic angle or moment, MUST include {motion_directive}, highest production value. Under 40 words.",
    "Prompt 3 — most elevated Director's Cut interpretation of the actual scene. Same subject/location, push the lighting and motion to cinematic extreme. Under 40 words."
  ],
  "negative_prompt": "static shot, frozen frame, no camera movement, cartoon, CGI, animation, text overlays, watermarks, wrong subject matter",
  "motion_directive": "{motion_directive}",
  "recommendation": "replace or cut — cut only if the scene content adds zero narrative value to a {video_type} video"
}}"""


def _cache_path(anchor_path: str) -> str:
    name = os.path.splitext(os.path.basename(anchor_path))[0]
    return str(config.CACHE / f"analyze_v2_{name}.json")


def _extract_frame_at(video_path: str, timestamp: float) -> str:
    """Extract a frame at timestamp, return base64 string."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        subprocess.run([
            ffmpeg, "-y", "-ss", f"{timestamp:.3f}", "-i", video_path,
            "-frames:v", "1", "-q:v", "3", tmp_path,
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(tmp_path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except Exception:
        return None
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _b64_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


@retry_api(max_retries=3, base_delay=5)
def _call_gpt(anchor_path: str, issues: list, video_path: Optional[str],
              slot: Optional[Slot], video_meta: dict) -> dict:
    from openai import OpenAI
    client = OpenAI(api_key=config.OPENAI_API_KEY)

    vtype    = video_meta.get("video_type", "general")
    vstyle   = video_meta.get("visual_style", "mixed")
    palette  = video_meta.get("color_palette", "neutral")
    lighting = video_meta.get("lighting", "mixed")
    subject  = video_meta.get("subject", "mixed")
    desc     = video_meta.get("description", "video footage")
    motion   = MOTION_BY_TYPE.get(vtype, DEFAULT_MOTION)

    prompt_text = PROMPT.format(
        video_type=vtype, visual_style=vstyle, color_palette=palette,
        lighting=lighting, subject=subject, description=desc,
        motion_directive=motion,
        issues=", ".join(issues) if issues else "general quality issues",
    )

    content = [{"type": "text", "text": prompt_text}]

    # ── Try to add temporal context (before/after frames) ──
    if video_path and slot and os.path.exists(video_path):
        fps = slot.fps
        mid_t   = ((slot.start_frame + slot.end_frame) / 2) / fps
        before_t = max(0.0, mid_t - 3.0)
        after_t  = mid_t + 3.0

        b64_before = _extract_frame_at(video_path, before_t)
        b64_after  = _extract_frame_at(video_path, after_t)

        if b64_before:
            content.append({"type": "text", "text": "FRAME A (context — before the problem):"})
            content.append({"type": "image_url",
                             "image_url": {"url": f"data:image/jpeg;base64,{b64_before}",
                                           "detail": "low"}})
    else:
        b64_before = None
        b64_after  = None

    # ── Problem frame (always present) ──
    content.append({"type": "text", "text": "FRAME B (the problem frame — flagged for issues):"})
    content.append({"type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{_b64_image(anchor_path)}",
                                  "detail": "high"}})

    if video_path and slot and b64_after:
        content.append({"type": "text", "text": "FRAME C (context — after the problem):"})
        content.append({"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{b64_after}",
                                       "detail": "low"}})

    resp = client.chat.completions.create(
        model=config.VLM_MODEL,
        messages=[{"role": "user", "content": content}],
        max_tokens=800,
        temperature=0,
    )
    return parse_json(resp.choices[0].message.content, "analyze") or {}


def analyze_anchor(anchor_path: str, issues: list,
                   video_path: Optional[str] = None,
                   slot: Optional[Slot] = None,
                   video_meta: Optional[dict] = None) -> SceneContext:
    video_meta = video_meta or {}

    cache_file = _cache_path(anchor_path)
    if os.path.exists(cache_file):
        print("[analyze] cache hit — skipping API call", flush=True)
        with open(cache_file) as f:
            d = json.load(f)
        return SceneContext.from_dict(d)

    print(f"[analyze] calling GPT-4o with temporal context, issues={issues}", flush=True)
    try:
        d = _call_gpt(anchor_path, issues, video_path, slot, video_meta)
        print(
            f"[analyze] → {d.get('description', '?')} "
            f"| recommendation={d.get('recommendation')} "
            f"| mood={d.get('mood')}",
            flush=True,
        )
        with open(cache_file, "w") as f:
            json.dump(d, f)
    except Exception as e:
        print(f"[analyze] ERROR: {e}", flush=True)
        d = {}

    vtype  = video_meta.get("video_type", "general")
    motion = MOTION_BY_TYPE.get(vtype, DEFAULT_MOTION)

    return SceneContext(
        description=d.get("description", "scene from video"),
        issues_detail=d.get("issues_detail", ", ".join(issues)),
        mood=d.get("mood", f"cinematic, {motion}"),
        replacement_prompts=d.get("replacement_prompts") or [
            f"cinematic replacement for a {vtype} video, "
            f"{video_meta.get('color_palette','neutral')} tones, {motion}, "
            "photorealistic 4K quality"
        ],
        recommendation=d.get("recommendation", "replace"),
        negative_prompt=d.get("negative_prompt",
                               "static shot, no camera movement, cartoon, CGI, watermarks"),
        motion_directive=d.get("motion_directive", motion),
    )
