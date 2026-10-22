"""Initialize, split and validate an Agent-authored video replacement task."""
from __future__ import annotations

import argparse
import re
import subprocess
from fractions import Fraction
from pathlib import Path

from common import (SECTIONS, command, decode_check, has_replacement, probe,
                    read_json, resolve, segment_paths, validate_plan, write_json)


def initialize(source, output, profile_path=None, keep_reference_audio=False):
    from workflow_config import load_profile, task_settings
    profile = load_profile(profile_path)
    source, root = Path(source).resolve(), Path(output).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("Use a new or empty task directory")
    info = probe(source, timestamps=True)
    original_info = dict(info)
    root.mkdir(parents=True, exist_ok=True)
    working = source
    # The local reference loader resamples reference video to native 24 fps.
    # Match the working timeline and generated playback to that same time base.
    rate = "24"
    if info["variable_frame_rate"] or abs(float(Fraction(info["avg_frame_rate"])) - 24) > 0.001:
        working = root / "工作源片.mp4"
        audio = (["-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k"]
                 if keep_reference_audio else ["-an"])
        command(["ffmpeg", "-v", "error", "-y", "-i", source, "-map", "0:v:0", *audio,
                 "-vf", f"fps={rate}", "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p", working])
        info = probe(working, timestamps=True)
        decode_check(working)
    task = {
        "schema_version": 1, "original_video": str(source), "source_video": str(working),
        "fps": rate, "source_frame_count": info["frame_count"],
        "width": info["width"], "height": info["height"],
        "requirements": "", "characters": {}, "segments": [],
        "settings": task_settings(profile),
    }
    task["settings"]["keep_reference_audio"] = keep_reference_audio
    write_json(root / "任务清单.json", task)
    write_json(root / "媒资信息.json", {"original": original_info, "working": info})
    print(root / "任务清单.json")


def split(root, task):
    validate_plan(task)
    info = probe(task["source_video"])
    if info["frame_count"] != task["source_frame_count"]:
        raise ValueError("Source frame count differs from manifest")
    original_rows, generation_rows = [], []
    for segment in task["segments"]:
        paths = segment_paths(root, task, segment)
        for key in ["source_clip", "video", "synopsis", "prompt", "result"]:
            paths[key].parent.mkdir(parents=True, exist_ok=True)
        start, end = segment["start_frame"], segment["end_frame"]
        if paths["source_clip"].exists() or paths["video"].exists():
            raise ValueError(f"{segment['id']}: clips already exist; use a new task for a new cut plan")
        keep_audio = task["settings"].get("keep_reference_audio", False)
        fps = float(Fraction(task["fps"]))
        audio = (["-map", "0:a:0?", "-af",
                  f"atrim=start={start / fps}:end={end / fps},asetpts=PTS-STARTPTS",
                  "-c:a", "aac", "-b:a", "192k"] if keep_audio else ["-an"])
        command(["ffmpeg", "-v", "error", "-i", task["source_video"], "-map", "0:v:0", *audio,
                 "-vf", f"trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS",
                 "-r", task["fps"], "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p", paths["source_clip"]])
        padding = paths["generation_frames"] - paths["original_frames"]
        audio = (["-map", "0:a:0?", "-af",
                  f"apad,atrim=duration={paths['generation_frames'] / fps},asetpts=PTS-STARTPTS",
                  "-c:a", "aac", "-b:a", "192k"] if keep_audio else ["-an"])
        edge = task["settings"]["reference_short_edge"]
        scale = (f"scale={edge}:{edge}:force_original_aspect_ratio=increase:force_divisible_by=2:flags=lanczos,"
                 if edge else "")
        command(["ffmpeg", "-v", "error", "-i", paths["source_clip"], "-map", "0:v:0", *audio,
                 "-vf", scale + f"tpad=stop_mode=clone:stop={padding}", "-frames:v", paths["generation_frames"],
                 "-r", task["fps"], "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p", paths["video"]])
        for key, expected in [("source_clip", paths["original_frames"]), ("video", paths["generation_frames"])]:
            if probe(paths[key])["frame_count"] != expected:
                raise ValueError(f"Wrong frame count: {paths[key]}")
            decode_check(paths[key])
        paths["synopsis"].write_text(segment["synopsis"].strip() + "\n", encoding="utf-8")
        original_rows.append(f"{segment['id']}.mp4\t{paths['original_frames']}")
        generation_rows.append(f"{segment['id']}.mp4\t{paths['generation_frames']}\t" + ",".join(segment["cast"]))
        print(f"split {segment['id']}: {start}:{end}, {paths['original_frames']} -> {paths['generation_frames']} frames", flush=True)
    folder = Path(root) / "补齐生成用"
    (folder / "原始帧数.txt").write_text("\n".join(original_rows) + "\n", encoding="utf-8")
    (folder / "补齐后帧数.txt").write_text("\n".join(generation_rows) + "\n", encoding="utf-8")


def cut_candidates(root, task):
    args = ["ffmpeg", "-hide_banner", "-i", task["source_video"], "-map", "0:v:0", "-an",
            "-vf", "setpts=PTS-STARTPTS,select='gt(scene,0.3)',showinfo", "-f", "null", "-"]
    result = subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:])
    times = [float(t) for t in re.findall(r"\bpts_time:([\d.eE+-]+)", result.stderr)]
    fps = float(Fraction(task["fps"]))
    candidates = [{"frame": round(t * fps), "seconds": t} for t in times]
    write_json(Path(root) / "切镜候选.json", candidates)
    print(f"{len(candidates)} scene-change candidates; Agent must inspect adjacent frames before accepting cuts")


