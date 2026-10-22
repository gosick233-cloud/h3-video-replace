# 本地 H3 工作流初始化与字段绑定

先请使用者提供 ComfyUI 路径和他们要使用的 H3 工作流。读取提供的文件后，再展示可调项目、当前值及输入／输出候选，询问使用者的默认设置。工作流版本、节点名称和编号由该文件决定。

H3 通用的六段提示词、24fps 参考时间轴、`17*k+5` 帧网格和末帧补齐保持不变。这里初始化的是具体工作流如何接收这些任务内容，以及使用者要保存的生成参数。

## 检查，提问，再保存

在 Skill 根目录执行，替换为使用者给出的真实路径：

```powershell
python scripts/setup.py inspect --comfyui "<ComfyUI 根目录>" --workflow "<使用者提供的 H3 工作流.json>" --out .local/inspection.json
```

已知服务地址时可加 `--comfy-url <本机地址>`，从该服务读取实际节点定义，展示控件选项和范围。检查报告不会激活配置，也不会提交生成。

第二轮询问并确认：

- 要采用的分辨率、画幅、步数、二采开关／倍率及该工作流实际提供的其他参数。
- 逐段提示词、参考视频和人物参考图分别对应哪些实际输入字段。
- 时长／帧数、fps、需要随源片变化的宽高或画幅、输出前缀对应哪些字段。
- 哪些节点产生最终视频；哪些额外输出节点（例如生成后卸载）要保留。
- 本机服务 URL、实际 input 目录、参考视频是否缩放短边及对应像素。

`adjustable_controls` 列出实际字段和当前值；`suggested_roles` 是提示候选，不会自动成为绑定。`parameters` 和 `bindings` 由第二轮确认后填写。模型和 LoRA 及未调整参数保留原工作流的内容。

```powershell
# 按使用者确认结果编辑检查报告中的 profile，然后保存：
python scripts/setup.py save --from .local/inspection.json
```

默认写入 `.local/config.json`，不提交到仓库。旧版分享配置若尚未包含输入／输出映射，需要按上述步骤重新确认一次。

## UI 与 API 格式

接受使用者提供的 UI 工作流或 API 格式工作流。API 格式是本机 ComfyUI 的执行图，由实际节点的 `class_type` 和 `inputs` 组成，直接保留用户自己的节点实现及连线。

UI 工作流根据本机 `/object_info` 和序列化控件转成执行图，不查找某个指定 H3 节点。自定义 UI 字段无法唯一还原时，向使用者索取**同一 H3 工作流的 API 格式导出**，而不是替换为作者的工作流。可对照 [ComfyUI 官方执行图示例](https://github.com/Comfy-Org/ComfyUI/blob/master/script_examples/basic_api_example.py)。

`ui_widget_map` 可以按节点 ID 明确列出序列化 widgets_values 的字段顺序，`null` 表示跳过一个纯 UI 项。前端开关若会改动连线或整条分支，应先在使用者的 UI 中设置好，再导出对应 API 图；实际绑定以执行图中的输入字段为准。

## 参数与任务输入映射

每个绑定目标用 `node_id` 和 `input` 指向该用户执行图的实际字段。链接字段应绑定上游真正的数值来源；不要只改被连线覆盖的显示值。需要时可用 `path` 指向字典／数组中的子项。

以下只展示结构，节点 ID、字段和值都要换成使用者工作流里的实际内容：

```json
{
  "parameters": {
    "使用者确认的某项参数": {
      "value": "使用者确认的默认值",
      "targets": [{"node_id": "实际节点ID", "input": "实际字段名"}]
    }
  },
  "bindings": [
    {"source": "prompt", "targets": [{"node_id": "提示词节点ID", "input": "实际提示词字段"}]},
    {"source": "media", "encoding": "json", "targets": [{"node_id": "素材节点ID", "input": "实际素材清单字段"}]},
    {"source": "seconds", "targets": [{"node_id": "时长节点ID", "input": "实际时长字段"}]}
  ],
  "output_node_ids": ["最终视频节点ID"],
  "extra_output_node_ids": ["要保留的其他输出节点ID"]
}
```

`parameters` 修改已确认的默认值；`bindings` 把本段实际内容填进去。可使用的任务来源：

| source | 含义 |
|---|---|
| prompt | 本段完整六段英文提示词 |
| media | 本段参考图及视频的素材对象列表；可用 json 编码 |
| reference_video | 本段已补帧参考视频的 input 相对路径 |
| reference_images | 本段参考图相对路径列表；index 从 0 开始选择某张图 |
| seconds / frames | 补齐后的生成时长／帧数 |
| original_frames | 本段未补齐帧数 |
| fps | H3 参考时间轴帧率 |
| width / height / aspect_ratio | 本段源画面宽／高／约分后的画幅；按实际字段支持情况确认 |
| output_prefix | 本次任务和段号组成的独立输出前缀 |
| seed | 根据任务和段号得到的稳定种子；只有明确绑定时才修改原种子 |

有的 H3 工作流用一个素材清单字段，有的分开接收视频和图片；两种形式都按实际字段映射。`encoding: json` 将列表／对象编码成字符串；`path_format: absolute` 可为文件输入提供本机绝对路径，默认使用 input 相对路径。

`output_node_ids` 决定从哪些节点读取最终视频结果，`extra_output_node_ids` 保留原工作流里需要执行的其他输出；不假定某种一采／二采保存节点。多个最终视频无法唯一选择时，按使用者确认设置从零开始的 `video_output_index`，不随机取一条。

用户原工作流作为只读依据；每段实际提交图保存在 `提交图/NNN.api.json`，原文件快照在 `工作流快照.json`。工作流的节点缺失、素材入口不明确或实际接口不匹配时，报告具体节点并完成对应映射或补充导出后再生成。

## 准备与生成边界

`local_h3.py build` 复制本次素材到确认的 input 目录，并使用实时节点定义校验提交图，不提交 GPU 作业。`run` 才在明确批准后串行执行。每次只生成一段，下载及裁帧完成后再提交下一段。

模型、LoRA、注意力、分块及没有绑定的参数不改。`完成分段/` 保存裁掉补帧后的视觉片段，最终成片音轨复用由使用者另行决定。

## 中断续接

报错时记录并退出，由 Agent 按 SKILL.md 最多进行两次排查修复后的恢复。已提交任务复用 prompt_id；成功输出的下载中断时补下载；已完成段跳过。确认为生成失败后，保留错误并只处理该失败段的记录再重新提交。提交响应丢失时先查本次队列／历史，不重复提交。不清空其他任务或改写原工作流。
