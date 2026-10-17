# VideoProcessor - 视频处理工具模块

## 概述

`VideoProcessor` 是一个强大的视频处理工具类，用于在 ComfyUI 中处理视频帧、音频和视频文件的生成。它提供了从图像帧合并为视频、音频提取、视频解析等完整的视频处理能力。

**模块路径**: `utils/video_processor.py`  
**类名**: `VideoProcessor`  
**分类**: 内部工具模块

---

## 核心功能

### 1. 帧到视频合并
将图像帧序列合并为 MP4 视频文件，支持自定义帧率、编码器和质量参数。

### 2. 音频处理
支持从视频提取音频，或在合并视频时添加音频轨道。

### 3. 视频解析
从视频文件提取帧序列和音频数据。

### 4. 临时文件管理
自动管理临时目录和文件清理。

---

## 主要方法

### `frames_to_video(frames, audio_data=None, fps=24, sample_rate=44100, codec="auto", crf=18)`

**功能**: 将图像帧和音频合并成视频文件

**参数**:
- `frames` (list, tuple, iterable): 图像帧数据
  - 支持 PyTorch 张量、NumPy 数组、Python 列表
  - 每帧格式: RGB 或 RGBA，值范围 0-1 或 0-255
  - 形状: (H, W, 3) 或 (H, W, 4)、(3, H, W) 等

- `audio_data` (dict or array, optional): 音频数据
  - 支持 NumPy 数组格式 (samples,) 或 (samples, channels)
  - 支持 ComfyUI AudioInput 字典格式 `{"waveform": ..., "sample_rate": ...}`
  - 值范围: 浮点 [-1, 1] 或整数 int16
  - 默认: `None`（不添加音频）

- `fps` (int): 视频帧率，默认 24

- `sample_rate` (int): 音频采样率（Hz），默认 44100

- `codec` (str): 视频编码器
  - `"auto"`: 自动选择可用编码器（推荐）
  - `"libx264"`: H.264（最广兼容）
  - `"libopenh264"`: OpenH264
  - `"h264_mf"`: Windows Media Foundation
  - `"mpeg4"`: MPEG-4

- `crf` (int): 质量参数（0-51，越低越好），默认 18
  - 0-18: 高质量
  - 19-28: 平衡
  - 29-51: 低质量/高压缩

**返回值**: 视频文件路径 (str)

**示例**:
```python
from utils.video_processor import VideoProcessor
import numpy as np

# 创建 24 帧的测试图像
frames = [np.random.randint(0, 256, (720, 1280, 3), dtype=np.uint8) for _ in range(24)]

# 合并为视频（无音频）
video_path = VideoProcessor.frames_to_video(frames, fps=30)
print(f"Video saved to: {video_path}")

# 合并为视频（含音频）
audio_data = np.random.randn(44100).astype(np.float32)  # 1 秒音频
video_path = VideoProcessor.frames_to_video(
    frames, 
    audio_data=audio_data, 
    fps=30, 
    sample_rate=44100
)
```

---

### `extract_frames_from_video(video_path, fps=None)`

**功能**: 从视频文件提取帧序列

**参数**:
- `video_path` (str): 视频文件路径

- `fps` (int, optional): 提取帧的速率
  - `None`: 使用视频原始帧率
  - 整数: 指定提取帧率

**返回值**: 帧列表，每个元素是 (H, W, 3) 的 uint8 NumPy 数组

**示例**:
```python
# 以原始帧率提取所有帧
frames = VideoProcessor.extract_frames_from_video("video.mp4")

# 以 15fps 的速率提取帧
frames = VideoProcessor.extract_frames_from_video("video.mp4", fps=15)
```

---

### `extract_audio_from_video(video_path)`

**功能**: 从视频文件提取音频

**参数**:
- `video_path` (str): 视频文件路径

**返回值**: 音频数据 (NumPy 数组)
- 形状: (samples,)（单声道）或 (samples, channels)（多声道）
- 数据类型: float32 或 float64

**示例**:
```python
audio_data = VideoProcessor.extract_audio_from_video("video.mp4")
print(f"Audio shape: {audio_data.shape}, dtype: {audio_data.dtype}")
```

---

### `get_frame_count(video_path)`

**功能**: 获取视频帧总数

**参数**:
- `video_path` (str): 视频文件路径

**返回值**: 帧数 (int) 或 None（获取失败时）

**示例**:
```python
frame_count = VideoProcessor.get_frame_count("video.mp4")
if frame_count:
    print(f"Total frames: {frame_count}")
```

---

### `cleanup(video_path)`

**功能**: 删除指定的临时视频文件

**参数**:
- `video_path` (str): 视频文件路径

**示例**:
```python
VideoProcessor.cleanup(video_path)
```

---

### `cleanup_all()`

**功能**: 清理所有临时文件和临时目录

**示例**:
```python
VideoProcessor.cleanup_all()
```

---

## 内部方法

