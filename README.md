# H3 本地视频人物替换 Skill

[English](README.en.md)

输入原视频、人物参考图及“谁替换谁”，由支持 Skill 的 AI 助手完成初始化、视觉拉片、切段、逐段完整提示词，并在确认后通过本机 ComfyUI 串行生成。生成期间持续监控错误与下载，出现中断先排查修复，最多恢复重试两次；画面效果由使用者验收。

仓库包含整套本地流程，不附带作者的工作流、模型、LoRA、个人路径、固定生成参数或视频素材。首次使用的默认参数由使用者自己的工作流和答复确定。

## 运行要求

- Python 3.10 或更新版本；脚本只依赖标准库。
- PATH 中可运行 `ffmpeg`、`ffprobe`。
- 已安装、可运行的本机 ComfyUI，及使用者自行准备的 H3 模型和节点。
- 使用者自行提供可运行的 **H3 工作流**，不限定版本、节点名称或节点编号。支持 API 格式执行图和可按本机节点定义还原的 UI 工作流；UI 自定义控件存在歧义时，补充同一工作流的 API 导出。H3 的六段提示词、24fps 与 `17*k+5` 补帧规则继续保留。
- 能读取本地文件、查看抽帧、执行命令并保持运行监控的 AI 助手。Windows 使用 PowerShell 7（`pwsh`）。

这套脚本不会安装 ComfyUI、下载模型，或替使用者选择模型与 LoRA。先确保所提供的工作流本身可以正常生成。

## 安装与使用

把本仓库放到 AI 助手的 Skill 目录，并将文件夹命名为 `h3-video-replace`。例如 Codex 的个人 Skill 目录为 `~/.codex/skills/`；也可以在对话中直接提供本仓库的 `SKILL.md` 路径。

调用示例：

> 使用 $h3-video-replace，帮我用两张参考图替换视频中指定的两个人。先进行首次初始化，素材和提示词准备好后再问我是否生成。

### 第一次使用：先问路径，再问参数

1. 助手先请使用者提供 **ComfyUI 根目录**和**工作流文件／路径**，等待答复。
2. 只读取提供的文件，列出里面实际存在的可调参数、当前值和对应节点，不主动查找其他工作流。
3. 助手根据检查表，询问默认分辨率、一采步数、二采开关／倍率／步数、减模糊等，并确认提示词、参考视频／图片、时长及最终视频输出的实际节点／字段映射。工作流没有的项目不编造；不明确的绑定先确认。
4. 确认本机服务 URL、实际 input 目录及参考视频是否缩放、缩放到多少像素。
5. 使用者确认后，保存本机 `.local/config.json`。下次复用，换工作流或环境时重新初始化。

没有预置作者的生成参数。报告中的数值来自刚刚提供的工作流，仍需经过第二轮询问与确认。

### 每次替换任务

先确认参考视频保留原音频还是去掉音频，然后开始建档和拉片。助手查看抽帧、确认角色映射与切镜，切出逐段源素材，写完可直接使用的六段英文提示词。中文分析保存在任务目录。

准备包完成后暂停，展示目录及实际参数，询问是否生成。批准后后台逐段生成和下载；助手持续检查脚本、日志、任务历史和文件完整性，不做画面验收，也不自动拼接。

生成或下载中断时，先定位原因再修复。复用已提交任务和已完成结果，下载失败优先补下载。同一中断恢复重试两次后仍失败，停止并报告。

## 手动命令入口

以下命令在仓库根目录运行；尖括号替换为使用者实际路径。`inspect` 后须由使用者确认参数，再编辑本地报告中的 `profile`。

```powershell
python scripts/setup.py inspect --comfyui "<ComfyUI 根目录>" --workflow "<工作流.json>" --out .local/inspection.json
# 展示可调项目并询问使用者，按确认结果编辑 inspection.json 中的 profile。
python scripts/setup.py save --from .local/inspection.json

# 确认原音频选择后建档；需要去掉时将 keep 改成 drop。
python scripts/prepare.py init "<视频.mp4>" "<新任务目录>" --reference-audio keep
python scripts/forensic.py "<任务使用的工作视频>" "<新取证目录>" --fps 2
python scripts/prepare.py cuts "<任务目录>"
# 助手实际看帧，填写任务清单及完整逐段提示词。
python scripts/prepare.py split "<任务目录>"
python scripts/prepare.py check "<任务目录>"
python scripts/local_h3.py build "<任务目录>"
# 仅在使用者明确批准本次生成后执行：
python scripts/batch_h3.py run "<任务目录>"
```

`forensic.py` 生成的是视觉证据，最终分镜和提示词需要助手实际看帧编写，脚本不会从一句简介自动扩写。保留音轨不代表会分析其内容，也不代表生成模型能逐样本复刻原声音。`完成分段/` 当前保存裁帧后的视觉片段，最终成片音轨另按使用者要求处理。

详细说明：[SKILL.md](SKILL.md)、[初始化与节点绑定](references/local-setup.md)、[视觉拉片](references/visual-analysis.md)、[任务清单](references/task-manifest.md)、[六段提示词](references/prompt-rules.md)。

## 仓库结构与开发

```text
SKILL.md                  助手执行流程及两次恢复上限
agents/openai.yaml        Skill 调用元数据
scripts/setup.py          两轮初始化的检查／保存命令
scripts/workflow_config.py 真实控件、输入／输出绑定和本地配置
scripts/workflow_graph.py  UI／API 执行图转换及节点定义校验
scripts/forensic.py       抽帧与网格
scripts/prepare.py        建档、切镜候选、按帧切段、提示词检查
scripts/local_h3.py       工作流编译、素材准备、串行执行及下载
scripts/batch_h3.py       隐藏窗口的后台启动器
scripts/common.py         媒资、帧网格和视频处理
references/               拉片、提示词和初始化规则
tests/                    人工构造的工作流、离线后端和 FFmpeg 行为测试
```

执行 `python -m unittest discover -s tests -v`。测试会用 FFmpeg 生成临时素材，模拟本机后端，不提交真实 GPU 任务。覆盖初始化、不同 H3 节点实现／字段／编号、API／UI 输入映射、音频选择、帧数、单／双采、串行执行及下载中断恢复。

`.local/`、`outputs/`、日志、视频和模型被 Git 忽略。不要把生成的本机配置或个人工作流复制进源码目录。

MIT 许可。视觉取证方法参考 [LoveRain1997/video-to-h3-prompt](https://github.com/LoveRain1997/video-to-h3-prompt)；说明及保留授权见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
