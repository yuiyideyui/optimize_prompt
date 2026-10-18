# Audio Combine (Batch / Concat)

**节点类名**: `AudioBatchOrConcat`  
**显示名称**: `Audio Combine (Batch / Concat)`  
**分类**: `my_comfyui/audio`

## 功能说明

Audio Combine 节点支持将多个音频输入进行**批处理（Batch）**或**时间拼接（Concat）**，自动处理采样率和声道数对齐。适用于：

- 多音频轨道合并
- 音频序列拼接
- 采样率和声道数标准化
- 音频批处理工作流

## 输入参数

| 参数名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `mode` | 选择 | `batch` | 处理模式：`batch`（批处理）或 `concat`（时间拼接） |
| `target_sample_rate` | 整数 | `32000` | 目标采样率（范围：8000-192000 Hz） |
| `audio_1` ~ `audio_8` | AUDIO | 可选 | 可选的音频输入（最多 8 个） |

### 模式说明

- **Batch 模式**：将多个音频堆叠为一个批次，每个音频作为单独的批处理元素
  - 输出形状：`[Total_Batch, Channels, Max_Length]`
  - 较短的音频自动补零到最长的长度

- **Concat 模式**：在时间轴上拼接所有音频
  - 输出形状：`[1, Channels, Total_Length]`
  - 音频按输入顺序依次连接

## 输出说明

| 输出名 | 类型 | 说明 |
|--------|------|------|
| `audio` | AUDIO | 处理后的音频对象，包含 `waveform` 和 `sample_rate` |

### 输出格式

```python
{
    "waveform": torch.Tensor,      # 形状取决于模式：
                                    # Batch: [B, C, L]
                                    # Concat: [1, C, Total_L]
    "sample_rate": int             # 目标采样率
}
```

## 工作原理

1. **音频收集**：收集所有已连接的音频输入（audio_1 到 audio_8）
2. **采样率统一**：使用 `torchaudio.functional.resample` 将所有音频重采样到目标采样率
3. **声道对齐**：补齐声道数至最大值（通过重复最后一个声道）
4. **模式处理**：
   - **Batch**：补零对齐长度后在 batch 维堆叠
   - **Concat**：在时间维直接拼接
5. **返回结果**：带有采样率元数据的最终音频对象

## 使用方法

### 基础使用

1. 在 ComfyUI 节点菜单中找到 `my_comfyui/audio` 分类
2. 添加 **Audio Combine** 节点
3. 连接 1 到 8 个 AUDIO 输入
4. 设置处理模式和目标采样率
5. 获取处理后的音频输出

### 使用示例

**场景 1：多音轨批处理**
```
音频 1 (44100 Hz, 立体声) ┐
音频 2 (48000 Hz, 单声道) ├─→ [Batch 模式] → 统一采样率和声道的批次
音频 3 (32000 Hz, 单声道) ┘
```

**场景 2：音频序列拼接**
```
背景音乐 (3 秒) ┐
过渡音效 (1 秒) ├─→ [Concat 模式] → 连续的 4 秒音频
语音 (2.5 秒)   ┘
```

## 依赖要求

- **必需**:
  - Python 3.8+
  - PyTorch (`torch`)
  - `torchaudio` — 用于音频重采样和处理

- **安装**:
```bash
pip install torch torchaudio
```

## 异常与限制

### 已知限制

1. **最多 8 个输入**：节点支持最多 8 个音频输入
2. **声道补齐**：通过重复最后一个声道对齐，可能不适合某些特殊音频处理需求
3. **采样率范围**：目标采样率限制在 8000-192000 Hz
4. **至少一个输入**：必须连接至少一个 AUDIO 输入，否则抛出错误

### 常见错误

| 错误信息 | 原因 | 解决方案 |
|---------|------|--------|
| `至少需要连接一个 AUDIO 输入！` | 没有连接任何音频 | 连接至少一个 `audio_1` 到 `audio_8` |
| `ModuleNotFoundError: No module named 'torchaudio'` | 缺少依赖 | 运行 `pip install torchaudio` |
| 采样率不变 | 重采样失败 | 检查 `target_sample_rate` 是否在有效范围内 |

## 工作流示例

### 示例 1：合并多个背景音效

```
[Load Audio 1] (吉他音乐, 44100 Hz)
         ↓
[Audio Combine (Batch)]
         ↑
[Load Audio 2] (鼓声, 48000 Hz)
         ↑
[Load Audio 3] (人声, 32000 Hz)

参数设置：
- mode: batch
- target_sample_rate: 44100

输出：统一采样率和声道的音频批次，适合后续模型处理
```

### 示例 2：拼接完整音频轨道

```
[Load Intro Music]      (3 秒)
         ↓
[Audio Combine (Concat)] ← [Load Transition]  (1 秒)
         ↓
[Apply Audio Effects]   ← [Load Main Music]   (30 秒)

参数设置：
- mode: concat
- target_sample_rate: 48000

输出：连续的 34 秒完整音频轨道
```

## 性能考虑

- **重采样开销**：采样率转换会增加计算时间，建议提前设置合适的采样率
- **内存使用**：Batch 模式会将所有音频加载到内存，大量长音频可能导致 OOM
- **最优配置**：
  - 使用标准采样率（32000、44100、48000 Hz）
  - 限制每个批次的音频数量
  - 对超长音频进行预分割

## 常见问题

**Q: Batch 和 Concat 有什么区别？**  
A: Batch 将音频作为独立样本堆叠（形如多张图像）；Concat 在时间轴拼接（形如视频帧序列）。

**Q: 声道数不同的音频会怎样处理？**  
A: 节点会自动将所有音频补齐到最大声道数（通过重复最后一个声道）。

**Q: 是否支持超过 8 个音频输入？**  
A: 当前不支持。可以使用多个节点级联处理。

**Q: 如何确保音频同步？**  
A: Concat 模式完全按顺序拼接；Batch 模式中的音频在时间轴上是独立的，不需要同步。

---

**最后更新**: 2026-10-19
