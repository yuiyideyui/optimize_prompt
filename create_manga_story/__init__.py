"""ComfyUI Comic2Anime first-stage custom nodes."""

from .nodes.comic_loader import ComicChapterLoader
from .nodes.chapter_summarizer import ComicChapterSummarizer
from .nodes.entity_resolver import ComicEntityResolver
from .nodes.gemini_analyzer import GeminiComicPageAnalyzer
from .nodes.knowledge_retriever import ComicKnowledgeRetriever
from .nodes.knowledge_updater import ComicKnowledgeBaseUpdater
from .nodes.page_context import ComicPageContextBuilder
from .nodes.rag_index_rebuilder import ComicKnowledgeIndexRebuilder

NODE_CLASS_MAPPINGS = {
    "ComicChapterLoader": ComicChapterLoader,
    "GeminiComicPageAnalyzer": GeminiComicPageAnalyzer,
    "ComicPageContextBuilder": ComicPageContextBuilder,
    "ComicEntityResolver": ComicEntityResolver,
    "ComicKnowledgeRetriever": ComicKnowledgeRetriever,
    "ComicChapterSummarizer": ComicChapterSummarizer,
    "ComicKnowledgeBaseUpdater": ComicKnowledgeBaseUpdater,
    "ComicKnowledgeIndexRebuilder": ComicKnowledgeIndexRebuilder,
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
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
