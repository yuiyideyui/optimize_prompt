"""Gemini-assisted, bounded asset prompt and visual-QC workflow nodes."""

import hashlib
import io
import json
import math
import uuid
from typing import Any, Dict, List, Optional, Tuple

from ..api.gemini_client import generate_structured_json
from ..core.asset_repository import ASSET_TYPES
from ..core.workflow_repository import ComicWorkflowRepository
from .asset_nodes import _encode_comfy_images, _json_input, _repository


def _workflow_repository() -> ComicWorkflowRepository:
    asset_repository = _repository()
    asset_repository.initialize()
    workflow = ComicWorkflowRepository(asset_repository.database)
    workflow.initialize()
    return workflow


_PROMPT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "prompt_version": {"type": "INTEGER"},
        "detailed_description": {"type": "STRING"},
        "reference_usage": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "reference_asset_id": {"type": "STRING"},
                    "role": {"type": "STRING"},
                    "instruction": {"type": "STRING"},
                },
                "required": ["reference_asset_id", "role", "instruction"],
            },
        },
        "must_preserve": {"type": "ARRAY", "items": {"type": "STRING"}},
        "allowed_changes": {"type": "ARRAY", "items": {"type": "STRING"}},
        "negative_constraints": {"type": "ARRAY", "items": {"type": "STRING"}},
        "uncertainties": {"type": "ARRAY", "items": {"type": "STRING"}},
        "validation_checklist": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": [
        "prompt_version", "detailed_description", "reference_usage",
        "must_preserve", "allowed_changes", "negative_constraints",
        "uncertainties", "validation_checklist",
    ],
}

_QC_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "decision": {
            "type": "STRING",
            "enum": ["PASS", "RETRY", "NEEDS_HUMAN_REVIEW", "ERROR"],
        },
        "overall_score": {"type": "NUMBER"},
        "checks": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "category": {"type": "STRING"},
                    "result": {"type": "STRING", "enum": ["PASS", "FAIL", "UNCERTAIN"]},
                    "severity": {"type": "STRING"},
                    "evidence": {"type": "STRING"},
                },
                "required": ["category", "result", "severity", "evidence"],
            },
        },
        "failures": {"type": "ARRAY"},
        "uncertainties": {"type": "ARRAY", "items": {"type": "STRING"}},
        "prompt_revision_instructions": {
            "type": "ARRAY", "items": {"type": "STRING"}
        },
        "reference_revision_instructions": {
            "type": "ARRAY", "items": {"type": "STRING"}
        },
        "human_review_required": {"type": "BOOLEAN"},
    },
    "required": [
        "decision", "overall_score", "checks", "failures", "uncertainties",
        "prompt_revision_instructions", "reference_revision_instructions",
        "human_review_required",
    ],
}


def _json_value(value: Any, field_name: str) -> Any:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("{} must contain valid JSON.".format(field_name)) from exc
    if not isinstance(value, dict):
        raise ValueError("{} must be a JSON object.".format(field_name))
    return value


def _single_image_bytes(image: Any) -> Tuple[bytes, str]:
    encoded = _encode_comfy_images(image)
    if len(encoded) != 1:
        raise ValueError("This workflow input requires exactly one IMAGE.")
    return encoded[0]["content"], "image/png"


def _optional_image_part(image: Any) -> Optional[Tuple[bytes, str]]:
    if image is None:
        return None
    return _single_image_bytes(image)


def _validate_prompt_response(result: Dict[str, Any], manifest: List[Dict[str, Any]]) -> Dict[str, Any]:
    required_arrays = (
        "must_preserve", "allowed_changes", "negative_constraints",
        "uncertainties", "validation_checklist", "reference_usage",
    )
    if (
        isinstance(result.get("prompt_version"), bool)
        or not isinstance(result.get("prompt_version"), int)
        or result["prompt_version"] < 1
        or not isinstance(result.get("detailed_description"), str)
        or not result["detailed_description"].strip()
    ):
        raise ValueError("Gemini prompt response has an invalid version or empty prompt.")
    for key in required_arrays:
        if not isinstance(result.get(key), list):
            raise ValueError("Gemini prompt response field {} must be an array.".format(key))
    for key in required_arrays[:-1]:
        if any(not isinstance(item, str) for item in result[key]):
            raise ValueError("Gemini prompt response {} must contain strings.".format(key))
    expected = {item["asset_id"] for item in manifest}
    usage = result["reference_usage"]
    if any(
        not isinstance(item, dict)
        or item.get("reference_asset_id") not in expected
        or not isinstance(item.get("role"), str)
        or not isinstance(item.get("instruction"), str)
        or not item["instruction"].strip()
        for item in usage
    ):
        raise ValueError("Gemini prompt response contains invalid reference usage.")
    if {item["reference_asset_id"] for item in usage} != expected:
        raise ValueError("Gemini prompt response must explain every selected reference.")
    return result


