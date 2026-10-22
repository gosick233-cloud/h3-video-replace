"""Synthetic workflow and schema: no real models, user paths, or private media."""
from copy import deepcopy

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
EASY = "ExampleH3Inputs"
FIRST_SAVE = "ExampleFirstVideoSave"
SECOND_SAVE = "ExampleSecondVideoSave"


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
    unload = node(602, 'ExampleAfterGeneration', {}, [])
    connect(second_save if second else first_save, 0, unload, 'video', 'VIDEO')
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
            'output_node': n['type'] in [FIRST_SAVE, SECOND_SAVE, 'ExampleAfterGeneration']}
    return {'nodes': nodes, 'links': links, 'extra': {}}, schemas


def reviewed_profile(report, second=True):
    """Simulated second-round user answers, not shipped production defaults."""
    profile = deepcopy(report['profile'])
    def param(value, ident, name):
        return {'value': value, 'targets': [{'node_id': str(ident), 'input': name}]}
    profile['parameters'] = {
        'resolution': param('720P', 83, 'resolution'),
        'first_steps': param(9, 121, 'value'),
        'second_steps': param(3, 174, 'value'),
        'upscale': param(1.5, 215, 'value'),
        'reference_image_size': param('2k', 83, 'ref_image_size'),
    }
    def binding(source, ident, name, **extra):
        return {'source': source, 'targets': [{'node_id': str(ident), 'input': name}], **extra}
    profile['bindings'] = [
        binding('prompt', 83, 'prompt'), binding('media', 83, 'media_manifest', encoding='json'),
        binding('seconds', 83, 'seconds'), binding('fps', 83, 'fps'),
        {'source': 'output_prefix', 'targets': [
            {'node_id': '487', 'input': 'filename_prefix'}, {'node_id': '519', 'input': 'filename_prefix'}]},
    ]
    profile['output_node_ids'] = ['519' if second else '487']
    profile['extra_output_node_ids'] = ['602']
    return profile
