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
from fixtures import fixture


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
            kind = local_h3.SECOND_SAVE if any(n['class_type'] == local_h3.SECOND_SAVE for n in graph.values()) else local_h3.FIRST_SAVE
            saver, node = next((k, n) for k, n in graph.items() if n['class_type'] == kind)
            number = node['inputs']['filename_prefix'].split('/')[-2]
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
        self.report = config.inspect_workflow(self.comfy, self.workflow_file, 'http://127.0.0.1:8199')
        self.profile = self.report['profile']
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

    def test_inspect_reads_supplied_values_without_activating(self):
        self.assertEqual(self.profile['parameters']['first_steps']['value'], 9)
        self.assertEqual(self.profile['parameters']['upscale']['value'], 1.5)
        self.assertEqual(self.profile['parameters']['first_resolution']['value'], '720P')
        self.assertEqual(self.profile['easy_node_id'], '83')
        self.assertIsNone(self.profile['reference_short_edge'])

    def test_reviewed_values_and_manual_control_binding(self):
        self.profile['parameters']['first_steps']['value'] = 11
        self.workflow['nodes'][2]['title'] = 'My sampling control'
        common.write_json(self.workflow_file, self.workflow)
        report = config.inspect_workflow(self.comfy, self.workflow_file)
        self.assertNotIn('first_steps', report['profile']['parameters'])
        self.assertTrue(any(c['node_id'] == '121' for c in report['adjustable_controls']))
        config.validate_profile(self.profile)
        changed = config.apply_parameters(copy.deepcopy(self.workflow), config.task_settings(self.profile))
        self.assertEqual(config.widgets(config.node_by_id(changed, 121))['value'], 11)

    def test_setup_cli_inspect_then_save(self):
        inspection, saved = self.base / 'inspection.json', self.base / 'saved.json'
        common.command([sys.executable, ROOT/'scripts/setup.py', 'inspect', '--comfyui', self.comfy,
                        '--workflow', self.workflow_file, '--out', inspection])
        self.assertFalse(saved.exists())
        report = common.read_json(inspection)
        self.assertIsNone(report['profile']['comfy_url'])
        report['profile']['comfy_url'] = 'http://localhost:8199'
        report['profile']['parameters']['second_steps']['value'] = 7
        common.write_json(inspection, report)
        common.command([sys.executable, ROOT/'scripts/setup.py', 'save', '--from', inspection, '--out', saved])
        self.assertEqual(config.load_profile(saved)['parameters']['second_steps']['value'], 7)

    def test_linked_parameter_uses_upstream_value(self):
        steps = config.node_by_id(self.workflow, 121)
        upstream = copy.deepcopy(steps)
        upstream['id'] = 1001
        upstream['title'] = 'Custom source value'
        upstream['widgets_values'] = [12]
        upstream['widgets_values_named'] = {'value': 12}
        steps['inputs'][0]['link'] = 999
        self.workflow['nodes'].append(upstream)
        self.workflow['links'].append([999, 1001, 0, 121, 0, 'INT'])
        common.write_json(self.workflow_file, self.workflow)
        report = config.inspect_workflow(self.comfy, self.workflow_file)
        parameter = report['profile']['parameters']['first_steps']
        self.assertEqual(parameter['value'], 12)
        self.assertEqual(parameter['targets'], [{'node_id': '1001', 'widget': 'value'}])
        with self.assertRaisesRegex(ValueError, 'upstream'):
            config.validate_profile(self.profile)

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
        self.assertEqual(jobs[0]['saver'], '519')
        local_h3.run(root, task, client, jobs)
        local_h3.run(root, task, client, jobs)
        self.assertEqual(len(client.posted), 2)
        self.assertEqual(self.workflow_file.read_bytes(), before)
        self.assertTrue(all(common.probe(common.segment_paths(root, task, s)['result'])['frame_count'] == 24
                            for s in task['segments']))

    def test_single_pass_only_and_original_unload(self):
        self.profile['parameters']['second_pass_enabled']['value'] = False
        common.write_json(self.profile_path, self.profile)
        root, task = self.task()
        client = FakeComfy(self.schemas, root, task)
        jobs = local_h3.build(root, task, client)
        self.assertEqual(jobs[0]['saver'], '487')
        self.assertNotIn('333', jobs[0]['graph'])
        self.assertEqual(jobs[0]['graph']['602']['inputs']['video'], ['487', 0])
        local_h3.run(root, task, client, jobs)
        self.assertEqual(len(client.posted), 2)

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
