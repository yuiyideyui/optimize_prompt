import os
import torch
import soundfile as sf

# 强制设置 torchaudio 或相关后端不依赖系统 sox
os.environ["HF_HUB_OFFLINE"] = "1"

from qwen_tts import Qwen3TTSModel

# 1. 加载模型
print("正在加载 Qwen3-TTS 模型到显卡...")
model = Qwen3TTSModel.from_pretrained(
    r"D:\ComfyUI_windows_portable\ComfyUI\custom_nodes\my_comfyui\voiceCreate\models\Qwen3-TTS-12Hz-1.7B-VoiceDesign",
    device_map="cuda:0",
    dtype=torch.bfloat16,
)

# 2. 语音生成（热血动漫少年音）
print("正在生成热血少年音...")
wavs, sr = model.generate_voice_design(
    text="喂！前面的家伙，快看那边！今天的冒险才刚刚开始呢！",
    language="Chinese",
    instruct="由年轻男声配音的10岁热血动漫小男孩，声音充满朝气与力量感，语速快",
)

# 3. 保存音频
output_path = "anime_boy.wav"
sf.write(output_path, wavs[0], sr)
print(f"🎉 语音生成成功！已保存至：{output_path}")