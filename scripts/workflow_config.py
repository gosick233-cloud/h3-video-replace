"""Inspect user-supplied H3 workflows; bind actual API inputs, not versioned nodes."""
from __future__ import annotations

import copy
from pathlib import Path
import re

from common import read_json
from workflow_graph import api_graph, execution_graph, input_schemas, is_link, widget_values

DEFAULT_PROFILE = Path(__file__).resolve().parents[1] / ".local" / "config.json"
SOURCES = {"prompt", "media", "reference_video", "reference_images", "seconds", "frames",
           "original_frames", "fps", "width", "height", "aspect_ratio", "output_prefix", "seed"}


def document_format(document):
    return "ui" if isinstance(document.get("nodes"), list) else "api"


def load_execution(profile, schemas=None):
    document = read_json(profile["workflow"])
    roots = [str(n) for n in profile["output_node_ids"] + profile.get("extra_output_node_ids", [])]
    if document_format(document) == "api":
        return api_graph(document), document
    controls = list(profile["parameters"].values()) + profile["bindings"]
    roots += [str(t["node_id"]) for entry in controls for t in entry["targets"]]
    return execution_graph(document, schemas, profile.get("ui_widget_map"), list(dict.fromkeys(roots))), document


def inspect_workflow(comfyui_root, workflow_path, comfy_url=None, schemas=None):
    root, path = Path(comfyui_root).resolve(), Path(workflow_path).resolve()
    if not root.is_dir():
        raise ValueError("Provide the user's existing ComfyUI root directory")
    document = read_json(path)
    kind = document_format(document)
    controls, unresolved, outputs = [], [], []
    if kind == "api":
        graph = api_graph(document)
        consumed = {v[0] for n in graph.values() for v in n["inputs"].values() if is_link(v, graph)}
        for ident, node in graph.items():
            schema = (schemas or {}).get(node["class_type"], {})
            if schema.get("output_node") or ident not in consumed:
                outputs.append({"node_id": ident, "node_type": node["class_type"],
                                "title": node.get("_meta", {}).get("title", node["class_type"])})
            specs = input_schemas(schema, node["inputs"])
            for name, value in node["inputs"].items():
                if not is_link(value, graph):
                    controls.append(control_row(ident, node["class_type"], node.get("_meta", {}).get("title"), name, value, specs))
    else:
        for node in document["nodes"]:
            ident = str(node["id"])
            schema = (schemas or {}).get(node["type"], {})
            try:
                values = widget_values(node, schema or None)
            except ValueError as exc:
                unresolved.append({"node_id": ident, "node_type": node["type"], "reason": str(exc),
                                   "widgets_values": node.get("widgets_values")})
                continue
            specs = input_schemas(schema, values)
            for name, value in values.items():
                linked = next((e.get("link") for e in node.get("inputs", []) if e["name"] == name), None)
                if linked is None:
                    controls.append(control_row(ident, node["type"], node.get("title"), name, value, specs))
            if schema.get("output_node") or not any(o.get("links") for o in node.get("outputs", [])):
                outputs.append({"node_id": ident, "node_type": node["type"], "title": node.get("title", node["type"])})
    return {"profile": {
        "schema_version": 2, "comfyui_root": str(root), "workflow": str(path), "comfy_url": comfy_url,
        "input_directory": str(root / "input"), "reference_short_edge": None,
        "parameters": {}, "bindings": [], "output_node_ids": [], "extra_output_node_ids": [],
        "video_output_index": None, "ui_widget_map": {}},
        "workflow_format": kind, "adjustable_controls": controls,
        "output_candidates": outputs, "unresolved_ui_widgets": unresolved,
        "review": "Ask the user to choose defaults, runtime bindings and final video outputs; no H3 workflow version is required."}


def control_row(ident, kind, title, name, value, specs):
    row = {"node_id": str(ident), "input": name, "node_type": kind, "title": title or kind, "value": value}
    if name in specs:
        spec = specs[name][0]
        if isinstance(spec[0], list):
            row["choices"] = spec[0]
        elif len(spec) > 1 and isinstance(spec[1], dict):
            row["options"] = {k: v for k, v in spec[1].items() if k in ("min", "max", "step")}
    matches = []
    for role, pattern in [("prompt", r"prompt|positive|text|提示"), ("media", r"manifest|assets"),
                          ("reference_video", r"video|视频"), ("reference_images", r"image|图片|参考图"),
                          ("seconds", r"seconds|duration|时长"), ("frames", r"frames|length|帧数"),
                          ("fps", r"fps|frame_rate|帧率"), ("output_prefix", r"filename_prefix|output_prefix")]:
        if re.search(pattern, name, re.I):
            matches.append(role)
    row["suggested_roles"] = matches
    return row


