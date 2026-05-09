"""
Stage 5: Splice approved replacements into the source video or cut bad segments.
Processes slots in reverse frame order so earlier indices stay valid.
"""
import os
import shutil
import subprocess
import tempfile
from ..models.schemas import Job
from .. import config


def _replace_segment(src_video: str, clip_path: str, start_frame: int, end_frame: int,
                     fps: float, out_path: str):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg required")

    import cv2
    cap = cv2.VideoCapture(src_video)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()

    head_t = start_frame / fps
    tail_t = (end_frame + 1) / fps

    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        clist = os.path.join(tmp, "concat.txt")

        if start_frame > 0:
            head = os.path.join(tmp, "head.mp4")
            subprocess.run([
                ffmpeg, "-y", "-i", src_video, "-t", f"{head_t:.6f}",
                "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-movflags", "+faststart", head,
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            parts.append(head)

        insert_reenc = os.path.join(tmp, "insert.mp4")
        subprocess.run([
            ffmpeg, "-y", "-i", clip_path,
            "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
            "-an", "-movflags", "+faststart", insert_reenc,
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        parts.append(insert_reenc)

        if end_frame < total - 1:
            tail = os.path.join(tmp, "tail.mp4")
            subprocess.run([
                ffmpeg, "-y", "-ss", f"{tail_t:.6f}", "-i", src_video,
                "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-movflags", "+faststart", tail,
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            parts.append(tail)

        with open(clist, "w") as f:
            for p in parts:
                f.write(f"file '{p}'\n")

        subprocess.run([
            ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", clist,
            "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", out_path,
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _cut_segment(src_video: str, start_frame: int, end_frame: int, fps: float, out_path: str):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg required")

    import cv2
    cap = cv2.VideoCapture(src_video)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()

    head_t = start_frame / fps
    tail_t = (end_frame + 1) / fps

    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        clist = os.path.join(tmp, "concat.txt")

        if start_frame > 0:
            head = os.path.join(tmp, "head.mp4")
            subprocess.run([
                ffmpeg, "-y", "-i", src_video, "-t", f"{head_t:.6f}",
                "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-movflags", "+faststart", head,
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            parts.append(head)

        if end_frame < total - 1:
            tail = os.path.join(tmp, "tail.mp4")
            subprocess.run([
                ffmpeg, "-y", "-ss", f"{tail_t:.6f}", "-i", src_video,
                "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-movflags", "+faststart", tail,
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            parts.append(tail)

        with open(clist, "w") as f:
            for p in parts:
                f.write(f"file '{p}'\n")

        subprocess.run([
            ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", clist,
            "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", out_path,
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def apply_decisions(job: Job) -> str:
    slots_by_id = {s.id: s for s in job.slots}
    decisions = [i for i in job.inserts if i.status in ("approved", "cut")]

    chosen = {}
    for ins in decisions:
        if ins.status == "approved":
            chosen[ins.slot_id] = ins
        elif ins.status == "cut" and ins.slot_id not in chosen:
            chosen[ins.slot_id] = ins

    ordered = sorted(chosen.values(), key=lambda i: slots_by_id[i.slot_id].start_frame, reverse=True)

    current = job.source_path
    for ins in ordered:
        slot = slots_by_id[ins.slot_id]
        next_path = os.path.join(config.OUTPUTS, f"{job.id}_step_{ins.id}.mp4")
        if ins.status == "approved":
            _replace_segment(current, ins.clip_path, slot.start_frame, slot.end_frame, slot.fps, next_path)
            ins.status = "applied"
        else:
            _cut_segment(current, slot.start_frame, slot.end_frame, slot.fps, next_path)
            ins.status = "applied"
        current = next_path

    final = os.path.join(config.OUTPUTS, f"{job.id}_final.mp4")
    shutil.copy(current, final)
    job.output_path = final
    return final
