# Telegram Bot 菜单和命令指南

## 可用命令

Bot 提供以下命令来查询和管理 ComfyUI 状态：

### `/start` - 主菜单
显示欢迎信息和快速访问按钮。

```
Welcome to ComfyUI Telegram Bot!

This bot helps you manage video review tasks for ComfyUI.

Available commands:
/status - Check ComfyUI status
/tasks - View pending review tasks
/help - Show this help message
```

**快速按钮：**
- 📊 Status
- 📋 Tasks  
- ❓ Help

### `/status` - 查看 ComfyUI 系统状态

实时显示 ComfyUI 的运行状态：

```
🖥️ ComfyUI Status

RAM: 8.5GB / 16.0GB
VRAM (GPU 0): 2048MB / 8192MB

Queue Status:
🔄 Running: 1
⏳ Pending: 3

Currently Executing:
• Task ID: 1234567890

Queued Tasks: 3
1. Task ID: 1234567891
2. Task ID: 1234567892
3. Task ID: 1234567893
```

**显示内容：**
- 系统内存使用 (RAM)
- 显卡内存使用 (VRAM)
- 当前运行中的任务
- 队列中等待的任务数量
- 队列中前 5 个任务的 ID

### `/tasks` - 查看待审核任务列表

显示所有正在等待你审核的任务：

```
Pending Review Tasks:

• Task ID: `abc12345`
  Status: pending
  Created: 2026-10-20

• Task ID: `def67890`
  Status: pending
  Created: 2026-10-20

... and 2 more tasks
```

**任务状态：**
- `pending` - 等待审核
- `approved` - 已批准
- `rejected` - 已拒绝

### `/help` - 获取帮助

显示完整的帮助信息和使用说明。

## 菜单按钮

### 主菜单按钮

在 `/start` 命令后显示：

```
[Status] [Tasks]
[Help]
```

点击按钮相当于执行对应命令。

### 审核视频按钮

当接收到待审核视频时显示：

```
[✅ Approve (Score 8.5)] [❌ Reject]
[📝 Custom]
```

**按钮说明：**

- **✅ Approve (Score 8.5)** - 批准视频，评分为 8.5
- **❌ Reject** - 拒绝视频
- **📝 Custom** - 输入自定义评分和反馈

## 自动消息

### 视频审核请求

当 Gemini 接口失败 4 次后，你会收到：

```
【待审核】任务 ID: abc12345

🎬 Video for Review

Task ID: `abc12345`
File: `video.mp4`

Original Prompt:
```
A beautiful anime girl with blonde hair...
```

Error Message:
```
API request failed (500): Service Unavailable
```

Please select:
✅ Approve - Score 8.5 (customizable)
❌ Reject - Does not meet requirements
📝 Custom - Enter specific score and feedback
```

**消息内容：**
- 任务 ID
- 视频文件（可下载）
- 原始生成提示词（前 500 字）
- 错误信息（前 300 字）
- 审核按钮

### 审核完成通知

你进行审核后会收到：

```
OK - Approved, Score 8.5
```

或

```
OK - Rejected
```

## 常见使用场景

### 场景 1：检查 ComfyUI 是否在运行

```
User: /status
Bot:  🖥️ ComfyUI Status
      RAM: 8.5GB / 16.0GB
      VRAM (GPU 0): 2048MB / 8192MB
      Queue Status: Running 1, Pending 3
```

### 场景 2：查看有多少个待审核任务

```
User: /tasks
Bot:  Pending Review Tasks: (显示列表)
```

### 场景 3：收到视频审核请求

```
Bot: 【待审核】任务 ID: abc12345
     (显示视频和按钮)

User: (点击 ✅ Approve 按钮)

Bot: OK - Approved, Score 8.5
```

## 工作流整合

### 完整的视频审核流程

1. **在 ComfyUI 中启动工作流**
   - Gemini Video Inspector 节点被调用
   - Gemini 接口偶发失败

2. **第 4 次失败时**
   - Video Inspector 捕获失败
   - 发送视频到 Telegram Bot
   - 返回"待审核"状态给工作流

3. **你收到 Telegram 消息**
   ```
   【待审核】任务 ID: task123
   (视频文件)
   [✅ 通过] [❌ 拒绝]
   ```

4. **你进行审核**
   - 点击 ✅ 通过 或 ❌ 拒绝
   - Bot 接收你的反馈

5. **结果保存和回传**
   - 审核结果保存到本地数据库
   - 通过 Webhook 通知 ComfyUI（可选）
   - ComfyUI 工作流继续执行

6. **查询审核历史**
   ```
   User: /tasks
   Bot: (显示已审核的任务)
   ```

## 状态查询说明

### RAM 显示

```
RAM: 8.5GB / 16.0GB
```

- 已使用: 8.5GB
- 总容量: 16.0GB

### VRAM 显示

```
VRAM (GPU 0): 2048MB / 8192MB
```

- 显卡: GPU 0
- 已使用: 2048MB
- 总容量: 8192MB

如果有多个显卡，会分别显示每个的使用情况。

### 队列状态

```
Queue Status:
🔄 Running: 1      # 当前执行的任务数
⏳ Pending: 3       # 等待执行的任务数
```

### 执行任务详情

```
Currently Executing:
• Task ID: 1234567890

Queued Tasks: 3
1. Task ID: 1234567891
2. Task ID: 1234567892
3. Task ID: 1234567893
... and 0 more
```

## 错误处理

### ComfyUI 未连接

```
User: /status

Bot: Error: Cannot connect to ComfyUI at http://localhost:8188
     Make sure ComfyUI is running and accessible.
```

**解决方法：**
1. 确保 ComfyUI 已启动
2. 确保 ComfyUI 监听在 `http://localhost:8188`
3. 检查防火墙设置

### 未知命令

```
User: /unknown

Bot: Unknown command: /unknown
     Type /help for help
```

## 高级用法

### 自定义 ComfyUI 地址

如果 ComfyUI 不在本地或不在默认端口，修改 `comfyui_client.py`：

```python
client = ComfyUIClient(base_url="http://192.168.1.100:8188")
```

### 获取更多队列信息

编辑 `comfyui_client.py` 中的 `format_status_message()` 函数来显示更多详情。

### 自定义菜单按钮

编辑 `_send_menu()` 函数中的 `keyboard` 变量来添加或修改按钮。

## 命令快速参考

| 命令 | 功能 |
|------|------|
| `/start` | 显示主菜单 |
| `/status` | 查看 ComfyUI 系统状态 |
| `/tasks` | 显示待审核任务列表 |
| `/help` | 显示帮助信息 |

## 下一步

1. ✅ 启动 Telegram Bot：`.\run_bot.ps1`
2. ✅ 在 Telegram 中找到你的 Bot
3. ✅ 发送 `/start` 查看菜单
4. ✅ 发送 `/status` 查看 ComfyUI 状态
5. ✅ 发送 `/help` 查看帮助

需要帮助？检查 `TELEGRAM_SETUP.md` 获取完整配置指南。
