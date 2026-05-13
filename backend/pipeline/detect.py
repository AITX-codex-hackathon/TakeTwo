"""
Stage 1: Two-pass video analysis + scene transition detection.
  Pass 1 (meta)     — 8 evenly-spaced frames → GPT-4o → understand video type, style, palette
  Pass 2 (detect)   — all sampled frames + meta context → GPT-4o → find up to MAX_BAD_CLIPS moments
  Pass 3 (handover) — per-slot: detect next hard cut, estimate incoming camera motion
                      → populates Slot.replace_end_frame and Slot.transition

Results cached by video MD5.  Returns (List[Slot], video_meta_dict).
"""
import os
import re
import json
import base64
import hashlib
import shutil
import subprocess
import tempfile
from typing import List, Tuple, Optional

from ..models.schemas import Slot, SceneTransition, new_id
from .. import config
from .api_utils import retry_api, parse_json, wait_for_openai_image_slot

INTERVAL_SHORT = 5.0
INTERVAL_LONG  = 10.0
META_FRAMES    = 8
# Look for a hard cut up to this many seconds after the slot's end frame
NEXT_CUT_SEARCH_WINDOW = 10.0
# Only extend replacement if next cut is beyond slot end AND within this many seconds
MAX_EXTENSION_S = 8.0
# Scene change sensitivity — 0.35 catches most hard cuts without false positives on camera moves
SCENE_THRESHOLD = 0.35


# ─── helpers ────────────────────────────────────────────────────────────────

