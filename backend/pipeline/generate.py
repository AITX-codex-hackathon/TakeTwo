"""
Stage 3: Generate a cinematic handover clip.

Providers (set I2V_PROVIDER in .env):
  fal_kling  — Kling v2.1 standard via fal.ai  (best default for real motion)
  fal_kling_v21 — Kling v2.1 standard via fal.ai
  fal_kling_v3 — Kling v3 Pro via fal.ai
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
import re
import time
import requests
import threading
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from ..models.schemas import Slot, SceneContext, Insert, new_id
from .. import config
from .api_utils import retry_api


_FAL_SEMAPHORE = threading.Semaphore(config.FAL_CONCURRENCY)


# ─── upscale helper ─────────────────────────────────────────────────────────

def _upscale_anchor(path: str) -> bytes:
    """Scale frame to 1024px wide — Kling benefits from higher-res input."""
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


def _configure_fal():
    if not config.FAL_API_KEY:
        raise RuntimeError(
            "FAL_API_KEY is missing. Refusing to fall back to static stub generation."
        )
    os.environ["FAL_KEY"] = config.FAL_API_KEY



# ─── providers ──────────────────────────────────────────────────────────────

def _poll_fal_result(handler, label: str):
    """Wait for a fal.ai job, but never forever."""
    from fal_client.client import Completed

    timeout_s = max(30, config.FAL_GENERATION_TIMEOUT_SEC)
    poll_s = max(1.0, config.FAL_POLL_INTERVAL_SEC)
    start = time.time()
    last_state = ""

    while True:
        elapsed = int(time.time() - start)
        if elapsed > timeout_s:
            raise TimeoutError(
                f"{label} did not finish after {timeout_s}s. "
                "The provider may be queued or stuck; retry the upload or switch I2V_PROVIDER=stub for local testing."
            )

        status = handler.status(with_logs=True)
        state = type(status).__name__
        if state != last_state or elapsed % max(1, int(poll_s * 4)) == 0:
            print(f"[generate/{label}] {elapsed}s - {state}", flush=True)
            last_state = state
        if hasattr(status, "logs") and status.logs:
            for log in status.logs:
                print(f"[generate/{label}]   {log.get('message', log)}", flush=True)
        if isinstance(status, Completed):
            if status.error:
                raise RuntimeError(f"{label} generation failed: {status.error}")
            return int(time.time() - start)

        time.sleep(poll_s)


def _gen_stub(anchor_path: str, prompt: str, neg_prompt: str, out_path: str,
              duration_s: int, end_frame_path: str = "") -> str:
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
                      out_path: str, duration_s: int,
                      end_frame_path: str = "") -> str:
    """
    Kling v3 Pro via fal.ai.
    Uses start_image_url and duration 3-15s so the generation can take over
    the scene until a clean cut instead of always producing a fixed 5s patch.
    """
    import fal_client
    _configure_fal()

    print("[generate/kling-v3] upscaling anchor to 1024px...", flush=True)
    img_bytes = _upscale_anchor(anchor_path)
    start_image_url = fal_client.upload(img_bytes, "image/png")
    print(f"[generate/kling-v3] uploaded anchor → {start_image_url}", flush=True)

    end_image_url = None
    if end_frame_path and os.path.exists(end_frame_path):
        print("[generate/kling-v3] upscaling resume frame for outro lock...", flush=True)
        end_bytes = _upscale_anchor(end_frame_path)
        end_image_url = fal_client.upload(end_bytes, "image/png")
        print(f"[generate/kling-v3] uploaded resume frame → {end_image_url}", flush=True)

    arguments = {
        "prompt":          prompt,
        "negative_prompt": neg_prompt,
        "start_image_url": start_image_url,
        "duration":        str(duration_s),
        "generate_audio":  False,
        "cfg_scale":       0.72,
    }
    if end_image_url:
        arguments["end_image_url"] = end_image_url

    print(f"[generate/kling-v3] submitting to Kling v3 Pro ({duration_s}s)...", flush=True)
    handler = fal_client.submit(
        "fal-ai/kling-video/v3/pro/image-to-video",
        arguments=arguments,
    )

    print("[generate/kling-v3] queued — polling for result...", flush=True)
    elapsed = _poll_fal_result(handler, "kling-v3")

    result    = handler.get()
    video_url = result["video"]["url"]
    print(f"[generate/kling-v3] done ({elapsed}s) — downloading...", flush=True)

    resp = requests.get(video_url, timeout=240)
    resp.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(resp.content)
    print(f"[generate/kling-v3] saved → {os.path.basename(out_path)}", flush=True)
    return out_path


@retry_api(max_retries=2, base_delay=10)
def _gen_fal_kling_v21(anchor_path: str, prompt: str, neg_prompt: str,
                       out_path: str, duration_s: int,
                       end_frame_path: str = "") -> str:
    """
    Kling v2.1 standard via fal.ai.
    v2.1 has significantly better 3D scene understanding than v2/master —
    it actually projects the anchor image into a depth map and moves a
    virtual camera through it, producing genuine parallax rather than
    pixel-level warping.
    """
    import fal_client
    _configure_fal()

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
    print("[generate/kling] queued — polling for result...", flush=True)
    elapsed = _poll_fal_result(handler, "kling")

    result    = handler.get()
    video_url = result["video"]["url"]
    print(f"[generate/kling] done ({elapsed}s) — downloading...", flush=True)

    resp = requests.get(video_url, timeout=180)
    resp.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(resp.content)
    print(f"[generate/kling] saved → {os.path.basename(out_path)}", flush=True)
    return out_path


@retry_api(max_retries=2, base_delay=10)
def _gen_fal_luma(anchor_path: str, prompt: str, neg_prompt: str, out_path: str,
                  duration_s: int, end_frame_path: str = "") -> str:
    """
    Luma Dream Machine via fal.ai.
    Produces softer, more dream-like motion — good for nature/travel.
    Kling is sharper and more geometrically accurate.
    """
    import fal_client
    _configure_fal()

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

    print("[generate/luma] queued — polling...", flush=True)
    elapsed = _poll_fal_result(handler, "luma")

    result    = handler.get()
    video_url = result["video"]["url"]
    print(f"[generate/luma] done ({elapsed}s) — downloading...", flush=True)

    resp = requests.get(video_url, timeout=180)
    resp.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(resp.content)
    print(f"[generate/luma] saved → {os.path.basename(out_path)}", flush=True)
    return out_path


PROVIDERS = {
    "stub":      _gen_stub,
    "fal":       _gen_fal_kling_v21,  # legacy alias
    "fal_kling": _gen_fal_kling_v21,
    "fal_kling_v3": _gen_fal_kling_v3,
    "fal_kling_v21": _gen_fal_kling_v21,
    "fal_luma":  _gen_fal_luma,
}


def _generation_duration(slot: Slot) -> int:
    duration = getattr(slot, "replacement_duration_sec", slot.duration_sec)
    if config.I2V_PROVIDER in ("fal", "fal_kling", "fal_kling_v21"):
        return 10 if duration > 7 else 5
    # Kling v3 Pro supports integer durations from 3s to 15s.
    return max(3, min(15, round(duration)))


def _style_motion(video_meta: dict, fallback_motion: str) -> str:
    video_type = (video_meta or {}).get("video_type", "cinematic").lower()
    subject = (video_meta or {}).get("primary_subject", "")
    base = fallback_motion or "slow push-in with subtle cinematic drift"

    if "vlog" in video_type or "talking" in video_type:
        style = (
            "handheld stabilized vlog motion, tiny shoulder sway, gentle push-in, "
            "natural face and body micro-movement"
        )
    elif "travel" in video_type or "landscape" in video_type:
        style = (
            "slow dolly forward with visible foreground-background parallax, "
            "soft pan following the environment, wind and atmospheric movement"
        )
    elif "product" in video_type or "object" in video_type:
        style = (
            "controlled slider move with a subtle orbit, crisp depth separation, "
            "realistic reflections and highlight rolloff"
        )
    elif "sports" in video_type or "action" in video_type:
        style = (
            "energetic stabilized tracking move, strong directional motion, "
            "realistic momentum and motion blur"
        )
    else:
        style = (
            "cinematic dolly or pan with real 3D parallax, layered foreground and "
            "background movement, physically plausible camera motion"
        )

    if subject:
        style += f", keep attention anchored on {subject}"
    return f"{base}. Movement style: {style}."


def _clean_prompt_text(prompt_text: str) -> str:
    return re.sub(r"^\s*prompt\s*\d+\s*[—:-]\s*", "", prompt_text or "", flags=re.I).strip()


def _conform_clip_duration(path: str, duration_s: float):
    """Trim or loop generated video so review/export clips match the slot length."""
    if duration_s <= 0:
        return
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        subprocess.run([
            ffmpeg, "-y", "-stream_loop", "-1", "-i", path,
            "-t", f"{duration_s:.6f}",
            "-an", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", tmp_path,
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.replace(tmp_path, path)
        print(f"[generate] conformed {os.path.basename(path)} to {duration_s:.3f}s", flush=True)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ─── entry point ────────────────────────────────────────────────────────────

def generate_for_slot(slot: Slot, ctx: SceneContext, video_meta: dict = None) -> List[Insert]:
    if config.I2V_PROVIDER not in PROVIDERS:
        raise RuntimeError(
            f"Unknown I2V_PROVIDER={config.I2V_PROVIDER!r}. "
            f"Expected one of: {', '.join(sorted(PROVIDERS))}."
        )
    provider_fn = PROVIDERS[config.I2V_PROVIDER]
    if config.I2V_PROVIDER != "stub" and not config.FAL_API_KEY:
        raise RuntimeError(
            f"I2V_PROVIDER={config.I2V_PROVIDER} requires FAL_API_KEY. "
            "Refusing to produce a static slideshow."
        )
    print(f"[generate] provider={config.I2V_PROVIDER} slot={slot.id[:8]}", flush=True)

    motion = _style_motion(
        video_meta or {},
        ctx.motion_directive or "slow push-in with subtle cinematic drift",
    )
    mood   = ctx.mood             or "cinematic"
    neg    = (
        ctx.negative_prompt
        or "static shot, frozen frame, no camera movement, CGI, cartoon, watermark"
    )
    neg = (
        f"{neg}, slideshow, still image, Ken Burns effect, fake parallax, "
        "cartoon, anime, CGI, plastic skin, waxy faces, oversaturated colors, "
        "fake HDR, AI-looking relight, glowing halos, neon relighting, beauty filter, "
        "over-denoised, synthetic bokeh, surreal objects, wrong subject, wrong location, "
        "text, subtitles, logo, watermark"
    )
    duration_s = _generation_duration(slot)
    replaced_s = getattr(slot, "replacement_duration_sec", slot.duration_sec)
    transition = slot.transition
    incoming_motion = "unknown"
    if transition:
        incoming_motion = f"{transition.motion_type} at {transition.motion_speed:.2f}px/frame"
    clean_cut = bool(transition and slot.resume_frame != slot.end_frame)
    end_frame_path = "" if clean_cut else getattr(slot, "resume_frame_path", "")

    generation_count = max(1, min(config.FAL_GENERATIONS_PER_SLOT, len(ctx.replacement_prompts)))
    prompt_items = list(enumerate(ctx.replacement_prompts[:generation_count], start=1))

    def _generate_prompt(item) -> tuple[int, Insert | None]:
        i, prompt_text = item
        prompt_text = _clean_prompt_text(prompt_text)
        iid      = new_id()
        out_path = os.path.join(config.CLIPS, f"{iid}.mp4")

        # Kinetic prompt: scene content → movement mandate → micro-motion → quality floor
        full_prompt = (
            f"{prompt_text}. "
            f"Duration: {duration_s} seconds, covering a {replaced_s:.1f}s source scene handover. "
            f"Camera: {motion}. "
            f"Mood: {mood}. "
            f"Incoming source motion: {incoming_motion}. "
            "Cinematic 3D motion, camera moves through the scene, high visual delta, realistic physics. "
            "Project the starting image into a coherent 3D space and move a virtual camera through it. "
            "Keep the exact subject, location, era, wardrobe, architecture, lens feel, and color theme. "
            "Improve only what is broken: stabilize amateur motion, add subtle dimensional camera movement, "
            "and if the shot is very dark, lift exposure like available practical light or soft bounce fill, "
            "preserving black levels, highlight rolloff, natural color temperature, and real skin texture. "
            "Keep scene alive with restrained real-world micro-motion: breathing, cloth, dust, reflections, "
            "wind, or shifting practical light. Photorealistic live-action footage, natural skin texture, "
            "real lens optics, physically plausible lighting, smooth cinematic motion, no compression artifacts. "
            "Do not make it cartoonish, surreal, glossy, fake-HDR, over-stylized, or unrelated. "
            "End on a composition that fits the next original frame or hard-cuts cleanly into it."
        )
        print(f"[generate] prompt {i}/{generation_count}: {full_prompt[:140]}...", flush=True)
        print(f"[generate] neg:    {neg[:80]}...", flush=True)

        if os.path.exists(out_path):
            print(f"[generate] clip {i} already exists — skipping generation", flush=True)
        else:
            try:
                with _FAL_SEMAPHORE:
                    provider_fn(slot.anchor_frame_path, full_prompt, neg, out_path,
                                duration_s, end_frame_path)
                _conform_clip_duration(out_path, replaced_s)
                print(f"[generate] clip {i} saved → {os.path.basename(out_path)}", flush=True)
            except Exception as e:
                print(f"[generate] ERROR clip {i}: {e}", flush=True)
                return i, None

        return i, Insert(
            id=iid,
            slot_id=slot.id,
            clip_path=out_path,
            prompt=full_prompt,
            label=prompt_text[:80],
        )

    inserts = []
    if generation_count == 1:
        _, insert = _generate_prompt(prompt_items[0])
        if insert:
            inserts.append(insert)
    else:
        max_workers = max(1, min(config.FAL_CONCURRENCY, generation_count))
        print(
            f"[generate] launching {generation_count} prompt generation(s) "
            f"with max_workers={max_workers}, fal_concurrency={config.FAL_CONCURRENCY}",
            flush=True,
        )
        generated = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_generate_prompt, item) for item in prompt_items]
            for fut in as_completed(futures):
                idx, insert = fut.result()
                if insert:
                    generated.append((idx, insert))
        inserts = [insert for _, insert in sorted(generated, key=lambda item: item[0])]

    if not inserts:
        raise RuntimeError(
            f"{config.I2V_PROVIDER} did not produce any replacement clips for slot {slot.id}."
        )

    return inserts
