# MiniMax H3 Reference to Video

**节点类名 / 注册名**：`MiniMaxH3BatchRefToVideo`  
**显示名称**：`MiniMax H3 Batch Ref to Video (T8)`  
**分类**：`model/conditioning/minimax`

本节点使用本仓库内移植的 T8 MiniMax H3 `conditioning.py`、`core.py`、
`prompt_tags.py` 和 `sampling.py` 实现。适配层保留原有 ComfyUI 节点注册方式、
`vae` 输入名称、前三个输出端口顺序，并将 `ref_images` 和 `ref_audios` 批次
拆成 T8 按序处理的参考项。

## 智能媒体过滤和重新索引

本节点现已支持**自动媒体过滤和重新索引**功能。

### 工作原理

当 prompt 中只使用了部分参考媒体时（例如只用 `<picture 1>` 和 `<picture 3>` 而跳过 `<picture 2>`），节点会：

1. **自动检测** prompt 中实际使用的所有 `<picture N>`、`<image N>`、`<audio N>` 标签序号
2. **过滤** 出只有被 prompt 使用的媒体，丢弃未使用的媒体
3. **重新编号** 保留的媒体使其形成连续序列（1、2、3...）
4. **重写** prompt 中的标签序号以匹配新的编号
5. **同步更新** letterbox 元数据（如果已连接）

### 示例

**输入状态**：
- `ref_images` batch 包含 3 张图片：image_1、image_2、image_3
- `ref_audios` batch 包含 3 个音频：audio_1、audio_2、audio_3
- `prompt = "A character based on <picture 1>, with details from <picture 3>, speaking with <audio 2>"`

**处理流程**：
1. 检测 prompt 使用：pictures {1, 3}、audios {2}
2. 过滤媒体：
   - 图片：保留 image_1 和 image_3，丢弃 image_2
   - 音频：保留 audio_2，丢弃 audio_1 和 audio_3
3. 重新编号：
   - 图片重映射：1 → 1、3 → 2
   - 音频重映射：2 → 1
4. 重写 prompt：`"A character based on <picture 1>, with details from <picture 2>, speaking with <audio 1>"`
5. 传给 MiniMax H3：2 张图片、1 个音频

**好处**：
- 节省计算资源（不处理未使用的媒体）
- 避免序号混乱（prompt 标签总是与实际媒体匹配）
- 灵活的工作流（可随意跳过某些参考而无需手动重排）

### 特殊情况处理

**Letterbox 元数据**：
- 如果连接了 `ref_images_metadata`（来自 Director Table），节点会自动更新其中的 `items` 索引
- 确保元数据中的裁剪坐标仍然对应正确的图片
- 若元数据更新失败，将输出警告但继续处理

**音频序号调整**：
- 如果 `prompt_primary_audio_ordinal` 设置的序号大于过滤后的音频总数，会被自动重置为 0（禁用源音频映射）
- 这避免了标签解析时的序号越界错误

**无使用媒体**：
- 如果 prompt 中没有使用任何 `<picture>`、`<audio>`、`<video>` 标签，则所有批次媒体都会被过滤掉
- 节点将仅使用 `first_frame` 和 `last_frame` 作为条件

必需输入包括 `clip`、`vae`（视频 VAE）、`audio_vae`、`prompt`、
`width`、`height` 和 `length`。宽高须为 32 的倍数；帧数会按 T8 规则处理到
MiniMax H3 的 `17n+5` 网格。