### `_prepare_frames(frames)` [静态方法]
将不同格式的帧转换为标准 NumPy 数组列表。

### `_tensor_to_numpy(frame)` [静态方法]
将单个帧（张量或数组）转换为 uint8 NumPy 数组，自动处理：
- 通道顺序转换 (CHW ↔ HWC)
- 灰度图像到 RGB 扩展
- Alpha 通道移除
- 值范围缩放 (float ↔ uint8)

### `_write_audio_wav(audio_data, output_path, sample_rate)` [静态方法]
将音频数据写入 WAV 文件。自动处理：
- 浮点 ↔ int16 转换
- 立体声/单声道检测
- ComfyUI AudioInput 格式解析

### `_write_video_ffmpeg(frame_array, output_path, fps, codec, crf, audio_path)` [静态方法]
使用 FFmpeg 编码视频。自动处理：
- 视频和音频同步合并
- 偶数尺寸补边（yuv420p 兼容）
- FFmpeg 进程管理和错误捕获

### `_select_video_codec(codec)` [静态方法]
自动检测和选择可用的视频编码器，避免硬编码依赖。

---

## 依赖要求

### 必需
- Python 3.8+
- NumPy
- FFmpeg（系统级，必须安装）

### 可选（按功能）
- **视频帧提取**: OpenCV (`cv2`)
- **音频提取**: `soundfile` 或 `librosa`

---

## 系统要求

- **FFmpeg**: 必须安装并在 PATH 中可访问
  - Windows: `ffmpeg.exe` 在 PATH 中
  - macOS: `brew install ffmpeg`
  - Linux: `apt-get install ffmpeg`

---

## 特性详解

### 格式兼容性

**输入帧格式支持**:
- PyTorch 张量 (on GPU/CPU)
- NumPy 数组
- Python 列表
- 任何可迭代序列

**帧值范围自动检测**:
- 浮点 [0, 1] → 自动缩放到 [0, 255]
- 浮点 [0, 255] → 直接转换
- 整数 → 直接转换

**通道顺序自动转换**:
- CHW → HWC（PyTorch 格式）
- 灰度图 → RGB（自动复制通道）
- RGBA → RGB（自动移除 Alpha）

### 编码器自动选择

`codec="auto"` 时，按优先级尝试：
1. `libx264` (H.264, 最广泛支持)
2. `libopenh264`
3. `h264_mf` (Windows)
4. `mpeg4` (fallback)

### 临时文件管理

- 自动创建临时目录: `/tmp/comfyui_video_*`（Linux/macOS）或系统临时目录（Windows）
- 音频文件在视频编码后自动删除
- 调用 `cleanup_all()` 清理所有临时文件

### 错误处理

- **缺少依赖**: 清晰的 RuntimeError 提示缺少 FFmpeg、OpenCV 等
- **无效帧格式**: 验证所有帧形状一致，尺寸有效
- **FFmpeg 失败**: 捕获 stderr 并提供详细错误信息
- **网络问题**: 如在节点中使用，配合 NetworkRetry 工具重试

---

## 使用场景

### 场景 1: 生成视频输出
```python
# 在节点中使用
frames = generate_frames()  # 从模型生成帧
video_path = VideoProcessor.frames_to_video(frames, fps=24)
# 返回给 ComfyUI
```

### 场景 2: 视频质检工作流
```python
# 提取原视频帧进行评估
original_frames = VideoProcessor.extract_frames_from_video("input.mp4")
# 处理后重新合并
processed_video = VideoProcessor.frames_to_video(processed_frames)
```

### 场景 3: 保留音频的视频处理
```python
# 提取原视频的音频
audio = VideoProcessor.extract_audio_from_video("input.mp4")
# 处理帧后，将原音频添加回去
output_video = VideoProcessor.frames_to_video(new_frames, audio_data=audio)
```

---

## 常见问题

**Q: 提示"ffmpeg not found"？**  
A: 确保 FFmpeg 已安装且在系统 PATH 中。检查 `ffmpeg --version`。

**Q: 视频质量太低？**  
A: 降低 `crf` 值（如 18 → 12），或使用更高的原始分辨率。

**Q: 视频编码很慢？**  
A: 尝试使用更快的编码器（如 `libopenh264`）或降低分辨率。

**Q: 内存占用过高？**  
A: 分批处理帧，不要一次性加载大量高分辨率视频。

**Q: 音频和视频不同步？**  
A: 确保 `fps` 和 `sample_rate` 正确，帧数和音频长度匹配。

---

## 性能建议

- **最佳帧率**: 24-30 fps（平衡质量和文件大小）
- **推荐 CRF**: 18-22（平衡质量和编码速度）
- **分辨率**: 1080p 及以下时编码速度最优
- **音频采样率**: 44100 Hz（CD 质量）或 48000 Hz（视频标准）

---

## 许可证

本模块遵循 MIT 许可证。

---

**最后更新**: 2026-10-17
