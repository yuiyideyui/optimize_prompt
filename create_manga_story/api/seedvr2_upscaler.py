"""SeedVR2 single-image cleanup/upscale adapter."""

import sys
from pathlib import Path
from typing import Any


def upscale_confirmed_image(image: Any) -> Any:
    """Run the supplied SeedVR2 image workflow with its exact parameters."""
    if image is None:
        raise ValueError("SeedVR2 requires a confirmed IMAGE.")
    try:
        from seedvr2_videoupscaler.src.interfaces import (
            SeedVR2LoadDiTModel,
            SeedVR2LoadVAEModel,
            SeedVR2VideoUpscaler,
        )
    except ImportError:
        custom_nodes_root = Path(__file__).resolve().parents[3]
        if str(custom_nodes_root) not in sys.path:
            sys.path.insert(0, str(custom_nodes_root))
        try:
            from seedvr2_videoupscaler.src.interfaces import (
                SeedVR2LoadDiTModel,
                SeedVR2LoadVAEModel,
                SeedVR2VideoUpscaler,
            )
        except Exception as exc:
            raise RuntimeError(
                "SeedVR2 nodes could not be loaded: {}".format(exc)
            ) from exc

    dit = SeedVR2LoadDiTModel.execute(
        model="seedvr2_ema_7b_sharp_fp16.safetensors",
        device="cuda:0",
        blocks_to_swap=36,
        swap_io_components=False,
        offload_device="cpu",
        cache_model=False,
        attention_mode="sdpa",
    ).result[0]
    vae = SeedVR2LoadVAEModel.execute(
        model="ema_vae_fp16.safetensors",
        device="cuda:0",
        encode_tiled=True,
        encode_tile_size=1024,
        encode_tile_overlap=128,
        decode_tiled=True,
        decode_tile_size=1024,
        decode_tile_overlap=128,
        tile_debug="false",
        offload_device="cuda:0",
        cache_model=False,
    ).result[0]
    return SeedVR2VideoUpscaler.execute(
        image=image,
        dit=dit,
        vae=vae,
        seed=42,
        resolution=4096,
        max_resolution=4096,
        batch_size=1,
        uniform_batch_size=False,
        color_correction="lab",
        temporal_overlap=0,
        prepend_frames=0,
        input_noise_scale=0.0,
        latent_noise_scale=0.0,
        offload_device="cuda:0",
        enable_debug=False,
    ).result[0]
