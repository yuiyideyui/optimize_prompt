"""Telegram Bot 配置管理"""

import os
import json
from pathlib import Path


class TelegramConfig:
    """Telegram bot 配置类"""

    def __init__(self):
        self.config_file = Path.home() / ".comfyui_telegram_config.json"
        self.data = self._load_config()

    def _load_config(self) -> dict:
        """从文件加载配置，如不存在则返回空配置"""
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[TelegramConfig] 加载配置文件失败: {e}")
        return {}

    def save_config(self) -> None:
        """保存配置到文件"""
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[TelegramConfig] 保存配置失败: {e}")

    def set(self, key: str, value) -> None:
        """设置配置值"""
        self.data[key] = value
        self.save_config()

    def get(self, key: str, default=None):
        """获取配置值"""
        return self.data.get(key, default)

    def get_bot_token(self) -> str:
        """获取 Bot Token"""
        return self.get("bot_token") or os.getenv("TELEGRAM_BOT_TOKEN", "")

    def get_user_id(self) -> str:
        """获取用户 ID（私聊）"""
        return self.get("user_id") or os.getenv("TELEGRAM_USER_ID", "")

    def get_webhook_url(self) -> str:
        """获取 ComfyUI webhook 地址"""
        return self.get("webhook_url") or os.getenv("COMFYUI_WEBHOOK_URL", "")

    def is_configured(self) -> bool:
        """检查是否已配置"""
        return bool(self.get_bot_token() and self.get_user_id())
