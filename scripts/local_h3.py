"""Compile the user's v1.14 workflow and run approved jobs strictly serially.

build reads local schemas and stages files; only run submits GPU work.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import shutil
import time
import urllib.parse
import urllib.request
import uuid
from fractions import Fraction
from pathlib import Path

from common import (has_replacement, read_json, resolve, segment_paths, trim_result,
                    write_json)
from prepare import validate_ready
from workflow_config import apply_parameters, node_by_id, set_widget, widgets

EASY = "gosick_233_MiniMaxH3EasyV114"
MOTION = "gosick_233_H3MotionBlurSwitchV114"
SECOND_SAVE = "gosick_233_ConditionalSecondPassSaveV114"
FIRST_SAVE = "gosick_233_MiniMaxH3ConditionalFirstPassSaveVideo"
MP = {"360P": .2, "416P": .3, "480P": .4, "540P": .5, "640P": .7,
      "720P": .9, "768P": 1., "832P": 1.2, "928P": 1.5, "1024P": 1.8, "1080P": 2.}


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


def named_node(workflow, *, title=None, kind=None):
    matches = [n for n in workflow["nodes"] if
               (title is None or n.get("title") == title) and (kind is None or n["type"] == kind)]
    if len(matches) != 1:
        raise ValueError(f"Expected one workflow node for {title or kind}, found {len(matches)}")
    return matches[0]


def canvas(task, schemas):
    requested = task["settings"]["first_resolution"]
    if requested == "custom":
        return {"resolution": requested, "width": task["settings"]["width"],
                "height": task["settings"]["height"]}
    ratio = task["width"] / task["height"]
    choices = schemas[EASY]["input"]["required"]["aspect_ratio"][0]
    nearest = min(choices, key=lambda s: abs(float(Fraction(s.replace(":", "/"))) - ratio))
    w, h = map(int, nearest.split(":"))
    if abs(w/h-ratio) / ratio < .01:
        return {"resolution": requested, "aspect_ratio": nearest}
    # Keep unusual source ratios instead of forcing them into a 16:9 frame.
    pixels = MP[requested] * 1024 * 1024
    width = max(32, round(math.sqrt(pixels * ratio) / 32) * 32)
    height = max(32, round(math.sqrt(pixels / ratio) / 32) * 32)
    return {"resolution": "custom", "aspect_ratio": nearest, "width": width, "height": height}


def configure(workflow, task, segment, prompt, media, prefix, schemas):
    workflow = apply_parameters(copy.deepcopy(workflow), task["settings"])
    node = node_by_id(workflow, task["settings"]["easy_node_id"])
    paths = segment_paths(".", task, segment)
    values = {"mode": "ref2va", "prompt": prompt, **canvas(task, schemas),
              "seconds": paths["generation_frames"] / float(Fraction(task["fps"])),
              "advanced": True, "fps": float(Fraction(task["fps"])),
              "keyframe_role": "first", "reference_mention_mode": "index",
              "media_manifest": json.dumps(media, ensure_ascii=False), "continuation_config": "{}"}
    # Some UI exports store a translated label for this backend combo value.
    current_size = widgets(node).get("ref_image_size")
    spec = input_schemas(schemas[EASY], widgets(node)).get("ref_image_size")
    if current_size is not None and spec and isinstance(spec[0][0], list):
        choices = spec[0][0]
        size = re.search(r"(\d+)\s*k", str(current_size), re.I)
        if current_size not in choices and size and size[1] + "k" in choices:
            values["ref_image_size"] = size[1] + "k"
    for name, value in values.items():
        set_widget(node, name, value)
    second_enabled = widgets(node).get("second_pass_enabled", False)
    by_id = {str(n["id"]): n for n in workflow["nodes"]}
    for n in workflow["nodes"]:
        if n["type"] == MOTION:
            for label, key in [("一采高动态减少模糊", "first_refiner_id"),
                               ("二采高动态减少模糊", "second_refiner_id")]:
                refiner = n.get("properties", {}).get(key)
                if refiner is not None and label in widgets(n):
                    by_id[str(refiner)]["mode"] = 0 if widgets(n)[label] else 4
        for kind, control in [("gosick_233_MiniMaxH3SecondPassSwitch", "开启二采"),
                              (FIRST_SAVE, "开启二采"), (SECOND_SAVE, "开启保存二采")]:
            if n["type"] == kind:
                set_widget(n, control, second_enabled)
        if "filename_prefix" in widgets(n):
            set_widget(n, "filename_prefix", f"{prefix}/{segment['id']}/node_{n['id']}")
        if n["type"] == "gosick_233_H3TiledSampler":
            set_widget(n, "target_frames", paths["generation_frames"])
    workflow.get("extra", {}).pop("minimax_h3_unified", None)
    properties = node.setdefault("properties", {})
    for name in ("minimax_h3_v114_continuation", "minimax_h3_v114_second_pass_resume"):
        properties.pop(name, None)
    properties["minimax_h3_v114_segments"] = [{
        "id": segment["id"], "prompt": prompt, "ai_idea": "", "seconds": values["seconds"],
        "assets": {kind: [a for a in media if a["media_type"] == kind]
                   for kind in ("image", "video", "audio")},
    }]
    properties["minimax_h3_v114_active_segment"] = 0
    return workflow


def output_roots(workflow, saver):
    roots = {str(saver["id"])}
    nodes = {str(n["id"]): n for n in workflow["nodes"]}
    for node in workflow["nodes"]:
        if node["type"] != "gosick_233_MiniMaxH3AutoUnload" or node.get("mode", 0) != 0:
            continue
        video = next(item for item in node["inputs"] if item["name"] == "video")
        link = next(link for link in workflow["links"] if link[0] == video["link"])
        old = nodes[str(link[1])]["outputs"][link[2]]
        old["links"].remove(link[0])
        link[1:3] = [saver["id"], 0]
        saver["outputs"][0].setdefault("links", []).append(link[0])
        roots.add(str(node["id"]))
    return roots


def input_schemas(schema, values):
    """Expand the selected v3 dynamic-combo branches into flat API keys."""
    result = {}

    def add(inputs, prefix=""):
        for category in ("required", "optional"):
            for name, spec in inputs.get(category, {}).items():
                key = prefix + name
                result[key] = (spec, category)
                if spec[0] == "COMFY_DYNAMICCOMBO_V3":
                    for option in spec[1].get("options", []):
                        if option["key"] == values.get(key):
                            add(option["inputs"], key + ".")
                            break
    add(schema["input"])
    return result


def compile_graph(workflow, schemas, output_ids=None):
    nodes = {n["id"]: n for n in workflow["nodes"]}
    links = {link[0]: link for link in workflow["links"]}

    def source(link_id, seen=None):
        seen = set() if seen is None else seen
        link = links[link_id]
        node_id, slot = link[1], link[2]
        n = nodes[node_id]
        if node_id in seen:
            raise ValueError("Cyclic bypass path")
        if n.get("mode", 0) == 4:
            output_type = n["outputs"][slot]["type"]
            candidates = [item for item in n.get("inputs", []) if item.get("link") is not None
                          and item["type"] == output_type]
            if len(candidates) != 1:
                raise ValueError(f"Ambiguous bypass on {node_id}")
            return source(candidates[0]["link"], seen | {node_id})
        if n.get("mode", 0) == 2:
            raise ValueError(f"Connection from disabled node {node_id}")
        return [str(node_id), slot]

    graph = {}
    for n in workflow["nodes"]:
        kind = n["type"]
        if kind in ("MarkdownNote", "Note", MOTION) or n.get("mode", 0) in (2, 4):
            continue
        if kind not in schemas:
            raise ValueError(f"Local node not registered: {kind}")
        values = widgets(n)
        inputs = {key: value for key, value in values.items()
                  if key in input_schemas(schemas[kind], values)}
        for item in n.get("inputs", []):
            if item.get("link") is not None:
                inputs[item["name"]] = source(item["link"])
        graph[str(n["id"])] = {"class_type": kind, "inputs": inputs,
                               "_meta": {"title": n.get("title", kind)}}
    # Retain the current workflow's output nodes and their dependencies.
    retained = set()

    def visit(key):
        if key in retained:
            return
        if key not in graph:
            raise ValueError(f"Missing node referenced by the compiled graph: {key}")
        retained.add(key)
        for value in graph[key]["inputs"].values():
            if isinstance(value, list):
                visit(value[0])
    for key, node in graph.items():
        if (key in output_ids if output_ids is not None else schemas[node["class_type"]].get("output_node")):
            visit(key)
    graph = {key: node for key, node in graph.items() if key in retained}
    validate_graph(graph, schemas)
    return graph


def validate_graph(graph, schemas):
    for key, node in graph.items():
        inputs = node["inputs"]
        for name, (spec, category) in input_schemas(schemas[node["class_type"]], inputs).items():
            if name not in inputs:
                if category == "required":
                    raise ValueError(f"{key}: missing {name}")
                continue
            value = inputs[name]
            typ, opts = spec[0], spec[1] if len(spec) > 1 else {}
            if isinstance(value, list):
                if len(value) != 2 or value[0] not in graph:
                    raise ValueError(f"{key}.{name}: invalid link")
                outputs = schemas[graph[value[0]]["class_type"]]["output"]
                out_type = outputs[value[1]]
                if not isinstance(typ, list) and typ not in ("*", out_type) and out_type != "*":
                    raise ValueError(f"{key}.{name}: {out_type} is incompatible with {typ}")
                continue
            choices = typ if isinstance(typ, list) else (opts.get("options") if typ == "COMBO" else None)
            if choices is not None and value not in choices:
                raise ValueError(f"{key}.{name}: unsupported choice {value!r}")
            if typ == "COMFY_DYNAMICCOMBO_V3" and value not in [o["key"] for o in opts["options"]]:
                raise ValueError(f"{key}.{name}: invalid dynamic choice")
            if typ in ("INT", "FLOAT"):
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ValueError(f"{key}.{name}: expected number")
                if value < opts.get("min", -math.inf) or value > opts.get("max", math.inf):
                    raise ValueError(f"{key}.{name}: numeric value out of range")
            if typ == "BOOLEAN" and not isinstance(value, bool):
                raise ValueError(f"{key}.{name}: expected boolean")


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
    input_root = Path(input_root or task["settings"]["input_directory"])
    workflow = read_json(task["settings"]["workflow"])
    namespace = "h3-replace/" + hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    jobs = []
    for segment in task["segments"]:
        if not has_replacement(task, segment):
            continue
        paths = segment_paths(root, task, segment)
        prompt = paths["prompt"].read_text(encoding="utf-8")
        media = stage(root, task, segment, input_root, namespace)
        ui = configure(workflow, task, segment, prompt, media, namespace, schemas)
        easy = node_by_id(ui, task["settings"]["easy_node_id"])
        saver = named_node(ui, kind=SECOND_SAVE if widgets(easy).get("second_pass_enabled", False) else FIRST_SAVE)
        graph = compile_graph(ui, schemas, output_roots(ui, saver))
        base = root / "提交图" / segment["id"]
        write_json(base.with_suffix(".workflow.json"), ui)
        write_json(base.with_suffix(".api.json"), graph)
        digest_builder = hashlib.sha256(json.dumps(graph, sort_keys=True).encode())
        for asset in media:
            with (input_root / asset["subfolder"] / asset["filename"]).open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest_builder.update(block)
        digest = digest_builder.hexdigest()
        jobs.append({"id": segment["id"], "graph": graph, "workflow": ui,
                     "saver": str(saver["id"]), "digest": digest})
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


def output_video(history, saver):
    output = history.get("outputs", {}).get(saver, {})
    found = []
    for value in output.values():
        if isinstance(value, list):
            found.extend(item for item in value if isinstance(item, dict)
                         and str(item.get("filename", "")).lower().endswith((".mp4", ".mkv", ".webm")))
    if len(found) != 1:
        raise RuntimeError(f"Expected one video output from save node {saver}; found {found}")
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
            item = output_video(history, job["saver"])
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
