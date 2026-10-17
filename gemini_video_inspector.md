# Gemini Video Inspector 节点

视频质量检验节点，基于 Gemini AI 多模态模型对视频帧序列进行质量评估和打分。

## 节点信息

- **节点名称**: `GeminiVideoInspector`
- **显示名称**: Gemini Video Inspector
- **分类**: `my_comfyui/AI`
- **实现文件**: [gemini_video_inspector.py](gemini_video_inspector.py)

## 功能简述

基于 Gemini AI 多模态模型，对输入的视频帧序列进行质量检验、评分及多维度评估。结合上游节点的评分与修改意见，自动判断视频是否通过检验，并在未通过时输出具体的优化建议，用于构建自动化视频质检与重生成（Refine）循环闭环。

## 输入参数

### 必需参数

| 参数名 | 类型 | 默认值 | 范围/选项 | 说明 |
|--------|------|--------|----------|------|
| `user_prompt` | STRING (multiline) | `""` | - | 生成该视频时使用的原始提示词，供 AI 对比原意 |
| `evaluation_criteria` | STRING (multiline) | `"检查动作是否连贯、画面有无畸变、主体是否符合提示词"` | - | 质检要求文案，定义评估标准 |
| `provider` | STRING | `gemini` | gemini / openai / claude | AI 服务提供商选择 |
| `api_key` | STRING | `""` | - | API 密钥（必填） |
| `model_name` | STRING | `gemini-2.0-flash-exp` | - | 模型选择 |
| `base_url` | STRING | `https://generativelanguage.googleapis.com` | - | API 基础 URL |
| `temperature` | FLOAT | `0.2` | 0.0-2.0 | 模型采样温度，低温度保持评估客观稳定 |
| `max_tokens` | INT | `1024` | 1-65536 | 输出结果的最大 Token 限制 |
| `pass_threshold` | FLOAT | `8.0` | 0.0-10.0 | 通过阈值，评分 >= 阈值即判定为通过 |
| `timeout` | INT | `120` | 1-600 | 请求超时时间（秒） |

### 可选参数

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `images` | IMAGE | 输入的视频帧图片流（Batch 格式）**和 `video` 二选一** |
| `video` | VIDEO | 直接输入视频，节点自动提取所有帧 **和 `images` 二选一** |
| `frame_count` | INT | 原始视频总帧数（仅在使用 `images` 时可用，`video` 模式下忽略） |
| `select_every_nth` | INT | 帧采样间隔（仅在使用 `images` 时可用，`video` 模式下忽略；默认：1）|
| `prev_score` | FLOAT | 上一轮评分，用于多轮迭代对比 |
| `prev_feedback` | STRING | 上一轮修改意见，用于检查问题是否已修正 |
| `prev_h3_optimize_prompt` | STRING | 上一轮输出的优化提示词；用于评分比较和在新一轮表现变差时保留较好的版本 |
| `prev_distilled_meta_prompt` | STRING | 从历史迭代中提炼的通用修改规则 |

> **输入说明**:
> - **VIDEO 模式（推荐）**: 直接连接视频节点，自动提取所有帧；`frame_count` 和 `select_every_nth` 被忽略
> - **IMAGE 模式（兼容）**: 连接图像序列，支持通过 `frame_count` 和 `select_every_nth` 进行细粒度控制
> - **首次检验说明**: 当 `prev_score`、`prev_feedback` 和 `prev_h3_optimize_prompt` 为空时，节点会自动识别为首次检验，并调整评估提示词

## 输出

| 输出名 | 类型 | 说明 |
|--------|------|------|
| `passed` | BOOLEAN | 是否通过检验（基于评分和阈值自动判定） |
| `score` | FLOAT | 本次检验的综合评分（0-10 分） |
| `feedback` | STRING | 详细的修改意见与不通过原因，如通过则输出肯定或优化建议 |
| `log_text` | STRING | 格式化的完整分析日志（包含 Prompt 及 AI 回复全貌，方便调试） |
| `h3_optimize_prompt` | STRING | 基于本轮视频问题改写的完整生成提示词；若上一轮评分更高且上一轮优化提示词非空，则沿用上一轮提示词 |
| `distilled_meta_prompt` | STRING | 从多轮迭代中提炼的通用修改规则，跨任务可复用的元知识 |

