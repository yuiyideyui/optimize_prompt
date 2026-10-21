# MiniMax H3 Context Conditioning Adapter

**节点类名**: `MiniMaxH3ContextConditioningAdapter`  
**注册名称**: `MiniMaxH3ContextConditioningAdapter`  
**分类**: `conditioning/minimax`

## 功能说明

该节点用于在 MiniMax H3 视频生成流程中自动调整条件张量（conditioning）中的关键帧潜码（keyframe latents）尺寸，使其与目标采样画布尺寸一致。

**核心作用**：
- 自动检测生成视频的画布尺寸（根据采样 latent）
- 智能缩放 conditioning 中的 `minimax_keyframes` 关键帧潜码
- 保持参考图片、视频、音频等媒体负载的原始尺寸（不修改）
- 提供详细的适配报告，记录处理过程和缓存命中情况

**适用场景**：
- 在 MiniMax H3 采样前调整 conditioning，确保关键帧尺寸与生成画布匹配
- 支持动态尺寸的视频生成工作流
- 与 `MiniMax H3 Reference to Video` 节点配合使用，实现完整的条件编码流程

## 输入参数

| 参数名 | 类型 | 说明 | 是否必填 |
|------|------|------|--------|
| `conditioning` | CONDITIONING | ComfyUI 条件张量列表，包含 cond_tensor 和 metadata 对象 | ✅ 是 |
| `latent` | LATENT | MiniMax H3 联合 AV (audio-video) 潜码，必须包含嵌套张量结构 | ✅ 是 |

### 参数说明

**conditioning（条件张量）**：
- 标准的 ComfyUI CONDITIONING 列表格式
- 每个条目为 `[cond_tensor, metadata_dict, ...]`
- metadata 中可包含 `minimax_keyframes` 列表，每个关键帧包含：
  - `latent`（可选）：关键帧潜码，形状应为 `[B, 24, T, H, W]`
  - 其他关键帧元数据（如时间戳、参考权重等）

**latent（采样 latent）**：
- MiniMax H3 生成的联合潜码结构
- 必须包含 `"samples"` 键
- samples 必须是嵌套张量，包含视频和音频两部分
- 视频部分形状为 `[B, 24, T, H, W]`（其中 H, W 定义目标画布尺寸）

## 输出说明

| 输出名 | 类型 | 说明 |
|------|------|------|
| `conditioning` | CONDITIONING | 调整后的条件张量（关键帧潜码已缩放到目标尺寸） |
| `report` | STRING | 适配过程的详细报告字符串 |

### 输出详情

**conditioning**：
- 结构与输入相同，保持向后兼容
- 关键帧潜码中的 latent 已缩放到目标尺寸
- 参考图片、视频、音频等媒体负载保持原始尺寸
- metadata 的其他字段保持不变

**report**：
- 包含画布尺寸、关键帧数量、实际缩放数量等信息
- 格式示例：`canvas=1024x576px; keyframe_latents=3; resized=2; reference image/video/audio payloads unchanged`

## 工作原理

### 处理流程

1. **画布尺寸检测**：从 latent 的视频部分提取目标画布的空间尺寸（H, W）
2. **conditioning 遍历**：逐一处理 conditioning 列表中的每个条目
3. **关键帧检查**：检查 metadata 中是否存在 `minimax_keyframes`
4. **尺寸匹配**：
   - 若关键帧潜码尺寸与目标尺寸一致，跳过处理
   - 若不一致，使用双三次插值（bicubic）缩放关键帧潜码
5. **媒体保护**：参考图片、视频、音频等媒体负载保持原始尺寸不变
6. **报告输出**：生成详细的适配报告

### 缩放细节

- **缩放方式**：双三次插值（bicubic，align_corners=False）
- **保留精度**：自动处理 FP16 / BF16 数据类型，必要时临时转为 FP32 再还原
- **时间轴保持**：缩放仅作用于空间维度（H, W），时间维度（T）保持不变
- **内存效率**：对每个时间步骤独立缩放，避免大规模临时张量

## 使用方法

### 基本工作流

