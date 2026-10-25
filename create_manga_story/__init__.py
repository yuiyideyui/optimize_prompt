"""ComfyUI Comic2Anime first-stage custom nodes."""

from .nodes.comic_loader import ComicChapterLoader
from .nodes.chapter_summarizer import ComicChapterSummarizer
from .nodes.entity_resolver import ComicEntityResolver
from .nodes.gemini_analyzer import GeminiComicPageAnalyzer
from .nodes.knowledge_retriever import ComicKnowledgeRetriever
from .nodes.knowledge_updater import ComicKnowledgeBaseUpdater
from .nodes.page_context import ComicPageContextBuilder
from .nodes.rag_index_rebuilder import ComicKnowledgeIndexRebuilder
from .nodes.qwen_edit_2511 import QwenImageEditCustomNode
from .nodes.asset_nodes import (
    ComicAssetAutoTargetBuilder,
    ComicAssetAutoTargetBatchBuilder,
    ComicAssetLoader,
    ComicAssetQuery,
    ComicAssetRegistrar,
    ComicAssetRequestBuilder,
    ComicAssetReview,
)

from .nodes.asset_workflow_nodes import (
    ComicAssetReferenceSelector,
    ComicAssetRetryPlanner,
    ComicAssetBatchGenerationLoop,
    ChatGPTImageAssetBatchGenerationLoop,
    ComicAssetImageBatchGenerationLoop,
    ComicAssetWorkflowStatus,
    ComicAssetWorkflowRegistrar,
    GeminiQwenAssetGenerationLoop,
    GeminiAssetPromptGenerator,
    GeminiAssetVisualQC,
)
from .nodes.story_workflow_nodes import (
    GeminiComicAdaptationPlanner,
    ComicAdaptationPlanSaver,
    ComicCharacterStateSnapshotBuilder,
    ComicShotPackageBuilder,
    ComicShotPlanReview,
    ComicShotRetryPlanner,
    ComicVideoQCRecorder,
    ComicVideoRunRecorder,
    ComicWholeStoryContextBuilder,
)
from .nodes.voice_story_nodes import (
    ComicCharacterVoiceReference,
    ComicAnimePageStoryboardPrompt,
    ComicPageVoiceReferences,
    ComicSaveAudioReference,
    ComicSaveStoryboardOutputs,
    ComicAssetVoiceGenerationAndExport,
)
from .nodes.voice_instruction_nodes import ComicPageVoiceInstructionBuilder
from .web.resource_manager import register_resource_routes

register_resource_routes()

