# MiniMax H3 Reference to Video

**节点类名 / 注册名**：`MiniMaxH3BatchRefToVideo`  
**显示名称**：`MiniMax H3 Batch Ref to Video (T8)`  
**分类**：`model/conditioning/minimax`

本节点使用本仓库内移植的 T8 MiniMax H3 `conditioning.py`、`core.py`、
`prompt_tags.py` 和 `sampling.py` 实现。适配层保留原有 ComfyUI 节点注册方式、
`vae` 输入名称、前三个输出端口顺序，并将 `ref_images` 和 `ref_audios` 批次
拆成 T8 按序处理的参考项。

## 输入

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

## 输出

| 输出序号 | 名称 | 类型 | 说明 |
| --- | --- | --- | --- |
| 1 | `positive` | CONDITIONING | T8 条件编码和参考媒体元数据 |
| 2 | `latent` | LATENT | MiniMax H3 嵌套 AV latent |
| 3 | `processed_prompt` | STRING | 标签校验、规范化后的 prompt |
| 4 | `mux_audio` | AUDIO | `final_audio`，或未连接时的 `drive_audio` |
| 5 | `media_map_json` | STRING | 标签序号到输入媒体的映射 |
| 6 | `report` | STRING | 任务、时长、画布和校验报告 |

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
