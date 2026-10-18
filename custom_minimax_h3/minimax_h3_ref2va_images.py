"""MiniMax H3 T8 conditioning and sampling nodes with batch media inputs."""

import json

import torch
import nodes

from .t8_port.conditioning import build_conditioning
from .t8_port.sampling import (
    DEFAULT_SAMPLER_NAME,
    DEFAULT_SCHEDULER_NAME,
    SAMPLER_OPTIONS,
    SCHEDULER_OPTIONS,
    setup_dual_clock_sampling,
)


def _image_batch_values(batch):
    if batch is None:
        return {}
    if isinstance(batch, torch.Tensor):
        if batch.ndim == 3:
            images = [batch.unsqueeze(0)]
        elif batch.ndim == 4:
            images = [image.unsqueeze(0) for image in batch.unbind(0)]
        else:
            raise ValueError("ref_images must have shape [H, W, C] or [B, H, W, C]")
    elif isinstance(batch, (list, tuple)):
        images = []
        for image in batch:
            if not isinstance(image, torch.Tensor):
                raise ValueError("Each ref_images item must be an IMAGE tensor")
            if image.ndim == 3:
                images.append(image.unsqueeze(0))
            elif image.ndim == 4:
                images.extend(item.unsqueeze(0) for item in image.unbind(0))
            else:
                raise ValueError("Each ref_images item must have shape [H, W, C] or [B, H, W, C]")
    else:
        raise ValueError("ref_images must be an IMAGE tensor or a list of IMAGE tensors")
    if not images:
        raise ValueError("ref_images must contain at least one image")
    if any(image.shape[-1] < 3 or any(size < 1 for size in image.shape) for image in images):
        raise ValueError("ref_images must contain non-empty [H, W, C] images with at least 3 channels")
    if len(images) > 9:
        raise ValueError("MiniMax H3 supports at most 9 reference images")
    return {
        f"ref_image_{index}": image.contiguous()
        for index, image in enumerate(images, 1)
    }


def _restore_letterboxed_batch(batch, metadata):
    if metadata is None:
        return batch
    if not isinstance(metadata, str):
        raise ValueError("ref_images_metadata must be the JSON string output from Director Table")
    try:
        data = json.loads(metadata)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid ref_images_metadata JSON: {exc.msg}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unsupported ref_images_metadata version")
    if data.get("resize_mode") != "letterbox":
        raise ValueError(
            "Image restoration requires Director Table batch_resize_mode='letterbox'; "
            "center_crop and stretch cannot recover original pixels"
        )
    if not isinstance(batch, torch.Tensor) or batch.ndim != 4:
        raise ValueError("ref_images_metadata requires ref_images with shape [B, H, W, C]")
    canvas = data.get("canvas")
    items = data.get("items")
    if (
        not isinstance(canvas, list)
        or len(canvas) != 2
        or canvas != [int(batch.shape[1]), int(batch.shape[2])]
    ):
        raise ValueError("ref_images_metadata canvas does not match the connected ref_images batch")
    if not isinstance(items, list) or len(items) != batch.shape[0]:
        raise ValueError(
            "ref_images_metadata item count does not match the connected ref_images batch"
        )

    restored = {}
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict) or item.get("index") != index:
            raise ValueError("ref_images_metadata items must be ordered and indexed from 1")
        top, left, height, width = (
            item.get("top"),
            item.get("left"),
            item.get("height"),
            item.get("width"),
        )
        if any(not isinstance(value, int) or isinstance(value, bool) for value in (top, left, height, width)):
            raise ValueError(f"ref_images_metadata item {index} has invalid crop coordinates")
        if (
            top < 0
            or left < 0
            or height < 1
            or width < 1
            or top + height > batch.shape[1]
            or left + width > batch.shape[2]
        ):
            raise ValueError(f"ref_images_metadata item {index} crop is outside the batch canvas")
        restored[f"ref_image_{index}"] = batch[
            index - 1:index,
            top:top + height,
            left:left + width,
            :,
        ].contiguous()
    return restored


def _audio_batch_values(batch):
    if batch is None:
        return {}
    if not isinstance(batch, dict):
        raise ValueError("ref_audios must be an AUDIO value containing waveform and sample_rate")
    waveform = batch.get("waveform")
    sample_rate = batch.get("sample_rate")
    if not isinstance(waveform, torch.Tensor) or waveform.ndim != 3 or sample_rate is None:
        raise ValueError("ref_audios waveform must have shape [B, C, L] and include sample_rate")
    if any(size < 1 for size in waveform.shape):
        raise ValueError("ref_audios waveform must not contain an empty dimension")
    if waveform.shape[0] > 3:
        raise ValueError("MiniMax H3 supports at most 3 standalone reference audios")
    return {
        f"ref_audio_{index}": {
            "waveform": waveform[index - 1:index],
            "sample_rate": sample_rate,
        }
        for index in range(1, waveform.shape[0] + 1)
    }


class MiniMaxH3SamplingSetup:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "av_latent": ("LATENT",),
                "steps": ("INT", {"default": 4, "min": 1, "max": 1000}),
                "shift_video": ("FLOAT", {"default": 12.0, "min": 0.01, "max": 100.0, "step": 0.01}),
                "shift_audio": ("FLOAT", {"default": 3.0, "min": 0.01, "max": 100.0, "step": 0.01}),
            },
            "optional": {
                "sampler_name": (SAMPLER_OPTIONS, {"default": DEFAULT_SAMPLER_NAME}),
                "scheduler": (SCHEDULER_OPTIONS, {"default": DEFAULT_SCHEDULER_NAME}),
            },
        }

    RETURN_TYPES = ("MODEL", "SAMPLER", "SIGMAS")
    RETURN_NAMES = ("model", "sampler", "sigmas")
    FUNCTION = "setup"
    CATEGORY = "model/sampling/minimax"

    def setup(
        self,
        model,
        av_latent,
        steps=4,
        shift_video=12.0,
        shift_audio=3.0,
        sampler_name=DEFAULT_SAMPLER_NAME,
        scheduler=DEFAULT_SCHEDULER_NAME,
    ):
        return setup_dual_clock_sampling(
            model,
            av_latent,
            steps,
            shift_video,
            shift_audio,
            sampler_name,
            scheduler,
        )


