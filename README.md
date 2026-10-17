# ComfyUI AI Nodes

这是一个 ComfyUI 自定义节点包，支持通过 Gemini 和 Claude API 进行文本生成和视频质检。

## 节点列表

### 1. [Gemini / Claude Text](gemini_claude_text.md)
**多模态文本生成节点**

- 支持 Gemini 和 Claude 两种 AI 提供商
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
├── README.md                        # 项目主文档（当前文件）
├── gemini_claude_text.md            # 文本生成节点详细文档
└── gemini_video_inspector.md        # 视频质检节点详细文档
```

## 依赖要求

- Python 3.8+
- ComfyUI
- Pillow (PIL)
- 标准库: `json`, `urllib`, `base64`, `io`

## API 密钥获取

### Gemini API
访问 [Google AI Studio](https://makersuite.google.com/app/apikey) 获取免费 API 密钥。

### Claude API
访问 [Anthropic Console](https://console.anthropic.com/) 注册并获取 API 密钥。

## 常见问题

**Q: 节点没有出现在菜单中？**  
A: 确保文件夹位置正确，重启 ComfyUI。检查控制台是否有报错信息。

**Q: API 请求超时？**  
A: 增加 `timeout` 参数值，或检查网络连接和 API 密钥是否有效。

**Q: 视频质检一直不通过？**  
A: 降低 `pass_threshold` 阈值，或检查 `evaluation_criteria` 是否过于严格。

**Q: 成本太高？**  
A: 使用 flash 模型（如 `gemini-2.0-flash-exp`），减少视频帧数，降低 `max_tokens`。

## 更新日志

### v1.0.0 (2026-10-17)
- ✨ 新增 Gemini / Claude Text 节点
- ✨ 新增 Gemini Video Inspector 节点
- 📝 完善文档结构

## 许可证

本项目遵循 MIT 许可证。

## 贡献

欢迎提交 Issue 和 Pull Request！

---

**提示**: 点击每个节点标题查看详细的使用文档和示例。
