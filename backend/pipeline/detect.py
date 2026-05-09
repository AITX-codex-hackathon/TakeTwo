"""
Stage 1: Detect bad clips in the source video.
Scores each shot for quality issues: blur, overexposure, shaky, low contrast, noise.
Returns Slots for segments that fall below the quality threshold.
"""
import os
import cv2
import numpy as np
from typing import List, Tuple
from ..models.schemas import Slot, new_id
from .. import config


def _to_gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _blur_score(gray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _exposure_score(gray) -> float:
    mean = float(np.mean(gray))
    if mean < 30:
        return max(0, mean / 30.0)
    if mean > 230:
        return max(0, (255 - mean) / 25.0)
    return 1.0


def _contrast_score(gray) -> float:
    std = float(np.std(gray))
    return min(std / 50.0, 1.0)


def _noise_score(gray) -> float:
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    diff = np.abs(gray.astype(np.float32) - blurred.astype(np.float32))
    noise_level = float(np.mean(diff))
    return max(0, 1.0 - noise_level / 15.0)


def _motion_stability(gray_a, gray_b) -> float:
    diff = float(np.mean(np.abs(gray_a.astype(np.float32) - gray_b.astype(np.float32))))
    if diff > 40:
        return 0.2
    return 1.0 - (diff / 50.0)


def _hist_diff(a, b) -> float:
    h1 = cv2.calcHist([a], [0, 1, 2], None, [8, 8, 8], [0, 256] * 3)
    h2 = cv2.calcHist([b], [0, 1, 2], None, [8, 8, 8], [0, 256] * 3)
    cv2.normalize(h1, h1)
    cv2.normalize(h2, h2)
    sim = max(min(cv2.compareHist(h1, h2, cv2.HISTCMP_CORREL), 1.0), -1.0)
    return 1.0 - ((sim + 1.0) / 2.0)


def detect_shots(frames) -> List[Tuple[int, int]]:
    if len(frames) < 2:
        return [(0, len(frames) - 1)]
    diffs = []
    for i in range(1, len(frames)):
        g1, g2 = _to_gray(frames[i - 1]), _to_gray(frames[i])
        hd = _hist_diff(frames[i - 1], frames[i])
        gd = min(float(np.mean(np.abs(g1.astype(np.float32) - g2.astype(np.float32)))) / 60.0, 1.0)
        diffs.append(0.6 * hd + 0.4 * gd)
    arr = np.array(diffs, dtype=np.float32)
    thr = max(arr.mean() + 2.5 * arr.std(), 0.28)
    starts = [0] + [i for i, d in enumerate(diffs, start=1) if d >= thr] + [len(frames)]
    starts = sorted(set(starts))
    return [(starts[i], starts[i + 1] - 1) for i in range(len(starts) - 1) if starts[i + 1] > starts[i]]


def score_quality(frames, start: int, end: int) -> Tuple[float, List[str]]:
    """
    Returns (quality_score, issues_list).
    quality_score: 0.0 = terrible, 1.0 = perfect.
    """
    if end - start < 1:
        return 1.0, []

    sample_indices = np.linspace(start, end, num=min(8, end - start + 1), dtype=int)
    issues = []

    blur_scores = []
    exposure_scores = []
    contrast_scores = []
    noise_scores = []
    stability_scores = []

    for idx in sample_indices:
        gray = _to_gray(frames[idx])
        blur_scores.append(_blur_score(gray))
        exposure_scores.append(_exposure_score(gray))
        contrast_scores.append(_contrast_score(gray))
        noise_scores.append(_noise_score(gray))

    for a, b in zip(sample_indices[:-1], sample_indices[1:]):
        g1, g2 = _to_gray(frames[a]), _to_gray(frames[b])
        stability_scores.append(_motion_stability(g1, g2))

    avg_blur = float(np.mean(blur_scores))
    avg_exposure = float(np.mean(exposure_scores))
    avg_contrast = float(np.mean(contrast_scores))
    avg_noise = float(np.mean(noise_scores))
    avg_stability = float(np.mean(stability_scores)) if stability_scores else 1.0

    blur_norm = min(avg_blur / 500.0, 1.0)

    if blur_norm < 0.15:
        issues.append("blurry")
    if avg_exposure < 0.5:
        issues.append("bad exposure")
    if avg_contrast < 0.3:
        issues.append("low contrast")
    if avg_noise < 0.4:
        issues.append("noisy")
    if avg_stability < 0.4:
        issues.append("shaky/unstable")

    quality = (
        0.30 * blur_norm
        + 0.20 * avg_exposure
        + 0.15 * avg_contrast
        + 0.15 * avg_noise
        + 0.20 * avg_stability
    )

    return float(quality), issues


def extract_anchor(video_path: str, frame_idx: int, out_path: str) -> str:
    cap = cv2.VideoCapture(video_path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"Could not read frame {frame_idx}")
    cv2.imwrite(out_path, frame)
    return out_path


def extract_clip(video_path: str, start_frame: int, end_frame: int, out_path: str, fps: float) -> str:
    import subprocess
    import shutil
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg required")
    start_t = start_frame / fps
    duration = (end_frame - start_frame + 1) / fps
    subprocess.run([
        ffmpeg, "-y", "-ss", f"{start_t:.6f}", "-i", video_path,
        "-t", f"{duration:.6f}",
        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
        "-an", "-movflags", "+faststart", out_path,
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_path


def find_bad_clips(video_path: str, resize_width: int = 640) -> List[Slot]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open: {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0

    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        h, w = f.shape[:2]
        if w > resize_width:
            f = cv2.resize(f, (resize_width, int(h * resize_width / w)))
        frames.append(f)
    cap.release()

    if len(frames) < 3:
        return []

    shots = detect_shots(frames)
    min_len = int(config.MIN_CLIP_SEC * fps)
    max_len = int(config.MAX_CLIP_SEC * fps * 2)

    candidates = []
    for s, e in shots:
        if (e - s + 1) < min_len or (e - s + 1) > max_len:
            continue
        quality, issues = score_quality(frames, s, e)
        if quality < config.QUALITY_THRESHOLD and len(issues) > 0:
            candidates.append((s, e, quality, issues))

    candidates.sort(key=lambda x: x[2])

    picked = []
    min_gap = int(config.MIN_GAP_SEC * fps)
    for s, e, quality, issues in candidates:
        if any(abs(s - ps) < min_gap or abs(e - pe) < min_gap for ps, pe, _, _ in picked):
            continue
        picked.append((s, e, quality, issues))
        if len(picked) >= config.MAX_BAD_CLIPS:
            break

    slots = []
    for s, e, quality, issues in picked:
        sid = new_id()
        mid = (s + e) // 2
        anchor_path = os.path.join(config.FRAMES, f"{sid}.png")
        extract_anchor(video_path, mid, anchor_path)
        clip_path = os.path.join(config.CLIPS, f"{sid}_original.mp4")
        extract_clip(video_path, s, e, clip_path, fps)
        slots.append(Slot(
            id=sid,
            start_frame=s,
            end_frame=e,
            fps=fps,
            quality_score=quality,
            anchor_frame_path=anchor_path,
            issues=issues,
        ))
    return slots
