"""Shared task files and video operations. Python standard library + FFmpeg."""
from __future__ import annotations

import json
import math
import subprocess
from fractions import Fraction
from pathlib import Path

SECTIONS = ["subject_definitions", "summary", "retention_analysis", "detailed_description",
            "overall_soundscape", "non_diegetic_music"]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def command(args):
    result = subprocess.run([str(x) for x in args], capture_output=True, encoding="utf-8", errors="replace",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError(f"Command failed: {args[0]}\n{result.stderr[-4000:]}")
    return result.stdout


def probe(path, timestamps=False):
    args = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
            "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,nb_read_frames:format=duration",
            "-of", "json"]
    if timestamps:
        args += ["-show_frames", "-show_entries", "frame=best_effort_timestamp_time"]
    data = json.loads(command([*args, path]))
    stream = data["streams"][0]
    stream["frame_count"] = int(stream["nb_read_frames"])
    if timestamps:
        pts = [float(f["best_effort_timestamp_time"]) for f in data["frames"]
               if "best_effort_timestamp_time" in f]
        diffs = [b-a for a, b in zip(pts, pts[1:])]
        rate = float(Fraction(stream["avg_frame_rate"]))
        stream["variable_frame_rate"] = any(abs(d - 1 / rate) > max(0.0002, 0.02 / rate) for d in diffs)
    return stream


def decode_check(path):
    command(["ffmpeg", "-v", "error", "-xerror", "-i", path, "-map", "0:v:0", "-an", "-f", "null", "-"])


def generation_frames(original, fps):
    # v1.14 clamps seconds >= 1 and snaps length to 17*k+5. Pick an exact
    # legal length at or above the original, rather than its nearest value.
    minimum = max(original, math.ceil(float(Fraction(fps))), 5)
    return max(0, math.ceil((minimum - 5) / 17)) * 17 + 5


def resolve(root, path):
    p = Path(path)
    return p if p.is_absolute() else Path(root) / p


def has_replacement(task, segment):
    return any(task["characters"][key].get("reference_image") for key in segment["cast"])


def segment_paths(root, task, segment):
    number = segment["id"]
    count = segment["end_frame"] - segment["start_frame"]
    generated = generation_frames(count, task["fps"])
    root = Path(root)
    return {
        "original_frames": count, "generation_frames": generated,
        "source_clip": root / "原始分段" / f"{number}.mp4",
        "video": root / "补齐生成用" / f"{number} {generated}.mp4",
        "synopsis": root / "补齐生成用" / f"{number}.txt",
        "prompt": root / "提示词" / f"{number}.txt",
        "result": root / "完成分段" / f"{number}.mp4",
    }


def validate_plan(task):
    if not Path(task["source_video"]).is_file():
        raise ValueError("Source video is missing")
    fps = float(Fraction(task["fps"]))
    if fps != 24:
        raise ValueError("Use prepare.py init to align the working timeline with H3's native 24fps reference loader")
    segments = task["segments"]
    if not segments:
        raise ValueError("Agent must write the visually verified segment plan first")
    last = 0
    for index, segment in enumerate(segments, 1):
        if segment["id"] != f"{index:03d}":
            raise ValueError("Segment ids must be sequential: 001, 002, ...")
        start, end = segment["start_frame"], segment["end_frame"]
        if type(start) is not int or type(end) is not int or start != last or end <= start:
            raise ValueError(f"{segment['id']}: invalid boundary, gap, or overlap")
        if len(segment["cast"]) != len(set(segment["cast"])):
            raise ValueError(f"{segment['id']}: duplicate character")
        for key in segment["cast"]:
            if key not in task["characters"]:
                raise ValueError(f"Unknown character: {key}")
        refs = sum(bool(task["characters"][key].get("reference_image")) for key in segment["cast"])
        if refs + 1 > 15:
            raise ValueError("Current H3 permits at most 15 assets including the source video")
        if has_replacement(task, segment) and generation_frames(end-start, task["fps"]) / fps > 30:
            raise ValueError(f"{segment['id']}: padded length exceeds H3's 30-second maximum")
        last = end
    if last != task["source_frame_count"]:
        raise ValueError("Segments must cover the complete working video exactly once")


def trim_result(source, output, frames, fps):
    info = probe(source)
    if info["frame_count"] < frames:
        raise ValueError(f"Generated clip has {info['frame_count']} frames; requires {frames}")
    if abs(float(Fraction(info["avg_frame_rate"])) - float(Fraction(fps))) > 0.01:
        raise ValueError("Generated frame rate differs from the planned frame rate")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.stem + ".partial.mp4")
    command(["ffmpeg", "-v", "error", "-y", "-i", source, "-map", "0:v:0", "-an",
             "-vf", f"trim=end_frame={frames},setpts=PTS-STARTPTS", "-r", fps,
             "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p", temporary])
    if probe(temporary)["frame_count"] != frames:
        raise ValueError("Trimmed output frame count is wrong")
    decode_check(temporary)
    temporary.replace(output)
