"""发送视频和消息到 Telegram"""

import os
import json
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Optional
from .config import TelegramConfig
from .review_manager import ReviewManager


class TelegramSender:
    """负责向 Telegram 发送视频和消息"""

    def __init__(self):
        self.config = TelegramConfig()
        self.review_manager = ReviewManager()
        self.api_base = "https://api.telegram.org/bot"

    def send_video_for_review(
        self,
        task_id: str,
        video_file: str,
        system_prompt: str,
        user_prompt: str,
        error_message: str,
        max_retries: int = 3,
    ) -> Optional[str]:
        """
        发送视频到 Telegram 进行人工审核

        Args:
            task_id: 任务 ID
            video_file: 视频文件路径
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            error_message: 错误信息
            max_retries: 最大重试次数

        Returns:
            Telegram 消息 ID（成功）或 None（失败）
        """
        if not self.config.is_configured():
            print("[TelegramSender] Telegram 未配置，跳过发送")
            return None

        bot_token = self.config.get_bot_token()
        user_id = self.config.get_user_id()

        if not os.path.isfile(video_file):
            print(f"[TelegramSender] 视频文件不存在: {video_file}")
            return None

        # 创建审核任务记录
        task = self.review_manager.create_task(
            task_id=task_id,
            video_file=video_file,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        # 第一步：上传视频
        message_id = self._upload_video(
            bot_token, user_id, video_file, task_id, max_retries
        )
        if not message_id:
            print("[TelegramSender] 视频上传失败")
            return None

        # 更新消息 ID
        self.review_manager.update_telegram_message_id(task_id, message_id)

        # 第二步：发送诊断信息和审核按钮
        caption = self._build_caption(
            task_id, user_prompt, error_message, video_file
        )
        self._send_review_buttons(bot_token, user_id, caption, message_id)

        print(f"[TelegramSender] 已发送到 Telegram 审核，任务 ID: {task_id}")
        return message_id

    def _upload_video(
        self, bot_token: str, user_id: str, video_file: str, task_id: str, max_retries: int
    ) -> Optional[str]:
        """上传视频文件到 Telegram"""
        url = f"{self.api_base}{bot_token}/sendVideo"

        file_size = os.path.getsize(video_file)
        print(
            f"[TelegramSender] 上传视频: {video_file}, 大小={file_size / 1024 / 1024:.2f}MB"
        )

        for attempt in range(1, max_retries + 1):
            try:
                with open(video_file, "rb") as f:
                    form_data = [
                        (
                            "chat_id",
                            user_id.encode("utf-8"),
                        ),
                        (
                            "video",
                            (os.path.basename(video_file), f, "video/mp4"),
                        ),
                        (
                            "caption",
                            f"【待审核】任务 ID: {task_id}\n正在加载详细信息...".encode(
                                "utf-8"
                            ),
                        ),
                    ]

                    boundary = "----WebKitFormBoundary" + os.urandom(16).hex()
                    body = self._encode_multipart_formdata(form_data, boundary)

                    request = urllib.request.Request(
                        url,
                        data=body,
                        headers={
                            "Content-Type": f"multipart/form-data; boundary={boundary}",
                        },
                        method="POST",
                    )

                    with urllib.request.urlopen(request, timeout=300) as response:
                        result = json.loads(response.read().decode("utf-8"))
                        if result.get("ok"):
                            message_id = str(result["result"]["message_id"])
                            print(
                                f"[TelegramSender] 视频上传成功，消息 ID: {message_id}"
                            )
                            return message_id
                        else:
                            print(
                                f"[TelegramSender] API 返回错误: {result.get('description')}"
                            )

            except Exception as e:
                print(f"[TelegramSender] 上传失败 ({attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    import time

                    time.sleep(5)

        return None

    def _send_review_buttons(
        self, bot_token: str, user_id: str, caption: str, reply_to_message_id: str
    ) -> None:
        """发送带有审核按钮的消息"""
        url = f"{self.api_base}{bot_token}/sendMessage"

        # 创建内联按钮
        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "✅ 通过（评分 8.5）",
                        "callback_data": f"approve_{reply_to_message_id}_8.5",
                    },
                    {
                        "text": "❌ 拒绝",
                        "callback_data": f"reject_{reply_to_message_id}",
                    },
                ],
                [
                    {
                        "text": "📝 自定义评分",
                        "callback_data": f"custom_{reply_to_message_id}",
                    },
                ],
            ]
        }

        payload = {
            "chat_id": user_id,
            "text": caption,
            "reply_markup": keyboard,
            "parse_mode": "Markdown",
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
                if result.get("ok"):
                    print(f"[TelegramSender] 审核按钮已发送")
                else:
                    print(f"[TelegramSender] 发送按钮失败: {result.get('description')}")
        except Exception as e:
            print(f"[TelegramSender] 发送审核按钮异常: {e}")

    def _build_caption(
        self, task_id: str, user_prompt: str, error_message: str, video_file: str
    ) -> str:
        """构建审核消息文本"""
        caption = f"""🎬 **视频待审核**

**任务 ID:** `{task_id}`
**文件:** `{os.path.basename(video_file)}`

**原始提示词:**
```
{user_prompt[:500]}...
```

**错误信息:**
```
{error_message[:300]}...
```

**请选择:**
✅ **通过** - 评分 8.5（可自定义）
❌ **拒绝** - 不符合要求
📝 **自定义** - 输入具体评分和反馈"""

        return caption

    @staticmethod
    def _encode_multipart_formdata(fields, boundary):
        """编码 multipart/form-data"""
        lines = []
        for name, value in fields:
            if isinstance(value, tuple):
                filename, file_obj, mime_type = value
                lines.append(f"--{boundary}".encode())
                lines.append(
                    f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode()
                )
                lines.append(f"Content-Type: {mime_type}".encode())
                lines.append(b"")
                lines.append(file_obj.read())
                lines.append(b"")
            else:
                lines.append(f"--{boundary}".encode())
                lines.append(f'Content-Disposition: form-data; name="{name}"'.encode())
                lines.append(b"")
                if isinstance(value, bytes):
                    lines.append(value)
                else:
                    lines.append(value.encode("utf-8"))
                lines.append(b"")

        lines.append(f"--{boundary}--".encode())
        lines.append(b"")
        return b"\r\n".join(lines)
