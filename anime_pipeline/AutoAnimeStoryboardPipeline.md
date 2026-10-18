# Auto Anime Storyboard Pipeline 节点

自动化动画分镜生成流水线，支持长篇连续剧情创作、自动精修、渐进式总结和续写功能。

## 节点信息

- **节点名称**: `AutoAnimeStoryboardPipeline`
- **显示名称**: Auto Anime Storyboard Pipeline (Long Story & Summary)
- **分类**: `LLM/Anime`
- **实现文件**: [anime_pipeline/AutoAnimeStoryboardPipeline.py](anime_pipeline/AutoAnimeStoryboardPipeline.py)

## 功能简述

这是一个专为长篇动画分镜创作设计的自动化流水线节点，具备以下核心能力：

1. **智能分段生成**: 根据用户剧情自动生成多个 Part（分镜段落）
2. **逐段精修**: 每个 Part 独立精修，保持前后连贯性
3. **上下文记忆**: 支持历史分镜上下文，实现无缝续写
4. **渐进式总结**: 自动生成和更新剧情总结，支持超长故事创作
5. **多模态输入**: 支持参考图片和音频素材引用
6. **格式严格**: 强制遵守 Markdown Part 格式和素材编号规则

## 输入参数

### 必需参数

| 参数名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `user_prompt` | STRING (multiline) | 示例剧情 | 用户剧情描述和创作要求 |
| `api_key` | STRING (password) | `""` | Gemini API 密钥，或使用环境变量 `GEMINI_API_KEY` |
| `model_name` | Combo | `gemini-2.5-flash` | 模型选择：`gemini-3.5-flash-lite`、`gemini-2.5-flash`、`gemini-2.5-pro` |
| `output_dir` | STRING | `output/anime_storyboard` | 输出目录（相对或绝对路径） |
| `part_duration` | INT | `7` | 每个 Part 的**最大**时长（秒，范围 1-60），可根据剧情节奏灵活调整，只要不超过此上限 |
| `max_retries` | INT | `2` | API 调用失败时的最大重试次数（0-5） |

### 可选参数

| 参数名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `images` | IMAGE | - | 参考图片（支持多张），在剧情中使用 `<picture 1>`, `<picture 2>` 等引用 |
| `ref_images_metadata` | STRING | `""` | 图片元数据（来自 Director Table 等节点），用于恢复被 letterbox 处理的原始尺寸，提升视觉准确性 |
| `history_context` | STRING (multiline) | `""` | 之前生成的分镜完整文本（用于续写） |
| `previous_summary` | STRING (multiline) | `""` | 之前的剧情总结（用于长篇故事的宏观背景） |

## 输出

| 输出名 | 类型 | 说明 |
|--------|------|------|
| `final_storyboard` | STRING | 完整的分镜文本（包含历史和新生成的内容） |
| `story_summary` | STRING | 更新后的剧情总结 |
| `output_directory` | STRING | 实际的输出目录路径 |
| `status` | STRING | 执行状态信息 |

## 工作流程

### 基本流程

1. **初始化**: 解析用户输入，确定参考素材数量和起始 Part 编号
2. **图片处理**: 
   - 如提供 `ref_images_metadata`，根据元数据恢复被 letterbox 处理的原始尺寸
   - 将图片压缩至 0.5x 分辨率以加快 API 调用
3. **生成大纲**: 调用 Gemini 生成初始分镜大纲（包含所有 Part）
4. **解析分段**: 使用正则表达式提取每个 Part
5. **逐段精修**: 按顺序精修每个 Part，保持上下文连贯
6. **生成总结**: 为本次生成的内容创建剧情摘要
7. **合并总结**: 如有历史总结，则进行演进式合并
8. **保存输出**: 写入最终分镜和总结到文件

### 续写机制

当提供 `history_context` 时：
- 自动解析历史 Part 编号，新内容从 `max(历史编号) + 1` 开始
- 在生成提示中包含历史回顾，确保剧情衔接
- 最终输出合并历史和新内容

### 素材引用规则

#### 图片素材
- 在 `user_prompt` 中使用 `<picture 1>`, `<picture 2>` 等声明
- 系统自动统计声明数量和实际上传数量
- **严格约束**: 所有分镜必须使用原始编号，禁止引用"上一帧图片"

#### 音频素材
- 在 `user_prompt` 中使用 `<Audio 1>`, `<audio 2>` 等声明（不区分大小写）
- 系统自动提取音频编号并加入约束规则
- **严格约束**: 所有音频引用必须保持原始格式

## 使用示例

### 示例 1: 首次创作

```
user_prompt: 
请根据参考图生成日本传统2D动画分镜（目标：约1分钟，共8个Part）。

剧情：在书房中，两名角色围绕一份文件展开对话。
角色A是严肃的上司，角色B是紧张的下属。

参考图：
- <picture 1>: 书房全景
- <picture 2>: 角色A的设定图
- <picture 3>: 角色B的设定图
```

**连接**:
- images: 连接包含 3 张图片的 IMAGE 输出
- 其他参数使用默认值

**输出**: 生成 8 个 Part 的完整分镜，保存到 `output/anime_storyboard/anime_storyboard_final.md`