| 输入 | 类型 | 说明 |
| --- | --- | --- |
| `ref_images` | IMAGE | 兼容的批次对照输入 `[B,H,W,C]`；每张图均作为参考图，顺序对应 `<Picture 1>`、`<Picture 2>`……，最多 9 张 |
| `ref_images_metadata` | STRING | 可选，连接 `Director Table` 的 `image_metadata` 输出；与 letterbox 图片 batch 配合，还原每张图的原始画面区域 |
| `ref_audios` | AUDIO | 兼容的批次对照输入 `[B,C,L]`；每条音频均作为独立参考音频，最多 3 条 |
| `ref_video_1`–`ref_video_3` | IMAGE | 每个输入为 `[T,H,W,C]` 视频帧序列 |
| `ref_video_audio_1`–`ref_video_audio_3` | AUDIO | 对应序号参考视频的伴音；音频参考顺序按 T8 规则先伴音、后独立音频 |
| `drive_audio` | AUDIO | 源音频，供 `audio_mode` 使用；非 `native` 模式需要连接 |
| `final_audio` | AUDIO | 可选传递到 `mux_audio`，未连接时输出 `drive_audio` |
| `first_frame` / `last_frame` | IMAGE | 可选首帧/尾帧条件；可与参考媒体组成 Hybrid 条件 |
| `model` | MODEL | 可选模型输入；连接后在续写路径中返回已打补丁的 H3 长视频模型 |
| `context` | H3_T8_CONTEXT | 可选上一段视频的续写上下文；连接后启用长视频续写路径 |
| `segment_index` | INT | 当前片段编号，从 `0` 开始；首段必须为 `0` |
| `context_frames` | INT | 续写时使用的尾部视频上下文帧数，可选 `0/5/22/39`；首段会自动强制为 `0` |
| `context_audio` | 选项 | `video_and_audio` 同时使用视频和音频尾部；`video_only` 只使用视频尾部 |
| `first_frame_reuse` | 选项 | `segment0_only` 仅首段使用首帧；`persistent_identity_reference` 将首帧作为续写中的身份参考 |
| `continuity_reference_image` | IMAGE | 可选持续性参考图，用于补强续写片段的外观连续性 |
| `persistent_identity_strategy` | 选项 | `single_reference` 使用单一身份参考；`scene_plus_identity` 同时强化场景和人物身份 |
| `persistent_identity_interval` | INT | 持续身份参考的应用间隔，默认 `1` |

### 长视频续写

当 `context` 端口连接有效的 H3 T8 上下文后，`MiniMaxH3BatchRefToVideo` 会切换到
长视频续写路径。两类输入承担不同职责，续写时建议同时连接：

- `context` 保留上一段的运动状态、镜头位置、人物空间关系、视频尾部 latent，以及可用的音频尾部信息。
- `ref_images` 和可选的 `ref_images_metadata` 保留人物外观、服装、场景细节和整体色调。
- `first_frame`、`continuity_reference_image` 可进一步约束首帧或跨片段身份一致性。

因此，连接 `context` 不会自动忽略 `ref_images`。如果续写中出现新场景或新角色，仍然可以
替换或追加新的 `ref_images`；旧片段的运动/空间连续性由 `context` 提供，新的角色和场景
外观由参考图提供。

首段设置 `segment_index=0`，并使用 `context_frames=0`。首段没有上一段尾部，节点会自动
按此规则处理；后续片段将 `segment_index` 递增，并从 Segment Planner 或工作流循环中传入
对应的 `context_frames`。不要把后续片段的帧数配置到首段。

`ref_images_metadata` 只有在 `ref_images` 已连接时才有效。使用 Director Table 的
`letterbox` batch 时，应把其 `image_metadata` 一起接入；没有参考图时请不要单独连接元数据。

### 上下文保存、导入与循环

节点 `MiniMax H3 Save Continuation Context (T8)` 接收采样后的 `av_latent`，输出：

- `context_path`：保存到磁盘的上下文文件路径，可用于跨工作流或下次启动后恢复。
- `report`：校验和、来源片段、帧窗口等信息。
- `context`：已验证的 H3_T8_CONTEXT，可直接连接到下一次 `MiniMaxH3BatchRefToVideo`。

节点 `MiniMax H3 Load Previous Context (T8)` 可以从 `context_path` 导入已保存文件；不填写
路径时则按 `chain_id` 和 `segment_index` 从默认上下文状态读取。首段没有上下文时，应使用
空的 context，并将片段编号设为 `0`；后续片段必须连接 `Context Load` 的 `context` 输出，
或直接连接上一轮 `Context Save` 的 `context` 输出。

在 ComfyUI 的循环/For 工作流中，推荐把每轮的 `Context Save.context` 回接到下一轮的
`MiniMaxH3BatchRefToVideo.context`，同时把 `segment_index` 递增。这样不需要手动复制文件；
若需要中断后继续，再用 `context_path` 连接 `Context Load` 恢复。

旧版独立的 `MiniMaxH3LongVideoPlanner` 和 `MiniMaxH3LongVideoRefToVideo` 已移除，续写
配置统一放在 `MiniMaxH3BatchRefToVideo`，上下文仅由 `Context Load/Save` 节点负责导入、导出。

### 主要选项

- `task_type`：`auto`、`T2VA`、`I2VA`、`FL2VA`、`L2VA`、`Ref2VA` 或 `Hybrid`。
- `audio_mode`：`native` 默认不锁定源音频；`reference_only`、`lock_source`、
  `remix_source` 需要连接 `drive_audio`。锁定/混音行为与 T8 相同。
