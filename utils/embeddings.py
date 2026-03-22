# utils/embeddings.py
#
# Local embedding model singleton using sentence-transformers (all-MiniLM-L6-v2).
# This produces 384D vectors suitable for cosine similarity search.

import logging
from typing import List

logger = logging.getLogger(__name__)

class EmbeddingModel:
    _instance = None
    _model = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(EmbeddingModel, cls).__new__(cls)
            cls._instance._load_model()
        return cls._instance

    def _load_model(self):
        """Lazy load the sentence-transformers model once."""
        try:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading local embedding model: all-MiniLM-L6-v2...")
            self._model = SentenceTransformer('all-MiniLM-L6-v2')
            logger.info("Model loaded successfully (384D).")
        except Exception as e:
            logger.error(f"Failed to load sentence-transformers: {e}")
            raise RuntimeError("Could not initialize local EmbeddingModel. Ensure 'sentence-transformers' is installed.")

    def embed(self, text: str) -> List[float]:
        """Generate a 384D embedding for the provided text."""
        if not text.strip():
            return []
        try:
            # result is a numpy array, convert to list
            embedding = self._model.encode(text)
            return embedding.tolist()
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return []

# Singleton instance access
_singleton = None

def get_embedding(text: str) -> List[float]:
    """Helper function to get an embedding using the local singleton model."""
    global _singleton
    if _singleton is None:
        _singleton = EmbeddingModel()
    return _singleton.embed(text)