def check_prompt(text, task, segment):
    headers = re.findall(r"^([a-z_]+):\s*$", text, re.M)
    if headers != SECTIONS or not text.startswith("subject_definitions:") or "```" in text:
        raise ValueError("Expected exactly six plain-text sections, in order")
    for index, name in enumerate(SECTIONS):
        body = text.split(name + ":", 1)[1]
        if index + 1 < len(SECTIONS):
            body = body.split(SECTIONS[index+1] + ":", 1)[0]
        if not body.strip():
            raise ValueError(f"Empty section: {name}")
    if "[video editing + reference generation]" not in text or "<Video 1>" not in text:
        raise ValueError("Missing video-editing prefix or source video reference")
    subjects = set(map(int, re.findall(r"<Subject (\d+)>", text)))
    expected = set(range(1, len(segment["cast"]) + 1))
    if subjects != expected:
        raise ValueError(f"Subject labels {subjects} differ from planned cast {expected}")
    definitions = text.split("summary:", 1)[0]
    definition_lines = re.findall(r"^<Subject (\d+)>\s+.+$", definitions, re.M)
    if list(map(int, definition_lines)) != list(range(1, len(segment["cast"])+1)):
        raise ValueError("Define each Subject once, in cast order")
    picture = 0
    for number, key in enumerate(segment["cast"], 1):
        line = re.search(rf"^<Subject {number}>\s+.+$", definitions, re.M).group(0)
        if task["characters"][key].get("reference_image"):
            picture += 1
            if f"<Picture {picture}>" not in line:
                raise ValueError(f"Subject {number} must refer to Picture {picture}")
        elif "<Picture " in line:
            raise ValueError("Unchanged source character cannot acquire a replacement image")
    pics = re.findall(r"<Picture (\d+)>", text)
    if pics != [str(i) for i in range(1, picture+1)]:
        raise ValueError("Identity pictures must appear exactly once, in their Subject definitions")
    retention = text.split("retention_analysis:", 1)[1].split("detailed_description:", 1)[0]
    for number in expected:
        if not re.search(rf"^<Subject {number}>[^\n:]*:\s*fully_preserved\b", retention, re.M):
            raise ValueError(f"Missing fully_preserved retention for Subject {number}")
    if not re.search(r"^<Video 1>[^\n:]*:\s*partially_preserved\b", retention, re.M):
        raise ValueError("Missing partially_preserved source retention")
    max_time = (segment["end_frame"] - segment["start_frame"]) / float(Fraction(task["fps"]))
    times = [int(m)*60 + float(s) for m, s in re.findall(r"(?<!\d)(\d{2}):(\d{2}\.\d{3})(?!\d)", text)]
    if any(t > max_time + 0.002 for t in times):
        raise ValueError("Prompt timestamp exceeds original segment duration")
    # Interval end/start timestamps may repeat; only actual cut times need strict
    # increase, which the Agent verifies against the frame evidence.
    cut_times = [int(m)*60 + float(s) for m, s in re.findall(
        r"\[Shot \d+\]\s+At (\d{2}):(\d{2}\.\d{3})", text)]
    if any(b <= a for a, b in zip(cut_times, cut_times[1:])):
        raise ValueError("Shot cut times must strictly increase")


def validate_ready(root, task, media=True):
    validate_plan(task)
    errors = []
    for key, character in task["characters"].items():
        ref = character.get("reference_image")
        if ref and not resolve(root, ref).is_file():
            errors.append(f"Missing reference: {key}: {ref}")
    for segment in task["segments"]:
        paths = segment_paths(root, task, segment)
        try:
            for key in ["source_clip", "video", "synopsis"]:
                if not paths[key].is_file():
                    raise ValueError(f"Missing {paths[key]}")
            if media:
                for key, expected in [("source_clip", paths["original_frames"]), ("video", paths["generation_frames"])]:
                    if probe(paths[key])["frame_count"] != expected:
                        raise ValueError(f"Wrong frame count: {paths[key]}")
            if has_replacement(task, segment):
                check_prompt(paths["prompt"].read_text(encoding="utf-8"), task, segment)
        except (ValueError, OSError) as exc:
            errors.append(f"{segment['id']}: {exc}")
    report = {"ready": not errors, "segments": len(task["segments"]), "errors": errors}
    write_json(Path(root) / "准备检查.json", report)
    if errors:
        raise ValueError("\n".join(errors))
    print(f"ready: {len(task['segments'])} segments; complete six-section prompts verified")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("source")
    init.add_argument("output")
    init.add_argument("--profile", help="Confirmed first-use local configuration")
    init.add_argument("--reference-audio", choices=["keep", "drop"], default="drop")
    for name in ["cuts", "split", "check"]:
        sub.add_parser(name).add_argument("task", help="Task directory")
    args = parser.parse_args()
    if args.command == "init":
        initialize(args.source, args.output, args.profile, args.reference_audio == "keep")
    else:
        root = Path(args.task).resolve()
        task = read_json(root / "任务清单.json")
        if args.command == "cuts":
            cut_candidates(root, task)
        elif args.command == "split":
            split(root, task)
        else:
            validate_ready(root, task)


if __name__ == "__main__":
    main()
