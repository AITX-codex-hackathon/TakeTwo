"""
Stage 1: Two-pass video analysis.
  Pass 1 (meta)  — 8 evenly-spaced frames → GPT-4o → understand video type, style, palette
  Pass 2 (detect)— all sampled frames + meta context → GPT-4o → find up to MAX_BAD_CLIPS moments
Results cached by video MD5.  Returns (List[Slot], video_meta_dict).
"""
import os
import json
import base64
import hashlib
import shutil
import subprocess
import tempfile
from typing import List, Tuple

from ..models.schemas import Slot, new_id
from .. import config
from .api_utils import retry_api, parse_json

# Sample every 5s for videos < 5 min, every 10s for longer ones
INTERVAL_SHORT = 5.0
INTERVAL_LONG  = 10.0
META_FRAMES    = 8      # frames sent in the meta pass


# ─── helpers ────────────────────────────────────────────────────────────────

def _video_hash(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _cache_path(video_path: str) -> str:
    # _v2 suffix so old single-list caches are ignored
    return str(config.CACHE / f"{_video_hash(video_path)}_detect_v2.json")


def _ffprobe(video_path: str) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe required")
    out = subprocess.check_output([
        ffprobe, "-v", "quiet", "-print_format", "json",
        "-show_streams", video_path,
    ])
    streams = json.loads(out)["streams"]
    video = next((s for s in streams if s["codec_type"] == "video"), None)
    if not video:
        raise ValueError("No video stream found")
    num, den = video.get("r_frame_rate", "30/1").split("/")
    fps = float(num) / float(den)
    duration = float(video.get("duration", 0))
    return {"fps": fps, "duration": duration}


def _extract_frame(video_path: str, timestamp: float, out_path: str):
    ffmpeg = shutil.which("ffmpeg")
    subprocess.run([
        ffmpeg, "-y", "-ss", f"{timestamp:.3f}", "-i", video_path,
        "-frames:v", "1", "-q:v", "2", out_path,
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _b64_frame(video_path: str, timestamp: float) -> str:
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
    _extract_frame(video_path, timestamp, tmp_path)
    with open(tmp_path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    os.unlink(tmp_path)
    return data


def extract_anchor(video_path: str, frame_idx: int, out_path: str, fps: float) -> str:
    _extract_frame(video_path, frame_idx / fps, out_path)
    return out_path


def extract_clip(video_path: str, start_frame: int, end_frame: int,
                 out_path: str, fps: float) -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg required")
    start_t = start_frame / fps
    duration = (end_frame - start_frame + 1) / fps
    subprocess.run([
        ffmpeg, "-y", "-ss", f"{start_t:.6f}", "-i", video_path,
        "-t", f"{duration:.6f}",
        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
        "-an", "-movflags", "+faststart", out_path,
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_path


# ─── GPT calls ──────────────────────────────────────────────────────────────

@retry_api(max_retries=3, base_delay=5)
def _gpt_video_meta(video_path: str, duration: float) -> dict:
    """Pass 1: understand what kind of video this is so downstream prompts match its style."""
    from openai import OpenAI
    client = OpenAI(api_key=config.OPENAI_API_KEY)

    n = min(META_FRAMES, max(4, int(duration / 15)))
    step = duration / (n + 1)
    sample_ts = [round(step * (i + 1), 2) for i in range(n)]

    print(f"[detect/meta] sampling {n} frames for video context...", flush=True)

    content = [{
        "type": "text",
        "text": (
            f"These {n} frames are evenly sampled from a {duration:.0f}s video.\n"
            "Identify the video's visual fingerprint so AI replacements can match its style.\n\n"
            "Return ONLY valid JSON:\n"
            '{"video_type": "vlog|travel|interview|documentary|action|product|tutorial|other", '
            '"visual_style": "handheld|stabilized|drone|tripod|mixed", '
            '"color_palette": "warm|cool|neutral|high_contrast|muted|golden_hour|blue_tone", '
            '"subject": "people|nature|urban|landscape|indoor|mixed", '
            '"lighting": "natural_bright|natural_dim|golden_hour|artificial|low_light|mixed", '
            '"description": "one sentence: what is happening in this video"}'
        ),
    }]
    for ts in sample_ts:
        b64 = _b64_frame(video_path, ts)
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
        })

    resp = client.chat.completions.create(
        model=config.VLM_MODEL,
        messages=[{"role": "user", "content": content}],
        max_tokens=300,
        temperature=0,
    )
    result = parse_json(resp.choices[0].message.content, "meta")
    if not result or not isinstance(result, dict):
        return {
            "video_type": "general", "visual_style": "mixed",
            "color_palette": "neutral", "subject": "mixed",
            "lighting": "mixed", "description": "video footage",
        }
    print(
        f"[detect/meta] type={result.get('video_type')} "
        f"style={result.get('visual_style')} "
        f"palette={result.get('color_palette')} "
        f"lighting={result.get('lighting')}",
        flush=True,
    )
    return result


@retry_api(max_retries=3, base_delay=5)
def _gpt_find_worst(video_path: str, timestamps: List[float],
                    video_meta: dict, max_clips: int) -> List[dict]:
    """Pass 2: detect bad clips with video context. Returns up to max_clips candidates."""
    from openai import OpenAI
    client = OpenAI(api_key=config.OPENAI_API_KEY)

    vtype   = video_meta.get("video_type", "general")
    palette = video_meta.get("color_palette", "neutral")
    vstyle  = video_meta.get("visual_style", "mixed")
    lighting = video_meta.get("lighting", "mixed")

    print(f"[detect/find] sending {len(timestamps)} frames, max_clips={max_clips}...", flush=True)

    content = [{
        "type": "text",
        "text": (
            f"You are a film director reviewing {len(timestamps)} frames from a {vtype} video "
            f"(style: {vstyle}, palette: {palette}, lighting: {lighting}).\n"
            f"Frames are labeled Frame 0–{len(timestamps) - 1}, sampled every few seconds.\n\n"
            "Score each frame on CINEMATIC VALUE — not just technical quality.\n"
            "  1.0 = Hollywood feature film shot\n"
            "  0.6 = Acceptable social media content\n"
            "  0.1 = Security camera / accidental footage\n\n"
            f"Flag up to {max_clips} frames below 0.6 cinematic score. "
            "Use ALL of these criteria:\n"
            "  TECHNICAL   — blurry, shaky (accidental not stylistic), underexposed, overexposed, "
            "grainy, out of focus, compression artifacts\n"
            "  DEAD AIR    — static shot with zero camera movement AND zero subject action "
            "(nothing is happening, no narrative purpose)\n"
            "  FLAT LIGHT  — uninspiring, muddy, or flat lighting with no mood, depth, or drama\n"
            "  WEAK FRAME  — centered boring composition, no leading lines, no depth, no "
            "cinematic intent\n"
            "  VLOG TRAP   — accidental handheld shake that looks amateur, not stylistic\n\n"
            "Return ONLY a JSON array (0–{max_clips} entries), lowest cinematic score first.\n"
            "If all frames score ≥ 0.6, return [].\n"
            '[{"frame_index": 3, "timestamp": 15.0, "cinematic_score": 0.3, '
            '"issues": ["dead_air", "flat_light"], "reason": "one concise sentence"}]'
        ).format(max_clips=max_clips),
    }]
    for i, ts in enumerate(timestamps):
        b64 = _b64_frame(video_path, ts)
        content.append({"type": "text", "text": f"Frame {i} ({ts:.1f}s):"})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
        })

    resp = client.chat.completions.create(
        model=config.VLM_MODEL,
        messages=[{"role": "user", "content": content}],
        max_tokens=1024,
        temperature=0,
    )
    result = parse_json(resp.choices[0].message.content, "detection")
    if result is None:
        return []
    if isinstance(result, dict):
        result = [result]
    if not isinstance(result, list):
        return []

    # Support both key names for backward cache compatibility
    def _score(r):
        return float(r.get("cinematic_score", r.get("quality_score", 1.0)))

    filtered = [r for r in result if isinstance(r, dict) and _score(r) < 0.6]
    filtered.sort(key=_score)

    print(f"[detect/find] GPT-4o returned {len(result)} candidate(s), "
          f"{len(filtered)} below cinematic threshold:", flush=True)
    for r in filtered[:max_clips]:
        print(f"[detect/find]   {r['timestamp']:.1f}s cinematic_score={_score(r):.2f} "
              f"issues={r.get('issues')} — {r.get('reason', '')}", flush=True)
    return filtered[:max_clips]


# ─── public entry point ─────────────────────────────────────────────────────

def find_bad_clips(video_path: str) -> Tuple[List[Slot], dict]:
    """
    Returns (slots, video_meta).
    video_meta is a dict with keys: video_type, visual_style, color_palette,
                                    subject, lighting, description.
    """
    info = _ffprobe(video_path)
    fps      = info["fps"]
    duration = info["duration"]

    if duration < 10.0:
        print(f"[detect] video too short ({duration:.1f}s), skipping", flush=True)
        return [], {}

    interval = INTERVAL_SHORT if duration < 300 else INTERVAL_LONG
    timestamps = []
    t = interval / 2
    while t < duration:
        timestamps.append(round(t, 2))
        t += interval
    print(f"[detect] video={duration:.1f}s, {len(timestamps)} sample frames "
          f"(interval={interval:.0f}s)", flush=True)

    cache_file = _cache_path(video_path)
    if os.path.exists(cache_file):
        with open(cache_file) as f:
            cached = json.load(f)
        # Support old list-only format
        if isinstance(cached, list):
            candidates, video_meta = cached, {}
        else:
            candidates = cached.get("candidates", [])
            video_meta = cached.get("video_meta", {})
        print(f"[detect] cache hit — {len(candidates)} candidate(s)", flush=True)
    else:
        print("[detect] Pass 1 — video context analysis...", flush=True)
        video_meta = _gpt_video_meta(video_path, duration)
        print(f"[detect] Pass 2 — bad clip detection (max {config.MAX_BAD_CLIPS})...",
              flush=True)
        candidates = _gpt_find_worst(video_path, timestamps, video_meta,
                                     config.MAX_BAD_CLIPS)
        with open(cache_file, "w") as f:
            json.dump({"candidates": candidates, "video_meta": video_meta}, f)
        print(f"[detect] cached to {cache_file}", flush=True)

    # Deduplicate: drop candidates within 5s of a higher-scoring one
    deduped = []
    for c in candidates:
        ts = float(c["timestamp"])
        if not any(abs(ts - float(p["timestamp"])) < 5.0 for p in deduped):
            deduped.append(c)

    clip_half = 2.5
    slots: List[Slot] = []
    for c in deduped:
        ts       = float(c["timestamp"])
        start_ts = max(0.0, ts - clip_half)
        end_ts   = min(duration, ts + clip_half)
        if end_ts - start_ts < 2.0:
            continue

        start_frame = int(start_ts * fps)
        end_frame   = int(end_ts   * fps)
        mid_frame   = int(ts       * fps)

        sid         = new_id()
        anchor_path = os.path.join(config.FRAMES, f"{sid}.png")
        clip_path   = os.path.join(config.CLIPS,  f"{sid}_original.mp4")

        print(f"[detect] extracting slot {sid[:8]} at {ts:.1f}s "
              f"({start_ts:.1f}→{end_ts:.1f}s)", flush=True)
        try:
            extract_anchor(video_path, mid_frame, anchor_path, fps)
            extract_clip(video_path, start_frame, end_frame, clip_path, fps)
        except Exception as e:
            print(f"[detect] extraction failed: {e}", flush=True)
            continue

        slots.append(Slot(
            id=sid,
            start_frame=start_frame,
            end_frame=end_frame,
            fps=fps,
            quality_score=float(
                c.get("cinematic_score", c.get("quality_score", 0.5))
            ),
            anchor_frame_path=anchor_path,
            issues=c.get("issues", []),
        ))
        print(f"[detect] slot {sid[:8]}: {c.get('reason', '')}", flush=True)

    print(f"[detect] final: {len(slots)} slot(s) from {len(deduped)} candidates", flush=True)
    return slots, video_meta
