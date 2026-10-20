# Telegram 人工审核 Bot 配置指南

当 Gemini 视频检查器接口连续失败 4 次后，系统会自动将视频上传到 Telegram，由你进行人工审核。审核完成后，bot 会通过 webhook 将结果回传给 ComfyUI 工作流。

## 一、创建 Telegram Bot

### 1.1 获取 Bot Token

1. 打开 Telegram，搜索 `@BotFather`
2. 发送 `/newbot` 命令
3. 按照提示输入 bot 名称（例如：`ComfyUI_VideoReviewer`）
4. 得到 **Bot Token**（例如：`123456789:ABCdefGHIjklmnoPQRstuvWXYZ`）

**保存此 Token！** 稍后会用到。

### 1.2 获取你的 User ID

1. 在 Telegram 中搜索 `@userinfobot`
2. 发送任意消息
3. Bot 会回复你的 **User ID**（一个纯数字，例如：`987654321`）

**保存此 ID！** 这样 bot 才能将审核消息发送给你。

## 二、配置 Telegram Bot

### 2.1 保存配置

运行以下命令来保存 Bot Token 和 User ID：

```bash
cd ComfyUI\custom_nodes\my_comfyui
python -m telegram_bot.startup --token 123456789:ABCdefGHIjklmnoPQRstuvWXYZ --user_id 987654321
```

**Windows PowerShell:**
```powershell
cd ComfyUI\custom_nodes\my_comfyui
python -m telegram_bot.startup --token "123456789:ABCdefGHIjklmnoPQRstuvWXYZ" --user_id "987654321"
```

或者设置环境变量：

**Windows (PowerShell):**
```powershell
$env:TELEGRAM_BOT_TOKEN = "123456789:ABCdefGHIjklmnoPQRstuvWXYZ"
$env:TELEGRAM_USER_ID = "987654321"
python -m telegram_bot.startup
```

**Linux/macOS:**
```bash
export TELEGRAM_BOT_TOKEN="123456789:ABCdefGHIjklmnoPQRstuvWXYZ"
export TELEGRAM_USER_ID="987654321"
python -m telegram_bot.startup
```

### 2.2 验证配置

命令执行后，你应该看到：
```
✅ Telegram Bot 已配置
   Bot Token: 123456789:ABCdefGH...
   User ID: 987654321
```

配置文件保存在：`~/.comfyui_telegram_config.json`

## 三、启动 Telegram Bot

### 3.1 Webhook 模式（推荐）

Webhook 模式使用 HTTP 服务器来接收 Telegram 回调，实时响应。

**启动方式：**

```bash
python -m telegram_bot.startup --webhook_port 8765
```

**你会看到：**
```
🚀 启动 Webhook 模式，端口 8765
   本地地址: http://localhost:8765/telegram
   
   如果需要外网访问，请使用 ngrok 或其他反向代理:
   ngrok http 8765
```

### 3.2 本地测试（无需外网）

如果 ComfyUI 和 Telegram bot 都在同一台机器上：

1. 启动 bot：
```bash
python -m telegram_bot.startup --webhook_port 8765
```

2. Bot 会将审核结果保存到本地数据库：
```
~/.comfyui_telegram_reviews/tasks.json
```

3. ComfyUI 可以通过查询本地数据库获取审核结果。

### 3.3 远程部署（需要外网访问）

如果 ComfyUI 在远程服务器或需要从外网访问：

**方法 A: 使用 ngrok（简单快速）**

