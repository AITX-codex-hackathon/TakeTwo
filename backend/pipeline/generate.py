"""
Stage 3: Image-to-video generation. Takes anchor frame + prompt -> replacement clip.
Provider-agnostic: stub (dev), luma (Dream Machine), fal (Kling/Minimax).
"""
import os
import time
import base64
import requests
import subprocess
import shutil
from typing import List
from ..models.schemas import Slot, SceneContext, Insert, new_id
from .. import config


def _gen_stub(anchor_path: str, prompt: str, duration: float, out_path: str) -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg required for stub provider")
    subprocess.run([
        ffmpeg, "-y", "-loop", "1", "-i", anchor_path,
        "-c:v", "libx264", "-t", f"{duration:.2f}",
        "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        out_path,
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_path


def _gen_luma(anchor_path: str, prompt: str, duration: float, out_path: str) -> str:
    headers = {"Authorization": f"Bearer {config.LUMA_API_KEY}"}
    with open(anchor_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    body = {
        "prompt": prompt,
        "keyframes": {"frame0": {"type": "image", "url": f"data:image/png;base64,{img_b64}"}},
        "model": "ray-2",
        "duration": f"{int(round(duration))}s",
    }
    r = requests.post("https://api.lumalabs.ai/dream-machine/v1/generations", json=body, headers=headers, timeout=60)
    r.raise_for_status()
    gen_id = r.json()["id"]
    for _ in range(90):
        time.sleep(5)
        s = requests.get(f"https://api.lumalabs.ai/dream-machine/v1/generations/{gen_id}", headers=headers, timeout=30)
        s.raise_for_status()
        data = s.json()
        if data.get("state") == "completed":
            video_url = data["assets"]["video"]
            v = requests.get(video_url, timeout=120)
            with open(out_path, "wb") as f:
                f.write(v.content)
            return out_path
        if data.get("state") == "failed":
            raise RuntimeError(f"Luma gen failed: {data.get('failure_reason')}")
    raise TimeoutError("Luma generation timed out")


def _gen_fal(anchor_path: str, prompt: str, duration: float, out_path: str) -> str:
    import fal_client
    with open(anchor_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    result = fal_client.subscribe(
        "fal-ai/kling-video/v2/master/image-to-video",
        arguments={
            "prompt": prompt,
            "image_url": f"data:image/png;base64,{img_b64}",
            "duration": str(min(int(round(duration)), 5)),
            "aspect_ratio": "16:9",
        },
    )
    video_url = result["video"]["url"]
    v = requests.get(video_url, timeout=120)
    with open(out_path, "wb") as f:
        f.write(v.content)
    return out_path


PROVIDERS = {"stub": _gen_stub, "luma": _gen_luma, "fal": _gen_fal}


def generate_for_slot(slot: Slot, ctx: SceneContext) -> List[Insert]:
    duration = min(max(slot.duration_sec, config.MIN_CLIP_SEC), config.MAX_CLIP_SEC)
    provider = PROVIDERS.get(config.I2V_PROVIDER, _gen_stub)
    inserts = []
    for prompt_text in ctx.replacement_prompts[:2]:
        iid = new_id()
        out_path = os.path.join(config.CLIPS, f"{iid}.mp4")
        full_prompt = (
            f"{prompt_text}. "
            f"Mood: {ctx.mood}. "
            f"Cinematic, high quality, no artifacts, smooth motion."
        )
        try:
            provider(slot.anchor_frame_path, full_prompt, duration, out_path)
        except Exception as e:
            print(f"[generate] failed: {e}")
            continue
        inserts.append(Insert(
            id=iid,
            slot_id=slot.id,
            clip_path=out_path,
            prompt=full_prompt,
            label=prompt_text,
        ))
    return inserts