def _validate_qc_response(result: Dict[str, Any], threshold: float) -> Dict[str, Any]:
    decision = result.get("decision")
    if decision not in ("PASS", "RETRY", "NEEDS_HUMAN_REVIEW", "ERROR"):
        raise ValueError("Gemini QC decision is invalid.")
    score = result.get("overall_score")
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(score)
        or not 0 <= score <= 1
    ):
        raise ValueError("Gemini QC overall_score must be finite and between 0 and 1.")
    checks = result.get("checks")
    failures = result.get("failures")
    if not isinstance(checks, list) or not checks or not isinstance(failures, list):
        raise ValueError("Gemini QC must include non-empty checks and a failures array.")
    for check in checks:
        if (
            not isinstance(check, dict)
            or check.get("result") not in ("PASS", "FAIL", "UNCERTAIN")
            or not isinstance(check.get("category"), str)
            or not isinstance(check.get("severity"), str)
            or not isinstance(check.get("evidence"), str)
            or not check["evidence"].strip()
        ):
            raise ValueError("Gemini QC checks require categories and image evidence.")
    for key in (
        "uncertainties", "prompt_revision_instructions",
        "reference_revision_instructions",
    ):
        if not isinstance(result.get(key), list) or any(
            not isinstance(item, str) for item in result[key]
        ):
            raise ValueError("Gemini QC field {} must be a string array.".format(key))
    if not isinstance(result.get("human_review_required"), bool):
        raise ValueError("Gemini QC human_review_required must be boolean.")
    if decision == "PASS" and (
        score < threshold
        or failures
        or result["uncertainties"]
        or result["human_review_required"]
        or any(check["result"] != "PASS" for check in checks)
    ):
        raise ValueError("Gemini QC PASS conflicts with its checks or configured score threshold.")
    if decision == "RETRY" and (
        not failures
        or not any(check["result"] == "FAIL" for check in checks)
        or not (
            result["prompt_revision_instructions"]
            or result["reference_revision_instructions"]
        )
    ):
        raise ValueError("Gemini QC RETRY requires an evidenced failure and actionable revision.")
    if decision == "NEEDS_HUMAN_REVIEW" and not result["human_review_required"]:
        raise ValueError("NEEDS_HUMAN_REVIEW must set human_review_required=true.")
    return result


class ComicAssetReferenceSelector:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": ""}),
                "asset_request": ("COMIC_ASSET_REQUEST",),
                "references_json": ("STRING", {"multiline": True, "default": "[]"}),
            }
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "IMAGE", "COMIC_ASSET_REFERENCES", "STRING")
    RETURN_NAMES = ("image1", "image2", "image3", "reference_manifest", "manifest_json")
    FUNCTION = "select"
    CATEGORY = "Comic2Anime/Assets"

    def select(self, project_id: str, asset_request: Dict[str, Any], references_json: str):
        request = _json_value(asset_request, "asset_request")
        if request.get("project_id") != project_id:
            raise ValueError("Asset request project does not match project_id.")
        unresolved = request.get("unresolved_reference_conflicts", [])
        if not isinstance(unresolved, list):
            raise ValueError("unresolved_reference_conflicts must be an array.")
        if unresolved:
            raise ValueError(
                "Resolve hard reference conflicts before selecting images: {}".format(
                    json.dumps(unresolved, ensure_ascii=False)
                )
            )
        references = _json_input(references_json, "references_json", list)
        if not references or len(references) > 3:
            raise ValueError("Select between one and three references; QwenEdit supports three.")
        if any(
            not isinstance(ref, dict)
            or not isinstance(ref.get("asset_id"), str)
            or not ref["asset_id"].strip()
            or not isinstance(ref.get("role"), str)
            or not ref["role"].strip()
            or not isinstance(ref.get("reason", ""), str)
            for ref in references
        ):
            raise ValueError("Each reference requires asset_id, role, and an optional reason.")
        ids = [ref["asset_id"] for ref in references]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate reference assets are not allowed.")
        repo = _repository()
        repo.initialize()
        try:
            import numpy as np
            import torch
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("Reference loading requires numpy, Pillow, and ComfyUI torch.") from exc
        images: List[Any] = []
        manifest = []
        for index, ref in enumerate(references):
            asset = repo.get_asset(ref["asset_id"], project_id)
            if asset["status"] != "approved":
                raise ValueError("Only approved assets can be used as formal references.")
            entity_type = request.get("entity_type")
            entity_id = request.get("entity_id")
            if (
                entity_type is None
                and isinstance(request.get("target_entity"), dict)
            ):
                entity_type = request["target_entity"].get("entity_type")
                entity_id = request["target_entity"].get("entity_id")
            compatible_entities = set()
            if isinstance(entity_type, str) and isinstance(entity_id, str):
                compatible_entities.add((entity_type, entity_id))
                with repo.database.connect() as connection:
                    if entity_type == "character_appearance":
                        parent = connection.execute(
                            """SELECT c.character_id FROM character_appearances a
                               JOIN characters c ON c.character_id = a.character_id
                               WHERE a.appearance_id = ? AND c.project_id = ?""",
                            (entity_id, project_id),
                        ).fetchone()
                        if parent:
                            compatible_entities.add(("character", parent["character_id"]))
                    elif entity_type == "location_state":
                        parent = connection.execute(
                            """SELECT location_id FROM location_states
                               WHERE state_id = ?""",
                            (entity_id,),
                        ).fetchone()
                        if parent:
                            compatible_entities.add(("location", parent["location_id"]))
            character_roles = {
                "character_identity", "face_detail", "outfit",
                "state_reference", "expression_pose", "prop_reference",
            }
            location_roles = {"scene_reference", "location_state", "environment"}
            if ref["role"] in character_roles | location_roles and compatible_entities:
                with repo.database.connect() as connection:
                    relation_rows = connection.execute(
                        """SELECT entity_type, entity_id FROM asset_relations
                           WHERE project_id = ? AND asset_id = ?""",
                        (project_id, ref["asset_id"]),
                    ).fetchall()
                relations = {
                    (row["entity_type"], row["entity_id"]) for row in relation_rows
                }
                relevant = (
                    {item for item in compatible_entities if item[0] in ("character", "character_appearance")}
                    if ref["role"] in character_roles
                    else {item for item in compatible_entities if item[0] in ("location", "location_state")}
                )
                if not relevant or not relations.intersection(relevant):
                    raise ValueError(
                        "Reference {} is not linked to the request's {}".format(
                            ref["asset_id"], ref["role"]
                        )
                    )
            declared = json.loads(asset.get("metadata_json") or "{}")
            incompatible = declared.get("incompatible_with", [])
            if not isinstance(incompatible, list):
                raise ValueError(
                    "Asset metadata incompatible_with must be a JSON array."
                )
            if any(other_id in ids for other_id in incompatible):
                raise ValueError(
                    "Selected references contain a declared incompatibility."
                )
            path = repo.resolve_asset_path(asset)
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != asset["file_hash"]:
                raise ValueError("Reference asset {} failed SHA-256 verification.".format(ref["asset_id"]))
            with Image.open(io.BytesIO(raw)) as decoded:
                decoded.load()
                pixels = np.asarray(decoded.convert("RGB"), dtype=np.float32) / 255.0
            images.append(torch.from_numpy(pixels[None, ...]))
            manifest.append(
                {
                    "asset_id": asset["asset_id"],
                    "version_hash": asset["file_hash"],
                    "asset_type": asset["asset_type"],
                    "asset_uri": asset["relative_path"],
                    "status": asset["status"],
                    "role": ref["role"],
                    "reason": ref.get("reason", ""),
                    "port": "image{}".format(index + 1),
                    "mime_type": asset["mime_type"],
                    "width": asset["width"],
                    "height": asset["height"],
                }
            )
        images.extend([None] * (3 - len(images)))
        return (
            images[0],
            images[1],
            images[2],
            manifest,
            json.dumps(manifest, ensure_ascii=False),
        )


