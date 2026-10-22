"""Create visual evidence and contact sheets; does not listen to audio."""
import argparse
from pathlib import Path

from common import command, probe, write_json


def extract(video, output, fps=2, start=0, end=None):
    if fps <= 0 or start < 0 or (end is not None and end <= start):
        raise ValueError("Use a positive frame sampling rate and a valid time interval")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    frames = output / "frames"
    frames.mkdir()
    arguments = ["ffmpeg", "-v", "error", "-ss", start, "-i", video]
    if end is not None:
        arguments += ["-t", end-start]
    command([*arguments, "-map", "0:v:0", "-an", "-vf", f"fps={fps},scale=480:-2",
             frames / "f_%06d.jpg"])
    paths = sorted(frames.glob("*.jpg"))
    if not paths:
        raise ValueError("No frames extracted for this interval")
    command(["ffmpeg", "-v", "error", "-framerate", "1", "-i", frames / "f_%06d.jpg",
             "-vf", "scale=240:-2,tile=4x4:padding=4:color=gray", output / "grid_%03d.jpg"])
    write_json(output / "evidence.json", {
        "video": str(Path(video).resolve()), "media": probe(video), "sampling_fps": fps,
        "start_seconds": start, "end_seconds": end,
        "frames": [{"file": str(p.relative_to(output)),
                    "sample_seconds": start + (i+.5)/fps} for i, p in enumerate(paths)],
        "timing_note": "Sparse sample centers are approximate; verify final cuts with exact zero-based frame extraction.",
    })
    print(f"Created {len(paths)} evidence frames in {output.resolve()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video")
    parser.add_argument("output")
    parser.add_argument("--fps", type=float, default=2)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--end", type=float)
    args = parser.parse_args()
    extract(args.video, args.output, args.fps, args.start, args.end)


if __name__ == "__main__":
    main()
