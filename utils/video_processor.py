import os
import shutil
import tempfile
import subprocess
import time
import numpy as np
from pathlib import Path


def _debug(message):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[VideoProcessor {timestamp}] {message}", flush=True)


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
    def frames_to_video(cls, frames, audio_data=None, fps=24, sample_rate=44100, codec="auto", crf=18):
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
        if frames is None:
            raise ValueError("frames cannot be None")

        _debug(
            f"frames_to_video start: frames_type={type(frames).__name__}, "
            f"audio_type={type(audio_data).__name__ if audio_data is not None else 'None'}, "
            f"fps={fps}"
        )
        temp_dir = cls._ensure_temp_dir()
        video_path = os.path.join(temp_dir, f"video_{id(frames)}.mp4")

        frame_array = cls._prepare_frames(frames)
        _debug(
            f"frames prepared: count={len(frame_array)}, "
            f"shape={frame_array[0].shape}, output={video_path}"
        )

        # 如果有音频数据，先写入临时 WAV 文件
        audio_path = None
        try:
            if audio_data is not None:
                audio_path = os.path.join(temp_dir, f"audio_{id(audio_data)}.wav")
                audio_sample_rate = (
                    audio_data.get("sample_rate", sample_rate)
                    if isinstance(audio_data, dict)
                    else sample_rate
                )
                cls._write_audio_wav(audio_data, audio_path, audio_sample_rate)
                # 如果音频文件写入失败，设置 audio_path 为 None
                if not os.path.exists(audio_path):
                    audio_path = None
                else:
                    _debug(
                        f"audio prepared: path={audio_path}, "
                        f"bytes={os.path.getsize(audio_path)}, sample_rate={audio_sample_rate}"
                    )

            # 使用 ffmpeg 合成视频和音频
            _debug("starting ffmpeg encode")
            cls._write_video_ffmpeg(frame_array, video_path, fps, codec, crf, audio_path)
            _debug(
                f"ffmpeg encode complete: output={video_path}, "
                f"bytes={os.path.getsize(video_path) if os.path.exists(video_path) else 0}"
            )
        finally:
            # 无论视频编码成功与否，都清理临时音频文件
            if audio_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except OSError:
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

        first_shape = frame_list[0].shape
        if len(first_shape) != 3 or first_shape[2] != 3:
            raise ValueError(f"Unsupported frame shape: {first_shape}")
        for index, frame in enumerate(frame_list):
            if frame.shape != first_shape:
                raise ValueError(
                    "All video frames must have the same shape; "
                    f"frame 0 is {first_shape}, frame {index} is {frame.shape}"
                )
            frame_list[index] = np.ascontiguousarray(frame, dtype=np.uint8)

        _debug(f"_prepare_frames complete: count={len(frame_list)}, shape={first_shape}")
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

        # 检查并处理音频数据格式
        if audio_data is None:
            return

        audio_sample_rate = sample_rate

        # 如果是字典，提取 ComfyUI AudioInput 的 waveform 和采样率
        if isinstance(audio_data, dict):
            audio_sample_rate = audio_data.get("sample_rate", sample_rate)
            if "waveform" in audio_data:
                audio_data = audio_data["waveform"]
            elif "data" in audio_data:
                audio_data = audio_data["data"]
            elif "audio" in audio_data:
                audio_data = audio_data["audio"]
            else:
                # 字典中没有音频数据，跳过
                return

        # 转换为 numpy 数组
        if hasattr(audio_data, "detach"):
            audio_data = audio_data.detach().cpu()
        if hasattr(audio_data, "numpy"):
            audio_data = audio_data.numpy()

        if not isinstance(audio_data, np.ndarray):
            try:
                audio_data = np.array(audio_data)
            except (ValueError, TypeError):
                # 无法转换为数组，跳过音频
                return

        if audio_data.size == 0:
            return

        # 标准化到 [-1, 1]
        if audio_data.dtype == np.float32 or audio_data.dtype == np.float64:
            audio_data = np.clip(audio_data, -1.0, 1.0)
            audio_data = (audio_data * 32767).astype(np.int16)
        elif audio_data.dtype != np.int16:
            try:
                audio_data = audio_data.astype(np.int16)
            except (ValueError, TypeError):
                # 无法转换为 int16，跳过音频
                return

        # 处理立体声/单声道
        # ComfyUI waveform 通常是 [B, C, T]；WAV 需要 [T, C]。
        if len(audio_data.shape) == 3:
            if audio_data.shape[0] != 1:
                raise ValueError(f"Unsupported batched audio shape: {audio_data.shape}")
            audio_data = audio_data[0]

        if len(audio_data.shape) == 1:
            channels = 1
        elif len(audio_data.shape) == 2:
            # [C, T] is the ComfyUI layout; [T, C] is already WAV-compatible.
            if audio_data.shape[0] <= 8 and audio_data.shape[1] > audio_data.shape[0]:
                audio_data = audio_data.T
            channels = audio_data.shape[1]
        else:
            # 不支持的音频形状，跳过
            return

        if channels < 1 or channels > 8:
            raise ValueError(f"Unsupported audio channel count: {channels}")

        # 写入 WAV 文件
        try:
            with wave.open(output_path, 'wb') as wav_file:
                wav_file.setnchannels(channels)
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(int(audio_sample_rate))
                wav_file.writeframes(audio_data.tobytes())
            _debug(
                f"WAV write complete: path={output_path}, channels={channels}, "
                f"samples={len(audio_data) if audio_data.ndim == 1 else audio_data.shape[0]}, "
                f"sample_rate={audio_sample_rate}"
            )
        except Exception as e:
            print(f"Warning: Failed to write audio: {e}")

    @staticmethod
    def _write_video_ffmpeg(frame_array, output_path, fps, codec, crf, audio_path=None):
        """使用 ffmpeg 写入视频文件，可选音频"""
        if not frame_array:
            raise ValueError("frame_array is empty")

        height, width = frame_array[0].shape[:2]
        if width <= 0 or height <= 0:
            raise ValueError(f"Invalid video dimensions: {width}x{height}")

        codec = VideoProcessor._select_video_codec(codec)
        _debug(
            f"ffmpeg command setup: codec={codec}, size={width}x{height}, "
            f"frames={len(frame_array)}, has_audio={audio_path is not None}"
        )

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
        cmd.extend(["-c:v", codec])
        if codec in ("libx264", "libopenh264"):
            cmd.extend(["-crf", str(crf)])
        else:
            cmd.extend(["-q:v", str(max(1, min(31, int(crf / 2))))])
        cmd.extend([
            # yuv420p 需要偶数尺寸；补边不会改变原画面内容或比例。
            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2:0:0",
            "-pix_fmt", "yuv420p",
        ])

        # 如果有音频，指定音频编码
        if audio_path:
            cmd.extend(["-c:a", "aac", "-b:a", "192k", "-shortest"])

        cmd.extend(["-y", output_path])  # 覆盖输出文件

        stderr_capture = tempfile.TemporaryFile(mode="w+b")
        try:
            _debug("launching ffmpeg process")
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                # 不能使用 PIPE 后再等全部帧写完才读取，否则 FFmpeg 的
                # stderr 缓冲区可能填满，导致 FFmpeg 和当前线程互相等待。
                stderr=stderr_capture,
            )

            try:
                for frame in frame_array:
                    process.stdin.write(frame.tobytes())
                process.stdin.close()
                _debug("all frames written to ffmpeg stdin")
            except (BrokenPipeError, OSError) as error:
                # FFmpeg 失败时不能让 BrokenPipeError 遮蔽真正的诊断信息。
                try:
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
                process.stdin = None
                process.wait()
                stderr_capture.seek(0)
                stderr = stderr_capture.read()
                raise RuntimeError(
                    "ffmpeg stopped while receiving video frames "
                    f"({width}x{height}, {len(frame_array)} frames): "
                    f"{stderr.decode('utf-8', errors='replace').strip() or 'no stderr output'}"
                ) from error

            # stdin 已经显式关闭，置空后再用 communicate() 收集 stderr，避免死锁。
            process.stdin.close()
            process.stdin = None
            process.wait()
            stderr_capture.seek(0)
            stderr = stderr_capture.read()

            if process.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg error ({width}x{height}, {len(frame_array)} frames): "
                    f"{stderr.decode('utf-8', errors='replace').strip()}"
                )
            _debug("ffmpeg process exited successfully")

        except FileNotFoundError:
            raise RuntimeError("ffmpeg not found. Please install ffmpeg.")
        finally:
            stderr_capture.close()

    @staticmethod
    def _select_video_codec(codec):
        """选择当前 FFmpeg 实际可用的编码器，避免固定依赖 libx264。"""
        if codec and codec != "auto":
            return codec

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg not found. Please install ffmpeg.")

        try:
            result = subprocess.run(
                [ffmpeg, "-hide_banner", "-encoders"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                text=True,
                errors="replace",
            )
        except OSError as error:
            raise RuntimeError(f"Failed to query ffmpeg encoders: {error}") from error

        available = result.stdout
        available_names = {
            parts[1]
            for line in available.splitlines()
            for parts in [line.strip().split(maxsplit=2)]
            if len(parts) >= 2 and parts[0].startswith("V")
        }
        for candidate in ("libx264", "libopenh264", "h264_mf", "mpeg4"):
            if candidate in available_names:
                _debug(f"selected video encoder: {candidate}")
                return candidate

        raise RuntimeError(
            "No compatible video encoder found in ffmpeg. "
            "Install an ffmpeg build with libx264, libopenh264, h264_mf, or mpeg4."
        )

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

    @staticmethod
    def extract_frames_from_video(video_path, fps=None):
        """
        从视频文件提取帧

        Args:
            video_path: 视频文件路径 (str)
            fps: 提取帧的速率，如果为 None 则使用视频原始 fps

        Returns:
            帧列表 (numpy arrays)，每个元素是 (H, W, 3) 的 uint8 数组
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        try:
            import cv2
        except ImportError:
            raise RuntimeError("OpenCV (cv2) is required to extract frames from video")

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Failed to open video file: {video_path}")

        frames = []
        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                # OpenCV 读取为 BGR，转换为 RGB
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frames.append(frame_rgb)
        finally:
            cap.release()

        if not frames:
            raise ValueError(f"No frames extracted from video: {video_path}")

        return frames

    @staticmethod
    def get_frame_count(video_path):
        """Return a video's frame count without decoding every frame."""
        try:
            import cv2
        except ImportError:
            return None

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None
        try:
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            return frame_count if frame_count > 0 else None
        finally:
            cap.release()

    @staticmethod
    def extract_audio_from_video(video_path):
        """
        从视频文件提取音频

        Args:
            video_path: 视频文件路径 (str)

        Returns:
            音频数据 (numpy array)，shape: (samples,) 或 (samples, channels)
        """
        try:
            import soundfile as sf
        except ImportError:
            try:
                import librosa
                import numpy as np

                audio_data, sr = librosa.load(video_path, sr=None, mono=False)
                if len(audio_data.shape) == 1:
                    return audio_data
                else:
                    return audio_data.T
            except ImportError:
                raise RuntimeError(
                    "soundfile or librosa is required to extract audio from video"
                )

        try:
            audio_data, sample_rate = sf.read(video_path)
            return audio_data
        except Exception as e:
            raise RuntimeError(f"Failed to extract audio from video: {e}")
