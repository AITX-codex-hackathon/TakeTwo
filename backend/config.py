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

CACHE = DATA / "cache"

for d in (UPLOADS, CLIPS, OUTPUTS, FRAMES, CACHE):
    d.mkdir(parents=True, exist_ok=True)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
VLM_MODEL = os.getenv("VLM_MODEL", "gemini-2.5-flash")

I2V_PROVIDER = os.getenv("I2V_PROVIDER", "stub")
LUMA_API_KEY = os.getenv("LUMA_API_KEY", "")
FAL_API_KEY = os.getenv("FAL_API_KEY", "")

MAX_BAD_CLIPS = int(os.getenv("MAX_BAD_CLIPS", "2"))
