import os
import json
import folder_paths
from PIL import Image, ImageOps
import numpy as np
import torch

class DirectorTableNode:
    """Load director-table images individually and as a legacy IMAGE batch."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                # 接收前端传来的 JSON 字符串（包含卡片数据、图片路径、角色标签等）
                "director_data": ("STRING", {"default": "[]", "multiline": True}),
                "batch_resize_mode": (
                    ["center_crop", "stretch", "letterbox"],
                    {"default": "center_crop"},
                ),
            }
        }

    RETURN_TYPES = ("IMAGE", "INT", "STRING", "STRING")
    RETURN_NAMES = ("images", "count", "roles_summary", "image_metadata")
    FUNCTION = "process_director_table"
    CATEGORY = "my_comfyui/director"

    def process_director_table(self, director_data, batch_resize_mode="center_crop"):
        if batch_resize_mode not in {"center_crop", "stretch", "letterbox"}:
            raise ValueError(f"Unknown batch_resize_mode: {batch_resize_mode}")

        try:
            items = json.loads(director_data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid director_data JSON: {exc.msg}") from exc
        if not isinstance(items, list):
            raise ValueError("director_data must be a JSON list of image cards")

        images = []
        roles = []
        base_dir = folder_paths.get_input_directory()

        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Each director_data item must be a JSON object")
            filename = item.get("filename")
            role_tag = item.get("role", "Default")
            
            if not filename:
                continue

            filepath = filename if os.path.isabs(filename) else os.path.join(base_dir, filename)
            if not os.path.isfile(filepath):
                raise FileNotFoundError(f"Director card image does not exist: {filepath}")

            try:
                with Image.open(filepath) as source:
                    img = source.convert("RGB")
                    img_array = np.array(img).astype(np.float32) / 255.0
            except (OSError, ValueError) as exc:
                raise ValueError(f"Could not load director card image {filepath}: {exc}") from exc
            images.append(torch.from_numpy(img_array))
            roles.append(role_tag)

        if not images:
            dummy = torch.zeros((1, 512, 512, 3), dtype=torch.float32)
            metadata = {
                "version": 1,
                "resize_mode": batch_resize_mode,
                "canvas": [512, 512],
                "items": [{
                    "index": 1,
                    "top": 0,
                    "left": 0,
                    "height": 512,
                    "width": 512,
                }],
            }
            return (
                dummy,
                0,
                "No valid images",
                json.dumps(metadata),
            )


        # IMAGE batches require a common canvas. The selected policy controls
        # how source aspect ratios are mapped to that shared output size.
        target_h = max(image.shape[0] for image in images)
        target_w = max(image.shape[1] for image in images)
        fitted_images = []
        image_metadata = []
        for img in images:
            top = left = 0
            fitted_h, fitted_w = target_h, target_w
            if img.shape[:2] == (target_h, target_w):
                fitted_images.append(img)
            else:
                if batch_resize_mode == "center_crop":
                    pil_img = Image.fromarray((img.numpy() * 255).round().astype(np.uint8))
                    pil_img = ImageOps.fit(
                        pil_img,
                        (target_w, target_h),
                        method=Image.Resampling.LANCZOS,
                        centering=(0.5, 0.5),
                    )
                    array = np.array(pil_img).astype(np.float32) / 255.0
                    fitted_images.append(torch.from_numpy(array))
                    fitted_h, fitted_w = img.shape[:2]
                    top = left = -1
                elif batch_resize_mode == "stretch":
                    pil_img = Image.fromarray((img.numpy() * 255).round().astype(np.uint8))
                    pil_img = pil_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
                    array = np.array(pil_img).astype(np.float32) / 255.0
                    fitted_images.append(torch.from_numpy(array))
                    fitted_h, fitted_w = img.shape[:2]
                    top = left = -1
                else:
                    padded = img.new_zeros((target_h, target_w, img.shape[2]))
                    top = (target_h - img.shape[0]) // 2
                    left = (target_w - img.shape[1]) // 2
                    padded[top:top + img.shape[0], left:left + img.shape[1]] = img
                    fitted_images.append(padded)
                    fitted_h, fitted_w = img.shape[:2]
            image_metadata.append({
                "index": len(image_metadata) + 1,
                "top": top,
                "left": left,
                "height": fitted_h,
                "width": fitted_w,
            })

        combined = torch.stack(fitted_images, dim=0)
        metadata = {
            "version": 1,
            "resize_mode": batch_resize_mode,
            "canvas": [target_h, target_w],
            "items": image_metadata,
        }
        return (
            combined,
            len(images),
            ", ".join(roles),
            json.dumps(metadata),
        )

NODE_CLASS_MAPPINGS = {
    "DirectorTableNode": DirectorTableNode,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "DirectorTableNode": "🎬 Director Table (Card Manager)",
}