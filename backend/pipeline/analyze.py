"""
Stage 2: Analyze the anchor frame of a bad clip using a VLM.
Describes what's wrong, the scene context, and suggests replacement prompts.
Supports Google (Gemini), Anthropic (Claude), and OpenAI-compatible backends.
"""
import base64
import json
from ..models.schemas import SceneContext
from .. import config


def _b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


PROMPT = """
You are a professional video editor's AI assistant analyzing a frame from a video clip
that has been flagged for quality issues.

The detected issues are: {issues}

Analyze this frame and return JSON with:
  description: one-sentence description of what's happening in this scene
  issues_detail: explain specifically what quality problems you see
  mood: short phrase for the mood/tone (e.g. "warm sunset interview", "dark moody b-roll")
  replacement_prompts: 2-3 prompts for generating a BETTER replacement clip of the
    same scene/subject. Each prompt should describe a high-quality cinematic version of
    what this clip was trying to show. Keep each under 30 words.
  recommendation: "replace" if the scene content is worth keeping (just needs better quality),
    or "cut" if the scene adds nothing and should be removed entirely.

Return ONLY valid JSON.
"""


def _analyze_anthropic(anchor_path: str, issues: list) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    b64 = _b64(anchor_path)
    resp = client.messages.create(
        model=config.VLM_MODEL,
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT.format(issues=", ".join(issues))},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            ],
        }],
    )
    text = resp.content[0].text
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def _analyze_openai(anchor_path: str, issues: list) -> dict:
    from openai import OpenAI
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    b64 = _b64(anchor_path)
    resp = client.chat.completions.create(
        model=config.VLM_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": PROMPT.format(issues=", ".join(issues))},
            {"role": "user", "content": [
                {"type": "text", "text": "Analyze this frame."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ],
        max_tokens=500,
    )
    text = resp.choices[0].message.content.replace("```json", "").replace("```", "")
    return json.loads(text)


def _analyze_vertexai(anchor_path: str, issues: list) -> dict:
    import vertexai
    from vertexai.generative_models import GenerativeModel, Part, Image
    vertexai.init(project=config.VERTEXAI_PROJECT, location=config.VERTEXAI_LOCATION)
    model = GenerativeModel(config.VLM_MODEL)
    with open(anchor_path, "rb") as f:
        img_bytes = f.read()
    resp = model.generate_content([
        PROMPT.format(issues=", ".join(issues)),
        Part.from_data(data=img_bytes, mime_type="image/png"),
    ])
    text = resp.text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def analyze_anchor(anchor_path: str, issues: list) -> SceneContext:
    try:
        if config.VLM_PROVIDER == "vertexai":
            d = _analyze_vertexai(anchor_path, issues)
        elif config.VLM_PROVIDER == "anthropic":
            d = _analyze_anthropic(anchor_path, issues)
        else:
            d = _analyze_openai(anchor_path, issues)
    except Exception as e:
        print(f"[analyze] VLM error: {e}")
        d = {}

    return SceneContext(
        description=d.get("description", "scene from video"),
        issues_detail=d.get("issues_detail", ", ".join(issues)),
        mood=d.get("mood", "neutral"),
        replacement_prompts=d.get("replacement_prompts", ["high quality cinematic version of this scene"]),
        recommendation=d.get("recommendation", "replace"),
    )
