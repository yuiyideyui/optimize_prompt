# ComfyUI AI Nodes

这是一个 ComfyUI 自定义节点包，支持通过 Gemini 和 Claude API 进行文本生成和视频质检。

## 节点列表

### 1. [Gemini / Claude / Gemma Text](gemini_claude_text.md)
**多模态文本生成节点**

- 支持 Gemini、Gemma 和 Claude 三种 AI 提供商
- 支持纯文本或图文混合输入
- 可配置温度、top_p、最大 token 数等参数
- 适用于图像描述、内容分析、创意生成等场景

**分类**: `my_comfyui/AI`  
**[查看详细文档 →](gemini_claude_text.md)**

---

### 2. [Gemini Video Inspector](gemini_video_inspector.md)
**视频质量检验节点**

- 基于 Gemini AI 多模态模型评估视频帧序列
- 支持多轮迭代检验（历史评分和反馈）
- 自动判断是否通过质检（可配置阈值）
- 输出详细的优化建议、可用于重生成的提示词和完整日志
- 评分低于上一轮时可沿用上一轮的优化提示词
- 适用于构建自动化视频质检与重生成闭环

**分类**: `my_comfyui/AI`  
**[查看详细文档 →](gemini_video_inspector.md)**

---

### 3. [Auto Anime Storyboard Pipeline](AutoAnimeStoryboardPipeline.md)
**自动化动画分镜生成流水线**

- 智能分段生成：自动生成多个 Part（分镜段落）
- 逐段精修：每个 Part 独立精修，保持前后连贯性
- 上下文记忆：支持历史分镜续写，无缝衔接剧情
- 渐进式总结：自动生成和演进剧情摘要，支持超长故事创作
- 多模态输入：支持参考图片和音频素材引用
- 格式严格：强制遵守 Markdown Part 格式和素材编号规则

**分类**: `LLM/Anime`  
**[查看详细文档 →](AutoAnimeStoryboardPipeline.md)**

---

### 4. [Image Counter](utils/ImageCounter.md)
**图像批次计数节点**

- 统计输入图像批次的数量
- 返回批次大小（整数）
- 适用于调试、条件判断和流程控制
- 零性能开销的实用工具节点

**分类**: `my_comfyui/utils`  
**[查看详细文档 →](utils/ImageCounter.md)**

---

### 5. [Batch Image Loader 📸](utils/BatchImageLoader.md)
**批量加载图片节点**

- 支持拖拽上传多张图片（最多 9 张）
- 支持从文件夹自动加载所有图片
- 输出为图像批次，便于批量处理
- 自动转换颜色空间和格式标准化
- 支持 PNG、JPG、BMP、GIF、WebP 等格式

**分类**: `my_comfyui/utils`  
**[查看详细文档 →](utils/BatchImageLoader.md)**

---

### 5. [MiniMax H3 Reference to Video](MiniMaxH3ReferenceToVideo.md)
**智能批量参考条件节点**

- 支持图片、音频、视频多模态参考输入
- 自动解析和重新索引 `<picture N>` 和 `<audio N>` 标签
- 灵活的参考图片尺寸模式（match / max）
- 完整的条件编码和 AV 潜空间生成
- 适用于高保真角色控制、音频驱动视频、风格继承等场景

**分类**: `model/conditioning/minimax`  
**[查看详细文档 →](MiniMaxH3ReferenceToVideo.md)**

---

### 6. [Network Retry](utils/NetworkRetry.md)
**网络请求重试工具**

- 为网络请求提供自动重试机制
- 支持自定义重试次数和间隔时间
- 默认配置：遇到错误后等待 60 秒重试，最多重试 4 次
- 已集成到所有网络节点（Gemini / Claude Text、Video Inspector）
- 提高不稳定网络环境下的工作流健壮性

**类型**: 内部工具模块  
**[查看详细文档 →](utils/NetworkRetry.md)**

---

### 6. [VideoProcessor](utils/VideoProcessor.md)
**视频处理工具模块**

- 支持将图像帧合并为视频文件
- 支持添加音频轨道或从视频提取音频
- 自动编码器选择和临时文件管理
- 灵活的输入格式支持（张量、数组、列表）
- 完整的视频解析和元数据提取

**类型**: 内部工具模块  
**[查看详细文档 →](utils/VideoProcessor.md)**

---

### 7. [🎬 Director Table (Card Manager)](utils/DirectorTable.md)
**导演卡片表管理节点**

- 加载和管理多个卡片图像（最多 9 个）
- 自动处理角色标签和元数据
- 灵活的图像缩放策略（中心裁剪、拉伸、letterbox）
- 输出图像批次和完整的位置信息元数据
- 支持相对路径和绝对路径