```
MiniMax H3 Reference to Video (生成 conditioning 和 latent)
        ↓
MiniMax H3 Context Conditioning Adapter (调整 conditioning)
        ↓
KSampler 或其他采样器（进行实际生成）
```

### 连接方式

1. 将 `MiniMax H3 Reference to Video` 节点的 `conditioning` 输出连接到本节点的 `conditioning` 输入
2. 将采样前获得的 MiniMax H3 `latent` 连接到本节点的 `latent` 输入
3. 将本节点的 `conditioning` 输出连接到采样器（如 KSampler）的 conditioning 输入
4. 参考 `report` 输出了解缩放情况

### 参数配置

该节点无可配置参数，完全自动化处理。所有参数由输入张量决定：
- 目标尺寸由采样 latent 确定
- 缩放目标由 conditioning 中的关键帧定义

## 异常与限制

### 常见错误

**"latent must be the joint MiniMax H3 AV latent"**
- 原因：输入的 latent 不是 MiniMax H3 格式，或缺少 `"samples"` 键
- 解决：确保 latent 来自 MiniMax H3 相关节点，不是普通的 SD / FLUX latent

**"latent must contain a nested MiniMax H3 video/audio latent"**
- 原因：latent 结构不是嵌套张量格式
- 解决：确认 latent 是 MiniMax H3 的联合 AV 潜码结构

**"Expected video and audio latent parts, got X"**
- 原因：嵌套张量的部分数不是 2（应该是视频和音频两部分）
- 解决：检查 latent 生成是否正确

**"conditioning must be a ComfyUI CONDITIONING list"**
- 原因：输入的 conditioning 不是列表或元组格式
- 解决：确保 conditioning 来自标准的 ComfyUI 条件节点

**"Unexpected MiniMax H3 video latent shape: ..."**
- 原因：视频 latent 的通道数不是 24，或维度不是 5D
- 解决：检查 MiniMax H3 模型和配置是否匹配

### 已知限制

1. **固定通道数**：仅支持 24 通道的 MiniMax H3 视频潜码（标准配置）
2. **嵌套张量支持**：latent 必须使用 PyTorch 嵌套张量格式
3. **无条件修改**：conditioning 中的其他字段（如非关键帧元数据）保持不变
4. **不支持自定义插值**：缩放方式固定为双三次插值

## 依赖要求

- **PyTorch**：1.12+ 版本（支持嵌套张量 API）
- **其他**：无额外依赖

## 示例

### 完整工作流示例

```python
# 伪代码示例

# 第 1 步：加载参考和生成条件
ref_to_video_node = MiniMaxH3ReferenceToVideo()
conditioning, latent = ref_to_video_node.execute(
    images=image_batch,
    audio=audio_tensor,
    video=video_tensor,
    ...
)

# 第 2 步：适配条件张量到采样尺寸
adapter = MiniMaxH3ContextConditioningAdapter()
adapted_conditioning, report = adapter.adapt(conditioning, latent)
print(report)  # 输出: canvas=1024x576px; keyframe_latents=3; resized=2; ...

# 第 3 步：使用适配后的条件进行采样
sampler = KSampler()
output_latent = sampler.sample(
    model=model,
    seed=42,
    steps=25,
    cfg=7.5,
    sampler_name="dpmpp_2m",
    scheduler="karras",
    positive=adapted_conditioning,
    negative=negative_conditioning,
    latent_image=latent,
)
```

### 报告示例

运行后 `report` 输出示例：

```
canvas=1024x576px; keyframe_latents=3; resized=2; reference image/video/audio payloads unchanged
```

- **canvas**：目标生成画布尺寸（像素）
- **keyframe_latents**：处理的关键帧总数
- **resized**：实际执行缩放的关键帧数
- **reference ...**：媒体负载保护状态

## 相关节点

- **[MiniMax H3 Reference to Video](../MiniMaxH3ReferenceToVideo.md)**：生成多模态条件张量和采样 latent
- **MiniMax H3 Ref2VA Images**：处理参考图片和音频重编号

## 更新记录

- **v1.8.0 (2026-10-21)**：新增本节点，用于自动调整关键帧潜码尺寸
