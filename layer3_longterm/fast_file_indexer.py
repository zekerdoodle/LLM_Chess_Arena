"""
Fast File Indexer for Layer 3 - Long Term Memory

Provides lightning-fast BM25 based indexing for code files while keeping
semantic embeddings for memory systems. BM25 gives superior search relevance
compared to TF-IDF with the same speed, providing Cursor-like performance
for file search while preserving AI intelligence for conversations.
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
import pickle
import re
from collections import defaultdict, Counter
import math

from utils.logger import get_logger
import os

logger = get_logger(__name__)


class FastFileIndexer:
    """Fast BM25 based file indexer optimized for code search with superior relevance."""
    
    def __init__(self, vault_path: Optional[str] = None, k1: float = 1.5, b: float = 0.75):
        """Initialize the fast file indexer with BM25 parameters.
        
        Args:
            vault_path: Path to vault directory for storing index
            k1: BM25 term frequency saturation parameter (default: 1.5)
            b: BM25 document length normalization parameter (default: 0.75)
        """
        try:
            if vault_path is None:
                from utils.vault_paths import get_vault_root
                self.vault_path = Path(get_vault_root())
            else:
                self.vault_path = Path(vault_path)
        except Exception:
            self.vault_path = Path(vault_path or "vault")
        self.vault_path.mkdir(parents=True, exist_ok=True)
        
        # BM25 parameters
        self.k1 = k1  # Term frequency saturation parameter
        self.b = b    # Document length normalization parameter
        
        # Index storage files
        self.index_file = self.vault_path / "fast_bm25_index.pkl"
        self.metadata_file = self.vault_path / "fast_bm25_metadata.json"
        
        # In-memory structures
        self.document_vectors = {}  # doc_id -> {term: bm25_score}
        self.document_term_freqs = {}  # doc_id -> {term: frequency}
        self.term_document_freq = defaultdict(int)  # term -> number of docs containing it
        self.document_lengths = {}  # doc_id -> document length (token count)
        self.document_metadata = {}  # doc_id -> metadata
        self.total_documents = 0
        self.avg_doc_length = 0.0
        
        # Load existing index
        self._load_index()
        
        logger.info(f"FastBM25Indexer initialized with {self.total_documents} documents (k1={self.k1}, b={self.b})")
    
    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text for indexing with code-aware preprocessing.
        
        Args:
            text: Text to tokenize
            
        Returns:
            List of tokens
        """
        # Code-aware tokenization
        # Split on common code delimiters while preserving meaningful tokens
        text = text.lower()
        
        # Replace camelCase and snake_case with space-separated words
        text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)  # camelCase
        text = re.sub(r'_+', ' ', text)  # snake_case
        
        # Split on non-alphanumeric but preserve important code symbols
        tokens = re.findall(r'\b\w+\b', text)
        
        # Filter out very short tokens and common stop words
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'is', 'are', 'was', 'were', 'be', 'been', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should'}
        tokens = [token for token in tokens if len(token) > 2 and token not in stop_words]
        
        return tokens
    
    def _compute_bm25(self, term_freq: Dict[str, int], doc_length: int) -> Dict[str, float]:
        """Compute BM25 scores for a document.
        
        Args:
            term_freq: Term frequencies for the document
            doc_length: Total number of terms in document
            
        Returns:
            Dictionary of term -> BM25 score
        """
        bm25_scores = {}
        
        for term, freq in term_freq.items():
            # IDF component (with smoothing to avoid zero division and negative values)
            doc_freq = self.term_document_freq.get(term, 1)
            if self.total_documents > 0:
                # Standard BM25 IDF with additional smoothing to avoid negative values
                idf = math.log((self.total_documents - doc_freq + 0.5) / (doc_freq + 0.5))
                # Ensure IDF is non-negative for small corpora
                idf = max(idf, 0.01)
            else:
                idf = 0
            
            # BM25 term frequency component with saturation
            tf_component = (freq * (self.k1 + 1)) / (freq + self.k1 * (1 - self.b + self.b * (doc_length / max(self.avg_doc_length, 1.0))))
            
            # BM25 score
            bm25_scores[term] = idf * tf_component
        
        return bm25_scores
    
    def add_document(self, doc_id: str, content: str, metadata: Dict[str, Any]) -> None:
        """Add or update a document in the index.
        
        Args:
            doc_id: Unique document identifier
            content: Document content to index
            metadata: Document metadata
        """
        # Remove existing document if it exists
        if doc_id in self.document_vectors:
            self.remove_document(doc_id)
        
        # Tokenize content
        tokens = self._tokenize(content)
        if not tokens:
            return
        
        # Compute term frequencies
        term_freq = Counter(tokens)
        doc_length = len(tokens)
        
        # Update global term document frequencies and total documents first
        for term in term_freq.keys():
            self.term_document_freq[term] += 1
        self.total_documents += 1
        
        # Store document length, term frequencies, and update average
        self.document_lengths[doc_id] = doc_length
        self.document_term_freqs[doc_id] = dict(term_freq)
        self.avg_doc_length = sum(self.document_lengths.values()) / len(self.document_lengths)
        
        # Now compute BM25 scores with correct total_documents count and avg_doc_length
        bm25_scores = self._compute_bm25(term_freq, doc_length)
        
        # Store document
        self.document_vectors[doc_id] = bm25_scores
        self.document_metadata[doc_id] = {
            **metadata,
            'indexed_at': time.time(),
            'token_count': doc_length,
            'unique_terms': len(term_freq)
        }
        
        logger.debug(f"Indexed document {doc_id} with {len(tokens)} tokens, {len(term_freq)} unique terms")
    
    def remove_document(self, doc_id: str) -> bool:
        """Remove a document from the index.
        
        Args:
            doc_id: Document ID to remove
            
        Returns:
            True if document was removed, False if not found
        """
        if doc_id not in self.document_vectors:
            return False
        
        # Update term document frequencies
        for term in self.document_vectors[doc_id].keys():
            self.term_document_freq[term] -= 1
            if self.term_document_freq[term] <= 0:
                del self.term_document_freq[term]
        
        # Remove document and update average length
        del self.document_vectors[doc_id]
        del self.document_metadata[doc_id]
        if doc_id in self.document_lengths:
            del self.document_lengths[doc_id]
        if doc_id in self.document_term_freqs:
            del self.document_term_freqs[doc_id]
        self.total_documents -= 1
        
        # Recalculate average document length
        if self.document_lengths:
            self.avg_doc_length = sum(self.document_lengths.values()) / len(self.document_lengths)
        else:
            self.avg_doc_length = 0.0
        
        return True
    
    def search(self, query: str, k: int = 10, similarity_threshold: float = 0.1) -> List[Tuple[str, float, Dict[str, Any]]]:
        """Search for documents matching the query.
        
        Args:
            query: Search query
            k: Number of results to return
            similarity_threshold: Minimum similarity score
            
        Returns:
            List of (doc_id, score, metadata) tuples
        """
        if not query.strip() or not self.document_vectors:
            return []
        
        # Tokenize query
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []
        
        # Compute query BM25 vector (simplified for query processing)
        query_term_freq = Counter(query_tokens)
        query_vector = {}
        
        for term, freq in query_term_freq.items():
            # For queries, we use a simplified BM25 scoring (IDF component mainly)
            doc_freq = self.term_document_freq.get(term, 0)
            if doc_freq > 0 and self.total_documents > 0:
                # BM25 IDF with smoothing
                idf = math.log((self.total_documents - doc_freq + 0.5) / (doc_freq + 0.5))
                # Simple term frequency for query (no length normalization needed)
                query_tf = (freq * (self.k1 + 1)) / (freq + self.k1)
                query_vector[term] = idf * query_tf
            else:
                query_vector[term] = 0.0
        
        # Compute BM25 scores for all documents
        scores = []
        
        for doc_id, doc_vector in self.document_vectors.items():
            # BM25 score is the sum of individual term contributions
            bm25_score = 0.0
            doc_term_freqs = self.document_term_freqs.get(doc_id, {})
            doc_length = self.document_lengths.get(doc_id, 1)
            
            for term in query_tokens:
                term_freq_in_doc = doc_term_freqs.get(term, 0)
                if term_freq_in_doc > 0:
                    # Get document frequency for IDF calculation
                    doc_freq = self.term_document_freq.get(term, 0)
                    if doc_freq > 0:
                        # BM25 IDF component (with smoothing and non-negative constraint)
                        idf = math.log((self.total_documents - doc_freq + 0.5) / (doc_freq + 0.5))
                        idf = max(idf, 0.01)  # Ensure non-negative for small corpora
                        
                        # BM25 TF component with saturation and length normalization
                        tf_component = (term_freq_in_doc * (self.k1 + 1)) / (term_freq_in_doc + self.k1 * (1 - self.b + self.b * (doc_length / max(self.avg_doc_length, 1.0))))
                        
                        # Add this term's contribution to the total BM25 score
                        bm25_score += idf * tf_component
            
            if bm25_score >= similarity_threshold:
                scores.append((doc_id, bm25_score, self.document_metadata[doc_id]))
        
        # Sort by similarity and return top k
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:k]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get index statistics.
        
        Returns:
            Dictionary with index statistics
        """
        return {
            'total_documents': self.total_documents,
            'total_unique_terms': len(self.term_document_freq),
            'average_terms_per_doc': sum(len(vec) for vec in self.document_vectors.values()) / max(self.total_documents, 1),
            'index_size_mb': self._estimate_memory_usage() / (1024 * 1024)
        }
    
    def _estimate_memory_usage(self) -> int:
        """Estimate memory usage in bytes."""
        # Rough estimation
        doc_vectors_size = sum(len(vec) * 64 for vec in self.document_vectors.values())  # 64 bytes per term
        term_freq_size = len(self.term_document_freq) * 32  # 32 bytes per term
        metadata_size = len(str(self.document_metadata)) * 2  # Rough string size
        return doc_vectors_size + term_freq_size + metadata_size
    
    def save_index(self) -> None:
        """Save the index to disk."""
        try:
            if os.getenv('THEO_FAST_INDEX_NO_FS', '0') == '1':
                return
            # Save main index data
            index_data = {
                'document_vectors': dict(self.document_vectors),
                'document_term_freqs': dict(self.document_term_freqs),
                'term_document_freq': dict(self.term_document_freq),
                'document_lengths': dict(self.document_lengths),
                'total_documents': self.total_documents,
                'avg_doc_length': self.avg_doc_length,
                'k1': self.k1,
                'b': self.b
            }
            
            with open(self.index_file, 'wb') as f:
                pickle.dump(index_data, f, protocol=pickle.HIGHEST_PROTOCOL)
            
            # Save metadata separately as JSON for readability
            with open(self.metadata_file, 'w', encoding='utf-8') as f:
                json.dump(self.document_metadata, f, indent=2, ensure_ascii=False)
            
            logger.debug(f"Saved fast BM25 index with {self.total_documents} documents")
            
        except Exception as e:
            logger.error(f"Failed to save fast file index: {e}")
    
    def _load_index(self) -> None:
        """Load the index from disk."""
        try:
            if os.getenv('THEO_FAST_INDEX_NO_FS', '0') == '1':
                # Skip loading from disk in fast mode
                return
            # Load main index data
            if self.index_file.exists():
                with open(self.index_file, 'rb') as f:
                    index_data = pickle.load(f)
                
                self.document_vectors = index_data.get('document_vectors', {})
                self.document_term_freqs = index_data.get('document_term_freqs', {})
                self.term_document_freq = defaultdict(int, index_data.get('term_document_freq', {}))
                self.document_lengths = index_data.get('document_lengths', {})
                self.total_documents = index_data.get('total_documents', 0)
                self.avg_doc_length = index_data.get('avg_doc_length', 0.0)
                
                # Load BM25 parameters (use defaults if not saved)
                self.k1 = index_data.get('k1', self.k1)
                self.b = index_data.get('b', self.b)
            
            # Load metadata
            if self.metadata_file.exists():
                with open(self.metadata_file, 'r', encoding='utf-8') as f:
                    self.document_metadata = json.load(f)
            
            if self.total_documents > 0:
                logger.info(f"Loaded fast BM25 index with {self.total_documents} documents")
            
        except Exception as e:
            logger.warning(f"Could not load fast file index: {e}")
            # Initialize empty index
            self.document_vectors = {}
            self.document_term_freqs = {}
            self.term_document_freq = defaultdict(int)
            self.document_lengths = {}
            self.document_metadata = {}
            self.total_documents = 0
            self.avg_doc_length = 0.0
    
    def clear_index(self) -> None:
        """Clear the entire index."""
        self.document_vectors = {}
        self.document_term_freqs = {}
        self.term_document_freq = defaultdict(int)
        self.document_lengths = {}
        self.document_metadata = {}
        self.total_documents = 0
        self.avg_doc_length = 0.0
        
        # Remove index files
        if os.getenv('THEO_FAST_INDEX_NO_FS', '0') != '1':
            if self.index_file.exists():
                self.index_file.unlink()
            if self.metadata_file.exists():
                self.metadata_file.unlink()
        
        logger.info("Cleared fast BM25 index")


# Global instance
_fast_indexer = None


def get_fast_indexer() -> FastFileIndexer:
    """Get or create the global fast file indexer instance."""
    global _fast_indexer
    if _fast_indexer is None:
        _fast_indexer = FastFileIndexer()
    return _fast_indexer


# Public API functions
def fast_index_document(doc_id: str, content: str, metadata: Dict[str, Any]) -> None:
    """Add a document to the fast index."""
    get_fast_indexer().add_document(doc_id, content, metadata)


def fast_search(query: str, k: int = 10, similarity_threshold: float = 0.1) -> List[Tuple[str, float, Dict[str, Any]]]:
    """Search the fast index."""
    return get_fast_indexer().search(query, k, similarity_threshold)


# Backward-compatibility alias expected by some tests
def bm25_search(query: str, k: int = 10, similarity_threshold: float = 0.1) -> List[Tuple[str, float, Dict[str, Any]]]:
    """Alias for fast_search to keep API stable in tests."""
    return fast_search(query, k, similarity_threshold)


def fast_index_stats() -> Dict[str, Any]:
    """Get fast index statistics."""
    return get_fast_indexer().get_stats()


def save_fast_index() -> None:
    """Save the fast index to disk."""
    get_fast_indexer().save_index()


def clear_fast_index() -> None:
    """Clear the fast index."""
    get_fast_indexer().clear_index()
