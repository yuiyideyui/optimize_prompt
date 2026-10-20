# Telegram Bot 快速启动指南

## Windows 用户 - 使用 ComfyUI 自带 Python

### 第一步：配置 Bot（只需一次）

打开 **PowerShell** 或 **CMD**，进入脚本目录：

```powershell
# PowerShell
cd D:\ComfyUI_windows_portable\ComfyUI\custom_nodes\my_comfyui\telegram_bot
```

或

```cmd
# CMD
cd D:\ComfyUI_windows_portable\ComfyUI\custom_nodes\my_comfyui\telegram_bot
```

然后运行配置命令（替换成你的 Token 和 User ID）：

#### 方式 A：使用 PowerShell 脚本

```powershell
cd D:\ComfyUI_windows_portable\ComfyUI\custom_nodes\my_comfyui
D:\ComfyUI_windows_portable\python_embeded\python.exe telegram_bot/startup.py --mode polling
```

#### 方式 B：使用 Batch 脚本

```cmd
run_bot.bat --token "123456789:ABCdefGHIjklmnoPQRstuvWXYZ" --user_id "987654321"
```

#### 方式 C：直接使用 Python

```powershell
D:\ComfyUI_windows_portable\python_embeded\python.exe -m telegram_bot.startup --token "YOUR_TOKEN" --user_id "YOUR_ID"
```

### 第二步：启动 Bot

配置成功后，每次启动都只需运行：

#### PowerShell

```powershell
.\run_bot.ps1
```

#### CMD / Batch

```cmd
run_bot.bat
```

#### 或直接 Python

```powershell
D:\ComfyUI_windows_portable\python_embeded\python.exe -m telegram_bot.startup
```

### 验证启动成功

你应该看到：

```
✅ Telegram Bot 已配置
   Bot Token: 123456789:ABCdefGH...
   User ID: 987654321

🚀 启动 Webhook 模式，端口 8765
   本地地址: http://localhost:8765/telegram
   
   ✅ Webhook 服务器已启动
   等待 Telegram 回调...
```

**按 Ctrl+C 停止 Bot**

## 获取 Token 和 User ID

### 1. 获取 Bot Token

1. 打开 Telegram，搜索 `@BotFather`
2. 发送 `/newbot`
3. 输入 bot 名称（例如：`ComfyUI_VideoReviewer`）
4. 复制返回的 **Token**

### 2. 获取 User ID

1. 在 Telegram 中搜索 `@userinfobot`
2. 发送任意消息
3. 复制返回的 **ID**

## 配置文件位置

配置自动保存到：

```
C:\Users\<你的用户名>\.comfyui_telegram_config.json
```

格式：
```json
{
  "bot_token": "123456789:ABCdefGHIjklmnoPQRstuvWXYZ",
  "user_id": "987654321",
  "webhook_url": ""
}
```

可以直接编辑此文件来修改配置。

## 常用命令

### 启动 Bot（监听端口 8765）

```powershell
.\run_bot.ps1
```

### 启动 Bot（自定义端口）

```powershell
.\run_bot.ps1 -WebhookPort 9000
```

### 启动 Bot 并设置 Webhook URL（用于远程部署）

```powershell
.\run_bot.ps1 -WebhookUrl "https://your-domain.com/telegram"
```

### 查看配置

```powershell
cat $env:USERPROFILE\.comfyui_telegram_config.json
```

### 查看审核结果

```powershell
cat $env:USERPROFILE\.comfyui_telegram_reviews\tasks.json
```

## 故障排查

### "Python 不存在"

检查 Python 路径是否正确：
```powershell
Test-Path "D:\ComfyUI_windows_portable\python_embeded\python.exe"
```

### "ModuleNotFoundError: telegram_bot"

确保当前目录正确：
```powershell
pwd  # 应该是 .../my_comfyui/telegram_bot
```

### "端口被占用"

使用不同的端口：
```powershell
.\run_bot.ps1 -WebhookPort 9000
```

### Bot 启动后没有反应

检查是否成功启动了服务器，应该看到：
```
✅ Webhook 服务器已启动
等待 Telegram 回调...
```

## 后台运行（可选）

### 使用 Windows 任务计划程序

1. 打开任务计划程序
2. 创建基本任务
3. 触发器：系统启动时
4. 操作：启动程序
   - 程序/脚本：`powershell.exe`
   - 参数：`-WindowStyle Hidden -File "D:\ComfyUI_windows_portable\ComfyUI\custom_nodes\my_comfyui\telegram_bot\run_bot.ps1"`
   - 起始目录：`D:\ComfyUI_windows_portable\ComfyUI\custom_nodes\my_comfyui\telegram_bot`

### 使用 nssm（非侵入式服务管理器）

```powershell
# 安装 nssm
choco install nssm

# 注册为服务
nssm install ComfyUITelegramBot `
  "D:\ComfyUI_windows_portable\python_embeded\python.exe" `
  "-m telegram_bot.startup"

# 启动服务
nssm start ComfyUITelegramBot

# 查看状态
nssm status ComfyUITelegramBot

# 停止服务
nssm stop ComfyUITelegramBot
```

## 完整工作流

1. **启动 Bot**
   ```powershell
   .\run_bot.ps1
   ```

2. **运行 ComfyUI 工作流**
   - Gemini 接口正常 → 正常返回评分
   - Gemini 接口失败 4 次 → 发送到 Telegram

3. **你收到 Telegram 消息**
   - 看到视频、提示词、错误信息
   - 三个按钮：✅ 通过 / ❌ 拒绝 / 📝 自定义

4. **点击审核按钮**
   - Bot 处理你的反馈
   - 保存结果到本地数据库
   - ComfyUI 工作流继续

## 下一步

1. ✅ 配置 Token 和 User ID
2. ✅ 启动 Bot：`.\run_bot.ps1`
3. ✅ 测试工作流（故意让 Gemini 失败）
4. ✅ 在 Telegram 中审核视频

有问题？查看 `TELEGRAM_SETUP.md` 获取完整指南。
