# 逐段完整六段提示词

每个需要替换的片段写一份独立英文 TXT，UTF-8 无 BOM，无解释和 Markdown 围栏。六个字段只出现一次且顺序固定。人物、Picture 顺序来自该段清单。时间轴来自拉片证据，不能只读一句简介就臆造分镜。

## subject_definitions:

每个可见人物一行 `<Subject N> is ...`。

替换人物的定义引用对应 `<Picture N>` 一次，具体写出参考图可辨认的脸、发型、体型、完整服装、鞋与可见配饰；参考图看不见的部分不编造。Subject 定义的是换上去的人，不是原演员套新脸。保持原视频渲染风格，身份图是照片也不自动把动画变成实拍。

保留人物从 `<Video 1>` 定义，说明其身份和服装保持原样。保留者不使用虚构 Picture。

用 `<Video 1> is the source video for the target video edit.` 明确源视频的编辑角色，再说明它提供本段动作、位置、镜头、时间、场景、灯光、道具和剪辑结构。身份图不单独写 `<Picture N> is ...`，不写成首帧、构图或背景参考。

## summary:

用 `[video editing + reference generation] The target video is an edited version of <Video 1>.` 开头。用源人物在本镜中的可见外观、位置或动作明确指认替换对象，再直接写明由哪个 Subject 整体替换。脸、头发、身体、手及整套服装都来自对应参考人物，从首帧到末帧保持替换；目标人物占据原人物的位置并执行原动作。明确哪些 Subject 原样保留。简述该段动作。

## retention_analysis:

逐 Subject 使用 `fully_preserved`：替换人物保留参考图身份与服装，保留人物沿用原身份；动作、姿态、表情变化按源片执行。

`<Video 1>: partially_preserved`：明确指定源人物的原脸、头发、身体、手及服装由参考人物的对应外观替换；保留动作、镜头、位置、时间、场景、光线、道具和剪辑。不要笼统写“去掉所有原人物或服装”，以免改掉用户要保留的人。

身份图不单独做 retention 行。通常不使用 `attribute_transfer`，因为 Subject 已经是参考图的人。去水印、删字等只按本次用户要求写，不带入旧项目的水印名称；普通场景文字也不自动删除。

## detailed_description:

开头简洁说明这是指定人物已被替换后的源片镜头，沿用源片风格、场景和光线。每个生成段只包含一个连续镜头。开场动作直接描述参考人物已经处于原人物的位置与姿态；按段内播放顺序写出景别、人物位置、动作、朝向、表情、光线变化和本镜头内的运镜。

- 每段只写一个 `[Shot 1]`，描述本段开场及这个连续镜头的动作；段内动作变化使用时间点或区间。遇到真实切镜时，先把素材拆成独立生成段，为新段另写一份从 `[Shot 1]` 开始的完整提示词，不在同一段续写 `[Shot 2]` 等镜头。
- 不改变谁先动、谁受力、道具归属、方向、动作次数、镜头路径或情绪因果；不合并关键动作，也不自行加走路、回头、开枪等动作。
- 完整外貌只在 Subject 定义写全一次。动作段点出这一镜实际可见且有助锁定身份的特征；脸部近景写脸和头发，手部特写写自己的手和袖子，背面写发型及背部服装。
- 遮脸、背面、模糊后景和后半段也维持同一身份，按需要补写当时可见特征；不把整套外貌贴进每一拍，避免盖过动作。
- 局部手臂按画面证据归属行动者；不能因为只看见一只手就新增人物，也不能把无法归属的手强行写成主角自己的。
- 未替换人物保持原身份、服装和动作。两人左右互换或进出画面时写清实际变化，不能固定成永久左右位置。
- 源视频已提供环境，不逐件罗列家具重搭房间。只在人物坐椅子、拿桌上道具等互动必要时提及相应物体。
- 时间从本段 00:00 开始，不超过原始段时长。同一长镜头拆成多段时，承接前段的连续动作状态；真实切镜后的新段按源片新机位与构图开场。

## overall_soundscape:

根据使用者本次要求填写；没有指定声音设计时写 `N/A`，不编造源片声音。保留参考视频音轨与提示词声音设计分别处理，不试听或分析音轨。

## non_diegetic_music:

根据使用者本次要求填写；没有指定配乐时写 `N/A`。源音轨是否用于最终成片，由使用者另行决定。

## 写法示例（须替换为本次真实内容）

```text
subject_definitions:
<Subject 1> is the replacement person in <Picture 1>, preserving [observed facial identity, hairstyle, body proportions, outfit, footwear and visible accessories].
<Subject 2> is the unchanged [source person identifiable from the video] in <Video 1>, retaining their original identity and outfit.
<Video 1> is the source video for the target video edit.

summary:
[video editing + reference generation] The target video is an edited version of <Video 1>. Replace [the source person identified by visible appearance, position or action] with <Subject 1>. Replace the source person's face, hair, body, hands and entire outfit with the reference person's appearance completely, from the first frame through the last frame. <Subject 1> takes the source person's exact position and performs the source actions. Keep <Subject 2> unchanged. Preserve [the actual action and camera progression].

retention_analysis:
<Subject 1>: fully_preserved - Preserve the reference person's identity and outfit while following the source actions and expressions.
<Subject 2>: fully_preserved - Preserve the original person's identity, outfit and performance.
<Video 1>: partially_preserved - Replace the specified source person's original face, hair, body, hands and outfit with the reference person's appearance. Retain the source actions, camera, timing, scene and lighting.

detailed_description:
This is the source shot with [the identifiable source person] visibly replaced by <Subject 1>. Maintain the source video's visual style, environment and lighting.
[Shot 1] <Subject 1> is already in [the source opening position and pose], with [the visible reference appearance]. [Write the complete timed action and camera movement.]

overall_soundscape:
N/A

non_diegetic_music:
N/A
```

括号内是编写说明，不是正式文件内容。Agent 必须查看素材后填成可以直接生成的正文。正式提交文件不引用另一个文件来补外貌，不写“同上”，也不依赖其他段的提示词。
