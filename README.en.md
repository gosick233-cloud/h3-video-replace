# H3 Local Video Character Replacement Skill

[中文](README.md)

A local workflow for an AI assistant: first-use setup, visual shot analysis,
frame-accurate splitting, complete per-clip six-section prompts, and approved
serial rendering through the user's ComfyUI. The assistant monitors execution
and downloads, diagnoses interruptions, and attempts recovery up to twice per
incident. Visual acceptance is left to the user.

This repository ships no author's workflow, model selections, LoRAs, private
paths, media, or fixed generation defaults.

## Requirements and installation

Python 3.10+, FFmpeg/FFprobe on PATH, and an existing working local ComfyUI with
the user's H3 models and custom nodes. Only Python's standard library is needed.
Windows users should use PowerShell 7. Install the repository as a skill folder
named `h3-video-replace`, or give its `SKILL.md` path to your assistant.

The current execution adapter supports an H3 v1.14 **UI workflow JSON** with one
`gosick_233_MiniMaxH3EasyV114` node. Node IDs may differ, and custom controls can
be explicitly bound. It does not claim to execute arbitrary model workflows or
API-format exports. Models and ComfyUI nodes are not bundled or installed.

## Two-stage first-use setup

1. **Ask first:** the user supplies their ComfyUI root and workflow file/path.
   Do not search their disk or reuse another workflow.
2. Inspect that supplied file and show the controls, current values and actual
   node/widget targets. This produces a report, not an active configuration.
3. **Ask again:** confirm the desired defaults for the controls present in that
   workflow, such as resolution, sampling steps, second-pass enable/scale/steps,
   and motion deblur. Resolve ambiguous custom controls before binding them.
4. Confirm the local service URL, real input directory, and whether reference
   clips should be resized to a chosen short edge or retain their source size.
5. Only after confirmation, save `.local/config.json`. Reinitialize when the
   workflow or environment changes. This configuration is ignored by Git.

The report derives proposed values from the supplied file. These are reviewed
with the user rather than silently becoming defaults. Models, LoRAs, seeds and
unmodified sampling configuration remain in the user's workflow.

## Each replacement job

Before task preparation, ask whether reference video clips should retain the
original audio. Then obtain the video, reference images, and replacement map.
The assistant inspects contact sheets and dense evidence, verifies cuts and
character identities, splits clips, and writes complete English six-section
prompts plus an editable Chinese analysis.

Prepare and show the assets and actual parameters, then ask for rendering
approval. Rendering is strictly serial, with hidden Windows subprocess windows.
Monitor the process, logs, queue/history and completed downloads until all clips
finish. Do not visually judge replacement quality or automatically merge clips.

For interruptions, investigate first. Reuse running prompt IDs and completed
jobs; retry downloads rather than rendering again when the output already
exists. After two unsuccessful recovery attempts for the same incident, stop
further submissions and report the error, attempted fixes and completed files.

## Commands

Run from the repository root; replace placeholders with the user's paths.

```text
python scripts/setup.py inspect --comfyui "<ComfyUI root>" --workflow "<workflow.json>" --out .local/inspection.json
# Ask the user about the controls; edit the report's profile to match their answers.
python scripts/setup.py save --from .local/inspection.json

python scripts/prepare.py init "<video.mp4>" "<new task directory>" --reference-audio keep
python scripts/forensic.py "<working video>" "<new evidence directory>" --fps 2
python scripts/prepare.py cuts "<task directory>"
# Inspect evidence and fill the shot plan and complete per-clip prompts.
python scripts/prepare.py split "<task directory>"
python scripts/prepare.py check "<task directory>"
python scripts/local_h3.py build "<task directory>"
# Only after explicit approval for this rendering job:
python scripts/batch_h3.py run "<task directory>"
```

Use `--reference-audio drop` when the user chooses to remove audio.
Non-native frame rates are normalized to H3's 24fps reference time base. Clips
are padded to its `17*k+5` frame grid and final visual results trimmed back to
the unpadded frame count. Retained reference audio is cut to the corresponding
interval, with silence in the padded tail. Audio is not listened to or analyzed.
The current `完成分段/` deliverables are visual clips; final soundtrack assembly
is a separate user request.

`build` only prepares assets and compiles/validates against live local node
schemas; it does not submit a generation request. The assistant, not the
forensics script, writes the actual shot analysis and full prompts.

## Development

The `scripts/` directory contains setup/configuration, visual forensics,
preparation, workflow compilation, serial execution and a background launcher.
`references/` holds the analysis/prompt rules and binding documentation.
`tests/` uses synthetic workflows and temporary FFmpeg media, with a simulated
backend; tests do not submit GPU work.

```text
python -m unittest discover -s tests -v
```

Local configuration, outputs, logs, media and weights are ignored by Git.
MIT license; visual-forensics guidance adapts
[LoveRain1997/video-to-h3-prompt](https://github.com/LoveRain1997/video-to-h3-prompt).
See `THIRD_PARTY_NOTICES.md` and retained license notices.
