class ImageCounter:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("INT",)
    RETURN_NAMES = ("count",)
    FUNCTION = "count_images"
    CATEGORY = "my_comfyui/utils"

    def count_images(self, images):
        count = len(images)
        return (count,)


NODE_CLASS_MAPPINGS = {
    "ImageCounter": ImageCounter,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "ImageCounter": "Image Counter",
}
