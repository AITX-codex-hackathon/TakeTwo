"""
Stage 4: Quality critic using GPT-4o.
Compares original bad frame vs generated replacement.
"""
import json
import base64
import cv2
import os
from ..models.schemas import Insert
from .. import config


PROMPT = """You are a video quality critic comparing two frames.
  IMAGE A: original clip flagged as bad quality
  IMAGE B: AI-generated cinematic replacement

Original was flagged for: {issues}

Return JSON:
{{"better_quality": true/false, "maintains_scene": true/false, "artifacts": true/false,
  "natural_looking": true/false, "pass": true/false, "notes": "short explanation"}}

"pass" = true means IMAGE B is a clear improvement and safe to use."""


def _first_frame(clip_path: str, out_path: str) -> str:
    cap = cv2.VideoCapture(clip_path)
    ok, f = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"Cannot read clip: {clip_path}")
    cv2.imwrite(out_path, f)
    return out_path


def review(insert: Insert, anchor_path: str, issues: list) -> Insert:
    first_path = os.path.join(config.FRAMES, f"{insert.id}_gen_first.png")
    _first_frame(insert.clip_path, first_path)

    issues_str = ", ".join(issues) if issues else "general quality"
    print(f"[critic] calling GPT-4o to compare frames, issues={issues_str}", flush=True)

    from openai import OpenAI
    try:
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        with open(anchor_path, "rb") as f:
            b64_a = base64.b64encode(f.read()).decode()
        with open(first_path, "rb") as f:
            b64_b = base64.b64encode(f.read()).decode()
        resp = client.chat.completions.create(
            model=config.VLM_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT.format(issues=issues_str)},
                    {"type": "text", "text": "Image A (original bad frame):"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_a}", "detail": "low"}},
                    {"type": "text", "text": "Image B (AI replacement):"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_b}", "detail": "low"}},
                ],
            }],
            max_tokens=300,
            temperature=0,
        )
        text = resp.choices[0].message.content.replace("```json", "").replace("```", "").strip()
        d = json.loads(text)
        print(f"[critic] GPT-4o: pass={d.get('pass')} — {d.get('notes')}", flush=True)
    except Exception as e:
        print(f"[critic] ERROR: {e}", flush=True)
        d = {"pass": False, "notes": f"critic error: {e}"}

    insert.critic_pass = bool(d.get("pass", False))
    insert.critic_notes = d.get("notes", "")
    return insert
