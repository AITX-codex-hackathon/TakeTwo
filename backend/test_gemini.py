"""
Quick smoke test for the Google Gemini VLM integration.
Creates a dummy PNG frame and runs it through analyze + critic.
"""
import sys
import os
import json

# Make backend importable when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import struct
import zlib

def _make_dummy_png(path: str):
    """Write a minimal valid 64x64 red PNG without any dependencies."""
    def chunk(tag, data):
        c = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", c)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
    # 64x64 RGB rows, each row prefixed with filter byte 0
    row = b"\x00" + b"\xff\x00\x00" * 64  # red pixels
    raw = row * 64
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(sig + ihdr + idat + iend)
    print(f"[test] created dummy PNG: {path}")


def test_analyze(frame_path: str):
    from backend.pipeline.analyze import _analyze_google
    print("\n--- analyze_anchor ---")
    issues = ["shaky", "blurry"]
    result = _analyze_google(frame_path, issues)
    print(json.dumps(result, indent=2))
    return result


def test_critic(frame_path: str):
    from backend.pipeline.critic import _review_google
    print("\n--- critic review (same frame as both A and B) ---")
    result = _review_google(frame_path, frame_path, "shaky, blurry")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    frame = "/tmp/test_frame.png"
    _make_dummy_png(frame)
    try:
        test_analyze(frame)
        test_critic(frame)
        print("\n[test] PASS — Gemini API is working.")
    except Exception as e:
        print(f"\n[test] FAIL — {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)