## 评估维度

节点会从以下维度对视频进行评估：

1. **动作连贯性**: 视频帧之间的动作是否流畅自然
2. **画面质量**: 是否存在畸变、模糊、闪烁等问题
3. **主体符合度**: 内容是否符合原始生成提示词的要求
4. **整体协调性**: 画面整体的和谐度和完成度

## 核心逻辑

### 1. 多模态封装
- 将输入的 IMAGE Batch 转换为 Gemini API 支持的 Base64 编码格式
- 连同 `user_prompt` 和 `evaluation_criteria` 组装成结构化的多模态 Prompt

### 2. 状态传递（闭环迭代）
- **首次检验**: 当 `prev_score`、`prev_feedback` 和 `prev_h3_optimize_prompt` 为空时，提示词中会标注"这是对该视频的首次检验"
- **迭代检验**: 当有历史信息时，会在提示词中加入：
  ```
  ## 历史迭代信息
  - 上一轮评分: X.XX
  - 上一轮修改意见: [具体意见]
  
  请重点检查本轮输入是否解决了上一轮提出的问题。
  ```

### 3. 结构化输出
- 强制 Gemini 返回 JSON 格式（通过 `responseMimeType: "application/json"`）
- 标准输出格式：
  ```json
  {
    "score": 8.5,
    "passed": true,
    "feedback": "详细评价内容",
    "h3_optimize_prompt": "可直接用于下一轮视频生成的完整提示词"
  }
  ```
- 自动校验：如果评分 >= 阈值但 `passed` 为 false，会自动修正为 true（反之亦然）
- 优化提示词选择：如果 `prev_score` 高于本轮评分且 `prev_h3_optimize_prompt` 非空，输出沿用上轮提示词；否则输出 Gemini 为本轮生成的优化提示词。空的上轮提示词会按不存在处理。

## 使用示例

### 场景 1: 视频直接输入（推荐）

```
工作流连接：
[视频文件] → [加载视频] → [GeminiVideoInspector]
                             ↓
                    [判断 passed 输出]
```

参数设置：
- `video`: 连接视频节点输出（自动提取所有帧）
- `frame_count`: 不需要连接（自动处理）
- `select_every_nth`: 不需要连接（自动处理）
- `user_prompt`: "一只可爱的橘猫在草地上奔跑"
- `evaluation_criteria`: "检查猫的动作是否流畅，草地是否自然，整体画面是否符合提示"
- `pass_threshold`: 8.0

优点：
- 无需手动提取帧
- 无需配置 `frame_count` 和 `select_every_nth`
- 自动使用视频的全部帧进行检验

### 场景 2: 图像序列输入（向后兼容）

```
工作流连接：
[视频文件] → [提取帧] → [GeminiVideoInspector]
             ↓
         (可选配置采样)
            ↓
        [帧计数]→[frame_count]
```

参数设置：
- `images`: 连接提取的帧序列
- `frame_count`: 连接原始视频总帧数（可选）
- `select_every_nth`: 设置为 2（每 2 帧取 1 帧，降低成本）
- 其他参数同上

### 场景 3: 首次视频质检

```
工作流连接：
[生成视频] → [加载视频] → [GeminiVideoInspector]
                             ↓
                    [判断 passed 输出]
```

参数设置：
- `video`: 连接生成的视频
- `user_prompt`: "一只可爱的橘猫在草地上奔跑"
- `evaluation_criteria`: "检查猫的动作是否流畅，草地是否自然，整体画面是否符合提示"
- `pass_threshold`: 8.0
- `prev_score`: 不连接（首次检验）
- `prev_feedback`: 不连接（首次检验）
- `prev_h3_optimize_prompt`: 不连接（首次检验）

输出：
- `passed`: true/false
- `score`: 7.5
- `feedback`: "动作整体连贯，但猫的尾巴在第3-5帧出现轻微畸变，建议优化"

