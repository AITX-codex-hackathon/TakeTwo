"""
Stage 4: Multi-frame quality critic using GPT-4o.
Samples 3 frames from the generated clip (25%, 50%, 75% through) and compares
all of them against the original anchor — catches mid-clip artifacts, temporal
inconsistency, and style drift that single-frame checks miss entirely.
"""
import json
import base64
import os
import shutil
import subprocess
import tempfile
from typing import Optional

from ..models.schemas import Insert
from .. import config
from .api_utils import retry_api, parse_json


PROMPT = """You are a senior motion picture quality-control supervisor reviewing an AI-generated replacement clip.

VIDEO CONTEXT: {video_type} video, {color_palette} palette, {visual_style} style
ORIGINAL ISSUES THAT FLAGGED THIS SHOT: {issues}

You will see five images:
  IMAGE A    — the original problem frame (low production value, flagged for replacement)
  IMAGE B1   — first quarter of the AI replacement (25% through)
  IMAGE B2   — middle of the AI replacement (50% through)
  IMAGE B3   — last quarter of the AI replacement (75% through)
  IMAGE CMP  — A and B2 side-by-side for continuity and quality comparison

Evaluate on six dimensions:

1. fixes_issues       — Does the replacement address the original issues ({issues})? (bool)
2. has_motion         — VISUAL DELTA TEST: Are B1 and B3 visually different?
                        Is there genuine camera movement or subject action between them?
                        A replacement where B1 ≈ B3 (near-zero delta) fails this check
                        because it is a glorified still image, not a living shot. (bool)
3. temporal_consistent— Are B1, B2, B3 coherent — no flicker, warp, or sudden color jump? (bool)
4. natural_looking    — Does this look like real camera footage, not AI-generated art? (bool)
5. better_than_original — Using CMP: does B2 have HIGHER cinematic production value than A?
                          Better lighting, composition, or visual interest? (bool)
6. seamless_cut       — Using CMP: could an editor cut between A and B2 without it being
                        jarring? Subject position, scale, and lighting continuity. (bool)

Return ONLY valid JSON:
{{
  "fixes_issues": true/false,
  "has_motion": true/false,
  "temporal_consistent": true/false,
  "natural_looking": true/false,
  "better_than_original": true/false,
  "seamless_cut": true/false,
  "pass": true/false,
  "confidence": 0.0-1.0,
  "notes": "be specific — especially call out has_motion and better_than_original verdicts"
}}

PASS RULES:
- "pass" = true only when: fixes_issues AND has_motion AND temporal_consistent AND natural_looking
- better_than_original and seamless_cut affect confidence score but do not block pass
- A static replacement (has_motion = false) is always a failed replacement"""


def _extract_frame_at_pct(clip_path: str, pct: float) -> str:
    """Extract a frame at pct (0.0–1.0) through the clip. Returns base64 or empty string."""
    ffprobe = shutil.which("ffprobe")
    ffmpeg  = shutil.which("ffmpeg")
    if not ffprobe or not ffmpeg:
        return ""
    try:
        out = subprocess.check_output([
            ffprobe, "-v", "quiet", "-print_format", "json",
            "-show_streams", clip_path,
        ])
        streams = json.loads(out)["streams"]
        video   = next((s for s in streams if s["codec_type"] == "video"), None)
        if not video:
            return ""
        duration = float(video.get("duration", 0))
        if duration <= 0:
            return ""
        ts = max(0.05, min(duration - 0.05, duration * pct))
    except Exception:
        return ""

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        subprocess.run([
            ffmpeg, "-y", "-ss", f"{ts:.3f}", "-i", clip_path,
            "-frames:v", "1", "-q:v", "3", tmp_path,
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(tmp_path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except Exception:
        return ""
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@retry_api(max_retries=3, base_delay=5)
def _call_gpt(anchor_b64: str, gen_frames: list, issues_str: str,
              video_meta: dict) -> dict:
    from openai import OpenAI
    client = OpenAI(api_key=config.OPENAI_API_KEY)

    vtype   = video_meta.get("video_type", "general")
    palette = video_meta.get("color_palette", "neutral")
    vstyle  = video_meta.get("visual_style", "mixed")

    content = [{
        "type": "text",
        "text": PROMPT.format(
            video_type=vtype, color_palette=palette,
            visual_style=vstyle, issues=issues_str,
        ),
    }]

    content.append({"type": "text", "text": "IMAGE A (original bad frame):"})
    content.append({"type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{anchor_b64}",
                                  "detail": "low"}})

    labels = ["IMAGE B1 (replacement — first quarter):",
              "IMAGE B2 (replacement — middle):",
              "IMAGE B3 (replacement — last quarter):"]
    for label, b64 in zip(labels, gen_frames):
        if b64:
            content.append({"type": "text", "text": label})
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}",
                                          "detail": "low"}})

    # Seamless-cut comparison: original anchor + mid-clip frame side by side
    # so GPT can judge subject position / continuity drift directly
    mid_b64 = gen_frames[1] if len(gen_frames) > 1 and gen_frames[1] else None
    if mid_b64:
        content.append({"type": "text",
                        "text": "IMAGE CMP — direct comparison for seamless_cut check:"})
        content.append({"type": "text", "text": "Original (A):"})
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{anchor_b64}",
                                      "detail": "low"}})
        content.append({"type": "text", "text": "Mid-replacement (B2):"})
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{mid_b64}",
                                      "detail": "low"}})

    resp = client.chat.completions.create(
        model=config.VLM_MODEL,
        messages=[{"role": "user", "content": content}],
        max_tokens=400,
        temperature=0,
    )
    return parse_json(resp.choices[0].message.content, "critic") or {}


def review(insert: Insert, anchor_path: str, issues: list,
           video_meta: Optional[dict] = None) -> Insert:
    video_meta = video_meta or {}
    issues_str = ", ".join(issues) if issues else "general quality"

    print(f"[critic] extracting 3 frames from generated clip...", flush=True)
    gen_frames = [
        _extract_frame_at_pct(insert.clip_path, 0.25),
        _extract_frame_at_pct(insert.clip_path, 0.50),
        _extract_frame_at_pct(insert.clip_path, 0.75),
    ]
    valid = sum(1 for f in gen_frames if f)
    if valid == 0:
        print("[critic] cannot read generated clip — skipping critic", flush=True)
        insert.critic_pass = False
        insert.critic_notes = "could not read generated clip"
        return insert

    print(f"[critic] calling GPT-4o ({valid}/3 frames captured), issues={issues_str}",
          flush=True)

    with open(anchor_path, "rb") as f:
        anchor_b64 = base64.b64encode(f.read()).decode()

    try:
        d = _call_gpt(anchor_b64, gen_frames, issues_str, video_meta)
        print(
            f"[critic] pass={d.get('pass')} "
            f"has_motion={d.get('has_motion')} "
            f"better={d.get('better_than_original')} "
            f"confidence={d.get('confidence')} "
            f"— {d.get('notes')}",
            flush=True,
        )
    except Exception as e:
        print(f"[critic] ERROR: {e}", flush=True)
        d = {"pass": False, "notes": f"critic error: {e}"}

    insert.critic_pass  = bool(d.get("pass", False))
    insert.critic_notes = d.get("notes", "")
    return insert
