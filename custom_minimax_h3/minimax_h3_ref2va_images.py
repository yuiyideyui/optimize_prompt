"""MiniMax H3 T8 conditioning and sampling nodes with batch media inputs."""

import json
import importlib
import sys
from pathlib import Path

import torch
import nodes
from safetensors import safe_open

from .t8_port.conditioning import build_conditioning
from .t8_port.sampling import (
    DEFAULT_SAMPLER_NAME,
    DEFAULT_SCHEDULER_NAME,
    SAMPLER_OPTIONS,
    SCHEDULER_OPTIONS,
    setup_dual_clock_sampling,
)


def _load_long_video_api():
    """Load the tested H3 long-video implementation when this node is installed alone."""
    try:
        return importlib.import_module("h3_t8.long_video")
    except ModuleNotFoundError:
        # The companion T8 package is shipped next to this custom node in the
        # portable ComfyUI distribution.  Keep the import lazy so normal
        # Ref2VA workflows do not depend on that package being present.
        custom_nodes = Path(__file__).resolve().parents[2]
        companion = custom_nodes / "comfyui-minimax-h3-audio-T8"
        if companion.is_dir() and str(companion) not in sys.path:
            sys.path.insert(0, str(companion))
        return importlib.import_module("h3_t8.long_video")


def _long_video_api():
    try:
        return _load_long_video_api()
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "MiniMax H3 continuation requires the companion "
            "comfyui-minimax-h3-audio-T8/h3_t8 package"
        ) from exc


