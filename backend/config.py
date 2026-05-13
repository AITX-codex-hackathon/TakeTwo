import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
CLIPS = DATA / "clips"
OUTPUTS = DATA / "outputs"
FRAMES = DATA / "frames"

CACHE = DATA / "cache"

for d in (UPLOADS, CLIPS, OUTPUTS, FRAMES, CACHE):
    d.mkdir(parents=True, exist_ok=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
VLM_MODEL = os.getenv("VLM_MODEL", "gpt-4o")
OPENAI_MAX_IMAGES_PER_REQUEST = max(1, int(os.getenv("OPENAI_MAX_IMAGES_PER_REQUEST", "1")))
OPENAI_IMAGE_MIN_INTERVAL_SEC = float(os.getenv(
    "OPENAI_IMAGE_MIN_INTERVAL_SEC",
    "65" if OPENAI_MAX_IMAGES_PER_REQUEST <= 1 else "0",
))
OPENAI_SKIP_CRITIC_WHEN_IMAGE_LIMITED = os.getenv(
    "OPENAI_SKIP_CRITIC_WHEN_IMAGE_LIMITED",
    "1",
).lower() not in ("0", "false", "no")


def require_openai_api_key() -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY is missing. Add it to .env as OPENAI_API_KEY=sk-... "
            "and keep .env out of git."
        )
    return OPENAI_API_KEY

I2V_PROVIDER = os.getenv("I2V_PROVIDER", "fal_kling_v21")
LUMA_API_KEY = os.getenv("LUMA_API_KEY", "")
FAL_API_KEY = os.getenv("FAL_API_KEY", "")
FAL_GENERATION_TIMEOUT_SEC = int(os.getenv("FAL_GENERATION_TIMEOUT_SEC", "240"))
FAL_POLL_INTERVAL_SEC = float(os.getenv("FAL_POLL_INTERVAL_SEC", "8"))
FAL_GENERATIONS_PER_SLOT = int(os.getenv("FAL_GENERATIONS_PER_SLOT", "2"))
FAL_CONCURRENCY = max(1, int(os.getenv("FAL_CONCURRENCY", "2")))

MAX_BAD_CLIPS = int(os.getenv("MAX_BAD_CLIPS", "2"))
FAL_MAX_GENERATED_SLOTS = max(1, int(os.getenv("FAL_MAX_GENERATED_SLOTS", str(MAX_BAD_CLIPS))))
