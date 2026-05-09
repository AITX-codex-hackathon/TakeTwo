"""
Stage 1: Find 1-2 timestamps for cinematic enhancement using GPT-4o.
Sends all sampled frames in ONE call. Caches results per video.
"""
import os
import json
import base64
import hashlib
import time
import shutil
import subprocess
import tempfile
from typing import List
from ..models.schemas import Slot, new_id
from .. import config

SAMPLE_INTERVAL = 10.0


def _video_hash(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _cache_path(video_path: str) -> str:
    return str(config.CACHE / f"{_video_hash(video_path)}_detect.json")


def _ffprobe(video_path: str) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe required")
    out = subprocess.check_output([
        ffprobe, "-v", "quiet", "-print_format", "json", "-show_streams", video_path
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


def extract_anchor(video_path: str, frame_idx: int, out_path: str, fps: float) -> str:
    _extract_frame(video_path, frame_idx / fps, out_path)
    return out_path


def extract_clip(video_path: str, start_frame: int, end_frame: int, out_path: str, fps: float) -> str:
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


def _b64_frame(video_path: str, timestamp: float) -> str:
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
    _extract_frame(video_path, timestamp, tmp_path)
    with open(tmp_path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    os.unlink(tmp_path)
    return data


def _gpt_find_worst(video_path: str, timestamps: List[float]) -> List[dict]:
    from openai import OpenAI
    client = OpenAI(api_key=config.OPENAI_API_KEY)

    print(f"[detect] extracting {len(timestamps)} frames for GPT-4o batch analysis...", flush=True)

    content = [
        {
            "type": "text",
            "text": (
                f"You are reviewing {len(timestamps)} frames sampled from a video "
                f"(one every {SAMPLE_INTERVAL:.0f}s, labeled Frame 0 to Frame {len(timestamps)-1}).\n\n"
                "Pick the 1-2 moments that most need improvement — bad quality (blurry, dark, shaky, noisy) "
                "OR dull/boring shots where a cinematic AI replacement would most enhance the video.\n\n"
                "Return ONLY a JSON array (1-2 entries), worst first:\n"
                '[{"frame_index": 0, "timestamp": 5.0, "quality_score": 0.3, '
                '"issues": ["blurry","underexposed"], "reason": "one sentence"}]'
            )
        }
    ]

    for i, ts in enumerate(timestamps):
        b64 = _b64_frame(video_path, ts)
        content.append({"type": "text", "text": f"Frame {i} ({ts:.1f}s):"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"}})

    print(f"[detect] sending {len(timestamps)} frames to GPT-4o in ONE call...", flush=True)
    resp = client.chat.completions.create(
        model=config.VLM_MODEL,
        messages=[{"role": "user", "content": content}],
        max_tokens=512,
        temperature=0,
    )
    text = resp.choices[0].message.content.replace("```json", "").replace("```", "").strip()
    result = json.loads(text)
    if isinstance(result, dict):
        result = [result]
    print(f"[detect] GPT-4o picked {len(result)} moment(s):", flush=True)
    for r in result:
        print(f"[detect]   {r['timestamp']:.1f}s — {r.get('reason', '')} (score={r.get('quality_score')})", flush=True)
    return result[:2]


def find_bad_clips(video_path: str) -> List[Slot]:
    info = _ffprobe(video_path)
    fps = info["fps"]
    duration = info["duration"]

    if duration < 10.0:
        print(f"[detect] video too short ({duration:.1f}s)", flush=True)
        return []

    timestamps = []
    t = SAMPLE_INTERVAL / 2
    while t < duration:
        timestamps.append(t)
        t += SAMPLE_INTERVAL

    cache_file = _cache_path(video_path)
    if os.path.exists(cache_file):
        print(f"[detect] using cached results (skipping API call)", flush=True)
        with open(cache_file) as f:
            candidates = json.load(f)
    else:
        print(f"[detect] video={duration:.1f}s, sampling {len(timestamps)} frames every {SAMPLE_INTERVAL}s", flush=True)
        candidates = _gpt_find_worst(video_path, timestamps)
        with open(cache_file, "w") as f:
            json.dump(candidates, f)
        print(f"[detect] cached to {cache_file}", flush=True)

    clip_half = 2.5
    slots = []
    for c in candidates:
        ts = float(c["timestamp"])
        start_ts = max(0.0, ts - clip_half)
        end_ts = min(duration, ts + clip_half)
        if end_ts - start_ts < 2.0:
            continue

        start_frame = int(start_ts * fps)
        end_frame = int(end_ts * fps)
        mid_frame = int(ts * fps)

        sid = new_id()
        anchor_path = os.path.join(config.FRAMES, f"{sid}.png")
        clip_path = os.path.join(config.CLIPS, f"{sid}_original.mp4")

        print(f"[detect] extracting slot at {ts:.1f}s ({start_ts:.1f}s → {end_ts:.1f}s)", flush=True)
        try:
            extract_anchor(video_path, mid_frame, anchor_path, fps)
            extract_clip(video_path, start_frame, end_frame, clip_path, fps)
        except Exception as e:
            print(f"[detect] failed to extract slot: {e}", flush=True)
            continue

        slots.append(Slot(
            id=sid,
            start_frame=start_frame,
            end_frame=end_frame,
            fps=fps,
            quality_score=float(c.get("quality_score", 0.5)),
            anchor_frame_path=anchor_path,
            issues=c.get("issues", []),
        ))
        print(f"[detect] slot {sid[:8]}: {ts:.1f}s — {c.get('reason', '')}", flush=True)

    print(f"[detect] found {len(slots)} slot(s)", flush=True)
    return slots
