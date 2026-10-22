import torch
import torch.nn.functional as F


def _video_canvas(latent):
    if not isinstance(latent, dict) or "samples" not in latent:
        raise ValueError("latent must be the joint MiniMax H3 AV latent")
    samples = latent["samples"]
    if not getattr(samples, "is_nested", False):
        raise ValueError("latent must contain a nested MiniMax H3 video/audio latent")
    parts = tuple(samples.unbind())
    if len(parts) != 2:
        raise ValueError(f"Expected video and audio latent parts, got {len(parts)}")
    video = parts[0]
    if video.ndim != 5 or video.shape[1] != 24:
        raise ValueError(f"Unexpected MiniMax H3 video latent shape: {tuple(video.shape)}")
    return int(video.shape[-2]), int(video.shape[-1])


def _resize_keyframe_latent(value, target_h, target_w):
    if not isinstance(value, torch.Tensor) or value.ndim != 5:
        raise ValueError("MiniMax H3 keyframe latent must have shape [B,C,T,H,W]")
    if value.shape[1] != 24:
        raise ValueError(
            f"MiniMax H3 keyframe latent must have 24 channels, got {value.shape[1]}"
        )
    source_h, source_w = int(value.shape[-2]), int(value.shape[-1])
    if (source_h, source_w) == (target_h, target_w):
        return value, False

    batch, channels, frames, _, _ = value.shape
    # 将时间维展平到批次维，独立缩放每个时间步的空间特征
    images = value.permute(0, 2, 1, 3, 4).reshape(batch * frames, channels, source_h, source_w)
    original_dtype = images.dtype
    if images.device.type == "cpu" and original_dtype in (torch.float16, torch.bfloat16):
        images = images.float()
    
    # 使用 bilinear 替代 bicubic，减少缩放高频振铃导致的边缘重影
    resized = F.interpolate(
        images,
        size=(target_h, target_w),
        mode="bilinear",
        align_corners=False,
    ).to(dtype=original_dtype)
    
    resized = resized.reshape(batch, frames, channels, target_h, target_w)
    return resized.permute(0, 2, 1, 3, 4).contiguous(), True


def adapt_keyframe_conditioning(conditioning, latent):
    """Copy conditioning metadata and resize only ``minimax_keyframes`` latents.

    Reference-image/video/audio payloads are intentionally left untouched: their
    native reference geometry is independent of the generated video canvas.
    """
    target_h, target_w = _video_canvas(latent)
    if not isinstance(conditioning, (list, tuple)):
        raise ValueError("conditioning must be a ComfyUI CONDITIONING list")

    adapted = []
    resized_count = 0
    keyframe_count = 0
    for entry in conditioning:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            raise ValueError("Unexpected CONDITIONING entry; expected [tensor, metadata]")
        cond_tensor, metadata = entry[0], entry[1]
        if not isinstance(metadata, dict):
            raise ValueError("CONDITIONING metadata must be a dictionary")

        metadata_copy = metadata.copy()
        keyframes = metadata.get("minimax_keyframes")
        if keyframes is not None:
            if not isinstance(keyframes, (list, tuple)):
                raise ValueError("minimax_keyframes must be a list")
            copied_keyframes = []
            for keyframe in keyframes:
                if not isinstance(keyframe, dict):
                    raise ValueError("Each minimax_keyframes item must be a dictionary")
                keyframe_copy = keyframe.copy()
                if "latent" in keyframe_copy:
                    keyframe_copy["latent"], changed = _resize_keyframe_latent(
                        keyframe_copy["latent"], target_h, target_w
                    )
                    keyframe_count += 1
                    resized_count += int(changed)
                copied_keyframes.append(keyframe_copy)
            metadata_copy["minimax_keyframes"] = copied_keyframes

        adapted.append([cond_tensor, metadata_copy, *entry[2:]])

    report = (
        f"canvas={target_w * 16}x{target_h * 16}px; "
        f"keyframe_latents={keyframe_count}; resized={resized_count}; "
        f"reference image/video/audio payloads unchanged"
    )
    return adapted, report


class MiniMaxH3ContextConditioningAdapter:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "conditioning": ("CONDITIONING",),
                "latent": ("LATENT",),
            }
        }

    RETURN_TYPES = ("CONDITIONING", "STRING")
    RETURN_NAMES = ("conditioning", "report")
    FUNCTION = "adapt"
    CATEGORY = "conditioning/minimax"

    def adapt(self, conditioning, latent):
        adapted, report = adapt_keyframe_conditioning(conditioning, latent)
        return (adapted, report)


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3ContextConditioningAdapter": MiniMaxH3ContextConditioningAdapter,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3ContextConditioningAdapter": "MiniMax H3 Context Conditioning Adapter",
}