"""审核任务管理 - 存储和查询待审核/已审核任务"""

import json
import time
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime


class ReviewTask:
    """单个审核任务"""

    def __init__(self, task_id: str, video_file: str, system_prompt: str,
                 user_prompt: str, telegram_message_id: Optional[str] = None):
        self.task_id = task_id
        self.video_file = video_file
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        self.telegram_message_id = telegram_message_id
        self.status = "pending"  # pending, approved, rejected
        self.created_at = datetime.now().isoformat()
        self.reviewed_at: Optional[str] = None
        self.score: Optional[float] = None
        self.feedback: str = ""
        self.h3_optimize_prompt: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "video_file": self.video_file,
            "system_prompt": self.system_prompt,
            "user_prompt": self.user_prompt,
            "telegram_message_id": self.telegram_message_id,
            "status": self.status,
            "created_at": self.created_at,
            "reviewed_at": self.reviewed_at,
            "score": self.score,
            "feedback": self.feedback,
            "h3_optimize_prompt": self.h3_optimize_prompt,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReviewTask":
        """从字典恢复"""
        task = cls(
            task_id=data["task_id"],
            video_file=data["video_file"],
            system_prompt=data["system_prompt"],
            user_prompt=data["user_prompt"],
            telegram_message_id=data.get("telegram_message_id"),
        )
        task.status = data.get("status", "pending")
        task.created_at = data.get("created_at", "")
        task.reviewed_at = data.get("reviewed_at")
        task.score = data.get("score")
        task.feedback = data.get("feedback", "")
        task.h3_optimize_prompt = data.get("h3_optimize_prompt", "")
        return task


class ReviewManager:
    """管理所有审核任务"""

    def __init__(self, storage_dir: Optional[str] = None):
        if storage_dir is None:
            storage_dir = str(Path.home() / ".comfyui_telegram_reviews")
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.db_file = self.storage_dir / "tasks.json"

    def _load_db(self) -> Dict[str, Dict[str, Any]]:
        """加载任务数据库"""
        if self.db_file.exists():
            try:
                with open(self.db_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[ReviewManager] 加载数据库失败: {e}")
        return {}

    def _save_db(self, db: Dict[str, Dict[str, Any]]) -> None:
        """保存任务数据库"""
        try:
            with open(self.db_file, "w", encoding="utf-8") as f:
                json.dump(db, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[ReviewManager] 保存数据库失败: {e}")

    def create_task(self, task_id: str, video_file: str, system_prompt: str,
                    user_prompt: str) -> ReviewTask:
        """创建新审核任务"""
        task = ReviewTask(task_id, video_file, system_prompt, user_prompt)
        db = self._load_db()
        db[task_id] = task.to_dict()
        self._save_db(db)
        return task

    def update_telegram_message_id(self, task_id: str, message_id: str) -> None:
        """更新 Telegram 消息 ID"""
        db = self._load_db()
        if task_id in db:
            db[task_id]["telegram_message_id"] = message_id
            self._save_db(db)

    def approve_task(self, task_id: str, score: float, feedback: str,
                     h3_optimize_prompt: str = "") -> Optional[ReviewTask]:
        """批准审核任务"""
        db = self._load_db()
        if task_id not in db:
            return None

        db[task_id]["status"] = "approved"
        db[task_id]["reviewed_at"] = datetime.now().isoformat()
        db[task_id]["score"] = score
        db[task_id]["feedback"] = feedback
        db[task_id]["h3_optimize_prompt"] = h3_optimize_prompt
        self._save_db(db)

        return ReviewTask.from_dict(db[task_id])

    def reject_task(self, task_id: str, feedback: str = "") -> Optional[ReviewTask]:
        """拒绝审核任务"""
        db = self._load_db()
        if task_id not in db:
            return None

        db[task_id]["status"] = "rejected"
        db[task_id]["reviewed_at"] = datetime.now().isoformat()
        db[task_id]["feedback"] = feedback
        self._save_db(db)

        return ReviewTask.from_dict(db[task_id])

    def get_task(self, task_id: str) -> Optional[ReviewTask]:
        """获取任务"""
        db = self._load_db()
        if task_id not in db:
            return None
        return ReviewTask.from_dict(db[task_id])

    def get_task_by_message_id(self, message_id: str) -> Optional[ReviewTask]:
        """通过 Telegram 消息 ID 获取任务"""
        db = self._load_db()
        for task_data in db.values():
            if task_data.get("telegram_message_id") == message_id:
                return ReviewTask.from_dict(task_data)
        return None

    def get_pending_tasks(self) -> list[ReviewTask]:
        """获取所有待审核任务"""
        db = self._load_db()
        return [
            ReviewTask.from_dict(task_data)
            for task_data in db.values()
            if task_data.get("status") == "pending"
        ]

    def get_all_tasks(self) -> list[ReviewTask]:
        """获取所有任务"""
        db = self._load_db()
        return [ReviewTask.from_dict(task_data) for task_data in db.values()]
