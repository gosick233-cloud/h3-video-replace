"""Launch the approved local serial batch independently of the chat."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from common import read_json, write_json


def start_background(command, root):
    log = root / "批量生成.log"
    options = {"stdin": subprocess.DEVNULL, "stderr": subprocess.STDOUT, "cwd": str(root)}
    if os.name == "nt":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    with log.open("ab") as output:
        process = subprocess.Popen(command, stdout=output, **options)
    time.sleep(.5)
    if process.poll() is not None:
        raise RuntimeError(f"Batch exited during startup; inspect {log}")
    return process


def launch(root, input_dir=None):
    root = Path(root).resolve()
    task = read_json(root / "任务清单.json")
    runner = "local_h3.py"
    command = [sys.executable, "-u", "-X", "utf8", str(Path(__file__).with_name(runner)), "run", str(root)]
    if input_dir is not None:
        command.extend(["--input-dir", str(input_dir)])
    (root / "完成分段").mkdir(exist_ok=True)
    (root / "生成原段").mkdir(exist_ok=True)
    process = start_background(command, root)
    record = {"pid": process.pid, "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "output_directory": str(root / "完成分段"), "raw_output_directory": str(root / "生成原段"),
              "log": str(root / "批量生成.log"), "command": command}
    write_json(root / "批量运行记录.json", record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run"])
    parser.add_argument("task")
    parser.add_argument("--input-dir")
    args = parser.parse_args()
    print(json.dumps(launch(args.task, args.input_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
