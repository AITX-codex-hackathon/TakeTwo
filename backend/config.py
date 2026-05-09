import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
CLIPS = DATA / "clips"
OUTPUTS = DATA / "outputs"
FRAMES = DATA / "frames"

for d in (UPLOADS, CLIPS, OUTPUTS, FRAMES):
    d.mkdir(parents=True, exist_ok=True)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

VLM_PROVIDER = os.getenv("VLM_PROVIDER", "anthropic")
VLM_MODEL = os.getenv("VLM_MODEL", "claude-sonnet-4-6")

I2V_PROVIDER = os.getenv("I2V_PROVIDER", "stub")
LUMA_API_KEY = os.getenv("LUMA_API_KEY", "")
FAL_API_KEY = os.getenv("FAL_API_KEY", "")

QUALITY_THRESHOLD = float(os.getenv("QUALITY_THRESHOLD", "0.45"))
MIN_CLIP_SEC = float(os.getenv("MIN_CLIP_SEC", "1.5"))
MAX_CLIP_SEC = float(os.getenv("MAX_CLIP_SEC", "6.0"))
MAX_BAD_CLIPS = int(os.getenv("MAX_BAD_CLIPS", "10"))
MIN_GAP_SEC = float(os.getenv("MIN_GAP_SEC", "5.0"))
