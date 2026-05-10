"""
Stage 2: Director's Cut analysis using GPT-4o.
Sends lead-in frames, the problem anchor, and the resume frame for context.
Treats video_meta as a hint, not a constraint — frame content is ground truth.
Injects a motion directive so every replacement inherits the incoming camera
velocity before easing into a cinematic handover.
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
from .api_utils import retry_api, parse_json, wait_for_openai_image_slot


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

MOTION_LABELS = {
    "static": "a static or nearly locked-off shot",
    "pan_left": "a leftward pan",
    "pan_right": "a rightward pan",
    "tilt_up": "an upward tilt",
    "tilt_down": "a downward tilt",
    "dolly_in": "a forward dolly-in",
    "dolly_out": "a backward dolly-out",
}


PROMPT = """You are a film director giving notes on a low-production-value moment in a video.

STYLE HINT (auto-detected — guide only, not a constraint):
- Type: {video_type}
- Visual style: {visual_style}
- Color palette: {color_palette}
- Lighting: {lighting}
- Subject: {subject}
- Summary: {description}

IMPORTANT: The supplied frames are ground truth. If the frames contradict the hint
(e.g., hint says "anamorphic cinema" but frames look like a phone vlog), trust
your eyes — base every decision on the actual frame content.

FLAGGED ISSUES: {issues}

CINEMATIC HANDOVER STRATEGY:
{handover_strategy}

TARGET REPLACEMENT DURATION: {replacement_duration}
INCOMING CAMERA MOTION: {incoming_motion}
NEXT CLEAN CUT: {next_cut}

MOTION DIRECTIVE (apply to every replacement prompt):
"{motion_directive}"
The original shot scored low on cinematic value — possibly dead air, darkness,
flat light, weak framing, or amateur motion. Do not simply fix pixels. Redesign
the shot as a subtle cinematic sequence that still belongs to this exact video.
The replacement MUST match the incoming camera velocity for the first few
frames, then gradually ramp into motivated 3D movement. Avoid excessive action.
If the source is underexposed, lift shadows naturally, preserve believable
highlights, and keep the scene mood. A static replacement is a failed
replacement. Even "still" shots must have small real-world motion: breathing,
cloth, leaves, dust, light, reflections, or handheld drift.

Frames, when provided:
  FRAME A1–A6 — optional lead-in frames immediately before the replacement start
  FRAME B — the anchor/problem frame where AI takes over (always present)
  FRAME C — optional clean resume point, usually after the hard cut

