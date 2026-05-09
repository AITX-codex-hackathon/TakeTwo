"""
Stage 4: Quality critic. Compares original bad frame vs generated replacement frame.
Checks if the replacement is actually better and maintains scene continuity.
"""
import base64
import json
import cv2
import os
from ..models.schemas import Insert
from .. import config


PROMPT = """
You are a video quality critic. You'll see two images:
  IMAGE A: a frame from the ORIGINAL clip that was flagged as bad quality
  IMAGE B: first frame of an AI-GENERATED replacement clip

The original was flagged for: {issues}

Evaluate whether the replacement is an improvement. Return JSON:
{{
  "better_quality": true/false,
  "maintains_scene": true/false,
  "artifacts": true/false,
  "natural_looking": true/false,
  "pass": true/false,
  "notes": "short explanation"
}}

"pass" = true means IMAGE B is a clear improvement over IMAGE A and safe to use.
"""


def _b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


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

    try:
        if config.VLM_PROVIDER == "vertexai":
            d = _review_vertexai(anchor_path, first_path, issues_str)
        elif config.VLM_PROVIDER == "anthropic":
            d = _review_anthropic(anchor_path, first_path, issues_str)
        else:
            d = _review_openai(anchor_path, first_path, issues_str)
    except Exception as e:
        d = {"pass": False, "notes": f"critic error: {e}"}

    insert.critic_pass = bool(d.get("pass", False))
    insert.critic_notes = d.get("notes", "")
    return insert


def _review_vertexai(anchor_path: str, gen_path: str, issues_str: str) -> dict:
    import vertexai
    from vertexai.generative_models import GenerativeModel, Part
    vertexai.init(project=config.VERTEXAI_PROJECT, location=config.VERTEXAI_LOCATION)
    model = GenerativeModel(config.VLM_MODEL)
    with open(anchor_path, "rb") as f:
        anchor_bytes = f.read()
    with open(gen_path, "rb") as f:
        gen_bytes = f.read()
    resp = model.generate_content([
        PROMPT.format(issues=issues_str),
        "Image A (original bad frame):",
        Part.from_data(data=anchor_bytes, mime_type="image/png"),
        "Image B (generated replacement):",
        Part.from_data(data=gen_bytes, mime_type="image/png"),
    ])
    text = resp.text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def _review_anthropic(anchor_path: str, gen_path: str, issues_str: str) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=config.VLM_MODEL,
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT.format(issues=issues_str)},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _b64(anchor_path)}},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _b64(gen_path)}},
            ],
        }],
    )
    text = resp.content[0].text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def _review_openai(anchor_path: str, gen_path: str, issues_str: str) -> dict:
    from openai import OpenAI
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=config.VLM_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": PROMPT.format(issues=issues_str)},
            {"role": "user", "content": [
                {"type": "text", "text": "Image A = original bad frame. Image B = generated replacement. Judge quality."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_b64(anchor_path)}"}},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_b64(gen_path)}"}},
            ]},
        ],
        max_tokens=300,
    )
    text = resp.choices[0].message.content.replace("```json", "").replace("```", "")
    return json.loads(text)
