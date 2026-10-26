"""Direct adapter for ComfyUI's native Qwen Image 2.1 edit graph."""

import io
from typing import Any, Optional


def compress_reference_for_qwen(
    image: Any, target_width: int = 1024, jpeg_quality: int = 30
) -> Any:
    """Reduce a reference before Qwen processing without changing the saved asset.

    Qwen receives a 1024px-wide JPEG-quality-30 copy. This prevents a previously
    optimized character sheet from making the edit encoder unnecessarily slow.
    """
    import numpy as np
    import torch
    from PIL import Image

    tensor = image.detach().cpu() if hasattr(image, "detach") else torch.from_numpy(np.asarray(image))
    if tensor.ndim == 3:
        tensor = tensor.unsqueeze(0)
    if tensor.ndim != 4 or tensor.shape[-1] not in (3, 4):
        raise ValueError("Qwen reference image must have shape [batch,height,width,channels].")
    first = tensor[0]
    array = first.numpy() if hasattr(first, "numpy") else np.asarray(first)
    array = np.clip(array * 255.0 if array.dtype.kind == "f" and array.max() <= 1.0 else array, 0, 255).astype(np.uint8)
    pil = Image.fromarray(array[:, :, :3], "RGB")
    if pil.width != target_width:
        height = max(1, round(pil.height * target_width / pil.width))
        pil = pil.resize((target_width, height), Image.Resampling.LANCZOS)
    encoded = io.BytesIO()
    pil.save(encoded, format="JPEG", quality=jpeg_quality, optimize=True)
    with Image.open(io.BytesIO(encoded.getvalue())) as decoded:
        pixels = np.asarray(decoded.convert("RGB"), dtype=np.float32) / 255.0
    return torch.from_numpy(pixels[None, ...])


def generate_qwen_image_21_edit(
    image1: Any,
    prompt: str,
    unet_name: str,
    clip_name: str,
    vae_name: str,
    seed: int,
    steps: int = 25,
    cfg: float = 1.0,
    image2: Optional[Any] = None,
    image3: Optional[Any] = None,
    resolution: int = 0,
    sampler_name: str = "euler",
    scheduler: str = "simple",
    cache_device: str = "auto",
    cache_dtype: str = "default",
    negative_prompt: str = "",
) -> Any:
    """Run the same core nodes used by the supplied Image Edit subgraph.

    The prompt-enhancer branch in the supplied workflow is disabled by default,
    so this adapter intentionally executes the native edit encoder directly.
    """
    if image1 is None:
        raise ValueError("Qwen Image 2.1 requires image1 as the primary reference.")
    if not prompt.strip():
        raise ValueError("Qwen Image 2.1 prompt cannot be empty.")
    if not unet_name.strip() or not clip_name.strip() or not vae_name.strip():
        raise ValueError("Qwen Image 2.1 requires UNet, CLIP, and VAE model names.")

    prompt = (
        "Use <image1> as the primary source of truth and the sole authority for "
        "visual style. Preserve the exact linework, brush/ink treatment, palette, "
        "shading, texture, proportions, composition, spatial structure, colors, "
        "lighting, and detail level visible in <image1>. "
        "Do not let supplementary references override <image1>.\n\n"
        + prompt
    )
    if "CHARACTER EXTRACTION MODE" in prompt:
        prompt += (
            "\n\nCHARACTER SHEET LAYOUT: This output must be one reference sheet on a "
            "uniform pure white background. Show the same character as a complete "
            "head-to-toe figure in both the front view and side profile, including "
            "hands, feet, shoes, clothing, and accessories, plus several distinct "
            "facial expressions in a separate row. Keep the character consistent across "
            "all views; do not output a headshot, bust, half-body crop, or floating head. "
            "Do not create separate characters, random poses, extra limbs, labels, text, "
            "borders, or panels. "
            "Complete only details supported by <image1> and preserve its exact style."
        )

    import nodes
    from comfy_extras.nodes_qwen import QwenImage21Cache, TextEncodeQwenImage21

    model = nodes.UNETLoader().load_unet(unet_name, "default")[0]
    model = QwenImage21Cache.execute(model, cache_device, cache_dtype)[0]
    clip = nodes.CLIPLoader().load_clip(clip_name, "qwen_image", "default")[0]
    vae = nodes.VAELoader().load_vae(vae_name)[0]

    references = {"image_1": image1}
    if image2 is not None:
        references["image_2"] = image2
    if image3 is not None:
        references["image_3"] = image3
    encoded = TextEncodeQwenImage21.execute(
        clip,
        prompt,
        negative_prompt,
        vae,
        resolution,
        references,
    ).result
    positive, negative, latent = encoded
    sampled = nodes.KSampler().sample(
        model,
        seed,
        steps,
        cfg,
        sampler_name,
        scheduler,
        positive,
        negative,
        latent,
        1.0,
    )[0]
    return nodes.VAEDecode().decode(vae, sampled)[0]