class GeminiAssetPromptGenerator:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "asset_request": ("COMIC_ASSET_REQUEST",),
                "reference_manifest_json": ("STRING", {"multiline": True, "default": "[]"}),
                "api_key": ("STRING", {"default": ""}),
                "model": ("STRING", {"default": "gemini-2.5-flash"}),
                "api_base_url": ("STRING", {"default": "https://generativelanguage.googleapis.com"}),
                "timeout_seconds": ("INT", {"default": 120, "min": 1, "max": 600}),
                "max_retries": ("INT", {"default": 2, "min": 0, "max": 5}),
            },
            "optional": {
                "reference_image1": ("IMAGE",),
                "reference_image2": ("IMAGE",),
                "reference_image3": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("COMIC_ASSET_PROMPT", "STRING", "STRING")
    RETURN_NAMES = ("asset_prompt", "qwen_prompt", "prompt_report_json")
    FUNCTION = "generate"
    CATEGORY = "Comic2Anime/Assets/Gemini"

    def generate(
        self,
        asset_request: Dict[str, Any],
        reference_manifest_json: str,
        api_key: str,
        model: str,
        api_base_url: str,
        timeout_seconds: int,
        max_retries: int,
        reference_image1: Any = None,
        reference_image2: Any = None,
        reference_image3: Any = None,
    ):
        request = _json_value(asset_request, "asset_request")
        if not isinstance(request.get("request_id"), str) or not request["request_id"].strip():
            raise ValueError("Asset request requires a stable request_id.")
        if request.get("asset_type") not in ASSET_TYPES:
            raise ValueError("Asset request has an unsupported asset_type.")
        manifest = _json_input(reference_manifest_json, "reference_manifest_json", list)
        if len(manifest) > 3:
            raise ValueError("QwenEdit accepts at most three reference images.")
        sockets = (reference_image1, reference_image2, reference_image3)
        if len(manifest) != sum(image is not None for image in sockets):
            raise ValueError("Connect the actual reference IMAGE for every manifest entry.")
        parts = []
        for index, (item, image) in enumerate(zip(manifest, sockets)):
            if item.get("port") != "image{}".format(index + 1):
                raise ValueError("Reference manifest port order does not match QwenEdit.")
            if image is None:
                raise ValueError("Selected reference IMAGE is not connected to Gemini.")
            parts.append(_single_image_bytes(image))
        prompt_text = (
            "Create a Qwen-Image-Edit 2511 image-edit prompt from this structured asset request. "
            "Use only stated evidence; do not invent facts. Treat reference roles separately. "
            "Do not alter established identity or source knowledge. Put unresolved facts in "
            "uncertainties. Return the required JSON.\\nASSET_REQUEST:\\n"
            + json.dumps(request, ensure_ascii=False)
            + "\\nREFERENCE_MANIFEST:\\n"
            + json.dumps(manifest, ensure_ascii=False)
            + "\\nQWEN_EDIT_INPUTS: required image1, optional image2 and image3; prompt STRING."
        )
        result = generate_structured_json(
            prompt_text, model, api_key, api_base_url, timeout_seconds,
            images=parts, response_schema=_PROMPT_SCHEMA, max_retries=max_retries,
        )
        result = _validate_prompt_response(result, manifest)
        result["model_name"] = model
        workflow = ComicWorkflowRepository(_repository().database)
        workflow.initialize()
        workflow.save_asset_request(request["project_id"], request)
        workflow.save_asset_iteration(
            request["request_id"], 0, result, manifest, status="prompt_ready"
        )
        return (
            result,
            result["detailed_description"],
            json.dumps(
                {
                    "request_id": request["request_id"],
                    "prompt_version": result["prompt_version"],
                    "model_name": model,
                    "reference_asset_ids": [item["asset_id"] for item in manifest],
                    "uncertainties": result["uncertainties"],
                },
                ensure_ascii=False,
            ),
        )


class GeminiAssetVisualQC:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "generated_image": ("IMAGE",),
                "asset_request": ("COMIC_ASSET_REQUEST",),
                "asset_prompt": ("COMIC_ASSET_PROMPT",),
                "reference_manifest_json": ("STRING", {"multiline": True, "default": "[]"}),
                "attempt_index": ("INT", {"default": 0, "min": 0, "max": 10}),
                "pass_threshold": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 1.0}),
                "api_key": ("STRING", {"default": ""}),
                "model": ("STRING", {"default": "gemini-2.5-flash"}),
                "api_base_url": ("STRING", {"default": "https://generativelanguage.googleapis.com"}),
                "timeout_seconds": ("INT", {"default": 120, "min": 1, "max": 600}),
                "max_retries": ("INT", {"default": 2, "min": 0, "max": 5}),
            },
            "optional": {
                "reference_image1": ("IMAGE",),
                "reference_image2": ("IMAGE",),
                "reference_image3": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("COMIC_ASSET_QC", "STRING", "STRING")
    RETURN_NAMES = ("qc_report", "decision", "qc_report_json")
    FUNCTION = "evaluate"
    CATEGORY = "Comic2Anime/Assets/Gemini"

    def evaluate(
        self,
        generated_image: Any,
        asset_request: Dict[str, Any],
        asset_prompt: Dict[str, Any],
        reference_manifest_json: str,
        attempt_index: int,
        pass_threshold: float,
        api_key: str,
        model: str,
        api_base_url: str,
        timeout_seconds: int,
        max_retries: int,
        reference_image1: Any = None,
        reference_image2: Any = None,
        reference_image3: Any = None,
    ):
        request = _json_value(asset_request, "asset_request")
        prompt = _json_value(asset_prompt, "asset_prompt")
        manifest = _json_input(reference_manifest_json, "reference_manifest_json", list)
        output_bytes, output_mime = _single_image_bytes(generated_image)
        images = [(output_bytes, output_mime)]
        reference_sockets = (reference_image1, reference_image2, reference_image3)
        if len(manifest) != sum(image is not None for image in reference_sockets):
            raise ValueError("Connect every selected reference IMAGE to visual QC.")
        for index, (item, image) in enumerate(zip(manifest, reference_sockets)):
            if image is None:
                continue
            if item.get("port") != "image{}".format(index + 1):
                raise ValueError("QC reference port mapping does not match the manifest.")
            images.append(_single_image_bytes(image))
        instructions = (
            "Visually inspect the first attached image: it is the actual latest Qwen output. "
            "Other attached images, if any, are reference inputs. Compare the output with the "
            "asset request and checklist. Cite concrete visible evidence. Never pass based on "
            "text alone. Return PASS only when every hard constraint passes, otherwise RETRY "
            "for fixable visual issues or NEEDS_HUMAN_REVIEW for ambiguity.\\nTASK:\\n"
            + json.dumps(request, ensure_ascii=False)
            + "\\nPROMPT AND CHECKLIST:\\n"
            + json.dumps(prompt, ensure_ascii=False)
            + "\\nREFERENCE MANIFEST:\\n"
            + json.dumps(manifest, ensure_ascii=False)
        )
        try:
            report = generate_structured_json(
                instructions, model, api_key, api_base_url, timeout_seconds,
                images=images, response_schema=_QC_SCHEMA, max_retries=max_retries,
            )
            report = _validate_qc_response(report, pass_threshold)
        except (RuntimeError, ValueError) as exc:
            report = {
                "decision": "ERROR",
                "overall_score": 0.0,
                "checks": [
                    {
                        "category": "api_or_response",
                        "result": "UNCERTAIN",
                        "severity": "high",
                        "evidence": str(exc),
                    }
                ],
                "failures": [],
                "uncertainties": ["Visual QC did not complete; no pass is recorded."],
                "prompt_revision_instructions": [],
                "reference_revision_instructions": [],
                "human_review_required": True,
            }
        report["request_id"] = request.get("request_id", "")
        report["attempt_index"] = attempt_index
        report["qc_model"] = model
        workflow = ComicWorkflowRepository(_repository().database)
        workflow.initialize()
        workflow.save_asset_request(request["project_id"], request)
        iteration_status = {
            "PASS": "pass_candidate",
            "RETRY": "retry_pending",
            "NEEDS_HUMAN_REVIEW": "needs_human_review",
            "ERROR": "error",
        }[report["decision"]]
        workflow.save_asset_iteration(
            request["request_id"], attempt_index, prompt, manifest,
            report, status=iteration_status,
        )
        return report, report["decision"], json.dumps(report, ensure_ascii=False)


class ComicAssetRetryPlanner:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "asset_request": ("COMIC_ASSET_REQUEST",),
                "asset_prompt": ("COMIC_ASSET_PROMPT",),
                "qc_report_json": ("STRING", {"multiline": True, "default": "{}"}),
                "reference_manifest_json": ("STRING", {"multiline": True, "default": "[]"}),
                "attempt_index": ("INT", {"default": 0, "min": 0, "max": 10}),
                "max_generation_retries": ("INT", {"default": 3, "min": 0, "max": 5}),
                "api_key": ("STRING", {"default": ""}),
                "model": ("STRING", {"default": "gemini-2.5-flash"}),
                "api_base_url": ("STRING", {"default": "https://generativelanguage.googleapis.com"}),
                "timeout_seconds": ("INT", {"default": 120, "min": 1, "max": 600}),
                "max_retries": ("INT", {"default": 2, "min": 0, "max": 5}),
            }
        }

    RETURN_TYPES = ("COMIC_ASSET_PROMPT", "STRING", "STRING")
    RETURN_NAMES = ("next_asset_prompt", "next_prompt", "retry_report_json")
    FUNCTION = "plan"
    CATEGORY = "Comic2Anime/Assets/Gemini"

    def plan(
        self,
        asset_request: Dict[str, Any],
        asset_prompt: Dict[str, Any],
        qc_report_json: str,
        reference_manifest_json: str,
        attempt_index: int,
        max_generation_retries: int,
        api_key: str,
        model: str,
        api_base_url: str,
        timeout_seconds: int,
        max_retries: int,
    ):
        request = _json_value(asset_request, "asset_request")
        current = _json_value(asset_prompt, "asset_prompt")
        qc = _json_input(qc_report_json, "qc_report_json", dict)
        manifest = _json_input(
            reference_manifest_json, "reference_manifest_json", list
        )
        if (
            attempt_index < 0
            or max_generation_retries < 0
            or max_generation_retries > 5
        ):
            raise ValueError("Retry counts must be within their configured limits.")
        decision = qc.get("decision")
        if decision == "PASS":
            _workflow_repository().save_asset_iteration(
                request["request_id"], attempt_index, current, manifest, qc,
                status="pass_candidate",
            )
            return current, current.get("detailed_description", ""), json.dumps(
                {"status": "pass_candidate", "attempt_index": attempt_index}
            )
        if decision != "RETRY":
            status = "needs_human_review" if decision == "NEEDS_HUMAN_REVIEW" else "error"
            _workflow_repository().save_asset_iteration(
                request["request_id"], attempt_index, current, manifest, qc,
                status=status,
            )
            return current, current.get("detailed_description", ""), json.dumps(
                {"status": status, "reason": "QC decision is not retryable."}
            )
        if qc.get("reference_revision_instructions"):
            _workflow_repository().save_asset_iteration(
                request["request_id"], attempt_index, current, manifest, qc,
                status="needs_human_review",
            )
            return current, current.get("detailed_description", ""), json.dumps(
                {
                    "status": "needs_human_review",
                    "reason": "Select a compatible reference image before retrying.",
                    "reference_revision_instructions": qc[
                        "reference_revision_instructions"
                    ],
                },
                ensure_ascii=False,
            )
        if attempt_index >= max_generation_retries:
            _workflow_repository().save_asset_iteration(
                request["request_id"], attempt_index, current, manifest, qc,
                status="needs_human_review",
            )
            return current, current.get("detailed_description", ""), json.dumps(
                {
                    "status": "needs_human_review",
                    "reason": "Maximum generation retry count reached.",
                    "attempt_index": attempt_index,
                    "max_generation_retries": max_generation_retries,
                    "qc_failures": qc.get("failures", []),
                },
                ensure_ascii=False,
            )
        revision_prompt = (
            "Revise only the Qwen edit prompt fields needed to address these QC findings. "
            "Do not change the source task, must_preserve, allowed_changes, or negative "
            "constraints. Do not invent facts. The corrected prompt must remain specific "
            "to this asset request. Return the same structured prompt schema.\\n"
            "IMMUTABLE TASK:\\n" + json.dumps(request, ensure_ascii=False)
            + "\\nCURRENT PROMPT:\\n" + json.dumps(current, ensure_ascii=False)
            + "\\nQC FINDINGS:\\n" + json.dumps(qc, ensure_ascii=False)
        )
        revised = generate_structured_json(
            revision_prompt, model, api_key, api_base_url, timeout_seconds,
            response_schema=_PROMPT_SCHEMA, max_retries=max_retries,
        )
        revised = _validate_prompt_response(revised, manifest)
        if (
            revised["must_preserve"] != current.get("must_preserve")
            or revised["allowed_changes"] != current.get("allowed_changes")
            or revised["negative_constraints"] != current.get("negative_constraints")
        ):
            raise ValueError("Gemini revision changed immutable task constraints.")
        revised["prompt_version"] = int(current.get("prompt_version", 0)) + 1
        revised["model_name"] = model
        workflow = ComicWorkflowRepository(_repository().database)
        workflow.initialize()
        workflow.save_asset_iteration(
            request["request_id"],
            attempt_index + 1,
            revised,
            manifest,
            qc_report=qc,
            status="retry_pending",
        )
        report = {
            "status": "retry_pending",
            "attempt_index": attempt_index + 1,
            "max_generation_retries": max_generation_retries,
            "changed_prompt": revised["detailed_description"],
            "reference_revision_instructions": qc.get(
                "reference_revision_instructions", []
            ),
            "requires_reference_reselection": bool(
                qc.get("reference_revision_instructions")
            ),
        }
        return revised, revised["detailed_description"], json.dumps(report, ensure_ascii=False)


