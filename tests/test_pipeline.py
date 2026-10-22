import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import common
import prepare
import local_h3
import workflow_config as config
import forensic
import batch_h3
from fixtures import fixture, reviewed_profile
from workflow_graph import execution_graph, validate_graph


def prompt():
    return '''subject_definitions:
<Subject 1> is the reference person in <Picture 1>, wearing the reference outfit.
<Video 1> is the source segment.

summary:
[video editing + reference generation] Replace the specified source person with <Subject 1>.

retention_analysis:
<Subject 1>: fully_preserved - reference identity and outfit.
<Video 1>: partially_preserved - source timing and action.

detailed_description:
[Shot 1] The replacement person already occupies the source position and performs the source action.

overall_soundscape:
N/A

non_diegetic_music:
N/A
'''


class FakeComfy:
    def __init__(self, schemas, root, task):
        self.schemas, self.root, self.task = schemas, root, task
        self.posted = []
        self.jobs = {}
        self.fail_download = False

    def request(self, route, data=None):
        if route == '/object_info':
            return self.schemas
        if route == '/queue':
            return {'queue_running': [], 'queue_pending': []}
        if route == '/prompt':
            if self.posted:
                previous = self.jobs[self.posted[-1]]['segment']
                assert common.segment_paths(self.root, self.task, previous)['result'].exists()
            graph = data['prompt']
            saver = self.task['settings']['output_node_ids'][0]
            node = graph[saver]
            number = node['inputs']['filename_prefix'].split('/')[-1]
            segment = next(s for s in self.task['segments'] if s['id'] == number)
            ident = f'job-{len(self.posted)}'
            self.jobs[ident] = {'segment': segment, 'saver': saver}
            self.posted.append(ident)
            return {'prompt_id': ident, 'node_errors': {}}
        if route.startswith('/history/'):
            ident = route.rsplit('/', 1)[1]
            job = self.jobs[ident]
            return {ident: {'status': {'completed': True}, 'outputs': {
                job['saver']: {'images': [{'filename': job['segment']['id']+'.mp4', 'type': 'output'}]}}}}
        raise AssertionError(route)

    def download(self, item, destination):
        if self.fail_download:
            raise ConnectionError('simulated download interruption')
        segment = next(s for s in self.task['segments'] if s['id'] == Path(item['filename']).stem)
        shutil.copy2(common.segment_paths(self.root, self.task, segment)['video'], destination)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='h3-public-')
        self.base = Path(self.temp.name)
        self.comfy = self.base / 'ComfyUI'
        (self.comfy / 'input').mkdir(parents=True)
        self.workflow, self.schemas = fixture()
        self.workflow_file = self.base / 'workflow.json'
        common.write_json(self.workflow_file, self.workflow)
        self.report = config.inspect_workflow(self.comfy, self.workflow_file, 'http://127.0.0.1:8199', self.schemas)
        self.profile = reviewed_profile(self.report)
        self.profile_path = self.base / 'profile.json'
        common.write_json(self.profile_path, self.profile)

    def tearDown(self):
        self.temp.cleanup()

    def source(self, fps=24, audio=False):
        source = self.base / f'source-{fps}-{audio}.mp4'
        args = ['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color=c=blue:s=64x48:r={fps}:d=2']
        if audio:
            args += ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2', '-c:a', 'aac']
        common.command([*args, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', source])
        return source

    def task(self, audio=False, keep=False, fps=24):
        source = self.source(fps=fps, audio=audio)
        root = self.base / '任务 带空格'
        prepare.initialize(source, root, self.profile_path, keep)
        task = common.read_json(root / '任务清单.json')
        picture = root / 'reference.png'
        picture.write_bytes(b'fixture image; only staged, not sent to a model')
        task['characters'] = {'person': {'reference_image': str(picture)}}
        task['segments'] = [
            {'id': '001', 'start_frame': 0, 'end_frame': 24, 'cast': ['person'], 'synopsis': 'first'},
            {'id': '002', 'start_frame': 24, 'end_frame': 48, 'cast': ['person'], 'synopsis': 'second'},
        ]
        common.write_json(root / '任务清单.json', task)
        prepare.split(root, task)
        for segment in task['segments']:
            common.segment_paths(root, task, segment)['prompt'].write_text(prompt(), encoding='utf-8')
        return root, task

    def test_first_use_does_not_search_for_a_workflow(self):
        with patch.object(config, 'DEFAULT_PROFILE', self.base / 'absent.json'):
            with self.assertRaisesRegex(ValueError, 'First use'):
                config.load_profile()

    def test_inspect_lists_actual_controls_without_activating_defaults(self):
        self.assertEqual(self.report['profile']['parameters'], {})
        self.assertEqual(self.report['profile']['bindings'], [])
        row = next(c for c in self.report['adjustable_controls'] if c['node_id'] == '121')
        self.assertEqual(row['value'], 9)
        self.assertIsNone(self.profile['reference_short_edge'])

    def test_confirmed_input_bindings_ignore_titles_and_node_family(self):
        self.profile['parameters']['first_steps']['value'] = 11
        self.workflow['nodes'][2]['title'] = 'My sampling control'
        common.write_json(self.workflow_file, self.workflow)
        config.validate_profile(self.profile, self.schemas)
        graph, _ = config.load_execution(self.profile, self.schemas)
        changed = config.apply_parameters(graph, self.profile)
        self.assertEqual(changed['121']['inputs']['value'], 11)

    def test_setup_cli_api_inspect_then_confirm_and_save(self):
        api_path = self.base / 'user_api.json'
        common.write_json(api_path, execution_graph(self.workflow, self.schemas))
        inspection, saved = self.base / 'inspection.json', self.base / 'saved.json'
        common.command([sys.executable, ROOT/'scripts/setup.py', 'inspect', '--comfyui', self.comfy,
                        '--workflow', api_path, '--out', inspection])
        self.assertFalse(saved.exists())
        report = common.read_json(inspection)
        self.assertIsNone(report['profile']['comfy_url'])
        report['profile'] = reviewed_profile(report)
        report['profile']['comfy_url'] = 'http://localhost:8199'
        report['profile']['parameters']['second_steps']['value'] = 7
        common.write_json(inspection, report)
        common.command([sys.executable, ROOT/'scripts/setup.py', 'save', '--from', inspection, '--out', saved])
        self.assertEqual(config.load_profile(saved)['parameters']['second_steps']['value'], 7)

    def test_binding_linked_input_requires_the_actual_upstream_control(self):
        self.profile['parameters']['first_steps']['targets'] = [{'node_id': '260', 'input': 'steps'}]
        with self.assertRaisesRegex(ValueError, 'upstream'):
            config.validate_profile(self.profile, self.schemas)

    def test_frame_padding_and_configurable_reference_size(self):
        self.profile['reference_short_edge'] = 96
        common.write_json(self.profile_path, self.profile)
        root, task = self.task()
        paths = common.segment_paths(root, task, task['segments'][0])
        self.assertEqual(common.probe(paths['source_clip'])['frame_count'], 24)
        info = common.probe(paths['video'])
        self.assertEqual(info['frame_count'], 39)
        self.assertEqual((info['width'], info['height']), (128, 96))

    def test_audio_keep_through_normalization_and_cut(self):
        root, task = self.task(audio=True, keep=True, fps=30)
        paths = common.segment_paths(root, task, task['segments'][1])
        streams = json.loads(common.command(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
                                             '-show_entries', 'stream=duration', '-of', 'json', paths['video']]))['streams']
        self.assertTrue(streams)
        self.assertAlmostEqual(float(streams[0]['duration']), 39/24, delta=.03)

    def test_audio_drop(self):
        root, task = self.task(audio=True, keep=False)
        paths = common.segment_paths(root, task, task['segments'][0])
        streams = json.loads(common.command(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
                                             '-show_entries', 'stream=index', '-of', 'json', paths['video']]))['streams']
        self.assertEqual(streams, [])

    def test_prompt_reference_mismatch_rejected(self):
        root, task = self.task()
        with self.assertRaisesRegex(ValueError, 'Picture'):
            prepare.check_prompt(prompt().replace('<Picture 1>', '<Picture 2>'), task, task['segments'][0])

    def test_two_pass_build_serial_run_and_resume(self):
        root, task = self.task()
        client = FakeComfy(self.schemas, root, task)
        before = self.workflow_file.read_bytes()
        jobs = local_h3.build(root, task, client)
        self.assertEqual(client.posted, [])
        graph = jobs[0]['graph']
        self.assertEqual(graph['121']['inputs']['value'], 9)
        self.assertEqual(graph['83']['inputs']['ref_image_size'], '2k')
        self.assertEqual(graph['41']['inputs']['model_name'], 'example-user-model')
        self.assertEqual(jobs[0]['savers'], ['519'])
        local_h3.run(root, task, client, jobs)
        local_h3.run(root, task, client, jobs)
        self.assertEqual(len(client.posted), 2)
        self.assertEqual(self.workflow_file.read_bytes(), before)
        self.assertTrue(all(common.probe(common.segment_paths(root, task, s)['result'])['frame_count'] == 24
                            for s in task['segments']))

    def test_single_pass_only_and_original_unload(self):
        self.workflow, self.schemas = fixture(second=False)
        common.write_json(self.workflow_file, self.workflow)
        self.profile = reviewed_profile(self.report, second=False)
        common.write_json(self.profile_path, self.profile)
        root, task = self.task()
        client = FakeComfy(self.schemas, root, task)
        jobs = local_h3.build(root, task, client)
        self.assertEqual(jobs[0]['savers'], ['487'])
        self.assertNotIn('333', jobs[0]['graph'])
        self.assertEqual(jobs[0]['graph']['602']['inputs']['video'], ['487', 0])
        local_h3.run(root, task, client, jobs)
        self.assertEqual(len(client.posted), 2)

    def test_other_h3_api_node_family_ids_and_input_names(self):
        graph = execution_graph(self.workflow, self.schemas)
        renames = {'83': 'conditioning-other', '519': 'final-video-other'}
        graph = {renames.get(k, k): n for k, n in graph.items()}
        for node in graph.values():
            for key, value in node['inputs'].items():
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                    value[0] = renames.get(value[0], value[0])
        old_kind = graph['conditioning-other']['class_type']
        new_kind = 'AnotherH3ConditioningImplementation'
        graph['conditioning-other']['class_type'] = new_kind
        self.schemas[new_kind] = self.schemas.pop(old_kind)
        fields = {'prompt': 'positive_caption', 'media_manifest': 'reference_items_json', 'seconds': 'duration_s'}
        inputs = graph['conditioning-other']['inputs']
        specs = self.schemas[new_kind]['input']['required']
        for old, new in fields.items():
            inputs[new] = inputs.pop(old)
            specs[new] = specs.pop(old)
        for entry in list(self.profile['parameters'].values()) + self.profile['bindings']:
            for target in entry['targets']:
                if target['node_id'] == '83':
                    target['input'] = fields.get(target['input'], target['input'])
                target['node_id'] = renames.get(target['node_id'], target['node_id'])
        self.profile['output_node_ids'] = ['final-video-other']
        common.write_json(self.workflow_file, graph)
        common.write_json(self.profile_path, self.profile)
        root, task = self.task()
        client = FakeComfy(self.schemas, root, task)
        jobs = local_h3.build(root, task, client)
        configured = jobs[0]['graph']['conditioning-other']['inputs']
        self.assertEqual(configured['positive_caption'], prompt())
        self.assertEqual(len(json.loads(configured['reference_items_json'])), 2)
        self.assertEqual(configured['duration_s'], 39/24)
        local_h3.run(root, task, client, jobs)
        self.assertEqual(len(client.posted), 2)

    def test_separate_video_and_image_input_bindings(self):
        graph = execution_graph(self.workflow, self.schemas)
        inputs = graph['83']['inputs']
        inputs.pop('media_manifest')
        inputs.update(reference_video_file='', character_image_file='')
        specs = self.schemas[graph['83']['class_type']]['input']['required']
        specs.pop('media_manifest')
        specs.update(reference_video_file=['STRING', {}], character_image_file=['STRING', {}])
        self.profile['bindings'] = [b for b in self.profile['bindings'] if b['source'] != 'media']
        self.profile['bindings'] += [
            {'source': 'reference_video', 'path_format': 'absolute',
             'targets': [{'node_id': '83', 'input': 'reference_video_file'}]},
            {'source': 'reference_images', 'index': 0,
             'targets': [{'node_id': '83', 'input': 'character_image_file'}]},
        ]
        common.write_json(self.workflow_file, graph)
        common.write_json(self.profile_path, self.profile)
        root, task = self.task()
        client = FakeComfy(self.schemas, root, task)
        jobs = local_h3.build(root, task, client)
        actual = jobs[0]['graph']['83']['inputs']
        self.assertTrue(Path(actual['reference_video_file']).is_file())
        self.assertTrue(actual['character_image_file'].endswith('/picture_1.png'))

    def test_ui_schema_can_resolve_standard_unnamed_widgets(self):
        for node in self.workflow['nodes']:
            node.pop('widgets_values_named', None)
            for entry in node.get('inputs', []):
                entry.pop('widget', None)
        common.write_json(self.workflow_file, self.workflow)
        root, task = self.task()
        jobs = local_h3.build(root, task, FakeComfy(self.schemas, root, task))
        self.assertEqual(jobs[0]['graph']['121']['inputs']['value'], 9)

    def test_download_interruption_resumes_same_prompt(self):
        root, task = self.task()
        client = FakeComfy(self.schemas, root, task)
        jobs = local_h3.build(root, task, client)
        client.fail_download = True
        with self.assertRaisesRegex(ConnectionError, 'interruption'):
            local_h3.run(root, task, client, jobs)
        self.assertEqual(len(client.posted), 1)
        client.fail_download = False
        local_h3.run(root, task, client, jobs)
        self.assertEqual(len(client.posted), 2)

    def test_visual_forensic_frames_and_grid(self):
        video = self.source()
        output = self.base / 'evidence'
        forensic.extract(video, output, fps=3)
        self.assertEqual(len(list((output/'frames').glob('*.jpg'))), 6)
        self.assertTrue(list(output.glob('grid_*.jpg')))

    def test_background_child_survives_and_keeps_logs(self):
        child = self.base/'child.py'
        child.write_text("import time\nfrom pathlib import Path\nprint('running',flush=True)\ntime.sleep(1)\nPath('done').write_text('ok')\n")
        process = batch_h3.start_background([sys.executable, '-u', str(child)], self.base)
        self.assertEqual(process.wait(timeout=10), 0)
        self.assertTrue((self.base/'done').exists())
        self.assertIn('running', (self.base/'批量生成.log').read_text())


if __name__ == '__main__':
    unittest.main(verbosity=2)
