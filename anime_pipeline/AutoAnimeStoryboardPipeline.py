import os
import re
import json
import logging
from pathlib import Path

import torch
import numpy as np
from PIL import Image

from .prompts import SYSTEM_PROMPT_GENERATE, SYSTEM_PROMPT_AUTO_REFINE

logger = logging.getLogger("ComfyUI-AutoAnimePipeline")


class AutoAnimeStoryboardPipeline:
    """
    Generate an anime storyboard, parse all Part sections, refine each Part in order,
    preserve previous-part context, generate progressive summaries for long stories,
    and save intermediate/final outputs.
    """
    CATEGORY = "LLM/Anime"
    FUNCTION = "run_pipeline"
    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("final_storyboard", "story_summary", "output_directory", "status")
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "user_prompt": ("STRING", {
                    "multiline": True,
                    "default": "请根据参考图和以下剧情生成日本传统2D动画分镜（本段目标：生成约1分钟的小剧情，共8个Part）。\n\n剧情：在书房中，两名角色围绕一份文件展开对话。"
                }),
                "api_key": ("STRING", {"default": "", "multiline": False, "password": True}),
                "model_name": (["gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro"],),
                "output_dir": ("STRING", {"default": "output/anime_storyboard", "multiline": False}),
                "part_duration": ("INT", {"default": 7, "min": 1, "max": 60, "step": 1}),
                "max_retries": ("INT", {"default": 2, "min": 0, "max": 5, "step": 1}),
            },
            "optional": {
                "images": ("IMAGE",),
                "ref_images_metadata": ("STRING", {"multiline": False}),
                "history_context": ("STRING", {"multiline": True, "default": ""}),
                "previous_summary": ("STRING", {"multiline": True, "default": ""}),
                "image_scale": ("FLOAT", {"default": 0.5, "min": 0.1, "max": 1.0, "step": 0.1}),
                "image_quality": ("INT", {"default": 85, "min": 1, "max": 100, "step": 5}),
            }
        }

    @staticmethod
    def _extract_dialogue_audio_refs(text):
        """提取文本中所有对话和关联的音频引用。返回 {audio_id: [dialogue_snippets]}"""
        audio_dialogues = {}
        # 匹配 <Audio N> 或 <audio N> 以及周围的对话内容
        pattern = r"[：:\"\']*([^：\"\'\n]*?)[：:\"\']*\s*<[Aa]udio\s+(\d+)>"
        for match in re.finditer(pattern, text):
            dialogue = match.group(1).strip()
            audio_id = match.group(2)
            if dialogue:
                if audio_id not in audio_dialogues:
                    audio_dialogues[audio_id] = []
                audio_dialogues[audio_id].append(dialogue)
        return audio_dialogues

    @staticmethod
    def _split_parts(text):
        pattern = re.compile(r"^#{1,6}\s+Part\s+(\d+)\b.*$", re.MULTILINE | re.IGNORECASE)
        matches = list(pattern.finditer(text))
        if not matches:
            raise ValueError("模型输出中未找到合法的 Markdown Part 标题（例如 ## Part 1）。")
        parts = []
        for i, match in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            part = text[match.start():end].strip()
            number = int(match.group(1))
            if not part:
                raise ValueError(f"Part {number} 内容为空。")
            parts.append((number, part))
        numbers = [n for n, _ in parts]
        if len(numbers) != len(set(numbers)):
            raise ValueError(f"检测到重复 Part 编号：{numbers}")
        if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
            raise ValueError(f"Part 编号不连续：{numbers}")
        return parts

    @staticmethod
    def _restore_letterboxed_batch(batch, metadata):
        """Restore original images from letterboxed batch using metadata.
        If restoration fails (e.g., due to size mismatch), return original batch."""
        if metadata is None or not isinstance(metadata, str) or not metadata.strip():
            return batch

        try:
            data = json.loads(metadata)
        except json.JSONDecodeError:
            # Invalid JSON, skip restoration
            return batch

        if not isinstance(data, dict) or data.get("version") != 1:
            return batch
        if data.get("resize_mode") != "letterbox":
            return batch
        if not isinstance(batch, torch.Tensor) or batch.ndim != 4:
            return batch

        canvas = data.get("canvas")
        items = data.get("items")
        if (
            not isinstance(canvas, list)
            or len(canvas) != 2
            or canvas != [int(batch.shape[1]), int(batch.shape[2])]
        ):
            return batch
        if not isinstance(items, list) or len(items) != batch.shape[0]:
            return batch

        restored = []
        try:
            for index, item in enumerate(items, 1):
                if not isinstance(item, dict) or item.get("index") != index:
                    return batch
                top, left, height, width = (
                    item.get("top"),
                    item.get("left"),
                    item.get("height"),
                    item.get("width"),
                )
                if any(not isinstance(value, int) or isinstance(value, bool) for value in (top, left, height, width)):
                    return batch
                if (
                    top < 0
                    or left < 0
                    or height < 1
                    or width < 1
                    or top + height > batch.shape[1]
                    or left + width > batch.shape[2]
                ):
                    return batch
                restored.append(batch[
                    index - 1:index,
                    top:top + height,
                    left:left + width,
                    :,
                ].contiguous())

            # Try to concatenate
            return torch.cat(restored, dim=0)
        except (RuntimeError, ValueError):
            # If concatenation fails or any other error, use original batch
            return batch

    @staticmethod
    def _images_to_pil(images, ref_images_metadata=None, image_scale=0.5, image_quality=85):
        result = []
        if images is None:
            if ref_images_metadata is not None:
                raise ValueError("ref_images_metadata requires images to be connected")
            return result
        tensor = images.detach().cpu() if hasattr(images, "detach") else torch.as_tensor(images)
        if tensor.ndim == 3:
            tensor = tensor.unsqueeze(0)
        if tensor.ndim != 4:
            raise ValueError("images must have shape [B, H, W, C]")

        # Only restore letterboxed batch if metadata is explicitly provided
        if ref_images_metadata is not None:
            tensor = AutoAnimeStoryboardPipeline._restore_letterboxed_batch(tensor, ref_images_metadata)

        for item in tensor:
            arr = item.numpy()
            arr = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
            if arr.shape[-1] == 4:
                arr = arr[:, :, :3]
            pil_img = Image.fromarray(arr, mode="RGB")
            # 压缩分辨率以加速 API 调用
            if image_scale < 1.0:
                compressed_size = (int(pil_img.width * image_scale), int(pil_img.height * image_scale))
                pil_img = pil_img.resize(compressed_size, Image.Resampling.LANCZOS)
            # 压缩画质（通过 JPEG 转换）
            if image_quality < 100:
                import io
                buffer = io.BytesIO()
                pil_img.save(buffer, format="JPEG", quality=image_quality)
                buffer.seek(0)
                pil_img = Image.open(buffer).convert("RGB")
            result.append(pil_img)
        return result

    @staticmethod
    def _call_model(client, model_name, contents, system_prompt, temperature, max_tokens, retries):
        last_error = None
        from google.genai import types
        for attempt in range(retries + 1):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=temperature,
                        max_output_tokens=max_tokens,
                    ),
                )
                text = getattr(response, "text", None)
                if not text or not text.strip():
                    raise RuntimeError("模型返回空文本。")
                return text.strip()
            except Exception as exc:
                last_error = exc
                logger.warning("Gemini 调用失败（%d/%d）：%s", attempt + 1, retries + 1, exc)
        raise RuntimeError(f"Gemini 调用失败，已尝试 {retries + 1} 次：{last_error}") from last_error

    def run_pipeline(
        self,
        user_prompt,
        api_key,
        model_name,
        output_dir,
        part_duration,
        max_retries,
        images=None,
        history_context="",
        previous_summary="",
        ref_images_metadata=None,
        image_scale=0.5,
        image_quality=85,
    ):
        from google import genai

        api_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        if not api_key:
            raise ValueError("请在节点的 api_key 字段填写 Gemini API Key，或设置环境变量 GEMINI_API_KEY。")

        client = genai.Client(api_key=api_key)
        pil_images = self._images_to_pil(images, ref_images_metadata, image_scale, image_quality)
        output_path = Path(output_dir)
        if not output_path.is_absolute():
            output_path = Path.cwd() / output_path
        output_path.mkdir(parents=True, exist_ok=True)

        # 1. 自动计算续写时的起始 Part 编号
        next_start_part = 1
        if history_context and history_context.strip():
            try:
                parsed_history = self._split_parts(history_context)
                if parsed_history:
                    next_start_part = max(n for n, _ in parsed_history) + 1
            except Exception as e:
                logger.warning("解析 history_context 失败，将按默认从 Part 1 开始：%s", e)

        reference_count = max(
            len(pil_images),
            max([int(x) for x in re.findall(r"<picture\s+(\d+)>", user_prompt or "", re.I)] or [0])
        )

        # 新增：解析 user_prompt 中声明的音频素材数量及编号（支持 <Audio 1>, <audio 1> 等）
        audio_matches = [int(x) for x in re.findall(r"<[Aa]udio\s+(\d+)>", user_prompt or "", re.I)]
        audio_count = max(audio_matches) if audio_matches else 0
        
        # 图像引用规则
        numbering_rule = (
            f"本次任务声明的原始参考图数量为 {reference_count} 张，编号槽位固定为 "
            + (f"`<picture 1>` 至 `<picture {reference_count}>`。" if reference_count else "0张。")
            + f"实际上传并可供模型查看的初始图片为 {len(pil_images)} 张。所有分镜中的素材调用**必须且只能**使用原始参考图编号（如 `<picture 1>`, `<picture 2>`），"
            + "绝对禁止引用或虚构所谓的“上一帧图片”，防止视频生成出现色彩累加失真。\n"
            + (f"当前为续写任务，新生成的 Part 编号必须从 **Part {next_start_part}** 开始连续编号，严禁从 Part 1 重新开始。" if next_start_part > 1 else "")
        )

        # 新增：音频引用规则约束
        audio_rule = ""
        if audio_count > 0:
            audio_rule = (
                f"本次任务声明的音频素材共有 {audio_count} 个，编号槽位固定为 `<Audio 1>` 至 `<Audio {audio_count}>`。"
                f"分镜中**仅当**涉及需要音频绑定的角色参考音频或配音时，必须且只能使用原生的音频编号（如 `<Audio 1>`, `<Audio 2>`），"
                "严禁随意编造或修改音频编号格式。无关角色的台词可以不标注音频编号。\n"
            )

        summary_section = ""
        if previous_summary and previous_summary.strip():
            summary_section = f"\n\n【前序剧情全局总结（供参考的宏观背景）】：\n{previous_summary.strip()}\n"

        history_section = ""
        if history_context and history_context.strip():
            history_section = (
                f"\n\n【最近详细分镜回顾（用于文字逻辑、站位与动作的顺畅衔接）】：\n"
                f"{history_context.strip()}\n\n"
                f"【续写指令】：请紧接着上文结尾的文字状态，从 **Part {next_start_part}** 开始向后续写，目标生成新的一批 Part（凑够 1 分钟剧情）。保持角色站位、情感及场景逻辑严密衔接，切勿直接复用旧图像像素。\n"
            )

        dialogue_rule = ""
        if audio_count > 0:
            dialogue_rule = (
                "【对话与音频绑定规则】：\n"
                '- 当需要音频绑定的角色说台词时，必须在台词后立即标注其对应的音频编号（如 `角色A："台词内容" <Audio 1>`）\n'
                "- 一个音频号对应一个角色或角色的一段台词，避免混乱和重复\n"
                "- 无关角色（没有提供 Audio 或 Picture 的角色）的台词**不需要**标注音频编号，可直接使用\n"
                '- 对话格式严格遵守：`"台词" <Audio N>` 或 `角色名："台词" <Audio N>`（仅对有音频绑定的角色）\n'
                "- 严禁对有绑定的角色篡改、丢失或错误映射音频编号\n\n"
            )

        generation_prompt = (
            f"{user_prompt}\n"
            f"{summary_section}"
            f"{history_section}\n\n"
            f"{numbering_rule}\n"
            f"{audio_rule}"
            f"{dialogue_rule}"
            f"每个Part的时长不超过 {part_duration} 秒（可根据剧情节奏灵活调整，只要不超过此上限）。\n"
            "请严格遵守系统提示词中的输出格式和镜头规则。注意动作连贯性与正反打剪辑规范。"
        )
        
        logger.info("自动分镜流水线启动：model=%s, images=%d, audio_count=%d, 起始Part=%d", model_name, len(pil_images), audio_count, next_start_part)
        raw_outline = self._call_model(
            client, model_name, [generation_prompt, *pil_images],
            SYSTEM_PROMPT_GENERATE, 0.4, 8192, max_retries
        )

        parts = self._split_parts(raw_outline)
        refined_parts = []
        for index, (part_number, part_content) in enumerate(parts, start=1):
            logger.info("精修 Part %d/%d (Part %d)", index, len(parts), part_number)
            previous_context = ""
            if refined_parts:
                previous_context = (
                    f"\n\n<PREVIOUS_PART_CONTEXT>\n"
                    f"以下是前一个已精修完成的Part文字内容，用于核对当前Part开头的空间位置与动作连续性（仅作文字参考，不涉及图像像素）：\n"
                    f"{refined_parts[-1]}\n</PREVIOUS_PART_CONTEXT>"
                )
            
            refine_prompt = (
                "精修以下分镜，只修复明确问题，不重写剧情。\n"
                f"当前处理序号：{index}/{len(parts)}；原始Part编号：{part_number}。\n"
                f"每个Part时长不超过 {part_duration} 秒（可根据剧情需要灵活调整，只要不超过此上限）。\n"
                f"本次声明参考图 {reference_count} 张；实际上传 {len(pil_images)} 张。\n"
                + (f"本次声明音频素材 {audio_count} 个。\n" if audio_count > 0 else "")
                + "所有图像素材名称必须写成原始的 `<picture number>` 占位符，绝对不要出现任何上一帧图片编号。\n"
                + ("对于有音频绑定的角色，所有音频引用必须严格保留原有的 `<Audio number>` 格式。无关角色的台词可不标注音频。\n" if audio_count > 0 else "")
                + ("对于有音频绑定的角色，所有对话必须保留其关联的音频编号（如 `台词 <Audio 1>`），确保对话-音频映射的完整性。\n" if audio_count > 0 else "")
                + "必须保留原始Part编号、标题、剧情、对白、镜头编号。\n"
                + f"{previous_context}\n\n<PART_CONTENT>\n{part_content}\n</PART_CONTENT>"
            )
            refined = self._call_model(
                client, model_name, [refine_prompt],
                SYSTEM_PROMPT_AUTO_REFINE, 0.3, 4096, max_retries
            )
            if not re.search(r"^#{1,6}\s+Part\s+\d+\b.*$", refined, re.M | re.I):
                raise ValueError(f"精修后的第 {part_number} 个 Part 缺少合法标题。")
            refined_parts.append(refined)

        new_refined_text = "\n\n".join(refined_parts)
        
        # 2. 自动对本次生成的新剧情进行阶段总结，并与旧总结进行合并演进
        summary_prompt = (
            "请根据以下新生成的动画分镜剧本，写一段精炼的剧情摘要（约100-200字），"
            "总结这段剧情里发生了什么核心事件、人物情感有什么变化、场景有无转换。\n\n"
            f"{new_refined_text}"
        )
        current_batch_summary = self._call_model(
            client, model_name, [summary_prompt],
            "你是一个专业的动漫编剧和剧情总结助手。", 0.3, 1024, max_retries
        )

        if previous_summary and previous_summary.strip():
            # 滚动合并总结
            merge_summary_prompt = (
                f"请将【已有剧情总总结】与【最新发生的剧情总结】融合成一个更全面、连贯的演进式故事大纲摘要（保留前因后果，控制在300字以内）：\n\n"
                f"【已有剧情总总结】：\n{previous_summary.strip()}\n\n"
                f"【最新发生的剧情总结】：\n{current_batch_summary}"
            )
            updated_summary = self._call_model(
                client, model_name, [merge_summary_prompt],
                "你是一个资深的动画总导演。", 0.3, 1024, max_retries
            )
        else:
            updated_summary = current_batch_summary

        # 3. 拼接全局历史文本
        if history_context and history_context.strip():
            final_text = history_context.strip() + "\n\n" + new_refined_text
        else:
            final_text = new_refined_text

        final_md = output_path / "anime_storyboard_final.md"
        final_md.write_text(final_text, encoding="utf-8")
        
        summary_md = output_path / "anime_storyboard_summary.md"
        summary_md.write_text(updated_summary, encoding="utf-8")

        status = f"完成：成功生成 {len(parts)} 个新 Part（从 Part {next_start_part} 开始）。全局剧情已更新。"
        logger.info(status)
        
        return (final_text, updated_summary, str(output_path), status)


NODE_CLASS_MAPPINGS = {
    "AutoAnimeStoryboardPipeline": AutoAnimeStoryboardPipeline,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "AutoAnimeStoryboardPipeline": "Auto Anime Storyboard Pipeline (Long Story & Summary)",
}