- `audio_denoise_strength`：`remix_source` 的源音频去噪强度。
- `add_source_as_reference`：是否将源音频也作为 prompt/模型参考。
- `prompt_primary_audio_ordinal` 和 `strict_prompt_tags`：控制音频标签映射和标签校验。
- `ref_image_size`：`match` 按生成画面面积适配；`max` 按 2048px 短边适配。
- `reference_video_policy`：`official_2_to_15s` 要求输入参考视频为 48–360 帧；
  `model_minimum` 仅要求至少 5 帧。两者均按 T8 规则截断并对齐。

标签使用 `<picture N>` / `<image N>`、`<audio N>`、`<video N>`，大小写不敏感。
`<picture 0>` / `<audio 0>` 作为第一项兼容写法；严格模式会报告无对应输入的显式标签。

### `ref_images` 批次注意事项

ComfyUI 内置 `ImageBatch` 要求 batch 内图片具有相同高宽；尺寸不同时，它会在输出
`IMAGE` batch 前先把后续图片缩放到第一张图片的尺寸（使用 bilinear/center）。
本插件的 `Director Table (Card Manager)` 已改为生成兼容 `ref_images` 的 `[B,H,W,C]`
batch，且提供 `batch_resize_mode` 参数。要在 MiniMax 节点恢复原始画面区域，必须选择
`letterbox` 并同时连接其 `image_metadata` 输出到 `ref_images_metadata`：

- `center_crop`（默认）：按比例填满共同画布并从中心裁剪，不产生黑边、不变形，但可能裁掉边缘内容。
- `stretch`：缩放到共同画布的宽高，完整保留画面但可能变形。
- `letterbox`：保持原尺寸居中补黑边，不裁切、不变形。

`letterbox` 会在共同 batch 画布中保留原图像素并输出每张图的原始宽高、偏移位置；MiniMax
根据元数据裁掉补边，再分别按 T8 的规则 resize 和 VAE encode。`center_crop` 和 `stretch`
会丢失边缘像素或改变图像比例，元数据无法逆转这些变换，MiniMax 会拒绝用它们恢复。
Director 的 `image_1`、`image_2` 等独立输出仍供其他工作流使用；此节点只使用
`ref_images` 批次及可选的 `ref_images_metadata`。
若同一批图片也接入 `AutoAnimeStoryboardPipeline`，将 `images` 接到该节点的 `images`，
并将 `image_metadata` 同时接到它的 `ref_images_metadata`；流水线会在上传图片前按元数据
裁掉 letterbox 填充，向视觉模型提供恢复后的原始画面。

## 输出

| 输出序号 | 名称 | 类型 | 说明 |
| --- | --- | --- | --- |
| 1 | `positive` | CONDITIONING | T8 条件编码和参考媒体元数据 |
| 2 | `latent` | LATENT | MiniMax H3 嵌套 AV latent |
| 3 | `processed_prompt` | STRING | 标签校验、规范化后的 prompt |
| 4 | `mux_audio` | AUDIO | `final_audio`，或未连接时的 `drive_audio` |
| 5 | `media_map_json` | STRING | 标签序号到输入媒体的映射 |
| 6 | `report` | STRING | 任务、时长、画布和校验报告 |
| 7 | `model` | MODEL | 可选的 H3 长视频模型输出；续写时连接到采样节点使用 |

前三个输出的位置保持旧节点的顺序，以免已有工作流断线；新增的 T8 输出追加在后。

## 采样

将 `model` 和 `latent` 接入 `MiniMax H3 Sampling Setup (T8)`。默认设置为
`dual_clock_euler` / `native_flow`、4 steps、video shift 12、audio shift 3；
此节点直接调用移植的 T8 sampler。其输出 `model`、`sampler`、`sigmas` 可接入
`BasicGuider` 和 `SamplerCustomAdvanced`，与 T8 工作流使用相同采样路径。

## 示例

将两张图组成 `ref_images` 批次时，可使用：

```text
A character based on <picture 1>, with details from <picture 2>
```

连接参考视频伴音、再连接一条 `ref_audios` 时，音频序号依次对应视频伴音和
独立参考音频。混合参考时，节点会按 T8 规则组织 tokenizer 项与 latent blocks，
而不是按标签在 prompt 中出现的先后顺序重排媒体。

仅复制 T8 conditioning 与采样路径不能保证不同模型权重、prompt、seed 或输入图像
下的视频完全一致；需要用相同模型和工作流实测画质。
