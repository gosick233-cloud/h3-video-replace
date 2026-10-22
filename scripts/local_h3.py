"""Run a user-supplied H3 workflow through confirmed, version-independent bindings."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import shutil
import time
import urllib.parse
import urllib.request
import uuid
from fractions import Fraction
from pathlib import Path

from common import (has_replacement, read_json, resolve, segment_paths, trim_result, write_json)
from prepare import validate_ready
from workflow_config import apply_parameters, load_execution, set_target, validate_profile
from workflow_graph import prune_graph, validate_graph


class LocalComfy:
    def __init__(self, url):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("This skill uses a local ComfyUI service")
        self.url = url.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, route, data=None):
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = urllib.request.Request(self.url + route, data=body,
                                     headers={"Content-Type": "application/json"})
        with self.opener.open(req, timeout=30) as response:
            return json.load(response)

    def download(self, item, destination):
        query = urllib.parse.urlencode({k: item.get(k, "") for k in ("filename", "subfolder", "type")})
        with self.opener.open(self.url + "/view?" + query, timeout=120) as response:
            with Path(destination).open("wb") as output:
                shutil.copyfileobj(response, output)


def configure(graph, task, segment, prompt, media, namespace, input_root):
    graph = apply_parameters(copy.deepcopy(graph), task["settings"])
    paths = segment_paths(".", task, segment)
    relative = lambda asset: asset["subfolder"] + "/" + asset["filename"]
    images = [relative(a) for a in media if a["media_type"] == "image"]
    video = next(relative(a) for a in media if a["media_type"] == "video")
    divisor = math.gcd(task["width"], task["height"])
    context = {"prompt": prompt, "media": media, "reference_video": video, "reference_images": images,
               "seconds": paths["generation_frames"] / float(Fraction(task["fps"])),
               "frames": paths["generation_frames"], "original_frames": paths["original_frames"],
               "fps": float(Fraction(task["fps"])), "width": task["width"], "height": task["height"],
               "aspect_ratio": f"{task['width']//divisor}:{task['height']//divisor}",
               "output_prefix": namespace + "/" + segment["id"],
               "seed": int.from_bytes(hashlib.sha256((namespace+segment["id"]).encode()).digest()[:6], "big")}
    for binding in task["settings"]["bindings"]:
        value = copy.deepcopy(context[binding["source"]])
        if "index" in binding:
            if not isinstance(value, list) or binding["index"] >= len(value):
                raise ValueError(f"{segment['id']}: this workflow binding requires a reference image missing from this clip")
            value = value[binding["index"]]
        if binding.get("path_format") == "absolute":
            value = [str(input_root / v) for v in value] if isinstance(value, list) else str(input_root / value)
        if binding.get("encoding") == "json":
            value = json.dumps(value, ensure_ascii=False)
        for target in binding["targets"]:
            set_target(graph, target, value)
    roots = task["settings"]["output_node_ids"] + task["settings"].get("extra_output_node_ids", [])
    return prune_graph(graph, roots)


def stage(root, task, segment, input_root, namespace):
    destination = input_root / namespace / segment["id"]
    destination.mkdir(parents=True, exist_ok=True)
    assets = []
    picture = 0
    for key in segment["cast"]:
        ref = task["characters"][key].get("reference_image")
        if ref:
            picture += 1
            source = resolve(root, ref)
            name = f"picture_{picture}{source.suffix.lower()}"
            shutil.copy2(source, destination / name)
            assets.append({"media_type": "image", "filename": name,
                           "subfolder": f"{namespace}/{segment['id']}", "type": "input"})
    name = "source.mp4"
    shutil.copy2(segment_paths(root, task, segment)["video"], destination / name)
    assets.append({"media_type": "video", "filename": name,
                   "subfolder": f"{namespace}/{segment['id']}", "type": "input"})
    return assets


def build(root, task, client, input_root=None):
    validate_ready(root, task)
    schemas = client.request("/object_info")
    validate_profile(task["settings"], schemas)
    input_root = Path(input_root or task["settings"]["input_directory"])
    graph, document = load_execution(task["settings"], schemas)
    write_json(root / "工作流快照.json", document)
    namespace = "h3-replace/" + hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    jobs = []
    for segment in task["segments"]:
        if not has_replacement(task, segment):
            continue
        paths = segment_paths(root, task, segment)
        prompt = paths["prompt"].read_text(encoding="utf-8")
        media = stage(root, task, segment, input_root, namespace)
        configured = configure(graph, task, segment, prompt, media, namespace, input_root)
        # File-selection combos may change after the staged files become visible.
        schemas = client.request("/object_info")
        validate_graph(configured, schemas)
        write_json((root / "提交图" / segment["id"]).with_suffix(".api.json"), configured)
        digest_builder = hashlib.sha256(json.dumps(configured, sort_keys=True).encode())
        for asset in media:
            with (input_root / asset["subfolder"] / asset["filename"]).open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest_builder.update(block)
        jobs.append({"id": segment["id"], "graph": configured, "workflow": configured,
                     "savers": [str(n) for n in task["settings"]["output_node_ids"]],
                     "digest": digest_builder.hexdigest()})
    print(f"Built {len(jobs)} local H3 jobs; no generation submitted", flush=True)
    return jobs


def wait_idle(client):
    while True:
        queue = client.request("/queue")
        if not queue["queue_running"] and not queue["queue_pending"]:
            return
        print("Existing local tasks are still running; waiting before the next submission", flush=True)
        time.sleep(5)


def wait_history(client, prompt_id):
    while True:
        result = client.request("/history/" + prompt_id)
        if prompt_id in result:
            history = result[prompt_id]
            status = history.get("status", {})
            if status.get("status_str") == "error":
                messages = status.get("messages", [])
                errors = [m[1] for m in messages if m[0] == "execution_error"]
                details = json.dumps(errors or messages, ensure_ascii=False)
                raise RuntimeError(details[-4000:])
            if status.get("completed"):
                return history
        else:
            queue = client.request("/queue")
            active = [q[1] for q in queue["queue_running"] + queue["queue_pending"]]
            if prompt_id not in active:
                raise RuntimeError(f"Task {prompt_id} has no history and is no longer queued; inspect local logs")
        time.sleep(3)


def output_video(history, savers, index=None):
    found = []
    def collect(value):
        if isinstance(value, dict):
            if str(value.get("filename", "")).lower().endswith((".mp4", ".mkv", ".webm", ".mov")):
                if value not in found:
                    found.append(value)
            else:
                for item in value.values():
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    for saver in savers:
        collect(history.get("outputs", {}).get(str(saver), {}))
    if index is not None and 0 <= index < len(found):
        return found[index]
    if len(found) != 1:
        raise RuntimeError(f"Confirm the final video output mapping; found {len(found)} video files")
    return found[0]


def run(root, task, client, jobs):
    state_path = root / "生成记录.json"
    state = read_json(state_path) if state_path.exists() else {"jobs": {}}
    by_id = {s["id"]: s for s in task["segments"]}
    for segment in task["segments"]:
        if not has_replacement(task, segment):
            paths = segment_paths(root, task, segment)
            shutil.copy2(paths["source_clip"], paths["result"])
    for job in jobs:
        number = job["id"]
        paths = segment_paths(root, task, by_id[number])
        record = state["jobs"].get(number, {})
        if record and record.get("digest") != job["digest"]:
            raise ValueError(f"{number}: prepared inputs changed; inspect/remove only this job's old record before rerendering")
        if record.get("status") == "complete" and paths["result"].is_file():
            print(f"skip completed {number}", flush=True)
            continue
        if not record.get("prompt_id"):
            if record:
                raise RuntimeError(f"{number}: previous submission has no prompt id; inspect queue/history before resubmitting")
            wait_idle(client)
            record.update(digest=job["digest"], status="submitting")
            state["jobs"][number] = record
            write_json(state_path, state)
            response = client.request("/prompt", {"prompt": job["graph"], "client_id": uuid.uuid4().hex,
                       "extra_data": {"extra_pnginfo": {"workflow": job["workflow"]}}})
            if not response.get("prompt_id") or response.get("error") or response.get("node_errors"):
                raise RuntimeError(json.dumps(response, ensure_ascii=False))
            record.update(prompt_id=response["prompt_id"], status="running")
            write_json(state_path, state)
        print(f"waiting {number}: {record['prompt_id']}", flush=True)
        try:
            history = wait_history(client, record["prompt_id"])
            item = output_video(history, job["savers"], task["settings"].get("video_output_index"))
            raw = root / "生成原段" / (number + Path(item["filename"]).suffix)
            raw.parent.mkdir(parents=True, exist_ok=True)
            client.download(item, raw)
            trim_result(raw, paths["result"], paths["original_frames"], task["fps"])
            record.update(status="complete", result=str(paths["result"]))
            write_json(state_path, state)
            print(f"completed {number}; original timing restored", flush=True)
        except Exception as exc:
            record.update(status="failed", error=str(exc))
            write_json(state_path, state)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build", "run"])
    parser.add_argument("task")
    parser.add_argument("--input-dir", help="Explicit per-run override of the initialized input directory")
    args = parser.parse_args()
    root = Path(args.task).resolve()
    task = read_json(root / "任务清单.json")
    client = LocalComfy(task["settings"]["comfy_url"])
    jobs = build(root, task, client, args.input_dir)
    if args.command == "run":
        run(root, task, client, jobs)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped the serial runner. No further jobs will be submitted; an already submitted job remains in ComfyUI.")
        raise SystemExit(130)
