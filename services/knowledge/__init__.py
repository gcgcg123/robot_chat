"""Local knowledge retrieval (RAG) over curated documents.

Separate from ``services/memory`` on purpose: the flywheel's slot/hot/cold tiering, decay
and hit promotion are meaningless for static documents, and its 500-candidate budget must
not be shared with them. The retrieval *code* is reused from
``services/memory/retriever.py`` -- only the data source differs.

The corpus pipeline is:

    data/knowledge/profiles/*.json   per-document cleaning rules (see profiles.py)
    data/RAG_data/md/*.md            the documents themselves
        -> scripts/analyze-rag-corpus.py   measure before importing
        -> scripts/import-knowledge.py     clean, chunk, embed, store (migration v6)
        -> services/knowledge/retriever.py KnowledgeProvider, wired as providers["rag"]
"""

from services.knowledge.profiles import CLEANER_VERSION, KnowledgeProfile, load_profiles, profile_for

__all__ = ["CLEANER_VERSION", "KnowledgeProfile", "load_profiles", "profile_for"]
