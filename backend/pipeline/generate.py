"""
Stage 3: Generate a cinematic handover clip.

Providers (set I2V_PROVIDER in .env):
  fal_kling  — Kling v3 Pro via fal.ai  (best cinematic handover)
  fal_kling_v21 — Kling v2.1 standard via fal.ai
  fal_luma   — Luma Dream Machine via fal.ai    (softer, dreamlike motion)
  stub       — static ffmpeg loop, zero GPU cost, for offline testing

Why fal_client.submit().get() instead of subscribe():
  submit() is non-blocking; we get a handle we can log progress on while
  the GPU does its work. subscribe() was fine but gave us less control over
  the polling loop and made it harder to surface queue position in logs.

cfg_scale = 0.7:
  Higher value = the model follows the text prompt more strictly.
  At 0.5 the motion words ("slow push-in", "parallax") were suggestions.
  At 0.7 they become mandates.
"""
import os
import math
import time
import requests
from typing import List

from ..models.schemas import Slot, SceneContext, Insert, new_id
from .. import config
from .api_utils import retry_api


# ─── upscale helper ─────────────────────────────────────────────────────────

def _upscale_anchor(path: str) -> bytes:
    """Scale anchor frame to 1024px wide — Kling v2.1 benefits from higher res input."""
    import subprocess
    import shutil
    import tempfile
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg required")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name
    subprocess.run(
        [ffmpeg, "-y", "-i", path, "-vf", "scale=1024:-2", "-frames:v", "1", tmp_path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    with open(tmp_path, "rb") as f:
        data = f.read()
    os.unlink(tmp_path)
    return data


# ─── providers ──────────────────────────────────────────────────────────────

def _gen_stub(anchor_path: str, prompt: str, neg_prompt: str, out_path: str,
              duration_s: int) -> str:
    """Offline fallback: static loop for UI testing. No GPU, no motion."""
    import subprocess, shutil
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg required for stub provider")
    subprocess.run([
        ffmpeg, "-y", "-loop", "1", "-i", anchor_path,
        "-c:v", "libx264", "-t", str(duration_s),
        "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        out_path,
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_path


@retry_api(max_retries=2, base_delay=10)
def _gen_fal_kling_v3(anchor_path: str, prompt: str, neg_prompt: str,
                      out_path: str, duration_s: int) -> str:
    """
    Kling v3 Pro via fal.ai.
    Uses start_image_url and duration 3-15s so the generation can take over
    the scene until a clean cut instead of always producing a fixed 5s patch.
    """
    import fal_client
    os.environ.setdefault("FAL_KEY", config.FAL_API_KEY)

    print("[generate/kling-v3] upscaling anchor to 1024px...", flush=True)
    img_bytes = _upscale_anchor(anchor_path)
    start_image_url = fal_client.upload(img_bytes, "image/png")
    print(f"[generate/kling-v3] uploaded anchor → {start_image_url}", flush=True)

    print(f"[generate/kling-v3] submitting to Kling v3 Pro ({duration_s}s)...", flush=True)
    handler = fal_client.submit(
        "fal-ai/kling-video/v3/pro/image-to-video",
        arguments={
            "prompt":          prompt,
            "negative_prompt": neg_prompt,
            "start_image_url": start_image_url,
            "duration":        str(duration_s),
            "generate_audio":  False,
            "cfg_scale":       0.7,
        },
    )

    from fal_client.client import Completed
    print("[generate/kling-v3] queued — polling for result...", flush=True)
    start = time.time()
    while True:
        status = handler.status(with_logs=True)
        elapsed = int(time.time() - start)
        state = type(status).__name__
        print(f"[generate/kling-v3] {elapsed}s — {state}", flush=True)
        if hasattr(status, "logs") and status.logs:
            for log in status.logs:
                print(f"[generate/kling-v3]   {log.get('message', log)}", flush=True)
        if isinstance(status, Completed):
            if status.error:
                raise RuntimeError(f"Kling v3 generation failed: {status.error}")
            break
        time.sleep(8)

    result    = handler.get()
    video_url = result["video"]["url"]
    print(f"[generate/kling-v3] done ({int(time.time()-start)}s) — downloading...", flush=True)

    resp = requests.get(video_url, timeout=240)
    resp.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(resp.content)
    print(f"[generate/kling-v3] saved → {os.path.basename(out_path)}", flush=True)
    return out_path


@retry_api(max_retries=2, base_delay=10)
def _gen_fal_kling_v21(anchor_path: str, prompt: str, neg_prompt: str,
                       out_path: str, duration_s: int) -> str:
    """
    Kling v2.1 standard via fal.ai.
    v2.1 has significantly better 3D scene understanding than v2/master —
    it actually projects the anchor image into a depth map and moves a
    virtual camera through it, producing genuine parallax rather than
    pixel-level warping.
    """
    import fal_client
    os.environ.setdefault("FAL_KEY", config.FAL_API_KEY)

    print("[generate/kling] upscaling anchor to 1024px...", flush=True)
    img_bytes = _upscale_anchor(anchor_path)
    image_url = fal_client.upload(img_bytes, "image/png")
    print(f"[generate/kling] uploaded anchor → {image_url}", flush=True)

    duration_s = 10 if duration_s > 7 else 5
    print(f"[generate/kling] submitting to Kling v2.1 standard ({duration_s}s)...", flush=True)
    handler = fal_client.submit(
        "fal-ai/kling-video/v2.1/standard/image-to-video",
        arguments={
            "prompt":          prompt,
            "negative_prompt": neg_prompt,
            "image_url":       image_url,
            "duration":        str(duration_s),
            "aspect_ratio":    "16:9",
            "cfg_scale":       0.7,   # 0.7 = prompt words are enforced, not just suggested
        },
    )

    # Poll with progress logging — Kling typically queues for 10-30s then runs 45-90s
    # fal_client 1.0: statuses are Queued / InProgress / Completed (no Failed class).
    # Completed.error is set on failure.
    from fal_client.client import Completed
    print("[generate/kling] queued — polling for result...", flush=True)
    start = time.time()
    while True:
        status = handler.status(with_logs=True)
        elapsed = int(time.time() - start)
        state = type(status).__name__
        print(f"[generate/kling] {elapsed}s — {state}", flush=True)
        if hasattr(status, "logs") and status.logs:
            for log in status.logs:
                print(f"[generate/kling]   {log.get('message', log)}", flush=True)
        if isinstance(status, Completed):
            if status.error:
                raise RuntimeError(f"Kling generation failed: {status.error}")
            break
        time.sleep(8)

    result    = handler.get()
    video_url = result["video"]["url"]
    print(f"[generate/kling] done ({int(time.time()-start)}s) — downloading...", flush=True)

    resp = requests.get(video_url, timeout=180)
    resp.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(resp.content)
    print(f"[generate/kling] saved → {os.path.basename(out_path)}", flush=True)
    return out_path


@retry_api(max_retries=2, base_delay=10)
def _gen_fal_luma(anchor_path: str, prompt: str, neg_prompt: str, out_path: str,
                  duration_s: int) -> str:
    """
    Luma Dream Machine via fal.ai.
    Produces softer, more dream-like motion — good for nature/travel.
    Kling is sharper and more geometrically accurate.
    """
    import fal_client
    os.environ.setdefault("FAL_KEY", config.FAL_API_KEY)

    print("[generate/luma] upscaling anchor...", flush=True)
    img_bytes = _upscale_anchor(anchor_path)
    image_url = fal_client.upload(img_bytes, "image/png")
    print(f"[generate/luma] uploaded → {image_url}", flush=True)

    print(f"[generate/luma] submitting to Luma Dream Machine ({duration_s}s)...", flush=True)
    handler = fal_client.submit(
        "fal-ai/luma-dream-machine/image-to-video",
        arguments={
            "prompt":       prompt,
            "image_url":    image_url,
            "duration":     f"{duration_s}s",
            "aspect_ratio": "16:9",
            "loop":         False,
        },
    )

    from fal_client.client import Completed
    print("[generate/luma] queued — polling...", flush=True)
    start = time.time()
    while True:
        status = handler.status(with_logs=True)
        elapsed = int(time.time() - start)
        state = type(status).__name__
        print(f"[generate/luma] {elapsed}s — {state}", flush=True)
        if isinstance(status, Completed):
            if status.error:
                raise RuntimeError(f"Luma generation failed: {status.error}")
            break
        time.sleep(8)

    result    = handler.get()
    video_url = result["video"]["url"]
    print(f"[generate/luma] done ({int(time.time()-start)}s) — downloading...", flush=True)

    resp = requests.get(video_url, timeout=180)
    resp.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(resp.content)
    print(f"[generate/luma] saved → {os.path.basename(out_path)}", flush=True)
    return out_path


PROVIDERS = {
    "stub":      _gen_stub,
    "fal":       _gen_fal_kling_v3,   # legacy alias
    "fal_kling": _gen_fal_kling_v3,
    "fal_kling_v3": _gen_fal_kling_v3,
    "fal_kling_v21": _gen_fal_kling_v21,
    "fal_luma":  _gen_fal_luma,
}


def _generation_duration(slot: Slot) -> int:
    duration = getattr(slot, "replacement_duration_sec", slot.duration_sec)
    # Kling v3 Pro supports integer durations from 3s to 15s.
    return max(3, min(15, math.ceil(duration)))


# ─── entry point ────────────────────────────────────────────────────────────

def generate_for_slot(slot: Slot, ctx: SceneContext) -> List[Insert]:
    provider_fn = PROVIDERS.get(config.I2V_PROVIDER, _gen_stub)
    print(f"[generate] provider={config.I2V_PROVIDER} slot={slot.id[:8]}", flush=True)

    motion = ctx.motion_directive or "slow push-in with subtle cinematic drift"
    mood   = ctx.mood             or "cinematic"
    neg    = ctx.negative_prompt  or "static shot, frozen frame, no camera movement, CGI, cartoon, watermark"
    duration_s = _generation_duration(slot)
    replaced_s = getattr(slot, "replacement_duration_sec", slot.duration_sec)
    transition = slot.transition
    incoming_motion = "unknown"
    if transition:
        incoming_motion = f"{transition.motion_type} at {transition.motion_speed:.2f}px/frame"

    inserts = []
    for i, prompt_text in enumerate(ctx.replacement_prompts[:2]):
        iid      = new_id()
        out_path = os.path.join(config.CLIPS, f"{iid}.mp4")

        # Kinetic prompt: scene content → movement mandate → micro-motion → quality floor
        full_prompt = (
            f"{prompt_text}. "
            f"Duration: {duration_s} seconds, covering a {replaced_s:.1f}s source scene handover. "
            f"Camera: {motion}. "
            f"Mood: {mood}. "
            f"Incoming source motion: {incoming_motion}. "
            "Keep scene alive: subtle wind, shifting light rays, ambient environmental motion, "
            "natural subject micro-movements. "
            "Photorealistic 4K, ARRI Alexa color science, shallow depth of field, "
            "professional color grade, smooth cinematic motion, no compression artifacts. "
            "End on a composition that can hard-cut cleanly into the next original scene."
        )
        print(f"[generate] prompt {i+1}: {full_prompt[:140]}...", flush=True)
        print(f"[generate] neg:    {neg[:80]}...", flush=True)

        if os.path.exists(out_path):
            print(f"[generate] clip {i+1} already exists — skipping generation", flush=True)
        else:
            try:
                provider_fn(slot.anchor_frame_path, full_prompt, neg, out_path, duration_s)
                print(f"[generate] clip {i+1} saved → {os.path.basename(out_path)}", flush=True)
            except Exception as e:
                print(f"[generate] ERROR clip {i+1}: {e}", flush=True)
                continue

        inserts.append(Insert(
            id=iid,
            slot_id=slot.id,
            clip_path=out_path,
            prompt=full_prompt,
            label=prompt_text[:80],
        ))

    return inserts