class ComicAssetWorkflowRegistrar:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "asset_request": ("COMIC_ASSET_REQUEST",),
                "asset_prompt": ("COMIC_ASSET_PROMPT",),
                "reference_manifest_json": ("STRING", {"multiline": True, "default": "[]"}),
                "qc_report_json": ("STRING", {"multiline": True, "default": "{}"}),
                "attempt_index": ("INT", {"default": 0, "min": 0, "max": 10}),
                "display_name": ("STRING", {"default": ""}),
                "seed": ("INT", {"default": -1, "min": -1, "max": 0xffffffffffffffff}),
                "qwen_parameters_json": ("STRING", {"multiline": True, "default": "{}"}),
            }
        }

    RETURN_TYPES = ("COMIC_ASSETS", "COMIC_ASSET_REPORT")
    RETURN_NAMES = ("assets", "registration_report")
    FUNCTION = "register"
    CATEGORY = "Comic2Anime/Assets"

    def register(
        self,
        image: Any,
        asset_request: Dict[str, Any],
        asset_prompt: Dict[str, Any],
        reference_manifest_json: str,
        qc_report_json: str,
        attempt_index: int,
        display_name: str,
        seed: int,
        qwen_parameters_json: str,
    ):
        request = _json_value(asset_request, "asset_request")
        prompt = _json_value(asset_prompt, "asset_prompt")
        manifest = _json_input(reference_manifest_json, "reference_manifest_json", list)
        qc = _json_input(qc_report_json, "qc_report_json", dict)
        if request.get("project_id") is None or request.get("asset_type") not in ASSET_TYPES:
            raise ValueError("Asset request lacks a valid project or asset type.")
        if qc.get("decision") not in (
            "PASS", "RETRY", "NEEDS_HUMAN_REVIEW", "ERROR"
        ):
            raise ValueError("QC report must contain an explicit decision.")
        if request.get("request_id") != qc.get("request_id"):
            raise ValueError("QC report belongs to a different asset request.")
        if qc.get("attempt_index") != attempt_index:
            raise ValueError("QC report belongs to a different iteration.")
        images = _encode_comfy_images(image)
        if not images:
            raise ValueError("At least one generated IMAGE is required.")
        for item in images:
            item["metadata"].update(
                {
                    "asset_request_id": request["request_id"],
                    "attempt_index": attempt_index,
                    "asset_prompt": prompt,
                    "reference_manifest": manifest,
                    "visual_qc": qc,
                }
            )
        parameters = _json_input(
            qwen_parameters_json, "qwen_parameters_json", dict
        )
        parameters["reference_manifest"] = manifest
        parameters["qc_decision"] = qc["decision"]
        repo = _repository()
        repo.initialize()
        run_id = "{}-{}-{}".format(
            request["request_id"], attempt_index, uuid.uuid4().hex[:12]
        )
        entity_type = request.get("entity_type")
        entity_id = request.get("entity_id")
        if entity_type is None and isinstance(request.get("target_entity"), dict):
            entity_type = request["target_entity"].get("entity_type")
            entity_id = request["target_entity"].get("entity_id")
        seed_value = None if seed < 0 else seed
        result = repo.register_batch(
            project_id=request["project_id"],
            asset_type=request["asset_type"],
            images=images,
            display_name=display_name,
            generation_run_id=run_id,
            prompt=prompt["detailed_description"],
            prompt_version=str(prompt.get("prompt_version", "")),
            model_name="Qwen-Image-Edit 2511",
            seed=seed_value,
            parameters=parameters,
            reference_asset_ids=[item["asset_id"] for item in manifest],
            entity_type=entity_type,
            entity_id=entity_id,
            relation_type=request["asset_type"],
        )
        iteration_status = {
            "PASS": "pass_candidate",
            "RETRY": "retry_pending",
            "NEEDS_HUMAN_REVIEW": "needs_human_review",
            "ERROR": "error",
        }[qc["decision"]]
        workflow = ComicWorkflowRepository(repo.database)
        workflow.initialize()
        workflow.save_asset_request(request["project_id"], request)
        workflow.save_asset_iteration(
            request["request_id"], attempt_index, prompt, manifest, qc,
            generation_run_id=run_id, status=iteration_status,
        )
        result["report"]["qc_decision"] = qc["decision"]
        result["report"]["workflow_status"] = iteration_status
        return result["assets"], result["report"]