1. 下载 [ngrok](https://ngrok.com/download)
2. 启动 bot：
```bash
python -m telegram_bot.startup --webhook_port 8765
```

3. 在另一个终端启动 ngrok：
```bash
ngrok http 8765
```

4. ngrok 会显示公网 URL（例如：`https://1234-56-78-90-12.ngrok.io`）

5. 在 Telegram 中设置 webhook：
```bash
curl -X POST https://api.telegram.org/bot<BOT_TOKEN>/setWebhook \
  -H "Content-Type: application/json" \
  -d '{"url":"https://1234-56-78-90-12.ngrok.io/telegram"}'
```

替换 `<BOT_TOKEN>` 和 URL。

**方法 B: 使用反向代理（Nginx）**

如果有自己的服务器和域名，可以用 Nginx 反向代理：

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;
    
    # SSL 证书配置...
    
    location /telegram {
        proxy_pass http://localhost:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

然后在 Telegram 中设置 webhook：
```bash
curl -X POST https://api.telegram.org/bot<BOT_TOKEN>/setWebhook \
  -H "Content-Type: application/json" \
  -d '{"url":"https://your-domain.com/telegram"}'
```

### 3.4 查询审核结果

本地审核结果存储在：
```
~/.comfyui_telegram_reviews/tasks.json
```

每个任务包含：
```json
{
  "task_id": "a1b2c3d4",
  "status": "approved",
  "score": 8.5,
  "feedback": "画面清晰，动作连贯",
  "h3_optimize_prompt": "",
  "reviewed_at": "2026-10-20T16:30:00"
}
```

## 四、工作流

### 4.1 正常流程

1. **Gemini 接口调用**
   - 正常调用 → 返回评分结果
   - 偶发错误 → 重试（最多 4 次）

2. **接口持续失败**
   - 第 4 次失败后 → 发送视频到 Telegram
   - ComfyUI 返回"待审核"状态

3. **你进行人工审核**
   - 在 Telegram 中收到视频和按钮
   - 点击 ✅ 通过 或 ❌ 拒绝

4. **结果回传**
   - Bot 接收你的审核结果
   - 保存到本地数据库
   - 通过 webhook 通知 ComfyUI（如已配置）

### 4.2 按钮说明

**✅ 通过（评分 8.5）**
- 视频通过审核，评分为 8.5
- 工作流继续执行

**❌ 拒绝**
- 视频不符合要求
- 工作流停止，返回错误

**📝 自定义评分**
- 可以设置自定义评分和反馈意见
- 目前支持快速按钮，完整反馈需要未来扩展

## 五、配置 Webhook 回传

如果需要 bot 在审核完成后主动通知 ComfyUI，需要配置 ComfyUI webhook：

```bash
python -m telegram_bot.startup \
  --webhook_url "http://localhost:8188/api/telegram/review_result" \
  --webhook_port 8765
```

ComfyUI 会收到的 webhook 数据：
```json
{
  "type": "telegram_review_result",
  "task_id": "a1b2c3d4",
  "status": "approved",
  "score": 8.5,
  "feedback": "画面清晰，动作连贯",
  "h3_optimize_prompt": ""
}
```

ComfyUI 可以监听此 webhook 来自动继续工作流。

## 六、故障排查

### 问题：没有收到视频消息

**可能原因：**
1. Bot Token 或 User ID 配置错误
   - 检查：`cat ~/.comfyui_telegram_config.json`
   - 重新配置：`python -m telegram_bot.startup --token XXX --user_id YYY`

2. Bot 没有启动
   - 确保执行了 `python -m telegram_bot.startup`

3. Webhook 未设置（远程部署时）
   - 确保执行了 `setWebhook` 命令

### 问题：无法连接到 Telegram 服务

**可能原因：**
1. 网络连接问题
   - 检查网络：`ping api.telegram.org`

2. 防火墙阻止
   - 允许访问 api.telegram.org:443

### 问题：Bot 启动后立即退出

**可能原因：**
1. 缺少依赖
   - 确保安装了 Python 标准库
   - 需要的包：urllib、json、io 等（都是标准库）

2. 端口被占用
   - 尝试用不同的端口：`--webhook_port 9000`

### 问题：审核结果没有回传给 ComfyUI

**可能原因：**
1. Webhook URL 配置错误
   - 检查：`cat ~/.comfyui_telegram_config.json`
   - 确认 ComfyUI 的 webhook 端点是否正确

2. 防火墙阻止
   - 允许从 bot 服务器访问 ComfyUI

3. ComfyUI 没有实现 webhook 处理
   - 需要在 ComfyUI 中添加 webhook 路由来处理审核结果

## 七、进阶配置

### 7.1 自定义评分

目前支持预设按钮（通过/拒绝），未来可以支持输入自定义评分：

```
/review_custom 7.5 画面有些闪烁，建议调整参数
```

### 7.2 多用户审核

如果多个人进行审核，可以配置多个 User ID：

```bash
python -m telegram_bot.startup \
  --token "YOUR_BOT_TOKEN" \
  --user_id "123456789" \
  # 注：当前版本仅支持单个 User ID，多用户需要扩展
```

### 7.3 审核历史

所有审核记录保存在：
```
~/.comfyui_telegram_reviews/tasks.json
```

可以查看：
- 哪些视频已审核
- 审核时间和评分
- 反馈意见

## 八、示例工作流

假设你的工作流使用 Gemini Video Inspector，接口失败 4 次后会自动：

1. **触发 Telegram 发送**
```python
# gemini_video_inspector.py 内部自动处理
telegram_sender = TelegramSender()
telegram_sender.send_video_for_review(
    task_id="a1b2c3d4",
    video_file="/path/to/video.mp4",
    system_prompt="...",
    user_prompt="...",
    error_message="API request failed (500): ..."
)
```

2. **你收到 Telegram 消息**
- 视频文件
- 原始提示词预览
- 错误信息
- 三个按钮：通过、拒绝、自定义

3. **你点击 ✅ 通过**
- Bot 保存审核结果到 `~/.comfyui_telegram_reviews/tasks.json`
- 发送 webhook 通知 ComfyUI（如已配置）

4. **ComfyUI 处理结果**
- 获取审核评分和反馈
- 继续或重新执行工作流

## 九、常见问题

**Q: Bot 需要一直运行吗？**
A: 是的，除非使用 polling 模式（实验性）。建议用 systemd/supervisor 等工具让 bot 后台运行。

**Q: 视频安全吗？**
A: 视频直接发送到 Telegram 服务器，需要信任 Telegram 的安全性。机密内容建议不要上传。

**Q: 能否自定义审核按钮？**
A: 当前支持预设按钮。可以通过修改 `telegram_sender.py` 中的 `_send_review_buttons` 函数来自定义。

**Q: 能否批量审核？**
A: 当前支持逐个审核。未来可以扩展支持批量操作。

## 十、下一步

1. 按照上述步骤配置好 Bot
2. 启动 `python -m telegram_bot.startup`
3. 测试：运行 ComfyUI 工作流，故意让 Gemini 接口失败，查看是否收到 Telegram 消息
4. 点击审核按钮，验证结果是否正确保存

有问题？检查日志输出或参考故障排查部分。