def _load_context_file(path, target_segment_index=1):
    """Import an exported H3 context file without requiring its original chain id."""
    api = _long_video_api()
    path = Path(str(path or "")).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"H3 context file does not exist: {path}")
    with safe_open(str(path), framework="pt", device="cpu") as handle:
        metadata = dict(handle.metadata() or {})
        if set(handle.keys()) != {"video_tail", "audio_tail"}:
            raise ValueError("Invalid H3 context file: expected video_tail and audio_tail")
        video_tail = handle.get_tensor("video_tail")
        audio_tail = handle.get_tensor("audio_tail")
    try:
        source_index = int(metadata["source_segment_index"])
        max_context_frames = int(metadata["max_context_frames"])
        if int(metadata["schema"]) != api.LONG_VIDEO_SCHEMA:
            raise ValueError("Unsupported H3 context schema")
        if metadata.get("fps") != str(api.FPS):
            raise ValueError("H3 context FPS does not match the current H3 implementation")
        if metadata.get("video_sha256") != api._tensor_sha256(video_tail):
            raise ValueError("H3 context video checksum failed")
        if metadata.get("audio_sha256") != api._tensor_sha256(audio_tail):
            raise ValueError("H3 context audio checksum failed")
        if max_context_frames not in api.CONTEXT_FRAME_STEPS:
            raise ValueError("H3 context frame window is unsupported")
    except KeyError as exc:
        raise ValueError(f"H3 context metadata is incomplete: {exc.args[0]}") from exc
    return {
        "schema": api.LONG_VIDEO_SCHEMA,
        "empty": False,
        "video_tail": video_tail,
        "audio_tail": audio_tail,
        "metadata": {
            "schema": api.LONG_VIDEO_SCHEMA,
            "source_segment_index": source_index,
            "target_segment_index": int(target_segment_index),
            "fps": api.FPS,
            "source_total_frames": int(metadata.get("source_total_frames", 0)),
            "max_context_frames": max_context_frames,
            "audio_overhang": float(metadata.get("audio_overhang", 0.0)),
            "model_id": metadata.get("model_id", "unknown"),
            "sampling_summary": metadata.get("sampling_summary", "unknown"),
            "path": str(path),
        },
    }, True, json.dumps({
        "imported": True,
        "path": str(path),
        "source_segment_index": source_index,
        "target_segment_index": int(target_segment_index),
        "max_context_frames": max_context_frames,
        "checksums_valid": True,
    }, ensure_ascii=False, indent=2)


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
                "model": ("MODEL",),
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
                # When context is connected this node becomes the continuation
                # path; when it is not connected the legacy Ref2VA path is used.
                "context": ("H3_T8_CONTEXT",),
                "segment_index": ("INT", {"default": 0, "min": 0, "max": 99999}),
                "context_frames": ([0, 5, 22, 39], {"default": 0}),
                "context_audio": (["video_and_audio", "video_only"], {"default": "video_and_audio"}),
                "first_frame_reuse": (["segment0_only", "persistent_identity_reference"], {"default": "persistent_identity_reference"}),
                "continuity_reference_image": ("IMAGE",),
                "persistent_identity_strategy": (["single_reference", "scene_plus_identity"], {"default": "single_reference"}),
                "persistent_identity_interval": ("INT", {"default": 1, "min": 1, "max": 32}),
            },
        }

    # Keep the original first three outputs in place for existing workflows.
    RETURN_TYPES = ("CONDITIONING", "LATENT", "STRING", "AUDIO", "STRING", "STRING", "MODEL")
    RETURN_NAMES = ("positive", "latent", "processed_prompt", "mux_audio", "media_map_json", "report", "model")
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
        # A connected context opts into the long-video implementation while
        # keeping the original node and its first six outputs compatible.
        context = kwargs.get("context")
        if context is not None:
            api = _long_video_api()
            current_segment = int(kwargs.get("segment_index", 0) or 0)
            requested_context_frames = int(kwargs.get("context_frames", 0) or 0)
            # Segment 0 has no previous tail by definition.  This also makes
            # loop/subgraph workflows safe when the widget keeps the
            # continuation default (22) for every iteration.
            effective_context_frames = 0 if current_segment == 0 else requested_context_frames
            # Be tolerant of loop/subgraph wiring: accepting the exported
            # context path here is equivalent to using ContextLoad, while an
            # empty value is a valid context only for segment 0.
            if isinstance(context, str):
                if context.strip():
                    context = _load_context_file(context, current_segment)[0]
                elif current_segment == 0:
                    context = {
                        "schema": api.LONG_VIDEO_SCHEMA,
                        "empty": True,
                        "chain_id": api.sanitize_chain_id("my_h3_long_video"),
                        "target_segment_index": 0,
                    }
            elif isinstance(context, (tuple, list)) and context and isinstance(context[0], dict):
                # Some loop wrappers forward a multi-output node as a tuple.
                context = context[0]
            # Context and reference images serve different purposes and must
            # be combined for continuation: context preserves motion/camera/
            # spatial continuity, while ref_images preserves character and
            # scene identity.  Do not discard the image references merely
            # because a previous latent tail is connected.
            metadata_connected = bool(
                isinstance(ref_images_metadata, str) and ref_images_metadata.strip()
            )
            images = (
                _restore_letterboxed_batch(ref_images, ref_images_metadata)
                if metadata_connected
                else _image_batch_values(ref_images)
            )
            effective_strict_prompt_tags = strict_prompt_tags
            audios = _audio_batch_values(ref_audios)
            videos, video_audios = {}, {}
            for index in range(1, 4):
                video = kwargs.get(f"ref_video_{index}")
                soundtrack = kwargs.get(f"ref_video_audio_{index}")
                if video is not None:
                    videos[f"ref_video_{index}"] = video
                if soundtrack is not None:
                    video_audios[f"ref_video_audio_{index}"] = soundtrack
            first_frame_reuse = kwargs.get("first_frame_reuse", "persistent_identity_reference")
            if current_segment == 0 and first_frame is None:
                first_frame_reuse = "segment0_only"
            result = api.build_long_video_conditioning(
                clip,
                vae,
                audio_vae,
                context,
                current_segment,
                effective_context_frames,
                kwargs.get("context_audio", "video_and_audio"),
                prompt,
                width,
                height,
                length,
                task_type,
                audio_mode,
                audio_denoise_strength,
                add_source_as_reference,
                prompt_primary_audio_ordinal,
                effective_strict_prompt_tags,
                ref_image_size,
                reference_video_policy,
                drive_audio,
                final_audio,
                first_frame,
                last_frame,
                images,
                videos,
                video_audios,
                audios,
                first_frame_reuse,
                kwargs.get("continuity_reference_image"),
                kwargs.get("persistent_identity_strategy", "single_reference"),
                kwargs.get("persistent_identity_interval", 1),
            )
            conditioning, latent, mux_audio, processed_prompt, media_map, report = result
            patched_model = (
                api.patch_long_video_model(kwargs.get("model"))
                if kwargs.get("model") is not None
                else None
            )
            return (conditioning, latent, processed_prompt, mux_audio, media_map, report, patched_model)

        metadata_connected = bool(
            isinstance(ref_images_metadata, str) and ref_images_metadata.strip()
        )
        if ref_images is None and metadata_connected:
            raise ValueError("ref_images_metadata is only valid when ref_images is connected")
        image_values = (
            _restore_letterboxed_batch(ref_images, ref_images_metadata)
            if metadata_connected
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
        return (conditioning, latent, processed_prompt, mux_audio, media_map, report, kwargs.get("model"))


class MiniMaxH3LongVideoContextLoad:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"chain_id": ("STRING", {"default": "my_h3_long_video"}),
                              "segment_index": ("INT", {"default": 0, "min": 0, "max": 99999})},
                "optional": {"context_path": ("STRING", {"default": "", "multiline": False})}}
    RETURN_TYPES = ("H3_T8_CONTEXT", "BOOLEAN", "STRING")
    RETURN_NAMES = ("context", "has_context", "report")
    FUNCTION = "load"
    CATEGORY = "model/conditioning/minimax/long video"

    def load(self, chain_id, segment_index, context_path=""):
        if context_path and str(context_path).strip():
            return _load_context_file(context_path, segment_index)
        return _long_video_api().load_context_state(chain_id, segment_index)


