import torch
import torch.nn.functional as F
import copy


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


def _resize_context_latent_tail(tail_latent, target_h, target_w):
    """Resize context video/audio tail latent to match the target canvas.

    Tail latent has shape [C, T, H, W] or [B, C, T, H, W]; resize only spatial dims H, W.
    """
    if tail_latent is None:
        return None

    if not isinstance(tail_latent, torch.Tensor):
        raise ValueError("Context tail latent must be a tensor")

    if tail_latent.ndim == 5:
        batch, channels, frames, source_h, source_w = tail_latent.shape
        if batch != 1:
            raise ValueError(f"Context tail latent batch size must be 1, got {batch}")
        tail_latent = tail_latent.squeeze(0)
    elif tail_latent.ndim != 4:
        raise ValueError(f"Context tail latent must have shape [C,T,H,W] or [B,C,T,H,W], got shape {tuple(tail_latent.shape)}")

    channels, frames, source_h, source_w = tail_latent.shape

    if (source_h, source_w) == (target_h, target_w):
        return tail_latent, False

    # Reshape to [T, C, H, W] for interpolation
    reshaped = tail_latent.permute(1, 0, 2, 3).reshape(frames, channels, source_h, source_w)
    original_dtype = reshaped.dtype
    if reshaped.device.type == "cpu" and original_dtype in (torch.float16, torch.bfloat16):
        reshaped = reshaped.float()

    # Bilinear interpolation for spatial dims
    resized = F.interpolate(
        reshaped,
        size=(target_h, target_w),
        mode="bilinear",
        align_corners=False,
    ).to(dtype=original_dtype)

    # Reshape back to [C, T, H, W]
    result = resized.reshape(frames, channels, target_h, target_w).permute(1, 0, 2, 3).contiguous()
    return result, True


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
    images = value.permute(0, 2, 1, 3, 4).reshape(batch * frames, channels, source_h, source_w)
    original_dtype = images.dtype
    if images.device.type == "cpu" and original_dtype in (torch.float16, torch.bfloat16):
        images = images.float()

    resized = F.interpolate(
        images,
        size=(target_h, target_w),
        mode="bilinear",
        align_corners=False,
    ).to(dtype=original_dtype)

    resized = resized.reshape(batch, frames, channels, target_h, target_w)
    return resized.permute(0, 2, 1, 3, 4).contiguous(), True


def _adapt_context_latent_tails(context_dict, target_h, target_w):
    """Adapt video_tail and audio_tail in context to match target canvas."""
    if context_dict is None or not isinstance(context_dict, dict):
        return context_dict, 0, 0

    adapted = copy.deepcopy(context_dict)
    video_tail_resized = 0
    audio_tail_resized = 0

    if "video_tail" in adapted:
        adapted["video_tail"], changed = _resize_context_latent_tail(
            adapted["video_tail"], target_h, target_w
        )
        video_tail_resized = int(changed)

    if "audio_tail" in adapted:
        adapted["audio_tail"], changed = _resize_context_latent_tail(
            adapted["audio_tail"], target_h, target_w
        )
        audio_tail_resized = int(changed)

    return adapted, video_tail_resized, audio_tail_resized


def adapt_keyframe_conditioning(conditioning, latent, context=None):
    """Adapt conditioning keyframes and context latent tails to match the target canvas.

    Reference-image/video/audio payloads are intentionally left untouched: their
    native reference geometry is independent of the generated video canvas.
    """
    target_h, target_w = _video_canvas(latent)
    if not isinstance(conditioning, (list, tuple)):
        raise ValueError("conditioning must be a ComfyUI CONDITIONING list")

    adapted = []
    resized_count = 0
    keyframe_count = 0
    context_video_tail_resized = 0
    context_audio_tail_resized = 0

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

    # Adapt context latent tails if provided
    if context is not None and isinstance(context, dict):
        context_adapted, ctx_video_resized, ctx_audio_resized = _adapt_context_latent_tails(
            context, target_h, target_w
        )
        context_video_tail_resized = ctx_video_resized
        context_audio_tail_resized = ctx_audio_resized
    else:
        context_adapted = context

    report = (
        f"canvas={target_w * 16}x{target_h * 16}px; "
        f"keyframe_latents={keyframe_count}; resized={resized_count}; "
        f"context_video_tail_resized={context_video_tail_resized}; "
        f"context_audio_tail_resized={context_audio_tail_resized}; "
        f"reference image/video/audio payloads unchanged"
    )
    return adapted, context_adapted, report


class MiniMaxH3ContextConditioningAdapter:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "conditioning": ("CONDITIONING",),
                "latent": ("LATENT",),
            },
            "optional": {
                "context": ("H3_T8_CONTEXT",),
            }
        }

    RETURN_TYPES = ("CONDITIONING", "H3_T8_CONTEXT", "STRING")
    RETURN_NAMES = ("conditioning", "context", "report")
    FUNCTION = "adapt"
    CATEGORY = "conditioning/minimax"

    def adapt(self, conditioning, latent, context=None):
        adapted_cond, adapted_context, report = adapt_keyframe_conditioning(conditioning, latent, context)
        return (adapted_cond, adapted_context, report)


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3ContextConditioningAdapter": MiniMaxH3ContextConditioningAdapter,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3ContextConditioningAdapter": "MiniMax H3 Context Conditioning Adapter",
}