class MiniMaxH3BatchRefToVideo:
    """T8 MiniMax H3 conditioning with IMAGE/AUDIO batch inputs."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clip": ("CLIP",),
                "vae": ("VAE",),
                "audio_vae": ("VAE",),
                "prompt": ("STRING", {"multiline": True}),
                "width": ("INT", {"default": 1344, "min": 32, "max": nodes.MAX_RESOLUTION, "step": 32}),
                "height": ("INT", {"default": 768, "min": 32, "max": nodes.MAX_RESOLUTION, "step": 32}),
                "length": ("INT", {"default": 124, "min": 5, "max": 3600, "step": 17}),
                "task_type": (["auto", "T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA", "Hybrid"], {"default": "auto"}),
                "audio_mode": (
                    ["native", "reference_only", "lock_source", "remix_source"],
                    {"default": "native"},
                ),
                "audio_denoise_strength": (
                    "FLOAT",
                    {"default": 0.35, "min": 0.0, "max": 1.0, "step": 0.01},
                ),
                "add_source_as_reference": ("BOOLEAN", {"default": True}),
                "prompt_primary_audio_ordinal": ("INT", {"default": 1, "min": 0, "max": 9, "step": 1}),
                "strict_prompt_tags": ("BOOLEAN", {"default": True}),
                "ref_image_size": (["match", "max"], {"default": "match"}),
                "reference_video_policy": (
                    ["official_2_to_15s", "model_minimum"],
                    {"default": "official_2_to_15s"},
                ),
                "allow_above_reference_area": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "drive_audio": ("AUDIO",),
                "final_audio": ("AUDIO",),
                "first_frame": ("IMAGE",),
                "last_frame": ("IMAGE",),
                "ref_images": ("IMAGE",),
                "ref_images_metadata": ("STRING",),
                "ref_audios": ("AUDIO",),
                "ref_video_1": ("IMAGE",),
                "ref_video_2": ("IMAGE",),
                "ref_video_3": ("IMAGE",),
                "ref_video_audio_1": ("AUDIO",),
                "ref_video_audio_2": ("AUDIO",),
                "ref_video_audio_3": ("AUDIO",),
            },
        }

    # Keep the original first three outputs in place for existing workflows.
    RETURN_TYPES = ("CONDITIONING", "LATENT", "STRING", "AUDIO", "STRING", "STRING")
    RETURN_NAMES = ("positive", "latent", "processed_prompt", "mux_audio", "media_map_json", "report")
    FUNCTION = "execute"
    CATEGORY = "model/conditioning/minimax"

    def execute(
        self,
        clip,
        vae,
        audio_vae,
        prompt,
        width,
        height,
        length,
        task_type="auto",
        audio_mode="native",
        audio_denoise_strength=0.35,
        add_source_as_reference=True,
        prompt_primary_audio_ordinal=1,
        strict_prompt_tags=True,
        ref_image_size="match",
        reference_video_policy="official_2_to_15s",
        allow_above_reference_area=True,
        drive_audio=None,
        final_audio=None,
        first_frame=None,
        last_frame=None,
        ref_images=None,
        ref_images_metadata=None,
        ref_audios=None,
        **kwargs,
    ):
        if ref_images is None and ref_images_metadata is not None:
            raise ValueError("ref_images_metadata is only valid when ref_images is connected")
        image_values = (
            _restore_letterboxed_batch(ref_images, ref_images_metadata)
            if ref_images_metadata is not None
            else _image_batch_values(ref_images)
        )
        audio_values = _audio_batch_values(ref_audios)

        video_values = {}
        video_audio_values = {}
        for index in range(1, 4):
            video = kwargs.get(f"ref_video_{index}")
            soundtrack = kwargs.get(f"ref_video_audio_{index}")
            if video is not None:
                video_values[f"ref_video_{index}"] = video
            if soundtrack is not None:
                video_audio_values[f"ref_video_audio_{index}"] = soundtrack

        result = build_conditioning(
            clip,
            vae,
            audio_vae,
            prompt,
            width,
            height,
            length,
            task_type,
            audio_mode,
            audio_denoise_strength,
            add_source_as_reference,
            prompt_primary_audio_ordinal,
            strict_prompt_tags,
            ref_image_size,
            reference_video_policy,
            drive_audio,
            final_audio,
            first_frame,
            last_frame,
            image_values,
            video_values,
            video_audio_values,
            audio_values,
            allow_above_reference_area=allow_above_reference_area,
        )
        conditioning, latent, mux_audio, processed_prompt, media_map, report = result
        return (conditioning, latent, processed_prompt, mux_audio, media_map, report)


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3BatchRefToVideo": MiniMaxH3BatchRefToVideo,
    "MiniMaxH3SamplingSetup": MiniMaxH3SamplingSetup,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3BatchRefToVideo": "MiniMax H3 Batch Ref to Video (T8)",
    "MiniMaxH3SamplingSetup": "MiniMax H3 Sampling Setup (T8)",
}