Return ONLY valid JSON:
{{
  "description": "one sentence: what is literally happening in frame B",
  "issues_detail": "specific visual/technical problems in frame B",
  "mood": "precise mood phrase from what you SEE + the handover motion, e.g. 'golden hour street, matching left pan into slow 3D push-in'",
  "replacement_prompts": [
    "Prompt 1 — ground truth: actual subject/setting visible in B, {color_palette} tones, {lighting} lighting, subtle photoreal cinematic improvement, MUST include the initial velocity match and gradual cinematic ramp. Under 50 words.",
    "Prompt 2 — same subject and location as frames, improved exposure if dark, different but compatible cinematic angle or moment, MUST cover the full handover duration and end cleanly. Under 50 words.",
    "Prompt 3 — most elevated but restrained Director's Cut interpretation of the actual scene. Same subject/location/theme, natural light improvement, realistic motion, no cartoon look. Under 50 words."
  ],
  "negative_prompt": "static shot, frozen frame, no camera movement, cartoon, CGI, animation, anime, plastic skin, over-sharpened, oversaturated, surreal, text overlays, watermarks, wrong subject matter",
  "motion_directive": "{motion_directive}",
  "recommendation": "replace or cut — cut only if the scene content adds zero narrative value to a {video_type} video"
}}"""


def _transition_context(slot: Optional[Slot], fallback_motion: str) -> dict:
    if not slot or not slot.transition:
        return {
            "motion_directive": fallback_motion,
            "handover_strategy": (
                "No reliable transition metadata is available. Start compositionally "
                "close to the anchor frame, then ease into cinematic motion."
            ),
            "replacement_duration": "5.0s",
            "incoming_motion": "unknown",
            "next_cut": "unknown",
        }

    transition = slot.transition
    duration = max(0.1, slot.replacement_duration_sec)
    motion_label = MOTION_LABELS.get(transition.motion_type, transition.motion_type)
    clean_cut = slot.resume_frame != slot.end_frame

    if transition.motion_type == "static":
        lead = (
            "Begin almost locked to the anchor composition for 0.3–0.5 seconds, "
            "then ease smoothly into motion; do not snap from stillness into a fast move."
        )
    else:
        lead = (
            f"Match the existing {motion_label} at roughly "
            f"{transition.motion_speed:.2f}px/frame for the first 0.5 seconds, "
            "then accelerate gradually into the cinematic move."
        )

    if clean_cut:
        strategy = (
            f"Take over the scene for {duration:.1f}s until the next clean cut. "
            "Do not try to stitch back into the amateur tail; build a complete "
            "cinematic beat that can hard-cut invisibly into the next original scene. "
            "Keep the shot restrained and thematically consistent."
        )
    else:
        strategy = (
            f"Replace the flagged {duration:.1f}s window. End on a stable, "
            "hard-cutable composition that matches the supplied resume frame so the "
            "splice does not feel like a visual jerk."
        )

    return {
        "motion_directive": f"{lead} Then {fallback_motion}. End cleanly for a hard cut.",
        "handover_strategy": strategy,
        "replacement_duration": f"{duration:.1f}s",
        "incoming_motion": f"{motion_label} ({transition.motion_speed:.2f}px/frame)",
        "next_cut": (
            f"{transition.next_cut_ts:.1f}s"
            if transition.next_cut_ts and transition.next_cut_ts > 0
            else "not detected within search window"
        ),
    }


def _cache_path(anchor_path: str) -> str:
    name = os.path.splitext(os.path.basename(anchor_path))[0]
    return str(config.CACHE / f"analyze_v4_i{config.OPENAI_MAX_IMAGES_PER_REQUEST}_{name}.json")


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
    client = OpenAI(api_key=config.require_openai_api_key())

    vtype    = video_meta.get("video_type", "general")
    vstyle   = video_meta.get("visual_style", "mixed")
    palette  = video_meta.get("color_palette", "neutral")
    lighting = video_meta.get("lighting", "mixed")
    subject  = video_meta.get("subject", "mixed")
    desc     = video_meta.get("description", "video footage")
    base_motion = MOTION_BY_TYPE.get(vtype, DEFAULT_MOTION)
    transition = _transition_context(slot, base_motion)
    motion = transition["motion_directive"]

    prompt_text = PROMPT.format(
        video_type=vtype, visual_style=vstyle, color_palette=palette,
        lighting=lighting, subject=subject, description=desc,
        motion_directive=motion,
        handover_strategy=transition["handover_strategy"],
        replacement_duration=transition["replacement_duration"],
        incoming_motion=transition["incoming_motion"],
        next_cut=transition["next_cut"],
        issues=", ".join(issues) if issues else "general quality issues",
    )

    content = [{"type": "text", "text": prompt_text}]
    image_budget = config.OPENAI_MAX_IMAGES_PER_REQUEST
    image_count = 0

    # ── Try to add temporal context (lead-in/resume frames) ──
    if image_budget > 1 and video_path and slot and os.path.exists(video_path):
        fps = slot.fps
        start_t = slot.start_frame / fps
        after_t = (slot.resume_frame + 1) / fps + 0.05

        lead_ts = []
        max_lead_frames = max(0, image_budget - 2)  # reserve anchor + optional resume
        for i in range(min(6, max_lead_frames)):
            ts = max(0.0, start_t - 0.9 + (i * 0.15))
            if not lead_ts or abs(ts - lead_ts[-1]) > 0.03:
                lead_ts.append(ts)

        for i, ts in enumerate(lead_ts, start=1):
            b64_lead = _extract_frame_at(video_path, ts)
            if b64_lead:
                content.append({
                    "type": "text",
                    "text": f"FRAME A{i} (lead-in {start_t - ts:.2f}s before AI takeover):",
                })
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64_lead}", "detail": "low"},
                })
                image_count += 1

        b64_after = _extract_frame_at(video_path, after_t) if image_count < image_budget - 1 else None
    else:
        b64_after  = None

    # ── Problem frame (always present) ──
    content.append({"type": "text", "text": "FRAME B (the problem frame — flagged for issues):"})
    content.append({"type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{_b64_image(anchor_path)}",
                                  "detail": "high"}})
    image_count += 1

    if video_path and slot and b64_after and image_count < image_budget:
        content.append({"type": "text", "text": "FRAME C (clean resume point after AI handover):"})
        content.append({"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{b64_after}",
                                       "detail": "low"}})
        image_count += 1

    wait_for_openai_image_slot(image_count)
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
    motion = _transition_context(slot, MOTION_BY_TYPE.get(vtype, DEFAULT_MOTION))["motion_directive"]

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
                               "static shot, no camera movement, cartoon, CGI, animation, anime, plastic skin, oversaturated, watermarks"),
        motion_directive=d.get("motion_directive", motion),
    )
