"""Import comic pages from a directory or ComfyUI IMAGE batch."""

import hashlib
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.database import ComicDatabase
from ..core.storage import get_data_root, safe_component


_SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_PAGE_NAMESPACE = uuid.UUID("8a03d902-b3af-4c6f-95a1-8b7f37e32b0a")


def _natural_key(path: Path) -> List[Tuple[int, Any]]:
    return [
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", path.name)
    ]


def _image_dimensions(path: Path) -> Tuple[int, int]:
    try:
        from PIL import Image
    except ImportError as error:
        raise RuntimeError("Pillow is required to import comic images.") from error
    with Image.open(str(path)) as image:
        image.verify()
    with Image.open(str(path)) as image:
        return image.size


def _write_tensor_image(tensor: Any, destination: Path) -> None:
    try:
        from PIL import Image
    except ImportError as error:
        raise RuntimeError("Pillow is required to import ComfyUI IMAGE inputs.") from error
    if not hasattr(tensor, "detach"):
        raise TypeError("Each IMAGE batch item must be a ComfyUI tensor.")
    pixels = tensor.detach().cpu().numpy()
    pixels = (pixels.clip(0.0, 1.0) * 255.0).round().astype("uint8")
    if pixels.ndim != 3 or pixels.shape[-1] not in (1, 3, 4):
        raise ValueError("ComfyUI IMAGE items must have shape [height, width, channels].")
    if pixels.shape[-1] == 1:
        pixels = pixels[:, :, 0]
    image = Image.fromarray(pixels)
    if image.mode not in ("RGB", "RGBA", "L"):
        image = image.convert("RGB")
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(destination), format="PNG")


class ComicChapterLoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": "project-1"}),
                "chapter_id": ("STRING", {"default": "chapter-1"}),
                "sort_order": (["natural_filename", "filename"],),
                "duplicate_policy": (["skip", "keep", "error"],),
            },
            "optional": {
                "directory": ("STRING", {"default": "", "multiline": False}),
                "images": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("COMIC_PAGES", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("pages", "project_id", "chapter_id", "import_report")
    FUNCTION = "load_chapter"
    CATEGORY = "Comic2Anime/Import"

    def load_chapter(
        self,
        project_id: str,
        chapter_id: str,
        sort_order: str,
        duplicate_policy: str,
        directory: str = "",
        images: Optional[Any] = None,
    ):
        project_id = project_id.strip()
        chapter_id = chapter_id.strip()
        if not project_id or not chapter_id:
            raise ValueError("Project ID and chapter ID are required.")
        if duplicate_policy not in ("skip", "keep", "error"):
            raise ValueError("Unsupported duplicate image policy.")
        if not directory.strip() and images is None:
            raise ValueError("Provide a comic image directory or connect an IMAGE batch.")

        root = get_data_root()
        source_directory = Path(directory).expanduser() if directory.strip() else None
        project_directory = safe_component(project_id)
        chapter_directory = safe_component(chapter_id)
        image_directory = (
            root / "images" / project_directory / chapter_directory
        )
        candidates: List[Tuple[str, Path, Optional[Any]]] = []
        if source_directory is not None:
            if not source_directory.is_dir():
                raise NotADirectoryError("Comic image directory not found: {}".format(source_directory))
            paths = [
                path for path in source_directory.iterdir()
                if path.is_file() and path.suffix.casefold() in _SUPPORTED_EXTENSIONS
            ]
            paths.sort(key=_natural_key if sort_order == "natural_filename" else lambda path: path.name.casefold())
            for path in paths:
                candidates.append((str(path.resolve()), path, None))

        if images is not None:
            if not hasattr(images, "shape") or len(images.shape) != 4:
                raise ValueError("ComfyUI IMAGE input must have shape [batch, height, width, channels].")
            for index in range(int(images.shape[0])):
                candidates.append(
                    ("comfy-image-{:05d}".format(index + 1), Path(), images[index])
                )
        if not candidates:
            raise ValueError("No supported comic images were found.")
        if sort_order not in ("natural_filename", "filename"):
            raise ValueError("Unsupported page sorting rule.")

        root.mkdir(parents=True, exist_ok=True)
        db = ComicDatabase(root / "comic2anime.sqlite3")
        db.initialize()
        pages: List[Dict[str, Any]] = []
        duplicate_count = 0
        duplicate_details: List[Dict[str, Any]] = []
        errors: List[Dict[str, str]] = []
        seen_hashes: Dict[str, str] = {}
        for source_path, source_file, tensor in candidates:
            stable_source = (
                source_path
                if tensor is not None
                else str(source_file.resolve().relative_to(source_directory.resolve())).replace("\\", "/")
            )
            page_id = str(uuid.uuid5(
                _PAGE_NAMESPACE,
                "{}\0{}\0{}".format(project_id, chapter_id, stable_source.casefold()),
            ))
            if tensor is not None:
                destination = image_directory / "{}.png".format(page_id)
                if not destination.is_file():
                    _write_tensor_image(tensor, destination)
            else:
                destination = source_file.resolve()
            try:
                width, height = _image_dimensions(destination)
                digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            except (OSError, ValueError) as error:
                errors.append({"source_path": source_path, "error": str(error)})
                continue
            duplicate_of = seen_hashes.get(digest)
            if duplicate_of is not None:
                duplicate_count += 1
                duplicate_details.append(
                    {
                        "page_id": page_id,
                        "source_path": source_path,
                        "duplicate_of": duplicate_of,
                        "kept": duplicate_policy == "keep",
                    }
                )
                if duplicate_policy == "error":
                    raise ValueError(
                        "Duplicate comic image detected at page {} (same as {}).".format(
                            source_path, duplicate_of
                        )
                    )
                if duplicate_policy == "skip":
                    continue
            seen_hashes[digest] = page_id
            pages.append(
                {
                    "page_id": page_id,
                    "project_id": project_id,
                    "chapter_id": chapter_id,
                    "page_index": len(pages) + 1,
                    "source_path": source_path,
                    "image_path": str(destination.resolve()),
                    "sha256": digest,
                    "width": width,
                    "height": height,
                }
            )
        if not pages:
            if errors:
                raise ValueError(
                    "No valid comic pages were imported. First error: {}".format(
                        errors[0]["error"]
                    )
                )
            raise ValueError("No pages remain after applying the duplicate image policy.")
        db.save_pages(pages)
        report = {
            "total_found": len(candidates),
            "imported": len(pages),
            "duplicates": duplicate_count,
            "duplicate_details": duplicate_details,
            "errors": errors,
            "duplicate_policy": duplicate_policy,
            "database_path": str(db.database_path),
        }
        import json

        return (pages, project_id, chapter_id, json.dumps(report, ensure_ascii=False))


NODE_CLASS_MAPPINGS = {"ComicChapterLoader": ComicChapterLoader}
NODE_DISPLAY_NAME_MAPPINGS = {"ComicChapterLoader": "Comic Chapter Loader"}
