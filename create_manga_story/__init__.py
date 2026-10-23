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
    ComicAssetLoader,
    ComicAssetQuery,
    ComicAssetRegistrar,
    ComicAssetRequestBuilder,
    ComicAssetReview,
)

from .nodes.asset_workflow_nodes import (
    ComicAssetReferenceSelector,
    ComicAssetRetryPlanner,
    ComicAssetWorkflowRegistrar,
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
    "ComicAssetRequestBuilder": ComicAssetRequestBuilder,
    "ComicAssetRegistrar": ComicAssetRegistrar,
    "ComicAssetQuery": ComicAssetQuery,
    "ComicAssetLoader": ComicAssetLoader,
    "ComicAssetReview": ComicAssetReview,

    "ComicAssetReferenceSelector": ComicAssetReferenceSelector,
    "GeminiAssetPromptGenerator": GeminiAssetPromptGenerator,
    "GeminiAssetVisualQC": GeminiAssetVisualQC,
    "ComicAssetRetryPlanner": ComicAssetRetryPlanner,
    "ComicAssetWorkflowRegistrar": ComicAssetWorkflowRegistrar,
    "ComicWholeStoryContextBuilder": ComicWholeStoryContextBuilder,
    "ComicCharacterStateSnapshotBuilder": ComicCharacterStateSnapshotBuilder,
    "ComicAdaptationPlanSaver": ComicAdaptationPlanSaver,
    "GeminiComicAdaptationPlanner": GeminiComicAdaptationPlanner,
    "ComicShotPackageBuilder": ComicShotPackageBuilder,
    "ComicShotPlanReview": ComicShotPlanReview,
    "ComicVideoRunRecorder": ComicVideoRunRecorder,
    "ComicVideoQCRecorder": ComicVideoQCRecorder,
    "ComicShotRetryPlanner": ComicShotRetryPlanner,
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
    "ComicAssetRequestBuilder": "Comic Asset Request Builder",
    "ComicAssetRegistrar": "Comic Asset Registrar",
    "ComicAssetQuery": "Comic Asset Query",
    "ComicAssetLoader": "Comic Asset Loader",
    "ComicAssetReview": "Comic Asset Review / Set Primary",

    "ComicAssetReferenceSelector": "Comic Asset Reference Selector (Qwen IMAGE x3)",
    "GeminiAssetPromptGenerator": "Gemini Asset Prompt Generator",
    "GeminiAssetVisualQC": "Gemini Asset Visual QC",
    "ComicAssetRetryPlanner": "Comic Asset Retry Planner",
    "ComicAssetWorkflowRegistrar": "Comic Asset Workflow Registrar",
    "ComicWholeStoryContextBuilder": "Comic Whole-Story Context Builder",
    "ComicCharacterStateSnapshotBuilder": "Comic Character State Snapshot Builder",
    "ComicAdaptationPlanSaver": "Comic Adaptation Plan Saver",
    "GeminiComicAdaptationPlanner": "Gemini Comic Adaptation Planner",
    "ComicShotPackageBuilder": "Comic Shot Package Builder",
    "ComicShotPlanReview": "Comic Shot Plan Review",
    "ComicVideoRunRecorder": "Comic Video Run Recorder",
    "ComicVideoQCRecorder": "Comic Video QC Recorder",
    "ComicShotRetryPlanner": "Comic Shot Retry Planner",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
