# Gemini / Claude / Gemma Text 节点

文本生成节点，支持通过 Gemini、Gemma 和 Claude API 进行多模态文本生成。

## 节点信息

- **节点名称**: `GeminiClaudeText`
- **显示名称**: Gemini / Claude Text
- **分类**: `my_comfyui/AI`
- **实现文件**: [gemini_claude_text.py](gemini_claude_text.py)

## 功能简述

支持 Gemini、Gemma 和 Claude 三种 AI 提供商，可处理纯文本或图文混合输入，生成高质量文本内容。适用于图像描述、内容分析、创意生成等场景。

## 输入参数

### 必需参数

| 参数名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `provider` | Combo | `gemini` | AI 提供商选择：`gemini`、`gemma`、`claude` 或 `openai` |
| `user_prompt` | STRING (multiline) | `""` | 用户提示词，描述你想让 AI 做什么 |
| `systems_prompt` | STRING (multiline) | `""` | 系统提示词，设定 AI 的角色和行为规范 |
| `api_key` | STRING | `""` | API 密钥（必填） |
| `model` | STRING (multiline) | `gemini-2.5-flash` | 模型名称；可每行填写一个备用模型，或用逗号分隔，按顺序尝试 |
| `base_url` | STRING | `https://generativelanguage.googleapis.com` | API 基础 URL，可自定义代理地址 |
| `temperature` | FLOAT | `0.7` | 温度参数（0.0-2.0），控制输出的随机性，越高越有创意 |
| `top_p` | FLOAT | `0.95` | Top-p 采样参数（0.0-1.0），核采样阈值 |
| `max_tokens` | INT | `2048` | 最大输出 token 数（1-65536） |
| `timeout` | INT | `120` | 请求超时时间（秒，1-600） |

### 可选参数

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `images` | IMAGE | 输入图像（可传入多张图片作为上下文） |

## 输出

| 输出名 | 类型 | 说明 |
|--------|------|------|
| `text` | STRING | 生成的文本内容 |

## 使用示例

### 纯文本生成
1. 添加节点到工作流
2. 选择 `provider` 为 `gemini`、`gemma` 或 `claude`
3. 填入 `api_key`
4. 在 `user_prompt` 中输入提示词，如："写一首关于春天的诗"
5. 执行工作流

### 图文混合生成
1. 连接图像输入到 `images` 接口
2. 在 `user_prompt` 中输入如："描述这张图片的内容"
3. 执行工作流，AI 会基于图片内容生成文本

### 使用系统提示词
- 在 `systems_prompt` 中设定角色，如："你是一个专业的摄影评论家，请用专业术语分析图片"
- AI 的回答会遵循这个角色设定

## 提供商差异

### Gemini
- **默认 URL**: `https://generativelanguage.googleapis.com`
- **推荐模型**: `gemini-2.0-flash-exp`、`gemini-2.5-flash`、`gemini-2.5-pro`
- **特点**: 速度快，对图像理解能力强

### Gemma
- **默认 URL**: `https://generativelanguage.googleapis.com`
- **推荐模型**: `gemma-2-27b`、`gemma-2-9b`、`gemma-2-2b`
- **特点**: 开源轻量级模型，成本低，适合本地部署或小规模任务
- **兼容性**: 使用与 Gemini 相同的 Google Generative Language API

### Claude
- **默认 URL**: 需要使用兼容的 API 端点
- **推荐模型**: `claude-3-5-sonnet-20241022`、`claude-3-opus-20240229`
- **特点**: 文本质量高，逻辑推理能力强

### OpenAI
- **默认 URL**: `https://api.openai.com/v1`
- **推荐模型**: `gpt-4o`、`gpt-4-turbo`、`gpt-3.5-turbo`
- **特点**: 功能全面，支持多种模型选择

## 核心实现函数

- `_post_json()` - 发送 JSON POST 请求的通用方法
- `_image_parts()` - 将图像张量转换为 base64 编码
- `_claude_url()` - 构建 Claude API URL
- `_gemini_url()` - 构建 Gemini/Gemma API URL
- `_gemini_text()` - 从 Gemini/Gemma 响应中提取文本
- `_claude_text()` - 从 Claude 响应中提取文本
- `_openai_text()` - 从 OpenAI 响应中提取文本

## 注意事项

1. **API 密钥安全**: 不要在公共工作流中硬编码 API 密钥
2. **超时设置**: 复杂任务建议增加 `timeout` 值
3. **Token 限制**: 注意 `max_tokens` 设置，避免输出被截断
4. **图像数量**: 多图场景会消耗更多 token，注意成本控制
5. **Gemma 兼容性**: Gemma 模型通过 Google API 调用，需要有效的 Google API 密钥
6. **模型降级**: 每个模型最多请求 4 次，失败后切换列表中的下一个模型
7. **Telegram 兜底**: 所有模型都失败后，会将提示词和输入图片发送到已配置的 Telegram Bot，并等待对该消息的直接回复；回复内容作为节点输出。Bot 必须运行并能接收 Telegram 更新。等待回复最多 1 小时，超时会报错。API 密钥不会发送到 Telegram

## 错误处理

节点会捕获以下错误并给出清晰提示：
- API 密钥为空或无效
- 模型名称为空
- 网络请求失败
- API 返回非 JSON 格式
- 响应中缺少文本内容

## 相关文件

- 实现代码: [gemini_claude_text.py](gemini_claude_text.py)
- 项目主页: [README.md](README.md)

