"""Character voice references and page-local storyboard prompt helpers."""

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List

from ..core.database import ComicDatabase
from ..core.asset_repository import AssetRepository
from ..core.storage import get_data_root, safe_component


REFERENCE_TEXT = (
    "有一天，兔子碰见乌龟，看见乌龟爬得这么慢，就想戏弄戏弄他，"
    "于是笑眯眯地说：“乌龟，乌龟，咱们来赛跑，好吗？”"
)

_MODEL_CACHE: Dict[str, Any] = {}


def _ensure_report_character_entities(
    database: ComicDatabase, project_id: str, report: Dict[str, Any], entries: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Materialize character IDs emitted by the page asset batch.

    Asset reports can outlive a resolver/database refresh.  The voice node is
    the handoff boundary, so it repairs missing project-local character rows
    from the report evidence before creating the FK-backed voice profile.
    """
    if not isinstance(report, dict):
        return entries
    evidence_by_id = {}
    for result in report.get("results", []):
        if not isinstance(result, dict) or result.get("decision") not in ("PASS", "REUSED"):
            continue
        request = result.get("asset_request") if isinstance(result.get("asset_request"), dict) else {}
        if (result.get("entity_type") or request.get("entity_type")) != "character":
            continue
        character_id = result.get("entity_id") or request.get("entity_id")
        if character_id:
            evidence = request.get("source_evidence")
            evidence_by_id[str(character_id)] = (
                evidence[0] if isinstance(evidence, list) and evidence and isinstance(evidence[0], dict) else {}
            )
    with database.connect() as connection:
        project = connection.execute(
            "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        if project is None:
            return entries
        for entry in entries:
            character_id = str(entry.get("matched_entity_id") or entry.get("entity_id") or "").strip()
            if not character_id:
                continue
            exists = connection.execute(
                "SELECT 1 FROM characters WHERE project_id = ? AND character_id = ?",
                (project_id, character_id),
            ).fetchone()
            if exists:
                continue
            evidence = evidence_by_id.get(character_id, {})
            source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
            name = evidence.get("name") or source.get("name") or character_id
            description = evidence.get("appearance") or evidence.get("description") or ""
            connection.execute(
                "INSERT INTO characters(character_id, project_id, canonical_name, stable_description, status) VALUES (?, ?, ?, ?, ?)",
                (character_id, project_id, str(name).strip(), str(description).strip(), "candidate"),
            )
    return entries


def _voice_gender_hint(entry: Dict[str, Any], profile: Dict[str, Any]) -> str:
    """Return an explicit gender hint when the page/entity data contains one."""
    values = []
    for container in (entry, profile, entry.get("source") if isinstance(entry.get("source"), dict) else {}):
        for key in ("gender", "sex", "voice_gender"):
            value = container.get(key)
            if value:
                values.append(str(value).strip().lower())
    text = " ".join(values)
    if any(token in text for token in ("female", "woman", "girl", "女", "女性", "女生", "女人")):
        return "女性声线"
    if any(token in text for token in ("male", "man", "boy", "男", "男性", "男生", "男人")):
        return "男性声线"
    return "性别未知；根据角色外观、年龄和身份资料选择自然匹配的声线，不要擅自改变角色性别。"


def _voice_age_hint(entry: Dict[str, Any], profile: Dict[str, Any]) -> str:
    """Return a deterministic age-band constraint for the TTS instruction."""
    values = []
    for container in (entry, profile, entry.get("source") if isinstance(entry.get("source"), dict) else {}):
        for key in ("age", "age_group", "age_band", "appearance", "identity_candidate"):
            value = container.get(key)
            if value:
                values.append(str(value).strip().lower())
    text = " ".join(values)
    if any(token in text for token in ("infant", "baby", "toddler", "幼儿", "婴儿")):
        return "婴幼儿声线"
    if any(token in text for token in ("child", "kid", " 어린", "儿童", "小孩", "小女孩", "小男孩")):
        return "儿童声线"
    if any(token in text for token in ("teen", "teenager", "adolescent", "少年", "少女", " adolescente")):
        return "少年或少女声线"
    if any(token in text for token in ("elderly", "old man", "old woman", "senior", "老人", "老年", "老者", "年迈")):
        return "老年声线"
    if any(token in text for token in ("middle-aged", "middle aged", "中年")):
        return "中年声线"
    if any(token in text for token in ("adult", "成年", "成年人", "青年", "young man", "young woman")):
        return "成年声线"
    return "年龄未明确；不得擅自使用儿童或老人声线，使用与角色外观和身份最接近的成年中性声线。"


def _voice_cache_matches_gender(existing_instruction: str, requested_gender: str) -> bool:
    """Avoid reusing a cached voice whose persisted gender conflicts."""
    gender = str(requested_gender or "").strip().lower()
    instruction = str(existing_instruction or "").lower()
    if gender in ("male", "man", "boy", "男", "男性", "男生", "男人"):
        return "男性声线" in instruction or "male" in instruction or "男声" in instruction
    if gender in ("female", "woman", "girl", "女", "女性", "女生", "女人"):
        return "女性声线" in instruction or "female" in instruction or "女声" in instruction
    return True


def _resolve_page_character_id(database: ComicDatabase, project_id: str, entry: Dict[str, Any]) -> str:
    """Resolve old/raw analyzer entries to an existing DB character safely."""
    source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
    # ``entity_id`` from ComicEntityResolver can be a generated candidate ID
    # for pending/new matches.  It is not necessarily a row in characters and
    # must never be used as a voice-profile FK.  Only matched_entity_id (or a
    # verified legacy entity_id) is eligible here.
    direct_values = (
        entry.get("matched_entity_id"),
        source.get("matched_entity_id"),
        entry.get("entity_id"),
        source.get("entity_id"),
    )
    with database.connect() as connection:
        for direct in direct_values:
            if not direct:
                continue
            row = connection.execute(
                "SELECT character_id FROM characters WHERE project_id = ? AND character_id = ?",
                (project_id, str(direct).strip()),
            ).fetchone()
            if row:
                return str(row["character_id"])
    names = [entry.get("name"), entry.get("identity_candidate"), source.get("name"), source.get("identity_candidate")]
    names = {str(name).strip().casefold() for name in names if name}
    if not names:
        return ""
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT character_id, canonical_name FROM characters WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    for row in rows:
        if str(row["canonical_name"]).strip().casefold() in names:
            return str(row["character_id"])
    return ""


def _json_object(value: Any, field: str) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("{} must contain valid JSON.".format(field)) from exc
    if not isinstance(parsed, dict):
        raise ValueError("{} must be a JSON object.".format(field))
    return parsed


def _json_array(value: Any, field: str) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        parsed = value
    else:
        try:
            parsed = json.loads(value or "[]")
        except json.JSONDecodeError as exc:
            raise ValueError("{} must contain valid JSON.".format(field)) from exc
    if not isinstance(parsed, list) or any(not isinstance(item, dict) for item in parsed):
        raise ValueError("{} must be a JSON array of objects.".format(field))
    return parsed


def _load_tts_model(model_path: str, device: str):
    key = "{}|{}".format(model_path, device)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    try:
        import torch
        from qwen_tts import Qwen3TTSModel
    except ImportError as exc:
        raise RuntimeError(
            "Qwen TTS is unavailable in the ComfyUI Python environment. "
            "Install qwen-tts and its dependencies first."
        ) from exc
    path = Path(model_path).expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError("Qwen TTS model directory not found: {}".format(path))
    target_device = device or ("cuda:0" if torch.cuda.is_available() else "cpu")
    dtype = torch.bfloat16 if target_device.startswith("cuda") else torch.float32
    model = Qwen3TTSModel.from_pretrained(
        str(path), device_map=target_device, dtype=dtype
    )
    _MODEL_CACHE[key] = model
    return model


def _audio_value(wav: Any, sample_rate: int) -> Any:
    import torch

    tensor = torch.as_tensor(wav, dtype=torch.float32).flatten()
    if tensor.numel() < 1:
        raise ValueError("Qwen TTS returned an empty waveform.")
    peak = tensor.abs().max()
    if peak > 1:
        tensor = tensor / peak
    return {"waveform": tensor.reshape(1, 1, -1), "sample_rate": int(sample_rate)}


def _save_wav(audio: Any, path: Path) -> None:
    import soundfile as sf

    waveform = audio["waveform"][0, 0].detach().cpu().numpy()
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), waveform, int(audio["sample_rate"]), subtype="PCM_16")


def _safe_audio_batch(audios: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pad reference voices into one MiniMax-compatible AUDIO batch."""
    import torch

    if not audios:
        raise ValueError("No character voice references were generated or found.")
    sample_rate = int(audios[0]["sample_rate"])
    waveforms = []
    max_length = 0
    for item in audios:
        waveform = item["waveform"]
        if waveform.ndim == 2:
            waveform = waveform.unsqueeze(0)
        if int(item.get("sample_rate", sample_rate)) != sample_rate:
            try:
                import torchaudio
                waveform = torchaudio.functional.resample(
                    waveform, int(item["sample_rate"]), sample_rate
                )
            except ImportError as exc:
                raise RuntimeError("torchaudio is required to combine different voice sample rates.") from exc
        waveform = waveform[:, :1, :].float()
        max_length = max(max_length, int(waveform.shape[-1]))
        waveforms.append(waveform)
    padded = []
    for waveform in waveforms:
        if waveform.shape[-1] < max_length:
            waveform = torch.nn.functional.pad(waveform, (0, max_length - waveform.shape[-1]))
        padded.append(waveform)
    return {"waveform": torch.cat(padded, dim=0), "sample_rate": sample_rate}


class ComicCharacterVoiceReference:
    """Generate or reuse exactly one reference voice for one real character."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": "project-1"}),
                "character_id": ("STRING", {"default": ""}),
                "voice_instruction": ("STRING", {"multiline": True, "default": "年轻、清晰、自然的动漫角色声音，语气亲切。"}),
                "reference_text": ("STRING", {"multiline": True, "default": REFERENCE_TEXT}),
                "language": (["Chinese", "English", "Japanese", "Korean"], {"default": "Chinese"}),
                "model_path": ("STRING", {"default": r"D:\ComfyUI_windows_portable\ComfyUI\custom_nodes\my_comfyui\voiceCreate\models\Qwen3-TTS-12Hz-1.7B-VoiceDesign"}),
            },
            "optional": {
                "character_profile_json": ("STRING", {"multiline": True, "default": "{}"}),
                "device": (["auto", "cuda:0", "cpu"], {"default": "auto"}),
                "force_regenerate": ("BOOLEAN", {"default": False}),
            },
        }

    RETURN_TYPES = ("AUDIO", "STRING", "STRING")
    RETURN_NAMES = ("ref_audio", "voice_id", "voice_manifest_json")
    FUNCTION = "generate"
    CATEGORY = "Comic2Anime/Voice"

    def generate(
        self, project_id: str, character_id: str, voice_instruction: str,
        reference_text: str, language: str, model_path: str,
        character_profile_json: str = "{}", device: str = "auto",
        force_regenerate: bool = False,
    ):
        project_id = project_id.strip()
        character_id = character_id.strip()
        if not project_id or not character_id:
            raise ValueError("project_id and the real character_id are required.")
        database = ComicDatabase(get_data_root() / "comic2anime.sqlite3")
        database.initialize()
        with database.connect() as connection:
            project = connection.execute("SELECT 1 FROM projects WHERE project_id = ?", (project_id,)).fetchone()
            character = connection.execute(
                "SELECT * FROM characters WHERE project_id = ? AND character_id = ?",
                (project_id, character_id),
            ).fetchone()
            if project is None or character is None:
                raise ValueError("Voice references require a real character entity in the project.")
            existing = connection.execute(
                "SELECT * FROM character_voice_profiles WHERE project_id = ? AND character_id = ?",
                (project_id, character_id),
            ).fetchone()
        profile = _json_object(character_profile_json, "character_profile_json")
        requested_gender = profile.get("gender") or ""
        if existing is not None and not force_regenerate and _voice_cache_matches_gender(
            existing["voice_instruction"], requested_gender
        ):
            path = Path(existing["audio_path"])
            if path.is_file():
                import soundfile as sf
                import torch
                samples, sample_rate = sf.read(str(path), dtype="float32")
                waveform = torch.from_numpy(samples).flatten().reshape(1, 1, -1)
                manifest = {
                    "voice_id": existing["voice_id"], "project_id": project_id,
                    "character_id": character_id, "reference_text": existing["sample_text"],
                    "audio_path": str(path), "sample_rate": int(sample_rate),
                    "reused": True,
                }
                return ({"waveform": waveform, "sample_rate": int(sample_rate)}, existing["voice_id"], json.dumps(manifest, ensure_ascii=False))

        profile_text = json.dumps(profile, ensure_ascii=False)
        instruction = "{}\n角色资料：{}".format(voice_instruction.strip(), profile_text)
        target_device = "cuda:0" if device == "auto" else device
        model = _load_tts_model(model_path, target_device)
        wavs, sample_rate = model.generate_voice_design(
            text=reference_text.strip() or REFERENCE_TEXT,
            language=language,
            instruct=instruction,
        )
        audio = _audio_value(wavs[0], int(sample_rate))
        voice_id = str(uuid.uuid4())
        audio_path = get_data_root() / "voice_references" / safe_component(project_id) / (safe_component(character_id) + ".wav")
        _save_wav(audio, audio_path)
        with database.connect() as connection:
            connection.execute(
                "INSERT INTO character_voice_profiles(project_id, character_id, voice_id, voice_instruction, language, model_name, sample_text, audio_path, sample_rate) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(project_id, character_id) DO UPDATE SET voice_id=excluded.voice_id, voice_instruction=excluded.voice_instruction, language=excluded.language, model_name=excluded.model_name, sample_text=excluded.sample_text, audio_path=excluded.audio_path, sample_rate=excluded.sample_rate, updated_at=CURRENT_TIMESTAMP",
                (project_id, character_id, voice_id, instruction, language, model_path, reference_text, str(audio_path), int(sample_rate)),
            )
        manifest = {
            "voice_id": voice_id, "project_id": project_id, "character_id": character_id,
            "reference_text": reference_text, "audio_path": str(audio_path),
            "sample_rate": int(sample_rate), "ref_audios_compatible": True,
        }
        return audio, voice_id, json.dumps(manifest, ensure_ascii=False)


class ComicAnimePageStoryboardPrompt:
    """Build a page-local Auto Anime user_prompt using picture/audio placeholders."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "page_analysis_json": ("STRING", {"multiline": True, "default": "{}"}),
                "asset_manifest_json": ("STRING", {"multiline": True, "default": "[]"}),
                "voice_manifest_json": ("STRING", {"multiline": True, "default": "[]"}),
                "current_story_outline": ("STRING", {"multiline": True, "default": ""}),
                "previous_page_summary": ("STRING", {"multiline": True, "default": ""}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("user_prompt", "current_page_summary", "material_mapping_json")
    FUNCTION = "build"
    CATEGORY = "Comic2Anime/Storyboard"

    def build(self, page_analysis_json, asset_manifest_json, voice_manifest_json, current_story_outline, previous_page_summary):
        analysis = _json_object(page_analysis_json, "page_analysis_json")
        assets = _json_array(asset_manifest_json, "asset_manifest_json")
        voices = _json_array(voice_manifest_json, "voice_manifest_json")
        pictures = []
        for index, item in enumerate(assets, 1):
            pictures.append(("<picture {}>".format(index), item.get("label") or item.get("name") or item.get("asset_type", "未命名素材")))
        audios = []
        for index, item in enumerate(voices, 1):
            audios.append(("<Audio {}>".format(index), item.get("character_name") or item.get("character_id", "角色声音")))
        character_names = [item.get("name") or item.get("identity_candidate") for item in analysis.get("characters", []) if isinstance(item, dict)]
        location_names = [item.get("name") for item in analysis.get("locations", []) if isinstance(item, dict)]
        outline = current_story_outline.strip() or analysis.get("summary") or analysis.get("story_summary") or json.dumps({"characters": character_names, "locations": location_names}, ensure_ascii=False)
        current_summary = "当前页：{}".format(outline[:1200])
        mapping_lines = ["### 素材映射表", "（只允许在分镜输出中使用占位符，不输出真实角色名或文件名）"]
        mapping_lines += ["- {}：{}".format(token, label) for token, label in pictures]
        mapping_lines += ["- {}：{}".format(token, label) for token, label in audios]
        binding_lines = []
        for index, item in enumerate(voices, 1):
            binding_lines.append(
                "- {} 只能绑定 character_id={}（角色={}，ref_audio_slot={}）。"
                "该角色的所有对白必须使用这个 Audio 编号。".format(
                    "<Audio {}>".format(item.get("ref_audio_slot", index)),
                    item.get("character_id", ""),
                    item.get("character_name") or item.get("character_id", "角色"),
                    item.get("ref_audio_slot", index),
                )
            )
        prompt = "\n".join(mapping_lines) + "\n\n### 角色-音频绑定（硬约束）\n" + (
            "\n".join(binding_lines) if binding_lines else "当前页没有可用角色参考音频。"
        ) + "\n\n### 使用规则（重要）\n1. 分镜中只能使用上述 <picture N> / <Audio N> 占位符。\n2. 有参考音频的角色每次说对白，必须在台词末尾标注其对应的 <Audio N>，同一角色不得更换编号。\n3. 未提供素材的角色、声音或场景只能用文字描写，不得套用其他素材。\n4. 只生成当前漫画页的剧情和分镜，不把前页分镜拼接到本页输出。\n5. 下一页仅依据前页摘要衔接人物状态、空间位置和未完成动作。\n6. 对话必须给角色留出完整说话时间，禁止在台词未说完前切镜。\n\n### 当前页故事大纲\n{}\n".format(outline)
        if previous_page_summary.strip():
            prompt += "\n### 前一页连续性摘要（只用于衔接，不要原样输出）\n{}\n".format(previous_page_summary.strip())
        prompt += "\n请输出当前页的 Auto Anime Storyboard Pipeline 分镜。每个 Part 必须使用素材占位符，并确保画面中没有对应素材时用文字描述其外观、动作和声音。"
        return prompt, current_summary, json.dumps({"pictures": pictures, "audios": audios}, ensure_ascii=False)


class ComicPageVoiceReferences:
    """Generate/reuse all real character voices visible on the current page."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": "project-1"}),
                "entity_resolution": ("COMIC_ENTITY_RESOLUTION",),
                "reference_text": ("STRING", {"multiline": True, "default": REFERENCE_TEXT}),
                "language": (["Chinese", "English", "Japanese", "Korean"], {"default": "Chinese"}),
                "model_path": ("STRING", {"default": r"D:\ComfyUI_windows_portable\ComfyUI\custom_nodes\my_comfyui\voiceCreate\models\Qwen3-TTS-12Hz-1.7B-VoiceDesign"}),
            },
            "optional": {
                "device": (["auto", "cuda:0", "cpu"], {"default": "auto"}),
                "force_regenerate": ("BOOLEAN", {"default": False}),
                # Connected to the asset batch report in the workflow.  It is
                # an execution dependency: character voices run after the
                # current-page character images have completed.
                "asset_generation_report": ("STRING", {"multiline": True, "default": "{}"}),
                "generated_character_images": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("AUDIO", "STRING", "STRING")
    RETURN_NAMES = ("ref_audios", "voice_manifest_json", "voice_report_json")
    FUNCTION = "generate_page_voices"
    CATEGORY = "Comic2Anime/Voice"

    def generate_page_voices(
        self, project_id, entity_resolution, voice_instruction, reference_text,
        language, model_path, device="auto", force_regenerate=False,
        asset_generation_report="{}",
        generated_character_images=None,
    ):
        if isinstance(entity_resolution, str):
            entity_resolution = _json_object(entity_resolution, "entity_resolution")
        if not isinstance(entity_resolution, dict):
            raise ValueError("entity_resolution must be a JSON object.")
        entries = entity_resolution.get("characters", [])
        if not isinstance(entries, list):
            raise ValueError("entity_resolution.characters must be an array.")
        seen = set()
        audios = []
        manifests = []
        skipped = []
        try:
            asset_report = json.loads(asset_generation_report or "{}")
        except json.JSONDecodeError:
            asset_report = {"raw": asset_generation_report}
        generated_character_ids = set()
        generated_character_names = set()
        asset_report_has_results = False
        if isinstance(asset_report, dict):
            asset_report_has_results = isinstance(asset_report.get("results"), list)
            generated_character_ids.update(
                str(item) for item in asset_report.get("character_entity_ids", []) if item
            )
            for result in asset_report.get("results", []):
                if not isinstance(result, dict) or result.get("decision") not in ("PASS", "REUSED"):
                    continue
                request = result.get("asset_request") or result.get("request") or {}
                nested = result.get("report") if isinstance(result.get("report"), dict) else {}
                character_id = (
                    result.get("entity_id") or request.get("entity_id")
                    or nested.get("entity_id")
                )
                entity_type = result.get("entity_type") or request.get("entity_type") or nested.get("entity_type")
                asset_type = result.get("asset_type") or request.get("asset_type") or nested.get("asset_type")
                # Accept reports written by both the current and older batch
                # nodes. Older reports may omit entity_type but still identify
                # a character through a character_* asset type.
                is_character = (
                    entity_type in (None, "", "character", "character_appearance")
                    or str(asset_type).startswith("character")
                )
                if character_id and is_character:
                    generated_character_ids.add(str(character_id))
                    for evidence in request.get("source_evidence", []) if isinstance(request.get("source_evidence"), list) else []:
                        if isinstance(evidence, dict):
                            for key in ("name", "identity_candidate"):
                                if evidence.get(key):
                                    generated_character_names.add(str(evidence[key]).strip().casefold())
            # Prefer the page-local character handoff from the asset batch.
            # This makes the audio node work even when an older workflow sends
            # a stale or empty entity_resolution object.
            report_entries = []
            for item in asset_report.get("character_entities", []):
                if not isinstance(item, dict):
                    continue
                character_id = item.get("character_id") or item.get("entity_id")
                if character_id:
                    report_entries.append({
                        "entity_id": str(character_id),
                        "matched_entity_id": str(character_id),
                        "source": {
                            "name": item.get("character_name", ""),
                            "identity_candidate": item.get("character_name", ""),
                            "appearance": item.get("appearance", ""),
                            "clothing": item.get("clothing", ""),
                            "emotion": item.get("emotion", ""),
                            "gender": item.get("gender", ""),
                            "age": item.get("age", ""),
                            "voice_instruction": item.get("voice_instruction", ""),
                        },
                    })
            if report_entries:
                # Report-driven mode: discard stale resolver entries.  The
                # successful asset batch is authoritative for this page.
                entries = report_entries
        node = ComicCharacterVoiceReference()
        candidates = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            database = ComicDatabase(get_data_root() / "comic2anime.sqlite3")
            database.initialize()
            character_id = _resolve_page_character_id(database, project_id, entry)
            if character_id:
                candidates.append((character_id, entry))
            else:
                skipped.append({"reason": "unresolved_character_id", "source": entry})
        for character_id, entry in sorted(candidates, key=lambda item: item[0]):
            if character_id in seen:
                continue
            seen.add(character_id)
            entry_names = {
                str(entry.get(key, "")).strip().casefold()
                for key in ("name", "identity_candidate") if entry.get(key)
            }
            source_entry = entry.get("source") if isinstance(entry.get("source"), dict) else {}
            entry_names.update(
                str(source_entry.get(key, "")).strip().casefold()
                for key in ("name", "identity_candidate") if source_entry.get(key)
            )
            character_asset_ready = (
                character_id in generated_character_ids
                or bool(entry_names & generated_character_names)
            )
            # The image batch is an execution dependency, not an eligibility
            # gate.  A first-page character can have no prior approved asset,
            # and older batch reports may omit entity IDs (especially when the
            # page also contains locations).  Once the page entry resolves to
            # a real character FK, generate/reuse its one voice here.  The
            # connected IMAGE and report are still retained in the manifest.
            source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
            profile = {
                "name": source.get("name", ""),
                "appearance": source.get("appearance", ""),
                "clothing": source.get("clothing", ""),
                "emotion": source.get("emotion", ""),
                "gender": source.get("gender") or entry.get("gender", ""),
                "age": source.get("age") or entry.get("age", ""),
                "identity": source.get("identity_candidate") or entry.get("identity_candidate") or source.get("name", ""),
            }
            gender_hint = _voice_gender_hint(entry, profile)
            age_hint = _voice_age_hint(entry, profile)
            profile["voice_gender_hint"] = gender_hint
            profile["voice_age_hint"] = age_hint
            character_voice_instruction = source.get("voice_instruction") or "自然、清晰、稳定的动漫角色参考音色。"
            effective_instruction = (
                character_voice_instruction.strip()
                + "\n硬性声音约束：性别必须是" + gender_hint
                + "；年龄必须是" + age_hint
                + "；角色身份为" + str(profile.get("identity") or "未知身份")
                + "。禁止使用与角色性别、年龄不符的声线；同一 character_id 后续始终复用同一音色。"
            )
            try:
                audio, voice_id, manifest_json = node.generate(
                    project_id, character_id, effective_instruction, reference_text,
                    language, model_path, json.dumps(profile, ensure_ascii=False),
                    device, force_regenerate,
                )
                manifest = json.loads(manifest_json)
                manifest["character_name"] = source.get("name") or character_id
                manifest["character_id"] = character_id
                audios.append(audio)
                manifests.append(manifest)
            except Exception as exc:
                skipped.append({"character_id": character_id, "reason": str(exc)})
        report = {
            "project_id": project_id,
            "character_count": len(manifests),
            "skipped": skipped,
            "ref_audios_compatible": True,
            "sample_rate": int(audios[0]["sample_rate"]) if audios else None,
            "asset_generation_dependency": asset_report,
            "generated_character_ids": sorted(generated_character_ids),
            "generated_character_names": sorted(generated_character_names),
            "asset_report_has_results": asset_report_has_results,
        }
        for audio_index, manifest in enumerate(manifests):
            manifest["audio_index"] = audio_index
            manifest["ref_audio_slot"] = audio_index + 1
        report["no_audio"] = not bool(audios)
        report["audio_order"] = [
            {
                "audio_index": item["audio_index"],
                "ref_audio_slot": item["ref_audio_slot"],
                "character_id": item["character_id"],
                "character_name": item.get("character_name", ""),
            }
            for item in manifests
        ]
        return (
            _safe_audio_batch(audios) if audios else None,
            json.dumps(manifests, ensure_ascii=False),
            json.dumps(report, ensure_ascii=False),
        )


class ComicSaveAudioReference:
    """Export generated reference voices while passing AUDIO through unchanged."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audio": ("AUDIO",),
                  # Relative paths are resolved below get_data_root().  Keep the
                  # default relative to the data root so we do not create a
                  # duplicated comic2anime_data/comic2anime_data directory.
                  "output_directory": ("STRING", {"default": "exports/audio"}),
                "filename_prefix": ("STRING", {"default": "character_reference"}),
            },
            "optional": {
                "voice_manifest_json": ("STRING", {"multiline": True, "default": "[]"}),
            },
        }

    RETURN_TYPES = ("AUDIO", "STRING")
    RETURN_NAMES = ("audio", "saved_audio_paths")
    FUNCTION = "save"
    CATEGORY = "Comic2Anime/Export"

    def save(self, audio, output_directory, filename_prefix, voice_manifest_json="[]"):
        import torch
        if audio is None:
            return None, "[]"
        waveform = audio.get("waveform") if isinstance(audio, dict) else None
        if waveform is None:
            raise ValueError("audio must be a ComfyUI AUDIO value.")
        manifests = _json_array(voice_manifest_json, "voice_manifest_json")
        output = Path(output_directory).expanduser()
        if not output.is_absolute():
            output = get_data_root() / output
        output.mkdir(parents=True, exist_ok=True)
        paths = []
        batch = waveform if waveform.ndim == 3 else waveform.unsqueeze(0)
        if manifests and len(manifests) != int(batch.shape[0]):
            raise ValueError("voice_manifest_json count must match AUDIO batch size.")
        for index in range(batch.shape[0]):
            item = {"waveform": batch[index:index + 1], "sample_rate": audio.get("sample_rate", 24000)}
            manifest = manifests[index] if manifests else {}
            character_id = safe_component(manifest.get("character_id", ""))
            character_name = safe_component(manifest.get("character_name", ""))
            suffix = character_id or character_name or "{:03d}".format(index + 1)
            path = output / (safe_component(filename_prefix) + "_" + suffix + ".wav")
            _save_wav(item, path)
            paths.append({
                "audio_index": index,
                "ref_audio_slot": index + 1,
                "character_id": manifest.get("character_id", ""),
                "character_name": manifest.get("character_name", ""),
                "voice_id": manifest.get("voice_id", ""),
                "path": str(path),
            })
        return audio, json.dumps(paths, ensure_ascii=False)


