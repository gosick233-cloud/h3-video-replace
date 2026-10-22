"""Synthetic workflow and schema: no real models, user paths, or private media."""
from copy import deepcopy

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from local_h3 import EASY, FIRST_SAVE, SECOND_SAVE


def fixture(second=True):
    nodes, links = [], []

    def node(ident, kind, values, outputs, title=None):
        n = {"id": ident, "type": kind, "title": title or kind, "mode": 0,
             "inputs": [{"name": k, "type": "*", "widget": {"name": k}, "link": None}
                        for k in values], "widgets_values": list(values.values()),
             "widgets_values_named": deepcopy(values),
             "outputs": [{"name": str(i), "type": typ, "links": []} for i, typ in enumerate(outputs)]}
        nodes.append(n)
        return n

    def connect(origin, slot, dest, field, typ):
        ident = len(links)+1
        entry = next((e for e in dest['inputs'] if e['name'] == field), None)
        if entry is None:
            entry = {"name": field, "type": typ}
            dest['inputs'].append(entry)
        entry['link'] = ident
        origin['outputs'][slot]['links'].append(ident)
        links.append([ident, origin['id'], slot, dest['id'], dest['inputs'].index(entry), typ])

    model = node(41, 'ExampleModel', {'model_name': 'example-user-model', 'lora': 'example-user-lora'}, ['MODEL'])
    easy = node(83, EASY, {'resolution': '720P', 'aspect_ratio': '4:3', 'width': 640, 'height': 480,
        'mode': 'ref2va', 'prompt': '', 'seconds': 1.0, 'advanced': True, 'fps': 24.0,
        'keyframe_role': 'first', 'reference_mention_mode': 'index', 'media_manifest': '[]',
        'continuation_config': '{}', 'ref_image_size': '短边最大2K像素', 'second_pass_enabled': second}, ['VIDEO'])
    connect(model, 0, easy, 'model', 'MODEL')
    first_steps = node(121, 'PrimitiveInt', {'value': 9}, ['INT'], 'First pass steps')
    second_steps = node(174, 'PrimitiveInt', {'value': 3}, ['INT'], 'Second pass steps')
    scale = node(215, 'PrimitiveFloat', {'value': 1.5}, ['FLOAT'], 'Upscale factor')
    first = node(260, 'ExampleSampler', {}, ['VIDEO'])
    connect(easy, 0, first, 'video', 'VIDEO')
    connect(first_steps, 0, first, 'steps', 'INT')
    second_node = node(333, 'ExampleUpscale', {}, ['VIDEO'])
    connect(first, 0, second_node, 'video', 'VIDEO')
    connect(second_steps, 0, second_node, 'steps', 'INT')
    connect(scale, 0, second_node, 'scale', 'FLOAT')
    first_save = node(487, FIRST_SAVE, {'filename_prefix': 'original', '开启二采': second}, ['VIDEO'])
    connect(first, 0, first_save, 'video', 'VIDEO')
    second_save = node(519, SECOND_SAVE, {'filename_prefix': 'original', '开启保存二采': second}, ['VIDEO'])
    connect(second_node, 0, second_save, 'video', 'VIDEO')
    unload = node(602, 'gosick_233_MiniMaxH3AutoUnload', {}, [])
    connect(second_save, 0, unload, 'video', 'VIDEO')
    schemas = {}
    for n in nodes:
        required = {}
        values = n.get('widgets_values_named', {})
        for entry in n['inputs']:
            key = entry['name']
            if key not in values:
                required[key] = [entry['type']]
                continue
            value = values[key]
            typ = 'BOOLEAN' if isinstance(value, bool) else 'INT' if isinstance(value, int) else 'FLOAT' if isinstance(value, float) else 'STRING'
            required[key] = [typ, {}]
        if n['type'] == EASY:
            required['aspect_ratio'] = [['4:3', '16:9', '9:16'], {}]
            required['resolution'] = [['720P', '640P', 'custom'], {}]
            required['ref_image_size'] = [['1k', '2k'], {}]
        schemas[n['type']] = {'input': {'required': required},
            'output': [o['type'] for o in n['outputs']],
            'output_node': n['type'] in [FIRST_SAVE, SECOND_SAVE, 'gosick_233_MiniMaxH3AutoUnload']}
    return {'nodes': nodes, 'links': links, 'extra': {}}, schemas