class MiniMaxH3LongVideoContextSave:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"av_latent": ("LATENT",),
                              "chain_id": ("STRING", {"default": "my_h3_long_video"}),
                              "segment_index": ("INT", {"default": 0, "min": 0, "max": 99999}),
                              "save_context": ("BOOLEAN", {"default": True})},
                "optional": {"model_id": ("STRING", {"default": "unknown"}),
                              "sampling_summary": ("STRING", {"default": "dual_clock_euler/native_flow"})}}
    RETURN_TYPES = ("STRING", "STRING", "H3_T8_CONTEXT")
    RETURN_NAMES = ("context_path", "report", "context")
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "model/conditioning/minimax/long video"

    def save(self, av_latent, chain_id, segment_index, save_context,
             model_id="unknown", sampling_summary="dual_clock_euler/native_flow"):
        api = _long_video_api()
        context_path, report = api.save_context_state(
            av_latent, chain_id, segment_index, model_id, sampling_summary, save_context
        )
        if save_context:
            # Reload the validated file so the outgoing socket carries the
            # exact same checksummed context that the next segment will use.
            context, _, _ = api.load_context_state(chain_id, int(segment_index) + 1)
        else:
            # A final segment has no valid continuation tail. Keep the socket
            # type stable and make it harmless if accidentally connected.
            context = {"schema": api.LONG_VIDEO_SCHEMA, "empty": True,
                       "chain_id": api.sanitize_chain_id(chain_id),
                       "target_segment_index": int(segment_index) + 1}
        return context_path, report, context


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3BatchRefToVideo": MiniMaxH3BatchRefToVideo,
    "MiniMaxH3SamplingSetup": MiniMaxH3SamplingSetup,
    "MiniMaxH3LongVideoContextLoad": MiniMaxH3LongVideoContextLoad,
    "MiniMaxH3LongVideoContextSave": MiniMaxH3LongVideoContextSave,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3BatchRefToVideo": "MiniMax H3 Batch Ref to Video (T8)",
    "MiniMaxH3SamplingSetup": "MiniMax H3 Sampling Setup (T8)",
    "MiniMaxH3LongVideoContextLoad": "MiniMax H3 Load Previous Context (T8)",
    "MiniMaxH3LongVideoContextSave": "MiniMax H3 Save Continuation Context (T8)",
}
