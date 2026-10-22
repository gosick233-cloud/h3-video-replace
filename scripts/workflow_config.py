"""Inspect an explicitly supplied workflow and store reviewed local defaults."""
from __future__ import annotations

import copy
from pathlib import Path
import re

from common import read_json

EASY = "gosick_233_MiniMaxH3EasyV114"
MOTION = "gosick_233_H3MotionBlurSwitchV114"
DEFAULT_PROFILE = Path(__file__).resolve().parents[1] / ".local" / "config.json"


def widgets(node):
    names = [entry.get("widget", {}).get("name", entry["name"])
             for entry in node.get("inputs", []) if entry.get("widget")]
    values = dict(zip(names, node.get("widgets_values") or []))
    values.update(node.get("widgets_values_named") or {})
    return values


def set_widget(node, name, value):
    names = [entry.get("widget", {}).get("name", entry["name"])
             for entry in node.get("inputs", []) if entry.get("widget")]
    node.setdefault("widgets_values_named", {})[name] = value
    if name in names:
        values = node.setdefault("widgets_values", [])
        while len(values) <= names.index(name):
            values.append(None)
        values[names.index(name)] = value


def node_by_id(workflow, ident):
    matches = [node for node in workflow["nodes"] if str(node["id"]) == str(ident)]
    if len(matches) != 1:
        raise ValueError(f"Workflow node {ident} is missing or ambiguous")
    return matches[0]


def target(node, widget):
    return {"node_id": str(node["id"]), "widget": widget}


def effective_control(workflow, node, name, seen=None):
    """A linked widget is controlled by its upstream value, not its UI label."""
    seen = set() if seen is None else seen
    if str(node["id"]) in seen:
        raise ValueError("Cyclic parameter binding")
    entry = next((e for e in node.get("inputs", []) if e["name"] == name), {})
    link_id = entry.get("link")
    if link_id is None:
        return node, name
    link = next(link for link in workflow["links"] if link[0] == link_id)
    upstream = node_by_id(workflow, link[1])
    names = list(widgets(upstream))
    if len(names) != 1:
        return None
    return effective_control(workflow, upstream, names[0], seen | {str(node["id"])})


def inspect_workflow(comfyui_root, workflow_path, comfy_url=None):
    root, path = Path(comfyui_root).resolve(), Path(workflow_path).resolve()
    if not root.is_dir():
        raise ValueError("Provide the user's existing ComfyUI root directory")
    workflow = read_json(path)
    if not isinstance(workflow.get("nodes"), list):
        raise ValueError("Provide an H3 v1.14 UI workflow JSON, not an API-format export")
    easy = [n for n in workflow["nodes"] if n["type"] == EASY]
    if len(easy) != 1:
        raise ValueError("This adapter requires one H3 v1.14 Easy node; inspect/adapt other node families first")
    easy = easy[0]
    parameters = {}
    candidates = []
    # List actual controls so an agent can ask the user, including custom graphs.
    control_words = re.compile(r"step|sigma|resolution|width|height|scale|strength|attention|tile|blur|步|分辨率|倍率|模糊|分块|稀疏", re.I)
    for node in workflow["nodes"]:
        for name, value in widgets(node).items():
            if isinstance(value, (int, float, bool)) or control_words.search(name + " " + node.get("title", "")):
                candidates.append({**target(node, name), "title": node.get("title", node["type"]),
                                   "node_type": node["type"], "value": value})

    def add(key, node, name):
        resolved = effective_control(workflow, node, name)
        if resolved is None:
            return
        node, name = resolved
        values = widgets(node)
        if name in values:
            parameters[key] = {"value": values[name], "targets": [target(node, name)]}

    for name in ["resolution", "width", "height", "second_pass_enabled"]:
        add("first_resolution" if name == "resolution" else name, easy, name)
    # Labels are suggestions only. Unrecognized controls remain in candidates
    # for explicit selection; no node IDs or sampling values are assumed.
    labels = {"first_steps": {"一采步数", "first steps", "first pass steps"},
              "second_steps": {"二采步数", "second steps", "second pass steps"},
              "upscale": {"二采倍率", "upscale", "upscale factor", "second pass scale"}}
    for key, names in labels.items():
        matches = [n for n in workflow["nodes"] if n.get("title", "").strip().lower() in names]
        if len(matches) == 1:
            add(key, matches[0], "value")
    motions = [n for n in workflow["nodes"] if n["type"] == MOTION]
    if len(motions) == 1:
        for key, name in [("first_motion_deblur", "一采高动态减少模糊"),
                          ("second_motion_deblur", "二采高动态减少模糊")]:
            add(key, motions[0], name)
    return {
        "profile": {"schema_version": 1, "comfyui_root": str(root), "workflow": str(path),
                    "comfy_url": comfy_url, "input_directory": str(root / "input"),
                    "easy_node_id": str(easy["id"]), "reference_short_edge": None, "parameters": parameters},
        "adjustable_controls": candidates,
        "review": "Ask the user which defaults to use before saving. Unrecognized controls require explicit node/widget targets.",
    }


def validate_profile(profile):
    from local_h3 import LocalComfy
    LocalComfy(profile["comfy_url"])
    if not Path(profile["comfyui_root"]).is_dir():
        raise ValueError("ComfyUI root does not exist")
    if not Path(profile["input_directory"]).is_dir():
        raise ValueError("Confirm the actual input directory before saving")
    workflow = read_json(profile["workflow"])
    easy = node_by_id(workflow, profile["easy_node_id"])
    if easy["type"] != EASY:
        raise ValueError("The selected node is not an H3 v1.14 Easy node")
    edge = profile["reference_short_edge"]
    if edge is not None and (type(edge) is not int or edge < 2 or edge % 2):
        raise ValueError("Reference short edge must be an even positive pixel count, or null to retain source size")
    seen = set()
    for name, parameter in profile["parameters"].items():
        if not parameter.get("targets"):
            raise ValueError(f"{name}: choose at least one actual workflow control")
        for binding in parameter["targets"]:
            node = node_by_id(workflow, binding["node_id"])
            if binding["widget"] not in widgets(node):
                raise ValueError(f"{name}: widget is absent from the supplied workflow")
            entry = next((e for e in node.get("inputs", []) if e["name"] == binding["widget"]), {})
            if entry.get("link") is not None:
                raise ValueError(f"{name}: bind the upstream source; this widget is overridden by a link")
            key = (str(node["id"]), binding["widget"])
            if key in seen:
                raise ValueError(f"{name}: the same widget is bound twice")
            seen.add(key)
    if "first_resolution" not in profile["parameters"]:
        raise ValueError("Confirm the first-pass resolution control")
    return profile


def load_profile(path=None):
    path = Path(path) if path else DEFAULT_PROFILE
    if not path.is_file():
        raise ValueError("First use: ask for ComfyUI path and workflow, inspect controls, ask for defaults, then run setup.py save")
    return validate_profile(read_json(path))


def task_settings(profile):
    settings = copy.deepcopy(profile)
    for name, parameter in profile["parameters"].items():
        settings[name] = parameter["value"]
    return settings


def apply_parameters(workflow, settings):
    for name, parameter in settings["parameters"].items():
        value = settings.get(name, parameter["value"])
        for binding in parameter["targets"]:
            set_widget(node_by_id(workflow, binding["node_id"]), binding["widget"], value)
    return workflow
