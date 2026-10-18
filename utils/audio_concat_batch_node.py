import torch
import torchaudio

class AudioBatchOrConcat:
    """ComfyUI Audio Batch & Concat Node.
    
    支持将多个输入的 AUDIO 拼接（Concat）或合并为批次（Batch），
    自动处理不同的采样率（Target Sample Rate）与声道数对齐。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mode": (["batch", "concat"], {"default": "batch"}),
                "target_sample_rate": ("INT", {"default": 32000, "min": 8000, "max": 192000, "step": 1000}),
            },
            "optional": {
                "audio_1": ("AUDIO",),
                "audio_2": ("AUDIO",),
                "audio_3": ("AUDIO",),
                "audio_4": ("AUDIO",),
                "audio_5": ("AUDIO",),
                "audio_6": ("AUDIO",),
                "audio_7": ("AUDIO",),
                "audio_8": ("AUDIO",),
            }
        }

    RETURN_TYPES = ("AUDIO",)
    RETURN_NAMES = ("audio",)
    FUNCTION = "process"
    CATEGORY = "my_comfyui/audio"

    def process(self, mode="batch", target_sample_rate=32000, **kwargs):
        # 1. 收集所有已连接的音频输入
        audios = []
        for i in range(1, 9):
            key = f"audio_{i}"
            if key in kwargs and kwargs[key] is not None:
                audios.append(kwargs[key])

        if not audios:
            raise ValueError("至少需要连接一个 AUDIO 输入！")

        processed_waveforms = []
        max_channels = 1
        max_length = 0

        # 2. 统一采样率并预处理波形格式
        for item in audios:
            waveform = item["waveform"]  # 期望格式: [B, C, L]
            sr = item.get("sample_rate", target_sample_rate)

            # 确保是 3D Tensor [B, C, L]
            if waveform.ndim == 2:
                waveform = waveform.unsqueeze(0)

            # 采样率对齐
            if sr != target_sample_rate:
                waveform = torchaudio.functional.resample(waveform, sr, target_sample_rate)

            b, c, l = waveform.shape
            max_channels = max(max_channels, c)
            max_length = max(max_length, l)
            processed_waveforms.append(waveform)

        # 3. 声道补齐 (例如单声道扩展为双声道/多声道对齐)
        aligned_waveforms = []
        for wf in processed_waveforms:
            b, c, l = wf.shape
            if c < max_channels:
                # 重复最后一个声道以填充到 max_channels
                repeat_factor = max_channels - c
                extra_channels = wf[:, -1:, :].repeat(1, repeat_factor, 1)
                wf = torch.cat([wf, extra_channels], dim=1)
            aligned_waveforms.append(wf)

        # 4. 根据模式合并音频
        if mode == "batch":
            # Batch 模式：填充到相同长度后堆叠为 [Total_Batch, C, Max_Length]
            padded_waveforms = []
            for wf in aligned_waveforms:
                b, c, l = wf.shape
                if l < max_length:
                    padding = torch.zeros((b, c, max_length - l), dtype=wf.dtype, device=wf.device)
                    wf = torch.cat([wf, padding], dim=-1)
                padded_waveforms.append(wf)

            final_waveform = torch.cat(padded_waveforms, dim=0)

        elif mode == "concat":
            # Concat 模式：按时间轴拼接为 [1, C, Total_Length]
            # 展平所有 batch 批次后在 length 维度拼接
            flat_waveforms = []
            for wf in aligned_waveforms:
                # 如果单个输入包含多个 batch [B, C, L]，先在时间轴上拼接
                b, c, l = wf.shape
                if b > 1:
                    wf = wf.reshape(1, c, b * l)
                flat_waveforms.append(wf)

            final_waveform = torch.cat(flat_waveforms, dim=-1)

        return ({"waveform": final_waveform, "sample_rate": target_sample_rate},)


# 节点映射导出
NODE_CLASS_MAPPINGS = {
    "AudioBatchOrConcat": AudioBatchOrConcat,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AudioBatchOrConcat": "Audio Combine (Batch / Concat)",
}