class GeminiQwenAssetGenerationLoop:
    @classmethod
    def INPUT_TYPES(cls):
        try:
            import folder_paths
        except ImportError:
            model_choices = ([""], [""], [""])
        else:
            model_choices = (
                folder_paths.get_filename_list("diffusion_models"),
                folder_paths.get_filename_list("text_encoders"),
                folder_paths.get_filename_list("vae"),
            )
        return {
            "required": {
                "asset_request": ("COMIC_ASSET_REQUEST",),
                "references_json": ("STRING", {"multiline": True, "default": "[]"}),
                "unet_name": (model_choices[0],),
                "clip_name": (model_choices[1],),
                "vae_name": (model_choices[2],),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "steps": ("INT", {"default": 40, "min": 1, "max": 1000}),
                "cfg": ("FLOAT", {"default": 4.0, "min": 0.0, "max": 100.0}),
                "max_generation_retries": ("INT", {"default": 3, "min": 0, "max": 3}),
                "pass_threshold": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 1.0}),
                "gemini_api_key": ("STRING", {"default": ""}),
                "gemini_model": ("STRING", {"default": "gemini-2.5-flash"}),
                "api_base_url": ("STRING", {"default": "https://generativelanguage.googleapis.com"}),
                "timeout_seconds": ("INT", {"default": 120, "min": 1, "max": 600}),
                "api_retries": ("INT", {"default": 2, "min": 0, "max": 5}),
            }
        }

    RETURN_TYPES = ("IMAGE", "COMIC_ASSET_WORKFLOW_REPORT", "STRING")
    RETURN_NAMES = ("latest_image", "workflow_report", "decision")
    FUNCTION = "generate"
    CATEGORY = "Comic2Anime/Assets/Gemini"

    def generate(
        self,
        asset_request: Dict[str, Any],
        references_json: str,
        unet_name: str,
        clip_name: str,
        vae_name: str,
        seed: int,
        steps: int,
        cfg: float,
        max_generation_retries: int,
        pass_threshold: float,
        gemini_api_key: str,
        gemini_model: str,
        api_base_url: str,
        timeout_seconds: int,
        api_retries: int,
    ):
        request = _json_value(asset_request, "asset_request")
        if (
            not isinstance(request.get("request_id"), str)
            or not request["request_id"].strip()
            or request.get("asset_type") not in ASSET_TYPES
            or not isinstance(request.get("project_id"), str)
        ):
            raise ValueError("Asset request must include request_id, project_id, and asset_type.")
        if max_generation_retries < 0 or max_generation_retries > 3:
            raise ValueError("max_generation_retries must be between 0 and 3.")
        if isinstance(seed, bool) or not 0 <= seed < 2**64:
            raise ValueError("seed must be an unsigned 64-bit integer.")
        _json_input(references_json, "references_json", list)
        image1, image2, image3, manifest, _ = ComicAssetReferenceSelector().select(
            request["project_id"], request, references_json
        )
        qwen_references = [
            image for image in (image2, image3) if image is not None
        ]
        images_for_gemini = [
            _single_image_bytes(image) for image in (image2, image3)
            if image is not None
        ]
        qwen_references.insert(0, image1)
        images_for_gemini.insert(0, _single_image_bytes(image1))

        repo = _repository()
        repo.initialize()
        workflow = ComicWorkflowRepository(repo.database)
        workflow.initialize()
        workflow.save_asset_request(request["project_id"], request)
        prompt_task = (
            "Create a Qwen-Image-Edit 2511 edit prompt for this asset. Use only confirmed "
            "request facts and evidence. Keep reference roles separate; state must-preserve, "
            "allowed changes, prohibited changes, and uncertainties. Do not invent facts. "
            "Return the required structured JSON.\\nASSET_REQUEST:\\n"
            + json.dumps(request, ensure_ascii=False)
            + "\\nREFERENCE_MANIFEST:\\n"
            + json.dumps(manifest, ensure_ascii=False)
            + "\\nQWEN PORTS: image1 required; image2 and image3 optional."
        )
        try:
            prompt = _validate_prompt_response(
                generate_structured_json(
                    prompt_task, gemini_model, gemini_api_key, api_base_url,
                    timeout_seconds, images=images_for_gemini,
                    response_schema=_PROMPT_SCHEMA, max_retries=api_retries,
                ),
                manifest,
            )
        except (RuntimeError, ValueError) as exc:
            workflow.save_asset_iteration(
                request["request_id"], 0, {}, manifest,
                qc_report={
                    "decision": "ERROR",
                    "error": str(exc),
                    "human_review_required": True,
                },
                status="error",
            )
            raise
        prompt["model_name"] = gemini_model
        workflow.save_asset_iteration(
            request["request_id"], 0, prompt, manifest, status="prompt_ready"
        )
        attempts = []
        latest_image = None
        final_decision = "ERROR"
        from .qwen_edit_2511 import QwenImageEditCustomNode

        for attempt_index in range(max_generation_retries + 1):
            attempt_seed = (seed + attempt_index) % (2**64)
            try:
                latest_image = QwenImageEditCustomNode().edit_image(
                    image1=qwen_references[0],
                    image2=qwen_references[1] if len(qwen_references) > 1 else None,
                    image3=qwen_references[2] if len(qwen_references) > 2 else None,
                    prompt=prompt["detailed_description"],
                    unet_name=unet_name,
                    clip_name=clip_name,
                    vae_name=vae_name,
                    seed=attempt_seed,
                    steps=steps,
                    cfg=cfg,
                )[0]
            except (RuntimeError, ValueError, OSError) as exc:
                failed_run_id = "{}-{}-{}".format(
                    request["request_id"], attempt_index, uuid.uuid4().hex[:12]
                )
                repo.record_generation_failure(
                    request["project_id"],
                    failed_run_id,
                    request["asset_type"],
                    str(exc),
                    prompt=prompt["detailed_description"],
                    prompt_version=str(prompt["prompt_version"]),
                    model_name="Qwen-Image-Edit 2511",
                    seed=attempt_seed,
                    parameters={
                        "unet_name": unet_name,
                        "clip_name": clip_name,
                        "vae_name": vae_name,
                        "steps": steps,
                        "cfg": cfg,
                    },
                    reference_asset_ids=[item["asset_id"] for item in manifest],
                )
                workflow.save_asset_iteration(
                    request["request_id"], attempt_index, prompt, manifest,
                    qc_report={"decision": "ERROR", "error": str(exc)},
                    generation_run_id=failed_run_id, status="error",
                )
                raise RuntimeError(
                    "Qwen generation failed on attempt {}: {}".format(
                        attempt_index, exc
                    )
                ) from exc
            generated_bytes, _ = _single_image_bytes(latest_image)
            qc_instructions = (
                "The first attached image is the actual latest Qwen output. The other images "
                "are selected references. Evaluate the visible result against the exact task "
                "and checklist. Cite concrete image evidence. Never PASS based on text alone. "
                "Return RETRY for fixable prompt issues; use NEEDS_HUMAN_REVIEW for ambiguity.\\n"
                "TASK:\\n" + json.dumps(request, ensure_ascii=False)
                + "\\nPROMPT:\\n" + json.dumps(prompt, ensure_ascii=False)
                + "\\nREFERENCES:\\n" + json.dumps(manifest, ensure_ascii=False)
            )
            try:
                qc = _validate_qc_response(
                    generate_structured_json(
                        qc_instructions, gemini_model, gemini_api_key, api_base_url,
                        timeout_seconds,
                        images=[(generated_bytes, "image/png")] + images_for_gemini,
                        response_schema=_QC_SCHEMA, max_retries=api_retries,
                    ),
                    pass_threshold,
                )
            except (RuntimeError, ValueError) as exc:
                qc = {
                    "decision": "ERROR",
                    "overall_score": 0.0,
                    "checks": [{
                        "category": "api_or_response",
                        "result": "UNCERTAIN",
                        "severity": "high",
                        "evidence": str(exc),
                    }],
                    "failures": [],
                    "uncertainties": ["Visual QC unavailable; image is not approved."],
                    "prompt_revision_instructions": [],
                    "reference_revision_instructions": [],
                    "human_review_required": True,
                }
            if qc["decision"] == "RETRY" and (
                qc["reference_revision_instructions"]
                or attempt_index >= max_generation_retries
            ):
                qc["decision"] = "NEEDS_HUMAN_REVIEW"
                qc["human_review_required"] = True
                qc["stop_reason"] = (
                    "Reference reselection requires human action."
                    if qc["reference_revision_instructions"]
                    else "Maximum generation retry count reached."
                )
            qc.update(
                {
                    "request_id": request["request_id"],
                    "attempt_index": attempt_index,
                    "qc_model": gemini_model,
                }
            )
            run_id = "{}-{}-{}".format(
                request["request_id"], attempt_index, uuid.uuid4().hex[:12]
            )
            encoded = _encode_comfy_images(latest_image)
            for item in encoded:
                item["metadata"].update(
                    {
                        "asset_request_id": request["request_id"],
                        "attempt_index": attempt_index,
                        "asset_prompt": prompt,
                        "reference_manifest": manifest,
                        "visual_qc": qc,
                    }
                )
            parameters = {
                "unet_name": unet_name,
                "clip_name": clip_name,
                "vae_name": vae_name,
                "steps": steps,
                "cfg": cfg,
                "reference_manifest": manifest,
                "qc_decision": qc["decision"],
                "attempt_index": attempt_index,
            }
            entity_type = request.get("entity_type")
            entity_id = request.get("entity_id")
            if entity_type is None and isinstance(request.get("target_entity"), dict):
                entity_type = request["target_entity"].get("entity_type")
                entity_id = request["target_entity"].get("entity_id")
            registered = repo.register_batch(
                project_id=request["project_id"],
                asset_type=request["asset_type"],
                images=encoded,
                display_name="{} attempt {}".format(
                    request.get("generation_goal", request["asset_type"]),
                    attempt_index + 1,
                ),
                generation_run_id=run_id,
                prompt=prompt["detailed_description"],
                prompt_version=str(prompt["prompt_version"]),
                model_name="Qwen-Image-Edit 2511",
                seed=attempt_seed,
                parameters=parameters,
                reference_asset_ids=[item["asset_id"] for item in manifest],
                entity_type=entity_type,
                entity_id=entity_id,
                relation_type=request["asset_type"],
            )
            status = {
                "PASS": "pass_candidate",
                "RETRY": "retry_pending",
                "NEEDS_HUMAN_REVIEW": "needs_human_review",
                "ERROR": "error",
            }[qc["decision"]]
            workflow.save_asset_iteration(
                request["request_id"], attempt_index, prompt, manifest, qc,
                generation_run_id=run_id, status=status,
            )
            attempts.append(
                {
                    "attempt_index": attempt_index,
                    "generation_run_id": run_id,
                    "asset_ids": [
                        item["asset_id"] for item in registered["assets"]
                    ],
                    "qc_report": qc,
                    "asset_status": "candidate",
                }
            )
            final_decision = qc["decision"]
            if final_decision != "RETRY":
                break
            try:
                revision = generate_structured_json(
                    "Revise only the Qwen prompt to address this QC report. Keep must_preserve, "
                    "allowed_changes, negative_constraints, and the asset request unchanged. "
                    "Do not invent facts. Return the same JSON prompt schema.\\nTASK:\\n"
                    + json.dumps(request, ensure_ascii=False)
                    + "\\nCURRENT PROMPT:\\n"
                    + json.dumps(prompt, ensure_ascii=False)
                    + "\\nQC REPORT:\\n"
                    + json.dumps(qc, ensure_ascii=False),
                    gemini_model,
                    gemini_api_key,
                    api_base_url,
                    timeout_seconds,
                    response_schema=_PROMPT_SCHEMA,
                    max_retries=api_retries,
                )
                revised = _validate_prompt_response(revision, manifest)
                if any(
                    revised[key] != prompt[key]
                    for key in ("must_preserve", "allowed_changes", "negative_constraints")
                ):
                    raise ValueError("Gemini revision changed immutable asset requirements.")
            except (RuntimeError, ValueError) as exc:
                workflow.save_asset_iteration(
                    request["request_id"], attempt_index + 1, {},
                    manifest, qc_report={"decision": "ERROR", "error": str(exc)},
                    status="error",
                )
                final_decision = "ERROR"
                break
            revised["prompt_version"] = int(prompt["prompt_version"]) + 1
            revised["model_name"] = gemini_model
            workflow.save_asset_iteration(
                request["request_id"], attempt_index + 1, revised, manifest,
                qc_report=qc, status="retry_pending",
            )
            prompt = revised

        final_report = {
            "request_id": request["request_id"],
            "decision": final_decision,
            "attempt_count": len(attempts),
            "max_generation_retries": max_generation_retries,
            "attempts": attempts,
            "all_saved_assets_are_candidates": True,
            "human_review_required": final_decision
            in ("NEEDS_HUMAN_REVIEW", "ERROR"),
        }
        return latest_image, final_report, final_decision