class ComicAssetVoiceGenerationAndExport:
    """Generate/reuse page voices after asset QC and export WAV files."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_id": ("STRING", {"default": "project-1"}),
                "asset_generation_report": ("STRING", {"multiline": True, "default": "{}"}),
                "reference_text": ("STRING", {"multiline": True, "default": REFERENCE_TEXT}),
                "language": (["Chinese", "English", "Japanese", "Korean"], {"default": "Chinese"}),
                "model_path": ("STRING", {"default": r"D:\ComfyUI_windows_portable\ComfyUI\custom_nodes\my_comfyui\voiceCreate\models\Qwen3-TTS-12Hz-1.7B-VoiceDesign"}),
                "output_directory": ("STRING", {"default": "exports/audio"}),
                "filename_prefix": ("STRING", {"default": "page_character_reference"}),
            },
            "optional": {
                "device": (["auto", "cuda:0", "cpu"], {"default": "auto"}),
                "force_regenerate": ("BOOLEAN", {"default": False}),
            },
        }

    RETURN_TYPES = ("AUDIO", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("ref_audios", "voice_manifest_json", "saved_audio_paths", "voice_report_json")
    FUNCTION = "generate_and_export"
    CATEGORY = "Comic2Anime/Assets/Voice"

    def generate_and_export(
        self, project_id, asset_generation_report, reference_text, language,
        model_path, output_directory, filename_prefix, device="auto",
        force_regenerate=False,
    ):
        try:
            parsed_report = json.loads(asset_generation_report or "{}")
        except json.JSONDecodeError:
            parsed_report = {}
        # This composite node is intentionally report-driven.  Do not let a
        # stale entity_resolution connection override the page's successful
        # asset handoff: batch_report_json is the single source of truth.
        report_characters = []
        if isinstance(parsed_report, dict):
            report_characters = parsed_report.get("character_entities", [])
            if not isinstance(report_characters, list) or not report_characters:
                report_characters = []
                for result in parsed_report.get("results", []):
                    if not isinstance(result, dict) or result.get("decision") not in ("PASS", "REUSED"):
                        continue
                    request = result.get("asset_request") if isinstance(result.get("asset_request"), dict) else {}
                    if (result.get("entity_type") or request.get("entity_type")) != "character":
                        continue
                    character_id = result.get("entity_id") or request.get("entity_id")
                    if character_id:
                        evidence = request.get("source_evidence")
                        evidence = evidence[0] if isinstance(evidence, list) and evidence and isinstance(evidence[0], dict) else {}
                        report_characters.append({
                            "character_id": str(character_id),
                            "character_name": evidence.get("name") or evidence.get("identity_candidate", ""),
                            "identity_candidate": evidence.get("identity_candidate", ""),
                            "gender": evidence.get("gender", "unknown"),
                            "age": evidence.get("age", ""),
                            "appearance": evidence.get("appearance", ""),
                            "clothing": evidence.get("clothing", ""),
                            "emotion": evidence.get("emotion", ""),
                            "voice_instruction": evidence.get("voice_instruction", ""),
                        })
        entity_resolution = {
            "characters": [
                {
                    "entity_id": str(item.get("character_id") or item.get("entity_id")),
                    "matched_entity_id": str(item.get("character_id") or item.get("entity_id")),
                    "source": {
                        "name": item.get("character_name", ""),
                        "identity_candidate": item.get("identity_candidate", ""),
                        "appearance": item.get("appearance", ""),
                        "clothing": item.get("clothing", ""),
                        "emotion": item.get("emotion", ""),
                        "gender": item.get("gender", "unknown"),
                        "age": item.get("age", ""),
                        "voice_instruction": item.get("voice_instruction", ""),
                    },
                }
                for item in report_characters
                if isinstance(item, dict) and (item.get("character_id") or item.get("entity_id"))
            ]
        }
        database = ComicDatabase(get_data_root() / "comic2anime.sqlite3")
        database.initialize()
        entity_resolution["characters"] = _ensure_report_character_entities(
            database, project_id, parsed_report, entity_resolution["characters"]
        )
        audio, manifest_json, report_json = ComicPageVoiceReferences().generate_page_voices(
            project_id, entity_resolution, "自然、清晰、稳定的动漫角色参考音色。", reference_text,
            language, model_path, device, force_regenerate,
            asset_generation_report, None,
        )
        if audio is None:
            return None, manifest_json, "[]", report_json
        _, saved_paths = ComicSaveAudioReference().save(
            audio, output_directory, filename_prefix, manifest_json
        )
        return audio, manifest_json, saved_paths, report_json


class ComicSaveStoryboardOutputs:
    """Export current-page storyboard and summary as separate files."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "storyboard": ("STRING", {"multiline": True, "default": ""}),
            "story_summary": ("STRING", {"multiline": True, "default": ""}),
            "output_directory": ("STRING", {"default": "output/anime_storyboard"}),
            "page_key": ("STRING", {"default": "page_001"}),
        }}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("saved_paths_json", "storyboard")
    FUNCTION = "save"
    CATEGORY = "Comic2Anime/Export"

    def save(self, storyboard, story_summary, output_directory, page_key):
        output = Path(output_directory).expanduser()
        if not output.is_absolute():
            output = Path.cwd() / output
        output.mkdir(parents=True, exist_ok=True)
        stem = safe_component(page_key) or "page"
        storyboard_path = output / (stem + "_storyboard.md")
        summary_path = output / (stem + "_summary.md")
        json_path = output / (stem + "_storyboard.json")
        storyboard_path.write_text(storyboard or "", encoding="utf-8")
        summary_path.write_text(story_summary or "", encoding="utf-8")
        json_path.write_text(json.dumps({"storyboard": storyboard or "", "summary": story_summary or ""}, ensure_ascii=False, indent=2), encoding="utf-8")
        return json.dumps({"storyboard": str(storyboard_path), "summary": str(summary_path), "json": str(json_path)}, ensure_ascii=False), storyboard


NODE_CLASS_MAPPINGS = {
    "ComicCharacterVoiceReference": ComicCharacterVoiceReference,
    "ComicAnimePageStoryboardPrompt": ComicAnimePageStoryboardPrompt,
    "ComicPageVoiceReferences": ComicPageVoiceReferences,
    "ComicSaveAudioReference": ComicSaveAudioReference,
    "ComicAssetVoiceGenerationAndExport": ComicAssetVoiceGenerationAndExport,
    "ComicSaveStoryboardOutputs": ComicSaveStoryboardOutputs,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ComicCharacterVoiceReference": "Comic2Anime Character Voice Reference (One Voice per Character)",
    "ComicAnimePageStoryboardPrompt": "Comic2Anime Current Page Storyboard Prompt",
    "ComicPageVoiceReferences": "Comic2Anime Current Page Character Voices",
    "ComicSaveAudioReference": "Comic2Anime Export Reference Audio",
    "ComicAssetVoiceGenerationAndExport": "Comic2Anime Asset Voice Generation + WAV Export",
    "ComicSaveStoryboardOutputs": "Comic2Anime Export Storyboard and Summary",
}