**分类**: `my_comfyui/director`  
**[查看详细文档 →](utils/DirectorTable.md)**

---

### 8. [Audio Combine (Batch / Concat)](utils/AudioBatchConcat.md)
**音频批处理和拼接节点**

- 支持批处理和时间拼接两种模式
- 自动采样率和声道数对齐
- 最多 8 个音频输入
- 灵活的音频处理选项（32kHz - 192kHz）
- 完整的音频元数据保留

**分类**: `my_comfyui/audio`  
**[查看详细文档 →](utils/AudioBatchConcat.md)**

---

## 快速开始

1. 将此文件夹放入 ComfyUI 的 `custom_nodes` 目录
2. 重启 ComfyUI 或刷新节点列表
3. 在节点菜单的 `my_comfyui/AI` 分类下找到相应节点
4. 准备好 Gemini 或 Claude 的 API 密钥
5. 开始使用！

## 文件结构

```
my_comfyui/
├── __init__.py                      # 模块入口，注册所有节点
├── gemini_claude_text.py            # 文本生成节点实现
├── gemini_video_inspector.py        # 视频质检节点实现
├── anime_pipeline/                  # 动画分镜管线
│   ├── __init__.py
│   ├── AutoAnimeStoryboardPipeline.py
│   └── prompts.py
├── custom_minimax_h3/               # MiniMax H3 节点
│   └── MiniMaxH3ReferenceToVideo.py  # 智能参考条件节点实现
├── utils/                           # 工具节点和模块
│   ├── __init__.py                  # 工具模块入口
│   ├── image_counter.py             # 图像计数节点实现
│   ├── director_node.py             # 导演卡片表节点实现
│   ├── audio_concat_batch_node.py   # 音频批处理节点实现
│   ├── network_retry.py             # 网络重试工具
│   ├── video_processor.py           # 视频处理工具
│   ├── ImageCounter.md              # 图像计数节点文档
│   ├── DirectorTable.md             # 导演卡片表节点文档
│   ├── AudioBatchConcat.md          # 音频批处理节点文档
│   ├── NetworkRetry.md              # 网络重试工具文档
│   └── VideoProcessor.md            # 视频处理工具文档
├── web/                             # 前端 Web 资源
├── README.md                        # 项目主文档（当前文件）
├── gemini_claude_text.md            # 文本生成节点详细文档
├── gemini_video_inspector.md        # 视频质检节点详细文档
├── AutoAnimeStoryboardPipeline.md   # 动画分镜管线文档
└── MiniMaxH3ReferenceToVideo.md     # MiniMax H3 参考条件文档
```

## 依赖要求

### 核心依赖
- Python 3.8+
- ComfyUI
- Pillow (PIL)
- NumPy
- 标准库: `json`, `urllib`, `base64`, `io`, `time`

### 节点特定依赖

#### 文本生成节点 (Gemini / Claude Text)
- `requests` — HTTP 请求库
- Google API 密钥（Gemini）或 Anthropic API 密钥（Claude）

#### 视频质检节点 (Gemini Video Inspector)
- `requests` — HTTP 请求库
- Google API 密钥（Gemini）
- OpenCV (`cv2`) — 用于视频帧提取

#### 动画分镜管线 (Auto Anime Storyboard Pipeline)
- `requests` — HTTP 请求库
- Google API 密钥（Gemini）或 Anthropic API 密钥（Claude）

#### 视频处理工具 (VideoProcessor)
- **必需**: FFmpeg（系统级，不是 Python 包）
- **可选**: 
  - OpenCV (`cv2`) — 用于帧提取和视频解析
  - `soundfile` 或 `librosa` — 用于音频提取

### 安装步骤

#### 1. 安装 FFmpeg（必需）

**Windows**:
```bash
# 使用 Chocolatey
choco install ffmpeg

# 或使用 winget
winget install FFmpeg

# 或手动下载：https://ffmpeg.org/download.html
```

**macOS**:
```bash
brew install ffmpeg
```

**Linux (Ubuntu/Debian)**:
```bash
sudo apt-get update
sudo apt-get install ffmpeg
```

验证安装：
```bash
ffmpeg -version
```

#### 2. 安装 Python 依赖

在 ComfyUI 根目录或虚拟环境中运行：
```bash
# 基础依赖
pip install pillow numpy requests

# 视频处理依赖（推荐）
pip install opencv-python soundfile

# 或使用 librosa（音频处理替代方案）
pip install librosa
```

#### 3. 配置 API 密钥

**方法 A: 环境变量**
```bash
# Windows (PowerShell)
$env:GEMINI_API_KEY = "your_gemini_key"
$env:CLAUDE_API_KEY = "your_claude_key"

# Linux/macOS
export GEMINI_API_KEY="your_gemini_key"
export CLAUDE_API_KEY="your_claude_key"
```