### 场景 4: 迭代质检（二次修正）

```
工作流连接：
[第一次检验结果 score] ────┐
[第一次检验结果 feedback] ─┤
[第一次检验结果 h3_optimize_prompt] ─┤
                           ↓
[重新生成视频] → [加载视频] → [GeminiVideoInspector]
                             ↓
                    [判断是否通过]
```

参数设置：
- `video`: 连接重新生成的视频
- `prev_score`: 7.5（连接第一次的 score 输出）
- `prev_feedback`: "动作整体连贯，但猫的尾巴..."（连接第一次的 feedback 输出）
- `prev_h3_optimize_prompt`: 连接第一次的 `h3_optimize_prompt` 输出

此时 AI 会重点检查上一轮提出的"尾巴畸变"问题是否已修复，并生成可用于下一轮视频生成的优化提示词。如果本轮评分低于上一轮且上一轮优化提示词非空，则保留上一轮的提示词。

### 场景 5: 自动化循环质检

```
[生成视频] → [加载视频] → [质检] → [条件节点 passed?]
                                  ↓ false
                             [重新生成] ──┐
                                  ↑       │
                                  └───────┘
                                  ↓ true
                             [输出最终视频]
```

通过 `passed` 输出控制循环，直到视频通过质检。

## 推荐配置

### 严格质检模式
```
temperature: 0.1
pass_threshold: 8.5
```
适用于需要高质量输出的场景。

### 平衡模式（默认）
```
temperature: 0.2
pass_threshold: 8.0
```
平衡质量和通过率。

### 宽松模式
```
temperature: 0.3
pass_threshold: 7.0
```
适用于快速迭代或对质量要求不高的场景。

## 核心实现函数

- `_build_system_prompt()` - 构建结构化的评估提示词，处理首次/迭代检验逻辑
- `_parse_response()` - 解析 Gemini 返回的 JSON 格式结果，并校验 passed 状态
- `_format_log()` - 格式化完整的分析日志，便于调试

## 注意事项

1. **输入模式选择**:
   - **VIDEO 模式**: 直接连接视频节点，推荐用于完整视频检验
   - **IMAGE 模式**: 连接提取的帧序列，支持细粒度的帧采样控制
   - 两种模式不能同时使用，至少提供其中之一

2. **视频帧数量**: 建议输入 8-32 帧
   - 太少无法评估连贯性
   - 太多会增加 API 成本和处理时间
   - VIDEO 模式使用全部帧，IMAGE 模式可通过 `select_every_nth` 降采样

3. **API 成本**: 每次检验会发送所有帧的图像数据，注意控制调用频率

4. **温度设置**: 建议使用低温度（0.1-0.3）以保持评估的一致性和客观性

5. **阈值设置**: 根据实际需求调整 `pass_threshold`，过高可能导致无限循环

6. **提示词质量**: `evaluation_criteria` 越具体，评估结果越精准

7. **首次检验**: 第一次运行时不要连接 `prev_score` 和 `prev_feedback`

8. **提供商选择**: 支持 Gemini（默认）、OpenAI、Claude 三种提供商，请确保 API 密钥和 base_url 对应

## 错误处理

节点会捕获并清晰提示以下错误：
- API 密钥为空或无效
- 模型名称为空
- 用户提示词或评估标准为空
- 图像输入为空
- 网络请求失败
- Gemini 返回的 JSON 格式无效
- 响应中缺少必需字段（score、passed、feedback）

## 日志格式

`log_text` 输出的完整日志格式：

```
=== Gemini Video Inspector Log ===

[System Prompt]
你是一个专业的视频质量检验 AI...
[完整的评估提示词]

[Gemini Response]
{"score": 8.5, "passed": true, "feedback": "..."}

[Parsed Result]
- Score: 8.50
- Pass Threshold: 8.00
- Passed: True
- Feedback: [具体反馈内容]

================================
```

方便复制粘贴到外部工具进行分析。

## 相关文件

- 实现代码: [gemini_video_inspector.py](gemini_video_inspector.py)
- 项目主页: [README.md](README.md)
