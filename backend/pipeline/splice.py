"""
Stage 5: Splice approved replacements into the source video or cut bad segments.
Processes slots in reverse frame order so earlier indices stay valid.
"""
import os
import json
import shutil
import subprocess
import tempfile
from ..models.schemas import Job
from .. import config


def _media_info(path: str) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe required")
    out = subprocess.check_output([
        ffprobe, "-v", "quiet", "-print_format", "json",
        "-show_streams", path,
    ])
    streams = json.loads(out)["streams"]
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        raise ValueError("No video stream found")
    num, den = video.get("r_frame_rate", "30/1").split("/")
    fps = float(num) / float(den)
    return {
        "width": int(video.get("width", 1920)),
        "height": int(video.get("height", 1080)),
        "fps": fps,
        "frames": int(video.get("nb_frames", 0) or 0),
        "has_audio": audio is not None,
    }


def _encode_source_segment(src_video: str, start_t: float, duration: float,
                           out_path: str, has_audio: bool):
    ffmpeg = shutil.which("ffmpeg")
    cmd = [
        ffmpeg, "-y", "-ss", f"{start_t:.6f}", "-i", src_video,
        "-t", f"{duration:.6f}",
        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
    ]
    if has_audio:
        cmd += ["-c:a", "aac", "-ar", "44100", "-ac", "2"]
    else:
        cmd += ["-an"]
    cmd += ["-movflags", "+faststart", out_path]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _encode_replacement_segment(src_video: str, clip_path: str, start_t: float,
                                duration: float, out_path: str, info: dict):
    ffmpeg = shutil.which("ffmpeg")
    width = info["width"]
    height = info["height"]
    fps = info["fps"]
    vf = (
        f"fps={fps:.6f},"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        "setsar=1,trim=start=0:"
        f"duration={duration:.6f},setpts=PTS-STARTPTS"
    )

    if info["has_audio"]:
        subprocess.run([
            ffmpeg, "-y",
            "-stream_loop", "-1", "-i", clip_path,
            "-ss", f"{start_t:.6f}", "-i", src_video,
            "-t", f"{duration:.6f}",
            "-filter_complex",
            f"[0:v]{vf}[v];[1:a]atrim=start=0:duration={duration:.6f},asetpts=PTS-STARTPTS[a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart", out_path,
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.run([
            ffmpeg, "-y", "-stream_loop", "-1", "-i", clip_path,
            "-t", f"{duration:.6f}",
            "-vf", vf,
            "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
            "-an", "-movflags", "+faststart", out_path,
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _replace_segment(src_video: str, clip_path: str, start_frame: int, end_frame: int,
                     fps: float, out_path: str):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg required")

    info = _media_info(src_video)
    head_t = start_frame / fps
    tail_t = (end_frame + 1) / fps
    duration = max(0.001, tail_t - head_t)
    width = info["width"]
    height = info["height"]
    src_fps = info["fps"]

    video_parts = []
    filters = []
    if head_t > 0:
        filters.append(f"[0:v]trim=start=0:end={head_t:.6f},setpts=PTS-STARTPTS[v0]")
        video_parts.append("[v0]")

    replacement_filter = (
        f"[1:v]trim=start=0:duration={duration:.6f},setpts=PTS-STARTPTS,"
        f"fps={src_fps:.6f},"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1[v1]"
    )
    filters.append(replacement_filter)
    video_parts.append("[v1]")

    filters.append(f"[0:v]trim=start={tail_t:.6f},setpts=PTS-STARTPTS[v2]")
    video_parts.append("[v2]")
    filters.append(f"{''.join(video_parts)}concat=n={len(video_parts)}:v=1:a=0[v]")

    cmd = [
        ffmpeg, "-y", "-i", src_video, "-stream_loop", "-1", "-i", clip_path,
        "-filter_complex", ";".join(filters),
        "-map", "[v]",
    ]
    if info["has_audio"]:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-ar", "44100", "-ac", "2"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", out_path]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _cut_segment(src_video: str, start_frame: int, end_frame: int, fps: float, out_path: str):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg required")

    info = _media_info(src_video)
    head_t = start_frame / fps
    tail_t = (end_frame + 1) / fps
    video_parts = []
    audio_parts = []
    filters = []

    if head_t > 0:
        filters.append(f"[0:v]trim=start=0:end={head_t:.6f},setpts=PTS-STARTPTS[v0]")
        video_parts.append("[v0]")
        if info["has_audio"]:
            filters.append(f"[0:a]atrim=start=0:end={head_t:.6f},asetpts=PTS-STARTPTS[a0]")
            audio_parts.append("[a0]")

    filters.append(f"[0:v]trim=start={tail_t:.6f},setpts=PTS-STARTPTS[v1]")
    video_parts.append("[v1]")
    if info["has_audio"]:
        filters.append(f"[0:a]atrim=start={tail_t:.6f},asetpts=PTS-STARTPTS[a1]")
        audio_parts.append("[a1]")

    filters.append(f"{''.join(video_parts)}concat=n={len(video_parts)}:v=1:a=0[v]")
    if info["has_audio"]:
        filters.append(f"{''.join(audio_parts)}concat=n={len(audio_parts)}:v=0:a=1[a]")

    cmd = [ffmpeg, "-y", "-i", src_video, "-filter_complex", ";".join(filters), "-map", "[v]"]
    if info["has_audio"]:
        cmd += ["-map", "[a]", "-c:a", "aac", "-ar", "44100", "-ac", "2"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", out_path]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
        resume_frame = slot.resume_frame
        if ins.status == "approved":
            _replace_segment(current, ins.clip_path, slot.start_frame, resume_frame, slot.fps, next_path)
            ins.status = "applied"
        else:
            _cut_segment(current, slot.start_frame, resume_frame, slot.fps, next_path)
            ins.status = "applied"
        current = next_path

    final = os.path.join(config.OUTPUTS, f"{job.id}_final.mp4")
    shutil.copy(current, final)
    job.output_path = final
    return final
