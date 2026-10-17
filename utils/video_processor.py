import os
import tempfile
import subprocess
import numpy as np
from pathlib import Path


class VideoProcessor:
    """处理视频帧合并和临时文件管理，支持音频"""

    TEMP_DIR = None

    @classmethod
    def _ensure_temp_dir(cls):
        """确保临时目录存在"""
        if cls.TEMP_DIR is None:
            cls.TEMP_DIR = tempfile.mkdtemp(prefix="comfyui_video_")
        return cls.TEMP_DIR

    @classmethod
    def frames_to_video(cls, frames, audio_data=None, fps=24, sample_rate=44100, codec="libx264", crf=18):
        """
        将图像帧和音频合并成视频文件

        Args:
            frames: 图像帧（可以是列表、张量或 numpy 数组）
            audio_data: 音频数据（numpy 数组，shape: (samples,) 或 (samples, channels)）
            fps: 视频帧率，默认 24
            sample_rate: 音频采样率，默认 44100
            codec: 视频编码器，默认 libx264（H.264）
            crf: 质量参数 (0-51，值越低质量越好)，默认 18

        Returns:
            视频文件路径 (str)
        """
        if not frames:
            raise ValueError("frames cannot be empty")

        temp_dir = cls._ensure_temp_dir()
        video_path = os.path.join(temp_dir, f"video_{id(frames)}.mp4")

        frame_array = cls._prepare_frames(frames)

        # 如果有音频数据，先写入临时 WAV 文件
        audio_path = None
        if audio_data is not None:
            audio_path = os.path.join(temp_dir, f"audio_{id(audio_data)}.wav")
            cls._write_audio_wav(audio_data, audio_path, sample_rate)

        # 使用 ffmpeg 合成视频和音频
        cls._write_video_ffmpeg(frame_array, video_path, fps, codec, crf, audio_path)

        # 清理临时音频文件
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except:
                pass

        return video_path

    @staticmethod
    def _prepare_frames(frames):
        """将不同格式的帧转换为 numpy 数组列表"""
        frame_list = []

        if isinstance(frames, (list, tuple)):
            for frame in frames:
                frame_list.append(VideoProcessor._tensor_to_numpy(frame))
        elif hasattr(frames, "__iter__") and not isinstance(frames, (str, bytes)):
            for frame in frames:
                frame_list.append(VideoProcessor._tensor_to_numpy(frame))
        else:
            frame_list.append(VideoProcessor._tensor_to_numpy(frames))

        if not frame_list:
            raise ValueError("No frames extracted")

        return frame_list

    @staticmethod
    def _tensor_to_numpy(frame):
        """将张量转换为 numpy 数组 (RGB, 0-255)"""
        if hasattr(frame, "detach"):
            frame = frame.detach().cpu()
        if hasattr(frame, "numpy"):
            frame = frame.numpy()

        if not isinstance(frame, np.ndarray):
            frame = np.array(frame)

        if frame.dtype != np.uint8:
            if frame.max() <= 1.0:
                frame = (frame * 255).astype(np.uint8)
            else:
                frame = np.clip(frame, 0, 255).astype(np.uint8)

        if len(frame.shape) == 3:
            if frame.shape[0] in (3, 4):
                frame = np.transpose(frame, (1, 2, 0))
            elif frame.shape[2] == 1:
                frame = np.repeat(frame, 3, axis=2)
        elif len(frame.shape) == 2:
            frame = np.stack([frame] * 3, axis=2)

        if frame.shape[2] == 4:
            frame = frame[:, :, :3]
        elif frame.shape[2] != 3:
            raise ValueError(f"Unsupported channel count: {frame.shape[2]}")

        return frame

    @staticmethod
    def _write_audio_wav(audio_data, output_path, sample_rate):
        """写入音频数据到 WAV 文件"""
        try:
            import wave
        except ImportError:
            raise RuntimeError("wave module not available")

        # 转换为 numpy 数组
        if hasattr(audio_data, "detach"):
            audio_data = audio_data.detach().cpu()
        if hasattr(audio_data, "numpy"):
            audio_data = audio_data.numpy()

        if not isinstance(audio_data, np.ndarray):
            audio_data = np.array(audio_data)

        # 标准化到 [-1, 1]
        if audio_data.dtype == np.float32 or audio_data.dtype == np.float64:
            audio_data = np.clip(audio_data, -1.0, 1.0)
            audio_data = (audio_data * 32767).astype(np.int16)
        elif audio_data.dtype != np.int16:
            audio_data = audio_data.astype(np.int16)

        # 处理立体声/单声道
        if len(audio_data.shape) == 1:
            channels = 1
            frames = audio_data
        elif len(audio_data.shape) == 2:
            channels = audio_data.shape[1]
            frames = audio_data
        else:
            raise ValueError(f"Unsupported audio shape: {audio_data.shape}")

        # 写入 WAV 文件
        with wave.open(output_path, 'wb') as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(sample_rate)
            if len(audio_data.shape) == 1:
                wav_file.writeframes(audio_data.tobytes())
            else:
                wav_file.writeframes(audio_data.tobytes())

    @staticmethod
    def _write_video_ffmpeg(frame_array, output_path, fps, codec, crf, audio_path=None):
        """使用 ffmpeg 写入视频文件，可选音频"""
        if not frame_array:
            raise ValueError("frame_array is empty")

        height, width = frame_array[0].shape[:2]

        # ffmpeg 命令基础部分
        cmd = [
            "ffmpeg",
            "-f", "rawvideo",
            "-pixel_format", "rgb24",
            "-video_size", f"{width}x{height}",
            "-framerate", str(fps),
            "-i", "-",  # 视频来自 stdin
        ]

        # 如果有音频，添加音频输入
        if audio_path:
            cmd.extend(["-i", audio_path])

        # 添加编码参数
        cmd.extend([
            "-c:v", codec,
            "-crf", str(crf),
            "-pix_fmt", "yuv420p",
        ])

        # 如果有音频，指定音频编码
        if audio_path:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])

        cmd.extend(["-y", output_path])  # 覆盖输出文件

        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            for frame in frame_array:
                if frame.dtype != np.uint8:
                    frame = np.clip(frame * 255, 0, 255).astype(np.uint8)
                process.stdin.write(frame.tobytes())

            stdout, stderr = process.communicate()

            if process.returncode != 0:
                raise RuntimeError(f"ffmpeg error: {stderr.decode()}")

        except FileNotFoundError:
            raise RuntimeError("ffmpeg not found. Please install ffmpeg.")

    @classmethod
    def cleanup(cls, video_path):
        """删除临时视频文件"""
        if video_path and os.path.exists(video_path):
            try:
                os.remove(video_path)
            except Exception as e:
                print(f"Warning: Failed to delete {video_path}: {e}")

    @classmethod
    def cleanup_all(cls):
        """清理所有临时文件"""
        if cls.TEMP_DIR and os.path.exists(cls.TEMP_DIR):
            try:
                import shutil
                shutil.rmtree(cls.TEMP_DIR)
                cls.TEMP_DIR = None
            except Exception as e:
                print(f"Warning: Failed to cleanup temp directory: {e}")