def _video_hash(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _cache_path(video_path: str) -> str:
    return str(
        config.CACHE
        / f"{_video_hash(video_path)}_detect_v4_i{config.OPENAI_MAX_IMAGES_PER_REQUEST}.json"
    )


def _default_video_meta() -> dict:
    return {
        "video_type": "general",
        "visual_style": "mixed",
        "color_palette": "neutral",
        "subject": "mixed",
        "lighting": "mixed",
        "description": "video footage",
    }


def _sample_evenly(items: List[float], max_count: int) -> List[float]:
    if max_count <= 0 or len(items) <= max_count:
        return items
    if max_count == 1:
        return [items[len(items) // 2]]
    step = (len(items) - 1) / (max_count - 1)
    return [items[round(i * step)] for i in range(max_count)]


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


def _local_score_frame(video_path: str, timestamp: float) -> Optional[dict]:
    """Cheap local visual-quality pass for very low OpenAI image-rate projects."""
    try:
        import cv2
    except ImportError:
        return None

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        _extract_frame(video_path, timestamp, tmp_path)
        img = cv2.imread(tmp_path)
        if img is None:
            return None
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        brightness = float(gray.mean()) / 255.0
        contrast = float(gray.std())
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        motion_delta = _local_motion_delta(video_path, timestamp)

        score = 0.85
        issues = []
        notes = []

        if brightness < 0.18:
            score -= 0.35
            issues.append("underexposed")
            notes.append("very dark")
        elif brightness > 0.88:
            score -= 0.30
            issues.append("overexposed")
            notes.append("very bright")

        if blur < 65:
            score -= 0.25
            issues.append("blurry")
            notes.append("low sharpness")

        if motion_delta is not None:
            if motion_delta < 1.8 and brightness >= 0.08:
                score -= 0.26
                issues.append("dead_air")
                notes.append("nearly frozen frame")
            elif motion_delta > 26 and blur < 95:
                score -= 0.20
                issues.append("messy_motion")
                notes.append("chaotic motion blur")
            elif motion_delta > 16 and blur < 55:
                score -= 0.16
                issues.append("motion_blur")
                notes.append("motion-soft frame")

        if contrast < 28:
            score -= 0.18
            issues.append("flat_light")
            notes.append("flat contrast")

        if not issues:
            return None

        return {
            "frame_index": -1,
            "timestamp": timestamp,
            "cinematic_score": max(0.1, min(0.95, score)),
            "issues": issues,
            "reason": ", ".join(notes),
            "motion_delta": motion_delta,
        }
    except Exception:
        return None
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _local_motion_delta(video_path: str, timestamp: float) -> Optional[float]:
    """Mean frame delta around timestamp; low means dead still, high+blur means messy motion."""
    try:
        import cv2
    except ImportError:
        return None

    frames = []
    for ts in (max(0.0, timestamp - 0.35), timestamp, timestamp + 0.35):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            _extract_frame(video_path, ts, tmp_path)
            img = cv2.imread(tmp_path, cv2.IMREAD_GRAYSCALE)
            if img is not None:
                frames.append(cv2.resize(img, (160, 90)))
        except Exception:
            pass
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    if len(frames) < 2:
        return None

    deltas = []
    for a, b in zip(frames, frames[1:]):
        deltas.append(float(cv2.absdiff(a, b).mean()))
    return sum(deltas) / len(deltas)


def _frame_quality(video_path: str, timestamp: float) -> Optional[dict]:
    try:
        import cv2
    except ImportError:
        return None

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        _extract_frame(video_path, timestamp, tmp_path)
        img = cv2.imread(tmp_path)
        if img is None:
            return None
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        brightness = float(gray.mean()) / 255.0
        contrast = float(gray.std())
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        usable = brightness >= 0.08 and contrast >= 12
        score = (brightness * 0.45) + (min(contrast / 80, 1.0) * 0.35) + (min(blur / 160, 1.0) * 0.2)
        return {
            "timestamp": timestamp,
            "brightness": brightness,
            "contrast": contrast,
            "blur": blur,
            "usable": usable,
            "score": score,
        }
    except Exception:
        return None
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _select_anchor_timestamp(video_path: str, start_ts: float, end_ts: float,
                             preferred_ts: float) -> tuple[float, Optional[dict]]:
    sample_count = 7
    if end_ts <= start_ts:
        quality = _frame_quality(video_path, preferred_ts)
        return preferred_ts, quality

    span = end_ts - start_ts
    timestamps = [start_ts + (span * i / max(1, sample_count - 1)) for i in range(sample_count)]
    if all(abs(preferred_ts - ts) > 0.05 for ts in timestamps):
        timestamps.append(preferred_ts)

    qualities = [q for q in (_frame_quality(video_path, ts) for ts in timestamps) if q]
    if not qualities:
        return preferred_ts, None

    usable = [q for q in qualities if q["usable"]]
    best = max(usable or qualities, key=lambda q: q["score"])
    return float(best["timestamp"]), best


def _local_find_worst(video_path: str, timestamps: List[float], max_clips: int) -> List[dict]:
    candidates = []
    motion_deltas = []
    for i, ts in enumerate(timestamps):
        result = _local_score_frame(video_path, ts)
        if result:
            result["frame_index"] = i
            if result.get("motion_delta") is not None:
                motion_deltas.append(float(result["motion_delta"]))
            candidates.append(result)

    if motion_deltas:
        ordered = sorted(motion_deltas)
        median_motion = ordered[len(ordered) // 2]
        if median_motion > 8.0:
            for result in candidates:
                issues = result.get("issues") or []
                if "dead_air" in issues and (result.get("motion_delta") or 0.0) < 2.0:
                    result["cinematic_score"] = max(
                        0.1,
                        float(result.get("cinematic_score", 1.0)) - 0.12,
                    )
                    reason = result.get("reason") or ""
                    suffix = "dead still compared to the rest of the moving video"
                    result["reason"] = f"{reason}, {suffix}" if reason else suffix

    candidates.sort(key=lambda r: float(r.get("cinematic_score", 1.0)))
    selected = candidates[:max_clips]
    print(
        f"[detect/local] found {len(candidates)} visually weak frame(s), "
        f"keeping {len(selected)}",
        flush=True,
    )
    for r in selected:
        print(
            f"[detect/local]   {r['timestamp']:.1f}s score={r['cinematic_score']:.2f} "
            f"issues={r.get('issues')} — {r.get('reason', '')}",
            flush=True,
        )
    return selected


# ─── scene transition detection ─────────────────────────────────────────────

def _detect_next_cut(video_path: str, search_start_ts: float,
                     search_window: float = NEXT_CUT_SEARCH_WINDOW) -> float:
    """
    Find the next hard cut after search_start_ts using ffmpeg scene detection.
    Returns the absolute timestamp of the first detected cut, or -1.0 if none.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return -1.0
    try:
        cmd = [
            ffmpeg,
            "-ss", f"{search_start_ts:.3f}",
            "-t",  f"{search_window:.1f}",
            "-i",  video_path,
            "-vf", f"select=gt(scene\\,{SCENE_THRESHOLD}),showinfo",
            "-vsync", "vfr", "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        # showinfo writes lines like: "pts_time:1.234" to stderr
        matches = re.findall(r'pts_time:([\d.]+)', result.stderr)
        if matches:
            offset = float(matches[0])
            # Skip cuts within the first 0.5s — those are in our slot window
            if offset > 0.5:
                return search_start_ts + offset
    except Exception as e:
        print(f"[detect/cut] scene detection failed: {e}", flush=True)
    return -1.0


def _estimate_motion_type(video_path: str, slot_start_ts: float,
                          fps: float) -> Tuple[str, float]:
    """
    Extract 6 frames before slot_start_ts and use phase correlation to classify
    incoming camera motion. Returns (motion_type, speed_px_per_frame).

    Phase correlation shift convention (cv2.phaseCorrelate returns shift of img2 vs img1):
      dx < 0  → objects moved left → camera panned right
      dx > 0  → objects moved right → camera panned left
      dy > 0  → objects moved down → camera tilted up
      dy < 0  → objects moved up → camera tilted down
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return "static", 0.0

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return "static", 0.0

    n = 6
    interval = 0.15  # seconds between sampled frames
    timestamps = [max(0.0, slot_start_ts - (n - i) * interval) for i in range(n)]

    frames = []
    for ts in timestamps:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            subprocess.run([
                ffmpeg, "-y", "-ss", f"{ts:.3f}", "-i", video_path,
                "-frames:v", "1", "-q:v", "5", tmp_path,
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            img = cv2.imread(tmp_path, cv2.IMREAD_GRAYSCALE)
            if img is not None:
                # Downscale for speed; phase correlation is resolution-independent
                frames.append(cv2.resize(img, (320, 180)).astype(np.float64))
        except Exception:
            pass
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    if len(frames) < 2:
        return "static", 0.0

    shifts = []
    for i in range(len(frames) - 1):
        try:
            shift, _response = cv2.phaseCorrelate(frames[i], frames[i + 1])
            shifts.append(shift)
        except Exception:
            pass

    if not shifts:
        return "static", 0.0

    avg_dx = sum(s[0] for s in shifts) / len(shifts)
    avg_dy = sum(s[1] for s in shifts) / len(shifts)
    speed  = (avg_dx ** 2 + avg_dy ** 2) ** 0.5

    STATIC_THRESHOLD = 0.4   # px/frame — below this is camera shake noise
    AXIS_RATIO       = 1.5   # horizontal must dominate by this factor to call it a pan

    if speed < STATIC_THRESHOLD:
        return "static", speed

    if abs(avg_dx) > abs(avg_dy) * AXIS_RATIO:
        return ("pan_right" if avg_dx < 0 else "pan_left"), speed
    elif abs(avg_dy) > abs(avg_dx) * AXIS_RATIO:
        return ("tilt_up" if avg_dy > 0 else "tilt_down"), speed
    else:
        # Diagonal — call it the dominant axis
        return ("pan_right" if avg_dx < 0 else "pan_left"), speed


# ─── GPT calls ──────────────────────────────────────────────────────────────

@retry_api(max_retries=3, base_delay=5)
def _gpt_video_meta(video_path: str, duration: float) -> dict:
    """Pass 1: understand what kind of video this is so downstream prompts match its style."""
    from openai import OpenAI
    client = OpenAI(api_key=config.require_openai_api_key())

    n = min(META_FRAMES, max(4, int(duration / 15)), config.OPENAI_MAX_IMAGES_PER_REQUEST)
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

    wait_for_openai_image_slot(n)
    resp = client.chat.completions.create(
        model=config.VLM_MODEL,
        messages=[{"role": "user", "content": content}],
        max_tokens=300,
        temperature=0,
    )
    result = parse_json(resp.choices[0].message.content, "meta")
    if not result or not isinstance(result, dict):
        return _default_video_meta()
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
    client = OpenAI(api_key=config.require_openai_api_key())

    vtype   = video_meta.get("video_type", "general")
    palette = video_meta.get("color_palette", "neutral")
    vstyle  = video_meta.get("visual_style", "mixed")
    lighting = video_meta.get("lighting", "mixed")

    timestamps = _sample_evenly(timestamps, config.OPENAI_MAX_IMAGES_PER_REQUEST)
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
            "  RANDOM STILL — a frame that feels like a dropped-in still photo or frozen filler, "
            "especially inside an otherwise moving video\n"
            "  BAD MOTION   — chaotic motion blur, accidental whip/shake, or smeared movement that "
            "does not feel intentional or cinematic\n"
            "  FIXABLE DARK — underexposed scenes where the subject/setting is worth saving with "
            "natural exposure and practical-light correction; do not flag intentional low-key "
            "cinema if the subject, mood, and silhouette are clear\n"
            "  FLAT LIGHT  — uninspiring, muddy, or flat lighting with no mood, depth, or drama\n"
            "  WEAK FRAME  — centered boring composition, no leading lines, no depth, no "
            "cinematic intent\n"
            "  VLOG TRAP   — accidental handheld shake that looks amateur, not stylistic\n\n"
            "When slots are limited, prioritize RANDOM STILL, BAD MOTION, and FIXABLE DARK "
            "above generic weak framing or merely average shots.\n\n"
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

    wait_for_openai_image_slot(len(timestamps))
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
    Each Slot includes:
      - replace_end_frame: frame to resume original footage (may be beyond end_frame
        if we're doing a "clean cut" to the next scene change)
      - transition: SceneTransition with incoming motion type and next cut timestamp
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
        if isinstance(cached, list):
            candidates, video_meta = cached, {}
        else:
            candidates = cached.get("candidates", [])
            video_meta = cached.get("video_meta", {})
        print(f"[detect] cache hit — {len(candidates)} candidate(s)", flush=True)
    else:
        if config.OPENAI_MAX_IMAGES_PER_REQUEST <= 1:
            print(
                "[detect] OpenAI image budget is 1/request — using local frame scoring "
                "for detection and saving GPT vision for per-slot analysis.",
                flush=True,
            )
            video_meta = _default_video_meta()
            candidates = _local_find_worst(video_path, timestamps, config.MAX_BAD_CLIPS)
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
        anchor_ts, anchor_quality = _select_anchor_timestamp(video_path, start_ts, end_ts, ts)
        mid_frame   = int(anchor_ts * fps)

        sid         = new_id()
        anchor_path = os.path.join(config.FRAMES, f"{sid}.png")
        resume_path = os.path.join(config.FRAMES, f"{sid}_resume.png")
        clip_path   = os.path.join(config.CLIPS,  f"{sid}_original.mp4")

        print(f"[detect] extracting slot {sid[:8]} at {ts:.1f}s "
              f"({start_ts:.1f}→{end_ts:.1f}s), anchor={anchor_ts:.1f}s", flush=True)
        if anchor_quality:
            print(
                f"[detect]   anchor quality: brightness={anchor_quality['brightness']:.2f} "
                f"contrast={anchor_quality['contrast']:.1f} usable={anchor_quality['usable']}",
                flush=True,
            )
        try:
            extract_anchor(video_path, mid_frame, anchor_path, fps)
        except Exception as e:
            print(f"[detect] extraction failed: {e}", flush=True)
            continue

        # ── Pass 3: scene handover analysis ──────────────────────────────
        print(f"[detect] handover analysis for slot {sid[:8]}...", flush=True)

        motion_type, motion_speed = _estimate_motion_type(video_path, start_ts, fps)
        print(f"[detect]   incoming motion: {motion_type} ({motion_speed:.2f}px/f)",
              flush=True)

        next_cut_ts = _detect_next_cut(video_path, end_ts, NEXT_CUT_SEARCH_WINDOW)

        # Extend replacement to next hard cut if it's within MAX_EXTENSION_S seconds
        # and actually beyond the current slot window
        if next_cut_ts > end_ts and (next_cut_ts - end_ts) <= MAX_EXTENSION_S:
            replace_until_ts = min(next_cut_ts, duration)
            replace_end_frame = min(int(replace_until_ts * fps), int(duration * fps) - 1)
            print(f"[detect]   next cut at {next_cut_ts:.1f}s — "
                  f"extending replacement to {replace_until_ts:.1f}s", flush=True)
        else:
            replace_until_ts = end_ts
            replace_end_frame = end_frame
            if next_cut_ts > 0:
                print(f"[detect]   next cut at {next_cut_ts:.1f}s — "
                      f"too far, keeping 5s window", flush=True)

        try:
            extract_clip(video_path, start_frame, replace_end_frame, clip_path, fps)
            resume_anchor_frame = min(replace_end_frame + 1, int(duration * fps) - 1)
            extract_anchor(video_path, resume_anchor_frame, resume_path, fps)
        except Exception as e:
            print(f"[detect] clean-cut clip extraction failed: {e}", flush=True)
            continue

        transition = SceneTransition(
            motion_type=motion_type,
            motion_speed=round(motion_speed, 2),
            next_cut_ts=round(next_cut_ts, 3),
            replace_until_ts=round(replace_until_ts, 3),
        )

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
            replace_end_frame=replace_end_frame,
            transition=transition,
            resume_frame_path=resume_path,
        ))
        print(f"[detect] slot {sid[:8]}: {c.get('reason', '')}", flush=True)

    print(f"[detect] final: {len(slots)} slot(s) from {len(deduped)} candidates", flush=True)
    return slots, video_meta
