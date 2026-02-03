"""
Web Search Tools for Layer 4

Provides web search capabilities using Perplexity's Search API.
Returns ranked web search results with advanced filtering and customization options.

Available Tools:
- web_search(query | queries) - Perform one or multiple web searches (parallel-capable)

Key Features:
- Uses Perplexity's Search API for high-quality web search results
- Supports filtering by recency, domain, language, and country
- Parallel processing up to 5 searches for efficiency
- Results optimized for AI consumption with citations
- Comprehensive error handling and logging following L4.tools specifications
"""

import asyncio
import os
import time
from typing import Any, Dict, List, Optional, Union

from utils.logger import get_logger

logger = get_logger(__name__)

# Optional import for Perplexity SDK
try:
    from perplexity import Perplexity
except ImportError:
    Perplexity = None


class PerplexitySearchClient:
    """
    Client for performing web searches using Perplexity's Search API.
    """

    def __init__(self):
        """
        Initialize the Perplexity search client.

        Reads API key from PERPLEXITY_API_KEY environment variable.
        """
        if not Perplexity:
            raise ImportError(
                "perplexityai package is required for web search. "
                "Install with: pip install perplexityai"
            )

        api_key = os.environ.get("PERPLEXITY_API_KEY")
        if not api_key:
            raise ValueError(
                "PERPLEXITY_API_KEY environment variable is not set. "
                "Please add it to your .env file."
            )

        self.client = Perplexity(api_key=api_key)

        # Maximum parallel searches allowed (Perplexity supports up to 5 queries)
        self.max_parallel_searches = 5

        # Default settings (medium defaults)
        self.default_max_results = 10
        self.default_max_tokens_per_page = 1024

        logger.info("L4.web_search [init] - PerplexitySearchClient initialized")

    async def search_single(
        self,
        query: str,
        max_results: Optional[int] = None,
        recency: Optional[str] = None,
        country: Optional[str] = None,
        domains: Optional[List[str]] = None,
        languages: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Perform a single web search using Perplexity's Search API.

        Args:
            query: The search query string
            max_results: Number of results (1-20, default 10)
            recency: Filter by time period ("day", "week", "month", "year")
            country: ISO 2-letter country code for regional filtering
            domains: List of domains to include/exclude (prefix with "-" to exclude)
            languages: List of ISO 639-1 language codes

        Returns:
            Dictionary containing search results and metadata
        """
        start_time = time.time()

        try:
            logger.debug(
                f"L4.web_search [tool:web_search] - Starting search for: '{query[:100]}...'"
            )

            # Build search parameters
            search_params: Dict[str, Any] = {
                "query": query,
                "max_results": max_results or self.default_max_results,
                "max_tokens_per_page": self.default_max_tokens_per_page,
            }

            # Add optional filters
            if recency:
                search_params["search_recency_filter"] = recency
            if country:
                search_params["country"] = country
            if domains:
                search_params["search_domain_filter"] = domains[:20]  # Max 20 domains
            if languages:
                search_params["search_language_filter"] = languages[:10]  # Max 10 languages

            # Execute search in thread pool to avoid blocking
            def _call():
                return self.client.search.create(**search_params)

            response = await asyncio.to_thread(_call)

            # Format results
            results = []
            if hasattr(response, "results") and response.results:
                for r in response.results:
                    result_item = {
                        "title": getattr(r, "title", ""),
                        "url": getattr(r, "url", ""),
                        "snippet": getattr(r, "snippet", ""),
                    }
                    if hasattr(r, "date") and r.date:
                        result_item["date"] = r.date
                    results.append(result_item)

            search_time = round(time.time() - start_time, 2)

            result = {
                "query": query,
                "results": results,
                "result_count": len(results),
                "search_time": search_time,
                "success": True,
            }

            logger.info(
                f"L4.web_search [tool:web_search] - Search completed "
                f"({len(results)} results, {search_time}s)"
            )
            return result

        except Exception as e:
            error_result = {
                "query": query,
                "error": str(e),
                "search_time": round(time.time() - start_time, 2),
                "success": False,
            }
            logger.error(f"L4.web_search [tool:web_search] - Search failed: {e}")
            return error_result

    async def search_parallel(
        self,
        queries: List[str],
        max_results: Optional[int] = None,
        recency: Optional[str] = None,
        country: Optional[str] = None,
        domains: Optional[List[str]] = None,
        languages: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Perform multiple web searches in parallel.

        Args:
            queries: List of search query strings (max 5)
            max_results: Number of results per query (1-20, default 10)
            recency: Filter by time period ("day", "week", "month", "year")
            country: ISO 2-letter country code for regional filtering
            domains: List of domains to include/exclude
            languages: List of ISO 639-1 language codes

        Returns:
            List of search result dictionaries
        """
        # Limit to maximum allowed parallel searches
        if len(queries) > self.max_parallel_searches:
            logger.warning(
                f"L4.web_search [tool:parallel_web_search] - "
                f"Limiting {len(queries)} queries to {self.max_parallel_searches}"
            )
            queries = queries[: self.max_parallel_searches]

        start_time = time.time()
        logger.info(
            f"L4.web_search [tool:parallel_web_search] - "
            f"Starting {len(queries)} parallel searches"
        )

        try:
            # Run searches concurrently using asyncio.gather
            tasks = [
                self.search_single(
                    query=q,
                    max_results=max_results,
                    recency=recency,
                    country=country,
                    domains=domains,
                    languages=languages,
                )
                for q in queries
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results and handle any exceptions
            processed_results = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    error_result = {
                        "query": queries[i] if i < len(queries) else "unknown",
                        "error": str(result),
                        "search_time": round(time.time() - start_time, 2),
                        "success": False,
                    }
                    processed_results.append(error_result)
                    logger.error(
                        f"L4.web_search [tool:parallel_web_search] - "
                        f"Search {i + 1} failed: {result}"
                    )
                else:
                    processed_results.append(result)

            total_time = round(time.time() - start_time, 2)
            successful_searches = sum(
                1 for r in processed_results if r.get("success", False)
            )

            logger.info(
                f"L4.web_search [tool:parallel_web_search] - "
                f"Completed {len(queries)} searches "
                f"({successful_searches} successful, total_time: {total_time}s)"
            )

            return processed_results

        except Exception as e:
            logger.error(
                f"L4.web_search [tool:parallel_web_search] - Parallel search failed: {e}"
            )
            return [
                {
                    "query": query,
                    "error": f"Parallel search failed: {e}",
                    "search_time": round(time.time() - start_time, 2),
                    "success": False,
                }
                for query in queries
            ]


# Global search client instance
_search_client: Optional[PerplexitySearchClient] = None


def _get_search_client() -> PerplexitySearchClient:
    """Get or create the global Perplexity search client instance."""
    global _search_client
    if _search_client is None:
        _search_client = PerplexitySearchClient()
    return _search_client


def _format_results(results: List[Dict[str, Any]]) -> str:
    """Format search results for display."""
    formatted = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        snippet = r.get("snippet", "")
        date = r.get("date", "")

        entry = f"{i}. **{title}**\n   {url}"
        if date:
            entry += f"\n   Published: {date}"
        if snippet:
            # Truncate long snippets
            if len(snippet) > 300:
                snippet = snippet[:300] + "..."
            entry += f"\n   {snippet}"
        formatted.append(entry)

    return "\n\n".join(formatted)


async def _web_search_async(
    query: str,
    max_results: Optional[int] = None,
    recency: Optional[str] = None,
    country: Optional[str] = None,
    domains: Optional[List[str]] = None,
    languages: Optional[List[str]] = None,
) -> str:
    """
    Perform a single web search using Perplexity's Search API.

    Args:
        query: The search query string
        max_results: Number of results (1-20, default 10)
        recency: Filter by time period ("day", "week", "month", "year")
        country: ISO 2-letter country code (e.g., "US", "GB", "DE")
        domains: List of domains to include/exclude (prefix with "-" to exclude)
        languages: List of ISO 639-1 language codes (e.g., ["en", "fr"])

    Returns:
        Formatted search results as a string
    """
    if not query or not query.strip():
        logger.warning("L4.web_search [tool:web_search] - Empty query provided")
        return "Error: Search query cannot be empty"

    try:
        client = _get_search_client()
        result = await client.search_single(
            query=query.strip(),
            max_results=max_results,
            recency=recency,
            country=country,
            domains=domains,
            languages=languages,
        )

        if result.get("success"):
            results = result.get("results", [])
            if results:
                formatted = _format_results(results)
                return (
                    f"Web Search Results for: '{result['query']}'\n\n"
                    f"{formatted}\n\n"
                    f"[{result['result_count']} results in {result['search_time']}s]"
                )
            else:
                return f"No results found for: '{result['query']}'"
        else:
            return (
                f"Web Search Error for: '{result.get('query', query)}'\n\n"
                f"Error: {result.get('error', 'Unknown error')}\n\n"
                f"[Search failed after {result.get('search_time', 0)}s]"
            )

    except Exception as e:
        logger.error(f"L4.web_search [tool:web_search] - Unexpected error: {e}")
        return f"Web Search Error: {e}"


async def _parallel_web_search_async(
    queries: Union[List[str], str],
    max_results: Optional[int] = None,
    recency: Optional[str] = None,
    country: Optional[str] = None,
    domains: Optional[List[str]] = None,
    languages: Optional[List[str]] = None,
) -> str:
    """
    Perform multiple web searches in parallel (up to 5 searches).

    Args:
        queries: List of search query strings (max 5), or comma-separated string
        max_results: Number of results per query (1-20, default 10)
        recency: Filter by time period ("day", "week", "month", "year")
        country: ISO 2-letter country code
        domains: List of domains to include/exclude
        languages: List of ISO 639-1 language codes

    Returns:
        Formatted results from all searches as a string
    """
    # Handle string input (comma-separated queries)
    if isinstance(queries, str):
        queries = [q.strip() for q in queries.split(",") if q.strip()]

    if not queries:
        logger.warning("L4.web_search [tool:parallel_web_search] - No queries provided")
        return "Error: No search queries provided"

    # Remove empty queries
    queries = [q for q in queries if q and q.strip()]

    if not queries:
        logger.warning(
            "L4.web_search [tool:parallel_web_search] - All queries were empty"
        )
        return "Error: All search queries were empty"

    try:
        client = _get_search_client()
        results = await client.search_parallel(
            queries=queries,
            max_results=max_results,
            recency=recency,
            country=country,
            domains=domains,
            languages=languages,
        )

        formatted_results = []
        successful_searches = 0

        for i, result in enumerate(results):
            query = result.get("query", f"Query {i + 1}")
            if result.get("success", False):
                successful_searches += 1
                search_results = result.get("results", [])
                if search_results:
                    formatted = _format_results(search_results)
                    formatted_results.append(
                        f"=== Search {i + 1}: '{query}' ===\n"
                        f"{formatted}\n"
                        f"[{result['result_count']} results in {result['search_time']}s]\n"
                    )
                else:
                    formatted_results.append(
                        f"=== Search {i + 1}: '{query}' ===\n"
                        f"No results found.\n"
                    )
            else:
                formatted_results.append(
                    f"=== Search {i + 1}: '{query}' (FAILED) ===\n"
                    f"Error: {result.get('error', 'Unknown error')}\n"
                    f"[Failed after {result['search_time']}s]\n"
                )

        header = f"Parallel Web Search Results ({successful_searches}/{len(results)} successful)\n\n"
        return header + "\n".join(formatted_results)

    except Exception as e:
        logger.error(
            f"L4.web_search [tool:parallel_web_search] - Unexpected error: {e}"
        )
        return f"Parallel Web Search Error: {e}"


async def web_search(
    query: Optional[str] = None,
    queries: Optional[Union[List[str], str]] = None,
    max_results: Optional[int] = None,
    recency: Optional[str] = None,
    country: Optional[str] = None,
    domains: Optional[List[str]] = None,
    languages: Optional[List[str]] = None,
) -> str:
    """
    Unified async tool entrypoint for web search (single or multiple queries).

    Args:
        query: Single search query string
        queries: Multiple search queries (list or comma-separated string, max 5)
        max_results: Number of results (1-20, default 10)
        recency: Filter by time period ("day", "week", "month", "year")
        country: ISO 2-letter country code (e.g., "US", "GB")
        domains: List of domains to include/exclude (prefix "-" to exclude)
        languages: List of ISO 639-1 language codes (e.g., ["en"])

    Returns:
        Formatted search results as a string
    """
    if queries:
        return await _parallel_web_search_async(
            queries=queries,
            max_results=max_results,
            recency=recency,
            country=country,
            domains=domains,
            languages=languages,
        )
    elif query:
        return await _web_search_async(
            query=query,
            max_results=max_results,
            recency=recency,
            country=country,
            domains=domains,
            languages=languages,
        )
    else:
        return "Error: Either 'query' or 'queries' must be provided"
