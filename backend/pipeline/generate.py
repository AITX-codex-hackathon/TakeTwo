"""
Stage 3: Generate a 5-second cinematic replacement clip.

Providers (set I2V_PROVIDER in .env):
  fal_kling  — Kling v2.1 standard via fal.ai  (default, best 3D motion)
  fal_luma   — Luma Dream Machine via fal.ai    (softer, dreamlike motion)
  stub       — static ffmpeg loop, zero GPU cost, for offline testing

Why fal_client.submit().get() instead of subscribe():
  submit() is non-blocking; we get a handle we can log progress on while
  the GPU does its work. subscribe() was fine but gave us less control over
  the polling loop and made it harder to surface queue position in logs.

cfg_scale = 0.7 (was 0.5):
  Higher value = the model follows the text prompt more strictly.
  At 0.5 the motion words ("slow push-in", "parallax") were suggestions.
  At 0.7 they become mandates.
"""
import os
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

def _gen_stub(anchor_path: str, prompt: str, neg_prompt: str, out_path: str) -> str:
    """Offline fallback: static loop for UI testing. No GPU, no motion."""
    import subprocess, shutil
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg required for stub provider")
    subprocess.run([
        ffmpeg, "-y", "-loop", "1", "-i", anchor_path,
        "-c:v", "libx264", "-t", "5",
        "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        out_path,
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_path


@retry_api(max_retries=2, base_delay=10)
def _gen_fal_kling(anchor_path: str, prompt: str, neg_prompt: str, out_path: str) -> str:
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

    print("[generate/kling] submitting to Kling v2.1 standard (5s)...", flush=True)
    handler = fal_client.submit(
        "fal-ai/kling-video/v2.1/standard/image-to-video",
        arguments={
            "prompt":          prompt,
            "negative_prompt": neg_prompt,
            "image_url":       image_url,
            "duration":        "5",
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
def _gen_fal_luma(anchor_path: str, prompt: str, neg_prompt: str, out_path: str) -> str:
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

    print("[generate/luma] submitting to Luma Dream Machine...", flush=True)
    handler = fal_client.submit(
        "fal-ai/luma-dream-machine/image-to-video",
        arguments={
            "prompt":       prompt,
            "image_url":    image_url,
            "duration":     "5s",
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
    "fal":       _gen_fal_kling,   # legacy alias
    "fal_kling": _gen_fal_kling,
    "fal_luma":  _gen_fal_luma,
}


# ─── entry point ────────────────────────────────────────────────────────────

def generate_for_slot(slot: Slot, ctx: SceneContext) -> List[Insert]:
    provider_fn = PROVIDERS.get(config.I2V_PROVIDER, _gen_stub)
    print(f"[generate] provider={config.I2V_PROVIDER} slot={slot.id[:8]}", flush=True)

    motion = ctx.motion_directive or "slow push-in with subtle cinematic drift"
    mood   = ctx.mood             or "cinematic"
    neg    = ctx.negative_prompt  or "static shot, frozen frame, no camera movement, CGI, cartoon, watermark"

    inserts = []
    for i, prompt_text in enumerate(ctx.replacement_prompts[:2]):
        iid      = new_id()
        out_path = os.path.join(config.CLIPS, f"{iid}.mp4")

        # Kinetic prompt: scene content → movement mandate → micro-motion → quality floor
        full_prompt = (
            f"{prompt_text}. "
            f"Camera: {motion}. "
            f"Mood: {mood}. "
            "Keep scene alive: subtle wind, shifting light rays, ambient environmental motion, "
            "natural subject micro-movements. "
            "Photorealistic 4K, ARRI Alexa color science, shallow depth of field, "
            "professional color grade, smooth cinematic motion, no compression artifacts."
        )
        print(f"[generate] prompt {i+1}: {full_prompt[:140]}...", flush=True)
        print(f"[generate] neg:    {neg[:80]}...", flush=True)

        if os.path.exists(out_path):
            print(f"[generate] clip {i+1} already exists — skipping generation", flush=True)
        else:
            try:
                provider_fn(slot.anchor_frame_path, full_prompt, neg, out_path)
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