def check_target(graph, target):
    ident, name = str(target["node_id"]), target["input"]
    if ident not in graph or name not in graph[ident]["inputs"]:
        raise ValueError(f"Mapping target {ident}.{name} is absent from the supplied execution graph")
    if is_link(graph[ident]["inputs"][name], graph):
        raise ValueError(f"{ident}.{name}: select the upstream control, not a linked input")
    value = graph[ident]["inputs"][name]
    for key in target.get("path", []):
        value = value[key]
    return ident, name


def set_target(graph, target, value):
    ident, name = check_target(graph, target)
    path = target.get("path", [])
    if not path:
        graph[ident]["inputs"][name] = copy.deepcopy(value)
    else:
        container = graph[ident]["inputs"][name]
        for key in path[:-1]:
            container = container[key]
        container[path[-1]] = copy.deepcopy(value)


def validate_profile(profile, schemas=None):
    from local_h3 import LocalComfy
    if profile.get("schema_version") != 2:
        raise ValueError("Reinitialize this profile: supply the user's H3 workflow and confirm its input/output bindings")
    LocalComfy(profile["comfy_url"])
    if not Path(profile["comfyui_root"]).is_dir() or not Path(profile["input_directory"]).is_dir():
        raise ValueError("Confirm the existing ComfyUI root and actual input directory")
    edge = profile["reference_short_edge"]
    if edge is not None and (type(edge) is not int or edge < 2 or edge % 2):
        raise ValueError("Reference short edge must be an even pixel count or null")
    if not profile["output_node_ids"]:
        raise ValueError("Ask the user which nodes produce the final video")
    if document_format(read_json(profile["workflow"])) == "ui" and schemas is None:
        schemas = LocalComfy(profile["comfy_url"]).request("/object_info")
    graph, _ = load_execution(profile, schemas)
    for ident in profile["output_node_ids"] + profile.get("extra_output_node_ids", []):
        if str(ident) not in graph:
            raise ValueError(f"Output node {ident} is absent from the supplied workflow")
    seen = set()
    for entry in list(profile["parameters"].values()) + profile["bindings"]:
        if not entry.get("targets"):
            raise ValueError("Choose actual input targets for each parameter/binding")
        for target in entry["targets"]:
            ident, name = check_target(graph, target)
            key = (ident, name, tuple(target.get("path", [])))
            if key in seen:
                raise ValueError(f"Duplicate parameter/runtime binding for {ident}.{name}")
            seen.add(key)
    if not profile["bindings"]:
        raise ValueError("Confirm how this H3 workflow receives the per-clip prompt and reference assets")
    for entry in profile["bindings"]:
        if entry["source"] not in SOURCES:
            raise ValueError("Unknown runtime source: " + entry["source"])
        if entry.get("encoding", "value") not in ("value", "json"):
            raise ValueError("Use value or json encoding")
        if entry.get("path_format", "relative") not in ("relative", "absolute"):
            raise ValueError("Use relative or absolute file paths")
        if entry.get("path_format") == "absolute" and entry["source"] not in ("reference_video", "reference_images"):
            raise ValueError("Absolute paths apply to reference video/image inputs")
        if "index" in entry and (type(entry["index"]) is not int or entry["index"] < 0):
            raise ValueError("Reference image index must be a nonnegative integer")
    return profile


def load_profile(path=None):
    path = Path(path) if path else DEFAULT_PROFILE
    if not path.is_file():
        raise ValueError("First use: ask for ComfyUI path and H3 workflow, inspect controls, then confirm defaults and bindings")
    profile = read_json(path)
    if profile.get("schema_version") != 2:
        raise ValueError("Reinitialize the H3 workflow bindings with setup.py")
    return profile


def task_settings(profile):
    return copy.deepcopy(profile)


def apply_parameters(graph, settings):
    for entry in settings["parameters"].values():
        for target in entry["targets"]:
            set_target(graph, target, entry["value"])
    return graph