NODE_CLASS_MAPPINGS = {
    "ComicChapterLoader": ComicChapterLoader,
    "GeminiComicPageAnalyzer": GeminiComicPageAnalyzer,
    "ComicPageContextBuilder": ComicPageContextBuilder,
    "ComicEntityResolver": ComicEntityResolver,
    "ComicKnowledgeRetriever": ComicKnowledgeRetriever,
    "ComicChapterSummarizer": ComicChapterSummarizer,
    "ComicKnowledgeBaseUpdater": ComicKnowledgeBaseUpdater,
    "ComicKnowledgeIndexRebuilder": ComicKnowledgeIndexRebuilder,
    "QwenImageEditCustomNode": QwenImageEditCustomNode,
    "ComicAssetAutoTargetBuilder": ComicAssetAutoTargetBuilder,
    "ComicAssetAutoTargetBatchBuilder": ComicAssetAutoTargetBatchBuilder,
    "ComicAssetRequestBuilder": ComicAssetRequestBuilder,
    "ComicAssetRegistrar": ComicAssetRegistrar,
    "ComicAssetQuery": ComicAssetQuery,
    "ComicAssetLoader": ComicAssetLoader,
    "ComicAssetReview": ComicAssetReview,

    "ComicAssetReferenceSelector": ComicAssetReferenceSelector,
    "GeminiAssetPromptGenerator": GeminiAssetPromptGenerator,
    "GeminiAssetVisualQC": GeminiAssetVisualQC,
    "ComicAssetRetryPlanner": ComicAssetRetryPlanner,
    "ComicAssetBatchGenerationLoop": ComicAssetBatchGenerationLoop,
    "ChatGPTImageAssetBatchGenerationLoop": ChatGPTImageAssetBatchGenerationLoop,
    "ComicAssetImageBatchGenerationLoop": ComicAssetImageBatchGenerationLoop,
    "ComicAssetWorkflowStatus": ComicAssetWorkflowStatus,
    "ComicAssetWorkflowRegistrar": ComicAssetWorkflowRegistrar,
    "GeminiQwenAssetGenerationLoop": GeminiQwenAssetGenerationLoop,
    "ComicWholeStoryContextBuilder": ComicWholeStoryContextBuilder,
    "ComicCharacterStateSnapshotBuilder": ComicCharacterStateSnapshotBuilder,
    "ComicAdaptationPlanSaver": ComicAdaptationPlanSaver,
    "GeminiComicAdaptationPlanner": GeminiComicAdaptationPlanner,
    "ComicShotPackageBuilder": ComicShotPackageBuilder,
    "ComicShotPlanReview": ComicShotPlanReview,
    "ComicVideoRunRecorder": ComicVideoRunRecorder,
    "ComicVideoQCRecorder": ComicVideoQCRecorder,
    "ComicShotRetryPlanner": ComicShotRetryPlanner,
    "ComicCharacterVoiceReference": ComicCharacterVoiceReference,
    "ComicAnimePageStoryboardPrompt": ComicAnimePageStoryboardPrompt,
    "ComicPageVoiceReferences": ComicPageVoiceReferences,
    "ComicSaveAudioReference": ComicSaveAudioReference,
    "ComicSaveStoryboardOutputs": ComicSaveStoryboardOutputs,
    "ComicPageVoiceInstructionBuilder": ComicPageVoiceInstructionBuilder,
    "ComicAssetVoiceGenerationAndExport": ComicAssetVoiceGenerationAndExport,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "ComicChapterLoader": "Comic Chapter Loader",
    "GeminiComicPageAnalyzer": "Gemini Comic Page Analyzer",
    "ComicPageContextBuilder": "Comic Page Context Builder",
    "ComicEntityResolver": "Comic Entity Resolver",
    "ComicKnowledgeRetriever": "Comic Knowledge Retriever",
    "ComicChapterSummarizer": "Comic Chapter Summarizer",
    "ComicKnowledgeBaseUpdater": "Comic Knowledge Base Updater",
    "ComicKnowledgeIndexRebuilder": "Comic Knowledge Index Rebuilder",
    "QwenImageEditCustomNode": "Qwen Image Edit (Custom)",
    "ComicAssetAutoTargetBuilder": "Comic Asset Auto Target + Approved Reference Builder",
    "ComicAssetAutoTargetBatchBuilder": "Comic Asset Auto Target Batch Builder (All Page Characters)",
    "ComicAssetRequestBuilder": "Comic Asset Request Builder",
    "ComicAssetRegistrar": "Comic Asset Registrar",
    "ComicAssetQuery": "Comic Asset Query",
    "ComicAssetLoader": "Comic Asset Loader",
    "ComicAssetReview": "Comic Asset Review / Set Primary",

    "ComicAssetReferenceSelector": "Comic Asset Reference Selector (Qwen IMAGE x3)",
    "GeminiAssetPromptGenerator": "Gemini Asset Prompt Generator",
    "GeminiAssetVisualQC": "Gemini Asset Visual QC",
    "ComicAssetRetryPlanner": "Comic Asset Retry Planner",
    "ComicAssetBatchGenerationLoop": "Comic Asset Batch Generation Loop",
    "ChatGPTImageAssetBatchGenerationLoop": "ChatGPT Image Asset Batch Generation Loop",
    "ComicAssetImageBatchGenerationLoop": "Comic Asset Image Batch Generation Loop (ChatGPT / Qwen Image 2)",
    "ComicAssetWorkflowStatus": "Comic Asset Workflow Status",
    "ComicAssetWorkflowRegistrar": "Comic Asset Workflow Registrar",
    "GeminiQwenAssetGenerationLoop": "Gemini + Qwen Asset Generation Loop",
    "ComicWholeStoryContextBuilder": "Comic Whole-Story Context Builder",
    "ComicCharacterStateSnapshotBuilder": "Comic Character State Snapshot Builder",
    "ComicAdaptationPlanSaver": "Comic Adaptation Plan Saver",
    "GeminiComicAdaptationPlanner": "Gemini Comic Adaptation Planner",
    "ComicShotPackageBuilder": "Comic Shot Package Builder",
    "ComicShotPlanReview": "Comic Shot Plan Review",
    "ComicVideoRunRecorder": "Comic Video Run Recorder",
    "ComicVideoQCRecorder": "Comic Video QC Recorder",
    "ComicShotRetryPlanner": "Comic Shot Retry Planner",
    "ComicCharacterVoiceReference": "Comic2Anime Character Voice Reference (One Voice per Character)",
    "ComicAnimePageStoryboardPrompt": "Comic2Anime Current Page Storyboard Prompt",
    "ComicPageVoiceReferences": "Comic2Anime Current Page Character Voices",
    "ComicSaveAudioReference": "Comic2Anime Export Reference Audio",
    "ComicSaveStoryboardOutputs": "Comic2Anime Export Storyboard and Summary",
    "ComicPageVoiceInstructionBuilder": "Comic2Anime Current Page Voice Instruction",
    "ComicAssetVoiceGenerationAndExport": "Comic2Anime Asset Voice Generation + WAV Export",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
