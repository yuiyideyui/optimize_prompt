"""ComfyUI 客户端 - 查询执行状态和进度"""

import json
import urllib.request
import urllib.error
from typing import Optional, Dict, Any


class ComfyUIClient:
    """与 ComfyUI 通信获取状态信息"""

    def __init__(self, base_url: str = "http://localhost:8188"):
        self.base_url = base_url.rstrip("/")
        self.api_url = f"{self.base_url}/api"

    def get_system_stats(self) -> Optional[Dict[str, Any]]:
        """获取系统统计信息"""
        endpoints = [
            f"{self.base_url}/system_stats",
            f"{self.api_url}/system",
            f"{self.base_url}/system",
        ]
        for url in endpoints:
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception:
                continue
        print(f"[ComfyUIClient] 无法从任何端点获取系统信息")
        return None

    def get_queue_info(self) -> Optional[Dict[str, Any]]:
        """获取队列信息（排队中的任务）"""
        endpoints = [
            f"{self.api_url}/queue",
            f"{self.base_url}/queue",
        ]
        for url in endpoints:
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception:
                continue
        print(f"[ComfyUIClient] 无法从任何端点获取队列信息")
        return None

    def get_status(self) -> Optional[Dict[str, Any]]:
        """获取当前执行状态"""
        endpoints = [
            f"{self.base_url}/queue",
            f"{self.api_url}/queue",
            f"{self.api_url}/status",
            f"{self.base_url}/status",
        ]
        for url in endpoints:
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception:
                continue
        print(f"[ComfyUIClient] 无法从任何端点获取执行状态")
        return None

    def format_status_message(self) -> str:
        """格式化状态信息为可读的文本"""
        lines = ["🖥️ *ComfyUI Status*\n"]

        # 获取系统信息
        system = self.get_system_stats()
        if system:
            # Current ComfyUI returns {system: {ram_total, ram_free}, devices: [...]};
            # older/custom builds may return the legacy mem/vram mappings.
            system_info = system.get("system", system)
            memory = system.get("mem", {})
            ram_total = system_info.get("ram_total")
            ram_free = system_info.get("ram_free")
            if ram_total is not None and ram_free is not None:
                ram_used = ram_total - ram_free
                lines.append(
                    f"RAM: {ram_used / 1024**3:.1f}GB / {ram_total / 1024**3:.1f}GB"
                )
            elif memory:
                lines.append(
                    f"RAM: {memory.get('used', 0) / 1024**3:.1f}GB / "
                    f"{memory.get('total', 0) / 1024**3:.1f}GB"
                )

            devices = system.get("devices", [])
            if devices:
                for device in devices:
                    if isinstance(device, dict) and device.get("vram_total"):
                        used = device["vram_total"] - device.get("vram_free", 0)
                        lines.append(
                            f"VRAM ({device.get('name', 'GPU')}): "
                            f"{used / 1024**2:.0f}MB / {device['vram_total'] / 1024**2:.0f}MB"
                        )
            else:
                vram = system.get("vram", {})
                for gpu_name, gpu_info in vram.items():
                    if isinstance(gpu_info, dict):
                        vram_used = gpu_info.get("used", 0) / 1024 / 1024
                        vram_total = gpu_info.get("total", 0) / 1024 / 1024
                        vram_info = f"VRAM ({gpu_name}): {vram_used:.0f}MB / {vram_total:.0f}MB"
                        lines.append(vram_info)

        # 获取执行状态
        status = self.get_status()
        if status:
            exec_info = status.get("exec_info", status)
            running = status.get("queue_running", exec_info.get("queue_running", 0))
            pending = status.get("queue_pending", exec_info.get("queue_pending", 0))
            queue_running = len(running) if isinstance(running, list) else running
            queue_pending = len(pending) if isinstance(pending, list) else pending

            lines.append("")
            lines.append("*Queue Status:*")
            lines.append(f"🔄 Running: {queue_running}")
            lines.append(f"⏳ Pending: {queue_pending}")

        # 获取队列信息
        queue = self.get_queue_info()
        if queue:
            queued = queue.get("queue", queue.get("queue_pending", []))
            executing = queue.get("executing", queue.get("queue_running", []))

            if executing:
                lines.append(f"\n*Currently Executing:*")
                for item in executing:
                    if isinstance(item, list) and len(item) > 0:
                        lines.append(f"• Task ID: {item[0]}")

            if queued:
                lines.append(f"\n*Queued Tasks: {len(queued)}*")
                for i, item in enumerate(queued[:5], 1):  # 最多显示5个
                    if isinstance(item, list) and len(item) > 0:
                        lines.append(f"{i}. Task ID: {item[0]}")
                if len(queued) > 5:
                    lines.append(f"... and {len(queued) - 5} more")

        return "\n".join(lines)

    def is_available(self) -> bool:
        """检查 ComfyUI 是否可用"""
        try:
            url = f"{self.base_url}/"
            with urllib.request.urlopen(url, timeout=5) as response:
                return response.status == 200
        except Exception:
            return False
