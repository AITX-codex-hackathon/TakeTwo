"""
Stage 3: Generate 5-sec cinematic replacement clip using fal/Kling.
Skips generation if the output file already exists (crash-safe resume).
"""
import os
import time
import requests
from typing import List
from ..models.schemas import Slot, SceneContext, Insert, new_id
from .. import config


def _gen_stub(anchor_path: str, prompt: str, out_path: str) -> str:
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


def _upscale_anchor(path: str) -> bytes:
    """Scale image to 768px wide (guarantees >300px for fal), keep aspect ratio."""
    import subprocess, shutil, tempfile
    ffmpeg = shutil.which("ffmpeg")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name
    subprocess.run([
        ffmpeg, "-y", "-i", path, "-vf", "scale=768:-2",
        "-frames:v", "1", tmp_path,
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(tmp_path, "rb") as f:
        data = f.read()
    os.unlink(tmp_path)
    return data


def _gen_fal(anchor_path: str, prompt: str, out_path: str) -> str:
    import fal_client
    os.environ.setdefault("FAL_KEY", config.FAL_API_KEY)

    print(f"[generate/fal] uploading anchor frame...", flush=True)
    img_bytes = _upscale_anchor(anchor_path)
    image_url = fal_client.upload(img_bytes, "image/png")
    print(f"[generate/fal] uploaded → {image_url}", flush=True)

    print(f"[generate/fal] submitting Kling v2 (5s)...", flush=True)
    result = fal_client.subscribe(
        "fal-ai/kling-video/v2/master/image-to-video",
        arguments={
            "prompt": prompt,
            "image_url": image_url,
            "duration": "5",
            "aspect_ratio": "16:9",
            "cfg_scale": 0.5,
        },
        with_logs=True,
        on_queue_update=lambda u: print(f"[generate/fal] {u}", flush=True),
    )
    video_url = result["video"]["url"]
    print(f"[generate/fal] downloading...", flush=True)
    v = requests.get(video_url, timeout=120)
    with open(out_path, "wb") as f:
        f.write(v.content)
    print(f"[generate/fal] saved → {os.path.basename(out_path)}", flush=True)
    return out_path


PROVIDERS = {"stub": _gen_stub, "fal": _gen_fal}


def generate_for_slot(slot: Slot, ctx: SceneContext) -> List[Insert]:
    provider_fn = PROVIDERS.get(config.I2V_PROVIDER, _gen_stub)
    print(f"[generate] provider={config.I2V_PROVIDER} slot={slot.id[:8]}", flush=True)

    inserts = []
    for i, prompt_text in enumerate(ctx.replacement_prompts[:2]):
        iid = new_id()
        out_path = os.path.join(config.CLIPS, f"{iid}.mp4")
        full_prompt = (
            f"{prompt_text}. "
            f"Mood: {ctx.mood}. "
            f"Photorealistic, cinematic 4K, shot on ARRI camera, shallow depth of field, "
            f"smooth motion, professional color grading, no blur, no artifacts, film quality."
        )
        print(f"[generate] prompt {i+1}: {full_prompt[:100]}...", flush=True)

        # Skip if already generated (resume after crash)
        if os.path.exists(out_path):
            print(f"[generate] clip {i+1} already exists, skipping", flush=True)
        else:
            try:
                provider_fn(slot.anchor_frame_path, full_prompt, out_path)
                print(f"[generate] clip {i+1} done → {os.path.basename(out_path)}", flush=True)
            except Exception as e:
                print(f"[generate] ERROR clip {i+1}: {e}", flush=True)
                continue

        inserts.append(Insert(
            id=iid,
            slot_id=slot.id,
            clip_path=out_path,
            prompt=full_prompt,
            label=prompt_text,
        ))
    return inserts