### 示例 2: 续写剧情

在示例 1 的基础上续写：

```
user_prompt:
继续生成后续分镜（目标：再生成约1分钟，共8个新Part）。

剧情继续：对话结束后，角色B离开书房，前往走廊。
```

**连接**:
- images: 同样的 3 张参考图
- history_context: 连接示例 1 的 `final_storyboard` 输出
- previous_summary: 连接示例 1 的 `story_summary` 输出

**输出**: 自动从 Part 9 开始生成，最终输出包含 Part 1-16 的完整分镜

### 示例 3: 音频绑定

```
user_prompt:
生成动画分镜，角色需绑定参考音频。

剧情：角色A和B进行对话。

素材：
- <picture 1>: 场景参考
- <picture 2>: 角色A
- <picture 3>: 角色B
- <Audio 1>: 角色A的参考音色
- <Audio 2>: 角色B的参考音色

要求：在对话部分标注使用哪个音频素材。
```

**说明**: 系统会自动识别音频素材数量，并在精修时强制保留音频编号格式

### 示例 4: 与 Director Table 集成

当使用 Director Table 节点管理参考图片时：

```
user_prompt:
生成动画分镜（使用导演卡片管理的参考图）。

剧情：根据导演卡片的角色设定进行分镜生成。

素材：
- <picture 1>: 场景
- <picture 2>: 主角
- <picture 3>: 配角
```

**连接**:
- `images`: 连接 Director Table 的 `images` 输出
- `ref_images_metadata`: 连接 Director Table 的 `image_metadata` 输出
- 其他参数按需配置

**效果**: 系统自动恢复被 letterbox 处理的原始尺寸，向视觉模型提供高保真参考

## 输出文件

生成在 `output_dir` 指定的目录下：

1. **anime_storyboard_final.md**: 完整分镜文本
2. **anime_storyboard_summary.md**: 剧情总结文本

## 核心技术细节

### 图片处理
- **Letterbox 恢复**: 当提供 `ref_images_metadata` 时，根据元数据中的 crop 坐标恢复原始尺寸，移除 letterbox 填充
- **图片压缩**: 将所有参考图片压缩至 0.5x 分辨率，加快 API 调用速度同时保留视觉细节
- **兼容性**: 若元数据不可用或格式错误，保留原始图片尺寸，确保流程继续进行

### Part 解析
- 使用正则表达式 `^#{1,6}\s+Part\s+(\d+)\b.*$` 匹配标题
- 验证编号连续性和唯一性
- 提取每个 Part 的完整内容

### 精修策略
- 每个 Part 精修时会获取前一个已精修的 Part 作为上下文
- 只修复明确问题，不重写剧情
- 强制保留原始编号、标题、剧情、对白

### 总结演进
- 首次生成：为当前批次创建总结
- 续写生成：合并旧总结与新总结，形成演进式大纲

### 提示词系统
- `SYSTEM_PROMPT_GENERATE`: 用于生成初始分镜大纲
- `SYSTEM_PROMPT_AUTO_REFINE`: 用于精修每个 Part

## 注意事项

1. **API 消耗**: 每次运行会调用多次 API（生成 + 每个 Part 精修 + 总结），注意成本控制
2. **时长灵活性**: `part_duration` 是每个 Part 的**最大时长**，实际时长可根据剧情需要灵活调整，只要不超过此上限。这样可以在保持总体时长控制的同时，为模型提供更大的创作自由度
3. **编号连续性**: 系统强制要求 Part 编号连续，不允许跳号或重复
4. **素材引用**: 必须先在 `user_prompt` 中声明素材，再在实际剧情中引用
5. **续写机制**: 续写时必须提供 `history_context`，系统会自动计算起始编号

## 错误处理

节点会捕获并清晰报告以下错误：
- API 密钥缺失
- 模型输出中未找到 Part 标题
- Part 编号重复或不连续
- Part 内容为空
- 精修后缺少标题
- API 调用失败（含重试机制）

## 性能优化建议

1. **选择合适的模型**:
   - `gemini-3.5-flash-lite`: 最快，成本最低，适合草稿
   - `gemini-2.5-flash`: 平衡速度和质量（推荐）
   - `gemini-2.5-pro`: 质量最高，速度较慢，成本较高

2. **控制 Part 数量**: 每批生成 6-10 个 Part 较为合理

3. **复用参考图**: 同一故事线使用相同参考图，避免重复上传

4. **合理使用续写**: 超过 20 个 Part 后，使用总结功能减少上下文长度

## 相关文件

- 实现代码: [anime_pipeline/AutoAnimeStoryboardPipeline.py](anime_pipeline/AutoAnimeStoryboardPipeline.py)
- 提示词定义: [anime_pipeline/prompts.py](anime_pipeline/prompts.py)
- 项目主页: [README.md](README.md)

## 典型应用场景

1. **长篇动画创作**: 章节式创作，每次生成一个场景，通过续写功能连接
2. **故事探索**: 生成多个版本，选择最佳方案继续发展
3. **分镜自动化**: 将文字剧本转换为结构化分镜脚本
4. **多模态素材管理**: 统一管理图片和音频素材的引用关系
