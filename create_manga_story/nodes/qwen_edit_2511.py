def execute_node_safely(node_instance, **kwargs):
    """自动获取 ComfyUI 节点中由 FUNCTION 指定的主函数并执行"""
    func_name = getattr(node_instance, "FUNCTION", None)
    if func_name and hasattr(node_instance, func_name):
        return getattr(node_instance, func_name)(**kwargs)
    raise AttributeError(f"Node {type(node_instance).__name__} does not have a valid execution function mapping.")


def get_core_nodes():
    """Resolve ComfyUI dependencies only when the node is executed.

    This keeps plugin discovery and unit tests working on ComfyUI builds that do
    not provide the Qwen Image Edit node set.
    """
    from nodes import NODE_CLASS_MAPPINGS

    required = (
        "UNETLoader", "CLIPLoader", "VAELoader", "FluxKontextImageScale",
        "TextEncodeQwenImageEditPlus", "ModelSamplingAuraFlow", "CFGNorm",
        "FluxKontextMultiReferenceLatentMethod", "VAEEncode", "KSampler", "VAEDecode",
    )
    missing = [name for name in required if name not in NODE_CLASS_MAPPINGS]
    if missing:
        raise RuntimeError(
            "QwenImageEditCustomNode requires ComfyUI nodes that are unavailable: "
            + ", ".join(missing)
        )
    return {name: NODE_CLASS_MAPPINGS[name]() for name in required}

class QwenImageEditCustomNode:
    @classmethod
    def INPUT_TYPES(s):
        import folder_paths

        return {
            "required": {
                "image1": ("IMAGE",),
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "unet_name": (folder_paths.get_filename_list("diffusion_models"),),
                "clip_name": (folder_paths.get_filename_list("text_encoders"),),
                "vae_name": (folder_paths.get_filename_list("vae"),),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "steps": ("INT", {"default": 40, "min": 1, "max": 1000}),
                "cfg": ("FLOAT", {"default": 4.0, "min": 0.0, "max": 100.0}),
            },
            "optional": {
                "image2": ("IMAGE",),
                "image3": ("IMAGE",),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("IMAGE",)
    FUNCTION = "edit_image"
    CATEGORY = "QwenImageEdit"

    def edit_image(self, image1, prompt, unet_name, clip_name, vae_name, seed, steps, cfg, image2=None, image3=None):
        return (generate_qwen_image_edit(
            image1=image1,
            prompt=prompt,
            unet_name=unet_name,
            clip_name=clip_name,
            vae_name=vae_name,
            seed=seed,
            steps=steps,
            cfg=cfg,
            image2=image2,
            image3=image3,
        ),)


def generate_qwen_image_edit(
    image1,
    prompt,
    unet_name,
    clip_name,
    vae_name,
    seed,
    steps,
    cfg,
    image2=None,
    image3=None,
):
    """Run Qwen Image Edit through the native ComfyUI node implementations.

    This is the shared execution boundary for the regular node and automated
    asset workflows. It deliberately returns only the decoded IMAGE so callers
    can keep workflow-specific persistence and reporting outside the model
    execution layer.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Qwen Image Edit prompt cannot be empty.")
    prompt = (
        "PRIMARY STYLE AND IDENTITY RULE: image1 is the authoritative comic reference. "
        "Preserve the exact original manga/anime visual style from image1: line art, "
        "ink weight, coloring method, cel shading or painterly shading, palette, "
        "facial rendering, proportions, texture, lighting, and detail level. "
        "Do not convert the character into photorealistic, 3D, western comic, generic "
        "anime, or another illustration style. Do not redesign, beautify, age, or "
        "change the character identity. Supplementary images may clarify missing details "
        "but must never override image1. Only perform the requested extraction, cleanup, "
        "completion, or resolution improvement.\n\n" + prompt
    )
    if "CHARACTER EXTRACTION MODE" in prompt:
        prompt += (
            "\n\nCHARACTER STYLE LOCK: Every front, side, and back view must depict the same "
            "character in the exact source comic style. Preserve the source face, hair, "
            "body proportions, clothing shapes, colors, accessories, and cel/painterly "
            "rendering. Improve clarity only; do not reinterpret the design."
        )
    nodes = get_core_nodes()

    # 1. 加载模型
    model = execute_node_safely(nodes["UNETLoader"], unet_name=unet_name, weight_dtype="default")[0]
    clip = execute_node_safely(nodes["CLIPLoader"], clip_name=clip_name, type="qwen_image", device="default")[0]
    vae = execute_node_safely(nodes["VAELoader"], vae_name=vae_name)[0]

    # 2. 图像缩放处理
    scaled_image = execute_node_safely(nodes["FluxKontextImageScale"], image=image1)[0]

    # 3. 文本编码 (正向 & 负向)
    cond_neg = execute_node_safely(
        nodes["TextEncodeQwenImageEditPlus"],
        clip=clip, vae=vae, prompt="", image1=scaled_image, image2=image2, image3=image3
    )[0]
    cond_pos = execute_node_safely(
        nodes["TextEncodeQwenImageEditPlus"],
        clip=clip, vae=vae, prompt=prompt, image1=scaled_image, image2=image2, image3=image3
    )[0]

    # 4. 模型采样参数调整与参考方法包装
    model_sampled = execute_node_safely(nodes["ModelSamplingAuraFlow"], model=model, shift=3.1)[0]
    patched_model = execute_node_safely(nodes["CFGNorm"], model=model_sampled, strength=1.0, pre_cfg=False)[0]
    cond_neg_ref = execute_node_safely(
        nodes["FluxKontextMultiReferenceLatentMethod"],
        conditioning=cond_neg, reference_latents_method="index_timestep_zero"
    )[0]
    cond_pos_ref = execute_node_safely(
        nodes["FluxKontextMultiReferenceLatentMethod"],
        conditioning=cond_pos, reference_latents_method="index_timestep_zero"
    )[0]

    # 5. VAE 编码
    latent_image = execute_node_safely(nodes["VAEEncode"], vae=vae, pixels=scaled_image)[0]

    # 6. KSampler 采样
    sampled_latent = execute_node_safely(
        nodes["KSampler"],
        model=patched_model,
        seed=seed,
        steps=steps,
        cfg=cfg,
        sampler_name="euler",
        scheduler="simple",
        positive=cond_pos_ref,
        negative=cond_neg_ref,
        latent_image=latent_image,
        denoise=1.0
    )[0]

    # 7. VAE 解码输出图像
    output_image = execute_node_safely(nodes["VAEDecode"], samples=sampled_latent, vae=vae)[0]

    return output_image