**方法 B: ComfyUI 环境配置**
- 在 ComfyUI web 界面中配置 API 密钥（通过节点参数）
- 每次使用节点时填入密钥

**方法 C: 配置文件**
- 在项目目录创建 `.env` 文件（如果节点支持）
```
GEMINI_API_KEY=your_gemini_key
CLAUDE_API_KEY=your_claude_key
```

## API 密钥获取

### Gemini API
1. 访问 [Google AI Studio](https://makersuite.google.com/app/apikey)
2. 登录 Google 账户（如需创建账户）
3. 点击 "Create API key"
4. 选择 "Create API key in new project" 或使用现有项目
5. 复制 API 密钥

**获取免费额度**: Gemini 提供免费 API 配额，每分钟限制请求数

### Claude API
1. 访问 [Anthropic Console](https://console.anthropic.com/)
2. 创建账户或登录
3. 导航到 "API Keys" 页面
4. 点击 "Create Key"
5. 复制 API 密钥

**计费方式**: Claude API 按使用量计费，查看 [定价页面](https://www.anthropic.com/pricing) 了解详情

## 常见问题

### 安装与配置

**Q: 节点没有出现在菜单中？**  
A: 
1. 确保文件夹位置正确：`ComfyUI/custom_nodes/my_comfyui`
2. 重启 ComfyUI（完全退出并重新启动）
3. 检查 ComfyUI 控制台是否有红色错误信息
4. 确认 Python 版本 ≥ 3.8
5. 验证依赖是否安装：`pip list | grep -E "pillow|numpy|requests"`

**Q: 导入时出现 "ModuleNotFoundError"？**  
A: 缺少依赖，运行：
```bash
pip install pillow numpy requests
pip install opencv-python soundfile  # 可选但推荐
```

**Q: FFmpeg 相关错误？**  
A: 
- 确认 FFmpeg 已安装：`ffmpeg -version`
- 确认 FFmpeg 在系统 PATH 中
- Windows 用户：重启计算机后系统 PATH 才会更新
- 重新安装 FFmpeg 到默认位置

### API 和网络

**Q: API 请求超时或网络错误？**  
A: 所有网络节点已内置自动重试机制。如果仍然失败：
- 检查网络连接：`ping www.google.com`
- 验证 API 密钥是否有效和未过期
- 检查防火墙是否阻止了请求
- 确认 API 服务状态：[Gemini 状态](https://status.cloud.google.com/)、[Anthropic 状态](https://www.anthropic.com/)
- 查看节点输出的完整错误信息

**Q: "Invalid API key" 错误？**  
A:
- 确认密钥复制无误（无多余空格或换行）
- 验证密钥未过期或被撤销
- 检查环境变量或节点参数中的密钥设置
- 重新获取新密钥并重试

**Q: 请求配额已满？**  
A:
- Gemini：等待配额重置（通常按分钟或天数重置）
- Claude：检查消费情况，可能需要升级账户或等待下个计费周期

### 视频处理

**Q: 视频质检一直不通过？**  
A: 
- 降低 `pass_threshold` 参数值
- 检查 `evaluation_criteria` 是否过于严格
- 尝试增加视频帧数或分辨率
- 查看详细反馈信息，根据建议调整参数

**Q: 视频生成失败或文件损坏？**  
A:
- 确保帧数据格式正确（RGB/RGBA，0-255 或 0-1）
- 检查所有帧尺寸一致
- 验证 FFmpeg 版本兼容性：`ffmpeg -codecs | grep h264`
- 尝试不同编码器（如改用 mpeg4）
- 检查磁盘空间是否充足

**Q: 音频和视频不同步？**  
A:
- 确保 `fps` 和 `sample_rate` 参数正确
- 验证帧数和音频长度逻辑上匹配
- 尝试使用标准帧率（24, 25, 30, 60 fps）
- 使用标准采样率（44100 或 48000 Hz）

### 性能优化

**Q: 成本太高？**  
A:
- 使用 flash 模型（如 `gemini-2.0-flash-exp`），成本更低但速度快
- 减少视频帧数或分辨率
- 降低 `max_tokens` 参数
- 复用评分结果，避免重复检验

**Q: 处理速度很慢？**  
A:
- 减少输入帧数
- 降低分辨率
- 使用更快的编码器
- 检查系统资源（CPU/GPU 使用率）
- 分批处理而不是一次处理所有内容

## 更新日志

### v1.7.0 (2026-10-19)
- ✨ 增强 Auto Anime Storyboard Pipeline：支持 `ref_images_metadata` 参数，与 Director Table 节点无缝集成
- 🎨 新增 Letterbox 图片恢复功能：自动恢复被 Director Table letterbox 处理的原始尺寸，提升视觉保真度
- ⚡ 优化性能：参考图片自动压缩至 0.5x 分辨率，加快 API 调用速度
- 📝 完善 Auto Anime Storyboard Pipeline 文档：添加集成示例和图片处理详细说明

### v1.6.0 (2026-10-19)
- ✨ 新增 Director Table (Card Manager) 节点：支持导演卡片表管理和多图像批处理
- ✨ 新增 Audio Combine (Batch / Concat) 节点：支持音频批处理和时间拼接
- 📝 完善节点文档：添加 DirectorTable.md 和 AudioBatchConcat.md 详细说明
- 📋 更新项目文件结构文档，反映新增节点和文档位置

### v1.5.0 (2026-10-18)
- ✨ 新增 Batch Image Loader 节点：支持拖拽上传和文件夹加载多张图片
- 🎨 支持两种加载模式（folder 和 select），最多 9 张图片拖拽上传
- 📊 自动合并图像批次，支持格式标准化和颜色空间转换
- 📝 完整的批量图片加载文档和使用示例

### v1.4.0 (2026-10-18)
- ✨ 新增 MiniMax H3 Reference to Video 节点：支持智能批量参考条件处理
- 📝 完善 MiniMax H3 Reference to Video 文档：详细说明多模态参考输入、标签映射、尺寸适配等功能
- 🔧 注册 MiniMaxH3ReferenceToVideo 节点到主模块，支持在 ComfyUI 中直接使用

### v1.3.0 (2026-10-18)
- 📝 完善 Auto Anime Storyboard Pipeline 文档：增加自动化分镜生成和精修的详细说明
- 🐛 修复 GeminiVideoInspector 输出格式：改正输出结构中的双花括号处理
- 🔧 增强 MiniMax H3 动漫提示词修正规则：提升角色动作、表情精确化及镜头稳定性要求
- 🎬 优化 VideoProcessor 和 GeminiVideoInspector：增强视频上传、音频处理和自动重试机制

### v1.2.0 (2026-10-17)
- ✨ 新增 VideoProcessor 工具模块：支持帧到视频合并、音频处理、视频解析
- 📝 完善文档：添加详细的依赖安装指南、API 配置说明、故障排查
- 🔧 优化 Gemini Video Inspector：支持 VideoFromComponents 对象和音频处理
- 📚 新增 VideoProcessor.md 详细文档

### v1.1.0 (2026-10-17)
- ✨ 新增网络重试机制：所有网络节点支持自动重试（默认 4 次，间隔 60 秒）
- 🔧 优化网络稳定性：提高不稳定网络环境下的工作流健壮性
- 📝 新增 Network Retry 工具文档

### v1.0.0 (2026-10-17)
- ✨ 新增 Gemini / Claude Text 节点
- ✨ 新增 Gemini Video Inspector 节点
- ✨ 新增 Auto Anime Storyboard Pipeline 节点
- 📝 完善文档结构

## 许可证

本项目遵循 MIT 许可证。

## 开发指南

### 本地开发环境

1. **克隆或复制项目**
```bash
cd ComfyUI/custom_nodes
# 复制项目文件夹到此目录
```

2. **安装开发依赖**
```bash
pip install -e .  # 如果有 setup.py
# 或手动安装所有依赖
pip install pillow numpy requests opencv-python soundfile
```

3. **调试模式**
- ComfyUI 启动时会自动加载 `custom_nodes` 中的模块
- 修改 Python 文件后通常需要重启 ComfyUI
- 检查控制台输出获取详细错误信息

### 项目结构说明

- **`__init__.py`**: 节点注册入口，定义 `NODE_CLASS_MAPPINGS` 和 `DISPLAY_NAME_MAPPINGS`
- **`utils/`**: 可复用工具和公共函数
- **`anime_pipeline/`**: 特定功能模块（动画分镜）
- **`*.md`**: 节点和模块文档

### 编码规范

- 遵循 PEP 8 风格指南
- 添加类型提示（Python 3.8+）
- 为公共方法添加 docstring
- 避免全局状态，优先使用类方法
- 错误处理要明确和有意义

### 测试建议

- 在 ComfyUI 中手动测试新节点
- 验证输入/输出类型和形状
- 检查极端情况（空输入、大数据等）
- 测试网络错误场景

## 贡献

欢迎提交 Issue 和 Pull Request！

### 提交 Issue
- 清楚描述问题和复现步骤
- 附加错误日志和系统信息
- 说明使用的节点和参数

### 提交 PR
- 从 `main` 分支创建特性分支
- 保持提交记录清晰
- 更新相关文档
- 添加必要的测试

---

**提示**: 点击每个节点标题查看详细的使用文档和示例。

**最后更新**: 2026-10-19
