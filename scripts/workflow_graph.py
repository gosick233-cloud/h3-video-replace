"""ComfyUI execution graphs, independent of model and custom-node names."""
import copy


def is_link(value, graph):
    return (isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)
            and value[0] in graph and type(value[1]) is int)


def api_graph(document):
    graph = document.get("prompt", document)
    if not isinstance(graph, dict) or not graph or not all(
            isinstance(n, dict) and "class_type" in n and isinstance(n.get("inputs"), dict)
            for n in graph.values()):
        raise ValueError("Not a ComfyUI API execution graph")
    return copy.deepcopy({str(key): value for key, value in graph.items()})


def input_schemas(schema, values):
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
    add(schema.get("input", {}))
    return result


def widget_values(node, schema=None, explicit_names=None):
    raw = node.get("widgets_values") or []
    named = dict(node.get("widgets_values_named") or {})
    if isinstance(raw, dict):
        return {**raw, **named}
    names = explicit_names
    if names is None:
        names = [e.get("widget", {}).get("name", e["name"])
                 for e in node.get("inputs", []) if e.get("widget")]
    if names:
        if len(raw) > len(names) and not named:
            raise ValueError(f"Node {node['id']}: confirm ui_widget_map or supply its API export; widget serialization is ambiguous")
        return {**{name: value for name, value in zip(names, raw) if name is not None}, **named}
    if named or not raw:
        return named
    if schema is None:
        raise ValueError(f"Node {node['id']}: node schema or explicit ui_widget_map required")
    result, index = {}, 0
    for name, (spec, _) in input_schemas(schema, {}).items():
        typ = spec[0]
        options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
        scalar = isinstance(typ, list) or typ in ("INT", "FLOAT", "BOOLEAN", "STRING", "COMBO")
        if not scalar or options.get("forceInput"):
            continue
        if index >= len(raw):
            break
        result[name] = raw[index]
        index += 1
        if name in ("seed", "noise_seed") and index < len(raw) and raw[index] in (
                "fixed", "increment", "decrement", "randomize"):
            index += 1
    if index != len(raw):
        raise ValueError(f"Node {node['id']}: confirm ui_widget_map or supply the API export for custom widgets")
    return result


def convert_ui(document, schemas=None, widget_map=None, output_ids=None):
    schemas, widget_map = schemas or {}, widget_map or {}
    nodes = {str(n["id"]): n for n in document["nodes"]}
    if not isinstance(document.get("links", []), list):
        raise ValueError("This UI link serialization needs the user's API export")
    links = {link[0]: link for link in document.get("links", [])}
    graph, visiting = {}, set()
    roots = output_ids or [ident for ident, n in nodes.items()
                           if schemas.get(n["type"], {}).get("output_node") and n.get("mode", 0) == 0]

    def source(link_id, seen=None):
        seen = set() if seen is None else seen
        link = links[link_id]
        ident, slot = str(link[1]), link[2]
        if ident in seen:
            raise ValueError("Cyclic UI bypass")
        node = nodes[ident]
        if node.get("mode", 0) == 2:
            raise ValueError(f"Node {ident} is disabled; inspect the actual workflow")
        if node.get("mode", 0) == 4:
            typ = node["outputs"][slot]["type"]
            candidates = [e for e in node.get("inputs", []) if e.get("link") is not None and e.get("type") == typ]
            if len(candidates) != 1:
                raise ValueError(f"Ambiguous bypass on node {ident}; supply the API export")
            return source(candidates[0]["link"], seen | {ident})
        if node["type"].startswith("Primitive") and node["type"] not in schemas:
            values = widget_values(node, explicit_names=widget_map.get(ident))
            if len(values) != 1:
                raise ValueError(f"Confirm the serialized primitive value for node {ident}")
            return next(iter(values.values()))
        visit(ident)
        return [ident, slot]

    def visit(ident):
        ident = str(ident)
        if ident in graph:
            return
        if ident in visiting:
            raise ValueError("Cyclic execution graph")
        visiting.add(ident)
        node = nodes[ident]
        kind = node["type"]
        if kind not in schemas:
            raise ValueError(f"Node {ident} ({kind}) needs its local schema or an API-format export")
        values = widget_values(node, schemas[kind], widget_map.get(ident))
        accepted = input_schemas(schemas[kind], values)
        inputs = {key: value for key, value in values.items() if key in accepted}
        for entry in node.get("inputs", []):
            if entry.get("link") is not None:
                inputs[entry["name"]] = source(entry["link"])
        graph[ident] = {"class_type": kind, "inputs": inputs,
                        "_meta": {"title": node.get("title", kind)}}
        visiting.remove(ident)

    if not roots:
        raise ValueError("Confirm the workflow's execution output nodes or supply its API export")
    for ident in roots:
        visit(str(ident))
    return graph


def execution_graph(document, schemas=None, widget_map=None, output_ids=None):
    if isinstance(document.get("nodes"), list):
        return convert_ui(document, schemas, widget_map, output_ids)
    graph = api_graph(document)
    return prune_graph(graph, output_ids) if output_ids else graph


def prune_graph(graph, output_ids):
    result, visiting = {}, set()

    def visit(ident):
        ident = str(ident)
        if ident in result:
            return
        if ident in visiting:
            raise ValueError("Cyclic execution graph")
        if ident not in graph:
            raise ValueError(f"Missing execution node {ident}")
        visiting.add(ident)
        for value in graph[ident]["inputs"].values():
            if is_link(value, graph):
                visit(value[0])
        result[ident] = copy.deepcopy(graph[ident])
        visiting.remove(ident)

    for ident in output_ids:
        visit(ident)
    return result


def validate_graph(graph, schemas):
    for ident, node in graph.items():
        kind = node["class_type"]
        if kind not in schemas:
            raise ValueError(f"Node {ident}: local node type is not installed: {kind}")
        inputs = node["inputs"]
        for name, (spec, category) in input_schemas(schemas[kind], inputs).items():
            if name not in inputs:
                if category == "required":
                    raise ValueError(f"{ident}: missing required input {name}")
                continue
            value, typ = inputs[name], spec[0]
            options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if is_link(value, graph):
                upstream = graph[value[0]]["class_type"]
                outputs = schemas[upstream].get("output", [])
                if value[1] < 0 or value[1] >= len(outputs):
                    raise ValueError(f"{ident}.{name}: invalid output slot")
                actual = outputs[value[1]]
                if not isinstance(typ, list) and typ not in ("*", actual) and actual != "*":
                    raise ValueError(f"{ident}.{name}: incompatible input link")
            elif isinstance(typ, list) and value not in typ:
                raise ValueError(f"{ident}.{name}: select a value supported by the actual node")
            elif typ in ("INT", "FLOAT"):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"{ident}.{name}: expected number")
                if not options.get("min", float('-inf')) <= value <= options.get("max", float('inf')):
                    raise ValueError(f"{ident}.{name}: numeric value out of range")
            elif typ == "BOOLEAN" and not isinstance(value, bool):
                raise ValueError(f"{ident}.{name}: expected boolean")
