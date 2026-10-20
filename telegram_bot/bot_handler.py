"""Telegram Bot 处理器 - 接收用户审核反馈并回传结果"""

import json
import urllib.request
import urllib.parse
from typing import Optional, Callable
from .config import TelegramConfig
from .review_manager import ReviewManager
from .comfyui_client import ComfyUIClient


class TelegramBotHandler:
    """处理 Telegram bot 消息和回调"""

    def __init__(self):
        self.config = TelegramConfig()
        self.review_manager = ReviewManager()
        self.api_base = "https://api.telegram.org/bot"

    def handle_update(self, update: dict, webhook_callback: Optional[Callable] = None) -> bool:
        """处理任何类型的 update（消息或回调）"""
        # 优先处理回调查询
        if self.handle_callback_query(update, webhook_callback):
            return True
        # 然后处理消息
        if self.handle_message(update):
            return True
        return False

    def handle_callback_query(
        self, update: dict, webhook_callback: Optional[Callable] = None
    ) -> bool:
        """
        处理 Telegram 按钮回调

        Args:
            update: Telegram Update 对象
            webhook_callback: 回调函数，用于通知 ComfyUI 审核结果

        Returns:
            是否处理成功
        """
        if "callback_query" not in update:
            return False

        callback_query = update["callback_query"]
        callback_id = callback_query.get("id")
        callback_data = callback_query.get("data", "")
        message = callback_query.get("message", {})
        message_id = str(message.get("message_id", ""))

        print(f"[TelegramBotHandler] 收到回调: {callback_data}, message_id={message_id}")

        # 获取任务
        task = self.review_manager.get_task_by_message_id(message_id)
        if not task:
            print(f"[TelegramBotHandler] 找不到对应的任务")
            self._answer_callback_query(callback_id, "❌ 任务未找到")
            return False

        # 解析回调数据
        parts = callback_data.split("_", 1)
        action = parts[0]  # approve, reject, custom

        try:
            if action == "approve":
                score = float(parts[1]) if len(parts) > 1 else 8.5
                return self._handle_approve(
                    task, score, callback_id, message_id, webhook_callback
                )
            elif action == "reject":
                return self._handle_reject(
                    task, callback_id, message_id, webhook_callback
                )
            elif action == "custom":
                return self._handle_custom(task, callback_id, message_id)
            else:
                self._answer_callback_query(callback_id, "❌ 未知操作")
                return False

        except Exception as e:
            print(f"[TelegramBotHandler] 处理回调异常: {e}")
            self._answer_callback_query(callback_id, f"❌ 错误: {str(e)[:50]}")
            return False

    def _handle_approve(
        self,
        task,
        score: float,
        callback_id: str,
        message_id: str,
        webhook_callback: Optional[Callable],
    ) -> bool:
        """处理通过审核"""
        feedback = f"已通过人工审核，评分 {score:.2f} 分。"

        # 更新任务
        task = self.review_manager.approve_task(
            task.task_id, score=score, feedback=feedback
        )

        # 回复用户
        self._answer_callback_query(callback_id, f"✅ 已批准，评分 {score:.2f}")

        # 调用 webhook
        if webhook_callback:
            webhook_callback(task)
        else:
            self._send_webhook(task)

        print(f"[TelegramBotHandler] 任务已批准: {task.task_id}")
        return True

    def _handle_reject(
        self,
        task,
        callback_id: str,
        message_id: str,
        webhook_callback: Optional[Callable],
    ) -> bool:
        """处理拒绝审核"""
        # 更新任务
        task = self.review_manager.reject_task(
            task.task_id, feedback="已拒绝人工审核，请重新生成视频。"
        )

        # 回复用户
        self._answer_callback_query(callback_id, "❌ 已拒绝")

        # 调用 webhook
        if webhook_callback:
            webhook_callback(task)
        else:
            self._send_webhook(task)

        print(f"[TelegramBotHandler] 任务已拒绝: {task.task_id}")
        return True

    def _handle_custom(
        self, task, callback_id: str, message_id: str
    ) -> bool:
        """处理自定义评分（暂时只支持通过按钮快速操作）"""
        self._answer_callback_query(
            callback_id,
            "📝 请回复: /review_custom <score> <feedback>\n例: /review_custom 7.5 画面有些闪烁",
        )
        return False

    def _answer_callback_query(self, callback_id: str, text: str) -> None:
        """回复 Telegram 的回调查询"""
        if not self.config.is_configured():
            return

        bot_token = self.config.get_bot_token()
        url = f"{self.api_base}{bot_token}/answerCallbackQuery"

        payload = {
            "callback_query_id": callback_id,
            "text": text,
            "show_alert": False,
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
                if not result.get("ok"):
                    print(f"[TelegramBotHandler] 回复失败: {result.get('description')}")
        except Exception as e:
            print(f"[TelegramBotHandler] 回复异常: {e}")

    def _send_webhook(self, task) -> None:
        """将审核结果通过 webhook 发送给 ComfyUI"""
        webhook_url = self.config.get_webhook_url()
        if not webhook_url:
            print("[TelegramBotHandler] webhook URL 未配置，无法回传结果")
            return

        payload = {
            "type": "telegram_review_result",
            "task_id": task.task_id,
            "status": task.status,
            "score": task.score,
            "feedback": task.feedback,
            "h3_optimize_prompt": task.h3_optimize_prompt,
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                print(
                    f"[TelegramBotHandler] webhook 已发送，状态码: {response.status}"
                )
        except Exception as e:
            print(f"[TelegramBotHandler] webhook 发送失败: {e}")

    def handle_message(self, update: dict) -> bool:
        """处理文本消息和命令"""
        if "message" not in update:
            return False

        message = update["message"]
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "").strip()

        if not chat_id or not text:
            return False

        print(f"[TelegramBotHandler] 收到消息: {text}")

        # 处理命令
        if text.startswith("/"):
            return self._handle_command(chat_id, text)

        return False

    def _handle_command(self, chat_id: str, text: str) -> bool:
        """处理 slash 命令"""
        parts = text.split()
        command = parts[0].lower()

        if command == "/start":
            self._send_menu(chat_id)
            return True
        elif command == "/status":
            self._send_status(chat_id)
            return True
        elif command == "/tasks":
            self._send_tasks(chat_id)
            return True
        elif command == "/help":
            self._send_help(chat_id)
            return True
        else:
            self._send_message(chat_id, f"Unknown command: {command}\nType /help for help")
            return True

    def _send_menu(self, chat_id: str) -> None:
        """发送主菜单"""
        menu_text = """Welcome to ComfyUI Telegram Bot!

This bot helps you manage video review tasks for ComfyUI.

Available commands:
/status - Check ComfyUI status
/tasks - View pending review tasks
/help - Show this help message

When a video fails Gemini review, you'll receive it here for manual approval."""

        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "Status", "callback_data": "cmd_status"},
                    {"text": "Tasks", "callback_data": "cmd_tasks"},
                ],
                [
                    {"text": "Help", "callback_data": "cmd_help"},
                ],
            ]
        }

        self._send_message_with_keyboard(chat_id, menu_text, keyboard)

    def _send_status(self, chat_id: str) -> None:
        """发送 ComfyUI 状态"""
        client = ComfyUIClient()

        if not client.is_available():
            self._send_message(
                chat_id,
                "Error: Cannot connect to ComfyUI at http://localhost:8188\n\n"
                "Make sure ComfyUI is running and accessible.",
            )
            return

        status_text = client.format_status_message()
        self._send_message(chat_id, status_text, parse_mode="Markdown")

    def _send_tasks(self, chat_id: str) -> None:
        """发送待审核任务列表"""
        pending_tasks = self.review_manager.get_pending_tasks()

        if not pending_tasks:
            self._send_message(chat_id, "No pending review tasks")
            return

        lines = ["*Pending Review Tasks:*\n"]
        for task in pending_tasks[:10]:  # 最多显示10个
            lines.append(f"• Task ID: `{task.task_id}`")
            lines.append(f"  Status: {task.status}")
            if task.created_at:
                lines.append(f"  Created: {task.created_at[:10]}")
            lines.append("")

        if len(pending_tasks) > 10:
            lines.append(f"... and {len(pending_tasks) - 10} more tasks")

        text = "\n".join(lines)
        self._send_message(chat_id, text, parse_mode="Markdown")

    def _send_help(self, chat_id: str) -> None:
        """发送帮助信息"""
        help_text = """*ComfyUI Bot Help*

*Commands:*
/start - Show main menu
/status - Check ComfyUI system status (RAM, VRAM, queue)
/tasks - List pending review tasks
/help - Show this help message

*Review Workflow:*
1. Gemini video review fails 4 times
2. Video is sent to you for manual review
3. You see video + buttons (Approve / Reject)
4. Click button to submit review
5. Bot saves result and notifies ComfyUI

*Buttons:*
✅ Approve (Score 8.5) - Video passes
❌ Reject - Video fails
📝 Custom - Enter custom score"""

        self._send_message(chat_id, help_text, parse_mode="Markdown")

    def _send_message(
        self, chat_id: str, text: str, parse_mode: str = "HTML"
    ) -> None:
        """发送纯文本消息"""
        if not self.config.is_configured():
            return

        bot_token = self.config.get_bot_token()
        url = f"{self.api_base}{bot_token}/sendMessage"

        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
                if not result.get("ok"):
                    print(
                        f"[TelegramBotHandler] 发送消息失败: {result.get('description')}"
                    )
        except Exception as e:
            print(f"[TelegramBotHandler] 发送消息异常: {e}")

    def _send_message_with_keyboard(
        self, chat_id: str, text: str, keyboard: dict
    ) -> None:
        """发送带键盘的消息"""
        if not self.config.is_configured():
            return

        bot_token = self.config.get_bot_token()
        url = f"{self.api_base}{bot_token}/sendMessage"

        payload = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": keyboard,
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
                if not result.get("ok"):
                    print(
                        f"[TelegramBotHandler] 发送消息失败: {result.get('description')}"
                    )
        except Exception as e:
            print(f"[TelegramBotHandler] 发送消息异常: {e}")


def create_telegram_webhook_handler(webhook_port: int = 8765):
    """
    创建一个简单的 HTTP 服务器来接收 Telegram webhook

    使用示例:
        handler = create_telegram_webhook_handler()
        # 在 Telegram 中设置 webhook:
        # curl -X POST https://api.telegram.org/bot<TOKEN>/setWebhook \\
        #   -H "Content-Type: application/json" \\
        #   -d '{"url":"http://your-public-ip:8765/telegram"}'
    """
    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import threading

        bot_handler = TelegramBotHandler()

        class TelegramWebhookHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path == "/telegram":
                    content_length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(content_length).decode("utf-8")

                    try:
                        update = json.loads(body)
                        bot_handler.handle_update(update)
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"ok": True}).encode())
                    except Exception as e:
                        print(f"[TelegramWebhook] 处理异常: {e}")
                        self.send_response(500)
                        self.end_headers()
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                # 抑制默认日志
                pass

        server = HTTPServer(("0.0.0.0", webhook_port), TelegramWebhookHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        print(f"[TelegramWebhook] HTTP 服务器启动在 0.0.0.0:{webhook_port}")
        return server

    except Exception as e:
        print(f"[TelegramWebhook] 启动失败: {e}")
        return None
