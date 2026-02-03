"""
Deep Research Tools (Layer 4)

Implements two research tools for low-cost, precise web research:
- url_retrieval(query): Free search to return URLs and basic metadata
- page_parser(url): Fetch, parse, clean, and optionally save webpage content

Design goals:
- Zero/low cost (DuckDuckGo search + direct HTTP fetch)
- Robust parsing (Readability + Markdown conversion with fallbacks)
- Token-aware truncation and optional saving to the vault
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from utils.logger import get_logger
from utils.config_loader import load_config, get_config_value
from utils.token_counter import count_tokens, truncate_text_to_tokens

logger = get_logger(__name__)


# Optional imports guarded for tests to mock easily
try:
    # New package name (preferred)
    from ddgs import DDGS as DDGS_NATIVE  # type: ignore
except Exception:  # pragma: no cover
    DDGS_NATIVE = None  # type: ignore
try:
    # Legacy package names (for back-compat)
    from duckduckgo_search import ddg  # type: ignore
except Exception:  # pragma: no cover - handled in tests via mocks
    ddg = None  # type: ignore
try:
    from duckduckgo_search import DDGS  # type: ignore
except Exception:  # pragma: no cover
    DDGS = None  # type: ignore

try:
    from readability import Document  # type: ignore
except Exception:  # pragma: no cover
    Document = None  # type: ignore

try:
    from markdownify import markdownify as html_to_md  # type: ignore
except Exception:  # pragma: no cover
    html_to_md = None  # type: ignore

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore

try:
    import pypdf  # type: ignore
except Exception:  # pragma: no cover
    pypdf = None  # type: ignore


# Defaults + constants
DEFAULT_MAX_TOKENS = 8192
DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) TheoBot/1.0 Chrome/120.0 Safari/537.36"
try:
    # Prefer centralized vault path for default save dir
    from utils.vault_paths import get_vault_root
    SAVE_DIR_DEFAULT = str(Path(get_vault_root()) / "docs" / "webresults")
except Exception:
    SAVE_DIR_DEFAULT = "vault/docs/webresults"
INDEX_FILENAME = "webresults_index.jsonl"

# Ephemeral in-process cache for url_retrieval results to prevent duplicate
# network calls within a short window (same process lifetime). Keyed by a
# normalized query signature.
_URL_CACHE: Dict[str, Dict[str, Any]] = {}


def _get_research_config() -> Dict[str, Any]:
    """Fetch research-related settings from config with sensible defaults."""
    try:
        config = load_config()
    except Exception:
        # In tests or minimal environments, allow defaults
        config = {
            "primary_model": "gpt-5",
        }
    research = config.get("research", {}) if isinstance(config, dict) else {}
    return {
        "save_dir": research.get("save_dir", SAVE_DIR_DEFAULT),
        "max_tokens": int(research.get("max_tokens", DEFAULT_MAX_TOKENS)),
        "user_agent": research.get("user_agent", DEFAULT_USER_AGENT),
        "cache_ttl_sec": int(research.get("cache_ttl_sec", 86400)),
        "respect_robots": bool(research.get("respect_robots", False)),
        "model_for_tokens": config.get("primary_model", "gpt-5"),
    }


def _slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "page"


def _canonicalize_url(url: str) -> str:
    # Unwrap known search engine redirectors, then strip common tracking parameters
    from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, unquote
    import base64

    def _unwrap(u: str) -> str:
        try:
            p = urlparse(u)
            host = (p.netloc or "").lower()
            qs = dict(parse_qsl(p.query))
            # DuckDuckGo redirect wrapper: https://duckduckgo.com/l/?uddg=<percent-encoded target>
            if host.endswith("duckduckgo.com") and p.path.startswith("/l/"):
                tgt = qs.get("uddg")
                if tgt:
                    t = unquote(tgt)
                    if t.startswith("http://") or t.startswith("https://"):
                        return t
            # Bing click wrapper: https://www.bing.com/ck/a?u=<encoded target>
            if host.endswith("bing.com") and p.path.startswith("/ck/"):
                tgt = qs.get("u") or qs.get("r")
                if tgt:
                    # Often percent-encoded URL; occasionally base64
                    try:
                        t = unquote(tgt)
                        if t.startswith("http://") or t.startswith("https://"):
                            return t
                    except Exception:
                        pass
                    try:
                        t = base64.urlsafe_b64decode(tgt + "==").decode("utf-8", errors="ignore")
                        if t.startswith("http://") or t.startswith("https://"):
                            return t
                    except Exception:
                        pass
        except Exception:
            pass
        return u

    # Attempt unwrap twice in case of nested wrappers
    current = str(url)
    for _ in range(2):
        nxt = _unwrap(current)
        if nxt == current:
            break
        current = nxt

    parsed = urlparse(current)
    query_pairs = [(k, v) for k, v in parse_qsl(parsed.query) if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid"}]
    new_query = urlencode(query_pairs)
    canonical = parsed._replace(query=new_query, fragment="")
    return urlunparse(canonical)


def _prepare_query(query: str, site: Optional[Union[str, List[str]]]) -> str:
    """Normalize query and merge site filters without duplication.

    - If `query` already contains site: filters, do not repeat them for the same host(s).
    - If `site` is a list, combine them as (site:a OR site:b) <query>.

    Args:
        query: Raw user query
        site: Optional site or list of sites to restrict results

    Returns:
        A cleaned query string with non-duplicative site filters applied.
    """
    q = (query or "").strip()
    if not site:
        return q

    # Collect existing site filters from the query
    existing_sites: set[str] = set()
    try:
        for m in re.finditer(r"\bsite:([^\s)]+)", q, flags=re.IGNORECASE):
            host = m.group(1).strip().lower()
            if host:
                existing_sites.add(host)
    except Exception:
        pass

    # Normalize the provided site(s)
    provided: List[str] = []
    if isinstance(site, str):
        provided = [site]
    elif isinstance(site, list):
        provided = [str(s) for s in site]

    provided = [s.strip().lower() for s in provided if s and str(s).strip()]

    # Filter out ones that already exist in the query
    to_add = [s for s in provided if s not in existing_sites]
    if not to_add:
        return q

    if len(to_add) == 1:
        filter_str = f"site:{to_add[0]}"
    else:
        filter_str = "(" + " OR ".join([f"site:{s}" for s in to_add]) + ")"

    return f"{filter_str} {q}".strip()


def _norm_query_signature(query: str, site: Optional[Union[str, List[str]]], time_range: Optional[str], max_results: int, include_snippets: bool) -> str:
    s: Union[str, List[str], None] = site
    if isinstance(s, list):
        s = ",".join(sorted([str(x).strip().lower() for x in s if str(x).strip()]))
    elif isinstance(s, str):
        s = s.strip().lower()
    tr = (time_range or "").strip().lower()
    return json.dumps({
        "q": (query or "").strip().lower(),
        "site": s,
        "tr": tr,
        "n": int(max_results or 10),
        "snip": bool(include_snippets),
    }, sort_keys=True)


def _hash_content(data: Union[str, bytes]) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8", errors="ignore")
    return hashlib.sha256(data).hexdigest()


def _build_session(user_agent: str) -> requests.Session:
    session = requests.Session()
    retries = Retry(total=2, backoff_factor=0.4, status_forcelist=(429, 500, 502, 503, 504))
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": user_agent, "Accept": "*/*"})
    return session


def _is_search_engine_host(host: str) -> bool:
    """Return True if host is a known search/redirect domain (exclude from results)."""
    h = (host or "").lower()
    return (
        h.endswith("bing.com")
        or h.endswith("duckduckgo.com")
        or h.endswith("google.com")
        or h.endswith("go.microsoft.com")
        or h.endswith("r.msn.com")
        or h.endswith("search.brave.com")
        or h.endswith("brave.com")
    )


def _search_brave_html(q: str, max_results: int, user_agent: str) -> List[Dict[str, Any]]:
    """HTML scraper for Brave Search results.

    Args:
        q: Fully prepared query string (may include site: filters)
        max_results: Maximum number of items to return
        user_agent: UA string for HTTP requests

    Returns:
        List of dicts with keys: title, href, body, date (optional)
    """
    results: List[Dict[str, Any]] = []
    try:
        if BeautifulSoup is None:
            return results
        session = _build_session(user_agent)
        resp = session.get(
            "https://search.brave.com/search",
            params={"q": q},
            timeout=10,
        )
        if resp.status_code >= 400:
            return results
        soup = BeautifulSoup(resp.text, "html.parser")
        container = soup.find(id="results") or soup
        # Iterate plausible card elements
        cards = container.select("article, section, div")
        for card in cards:
            a = card.select_one('a[href^="http"]')
            if not a:
                continue
            href = _canonicalize_url(a.get("href") or "")
            domain = _extract_domain(href)
            # Skip internal/search links
            if _is_search_engine_host(domain):
                continue
            title = (a.get_text(" ", strip=True) or "").strip() or domain or "(no title)"
            # prefer <p> inside card as snippet
            snip = ""
            p = card.select_one("p")
            if p and p.get_text():
                snip = p.get_text(" ", strip=True)
            if not snip:
                # fallback: card text without link title
                txt = card.get_text(" ", strip=True)
                if title and txt:
                    snip = txt.replace(title, "").strip()
            results.append({
                "title": title,
                "href": href,
                "body": snip,
                "date": "",
            })
            if len(results) >= max(1, int(max_results or 10)):
                break
        return results
    except Exception:
        return []


def _search_bing_html(q: str, max_results: int, user_agent: str) -> List[Dict[str, Any]]:
    """Fallback HTML scraper for Bing search results.

    This is used only when duckduckgo_search is unavailable or yields no results.
    It fetches the Bing SERP and extracts titles, URLs, and snippets.

    Args:
        q: Fully prepared query string (may include site: filters)
        max_results: Maximum number of items to return
        user_agent: UA string for HTTP requests

    Returns:
        List of dicts with keys: title, href, body, date (optional)
    """
    results: List[Dict[str, Any]] = []
    try:
        if BeautifulSoup is None:
            return results
        session = _build_session(user_agent)
        resp = session.get(
            "https://www.bing.com/search",
            params={"q": q},
            timeout=10,
        )
        if resp.status_code >= 400:
            return results
        soup = BeautifulSoup(resp.text, "html.parser")
        # Typical result container (exclude ads)
        items = [li for li in soup.select("li.b_algo") if "b_ad" not in (li.get("class") or [])]
        for it in items:
            a = it.select_one("h2 a")
            if not a:
                continue
            href = a.get("href") or ""
            href = _canonicalize_url(href)
            # Skip if it still points to a search engine/redirect host
            if _is_search_engine_host(_extract_domain(href)):
                continue
            title = a.get_text(strip=True) or "(no title)"
            # Snippet
            snip = ""
            cap = it.select_one("div.b_caption p")
            if cap and cap.get_text():
                snip = cap.get_text(" ", strip=True)
            elif it.select_one("p"):
                snip = it.select_one("p").get_text(" ", strip=True)  # type: ignore
            results.append({
                "title": title,
                "href": href,
                "body": snip,
                "date": "",
            })
            if len(results) >= max(1, int(max_results or 10)):
                break
        return results
    except Exception:
        return []


def _is_pdf_response(resp: requests.Response) -> bool:
    ctype = (resp.headers.get("Content-Type") or "").lower()
    return "application/pdf" in ctype or resp.url.lower().endswith(".pdf")


def _extract_domain(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).netloc


def _compute_filename(url: str, title: str) -> str:
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    domain = _extract_domain(url)
    slug = _slugify(title or domain)
    return f"{ts}_{_slugify(domain)}_{slug}.md"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _save_webresult(save_dir: Path, meta: Dict[str, Any], content_md: str) -> Path:
    _ensure_dir(save_dir)
    filename = _compute_filename(meta.get("url", ""), meta.get("title", ""))
    file_path = save_dir / filename

    # YAML frontmatter
    frontmatter = {
        "url": meta.get("url"),
        "title": meta.get("title"),
        "domain": meta.get("domain"),
        "fetched_at": meta.get("fetched_at"),
        "status_code": meta.get("status_code"),
        "content_type": meta.get("content_type"),
        "token_count": meta.get("token_count"),
        "truncated": meta.get("truncated", False),
        "content_hash": _hash_content(content_md),
    }
    fm_lines = ["---"] + [f"{k}: {json.dumps(v)}" for k, v in frontmatter.items()] + ["---", ""]
    file_path.write_text("\n".join(fm_lines) + content_md, encoding="utf-8")

    # Append index line
    index_path = save_dir / INDEX_FILENAME
    with index_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(frontmatter) + "\n")

    return file_path


def url_retrieval(
    query: str,
    max_results: int = 10,
    site: Optional[Union[str, List[str]]] = None,
    time_range: Optional[str] = None,
    include_snippets: bool = True,
) -> str:
    """
    Retrieve relevant URLs using DuckDuckGo (free).

    - Returns a formatted list of results with title, URL, and optional snippet.
    - `site` may be a string or list of domains to prioritize via query filter.
    - `time_range` uses DDG's available timelimit options when supported.
    """
    if not query or not query.strip():
        return "Error: Search query cannot be empty"

    # Prepare query with non-duplicative site filters
    q = _prepare_query(query, site)

    try:
        # Cache check
        sig = _norm_query_signature(query, site, time_range, max_results, include_snippets)
        try:
            cfg = _get_research_config()
            ttl = int(cfg.get("cache_ttl_sec", 0) or 0)
        except Exception:
            ttl = 0
        now = time.time()
        if ttl > 0 and sig in _URL_CACHE:
            entry = _URL_CACHE.get(sig) or {}
            if (now - float(entry.get("ts", 0))) <= ttl:
                cached = entry.get("text")
                if isinstance(cached, str) and cached:
                    return cached

        results: List[Dict[str, Any]] = []
        # Quiet noisy third‑party logs during search
        try:
            import logging as _logging
            for name in ("duckduckgo_search", "httpx", "urllib3"):
                _logging.getLogger(name).setLevel(_logging.WARNING)
        except Exception:
            pass
        # Prefer modern DDGS API (new package) if available
        if 'DDGS_NATIVE' in globals() and DDGS_NATIVE is not None:
            try:
                with DDGS_NATIVE() as ddgs:
                    # Map time_range roughly to timelimit
                    timelimit = None
                    if isinstance(time_range, str):
                        tr = time_range.strip().lower()
                        mapping = {"day": "d", "past_day": "d", "1d": "d", "d": "d", "week": "w", "7d": "w", "w": "w", "month": "m", "30d": "m", "m": "m", "year": "y", "365d": "y", "y": "y"}
                        timelimit = mapping.get(tr, tr)
                    for r in ddgs.text(q, region="us-en", safesearch="moderate", timelimit=timelimit, max_results=max_results):
                        results.append({
                            "title": r.get("title"),
                            "href": r.get("href") or r.get("link") or r.get("url"),
                            "body": r.get("body") or r.get("snippet"),
                            "date": r.get("date") or r.get("published")
                        })
            except Exception as e:
                logger.debug(f"DDGS(native) search failed, will try legacy: {e}")
        # Else try legacy DDGS from duckduckgo_search
        if not results and DDGS is not None:
            try:
                with DDGS() as ddgs:
                    # Map time_range roughly to timelimit
                    timelimit = None
                    if isinstance(time_range, str):
                        # Normalize common inputs to DDG's timelimit keys
                        tr = time_range.strip().lower()
                        mapping = {
                            "day": "d", "past_day": "d", "1d": "d", "d": "d",
                            "week": "w", "7d": "w", "w": "w",
                            "month": "m", "30d": "m", "m": "m",
                            "year": "y", "365d": "y", "y": "y",
                        }
                        timelimit = mapping.get(tr, tr)
                    for r in ddgs.text(q, region="us-en", safesearch="moderate", timelimit=timelimit, backend="auto", max_results=max_results):
                        results.append({
                            "title": r.get("title"),
                            "href": r.get("href") or r.get("link") or r.get("url"),
                            "body": r.get("body") or r.get("snippet"),
                            "date": r.get("date") or r.get("published")
                        })
            except Exception as e:
                logger.debug(f"DDGS search failed, will try legacy ddg: {e}")
        if not results and ddg is not None:
            # Legacy function path
            results = ddg(q, max_results=max_results) or []
        # Last resort: lightweight HTML fallback (Bing SERP)
        if not results:
            try:
                cfg = _get_research_config()
                # Prefer Brave HTML (more static) then Bing as last resort
                results = _search_brave_html(q, max_results, cfg.get("user_agent", DEFAULT_USER_AGENT))
                if not results:
                    results = _search_bing_html(q, max_results, cfg.get("user_agent", DEFAULT_USER_AGENT))
            except Exception:
                results = []
        if not results:
            return f"No results found for: '{query}'"

        def _clean(results_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            """Canonicalize, filter, and dedupe a raw results list."""
            seen_local: set[str] = set()
            cleaned_local: List[Dict[str, Any]] = []
            for r in results_list:
                href_raw = r.get("href") or r.get("url") or r.get("link") or ""
                if not isinstance(href_raw, str) or not href_raw:
                    continue
                href = _canonicalize_url(href_raw)
                # Skip obvious search-engine or ad redirect hosts
                if _is_search_engine_host(_extract_domain(href)):
                    continue
                if not (href.startswith("http://") or href.startswith("https://")):
                    continue
                # Normalize URL for deduplication (lowercase, strip trailing slash)
                normalized_href = href.lower().rstrip('/')
                if normalized_href in seen_local:
                    continue
                seen_local.add(normalized_href)
                cleaned_local.append({
                    "title": r.get("title") or r.get("heading") or "(no title)",
                    "href": href,
                    "body": r.get("body") or r.get("snippet") or "",
                    "date": r.get("date") or r.get("published") or "",
                })
            return cleaned_local

        # First pass cleaning from DDG/DDGS (or legacy)
        cleaned: List[Dict[str, Any]] = _clean(results)

        # If everything got filtered out (e.g., DDG only returned SERP links),
        # try lightweight HTML scraping fallback to extract first‑party links.
        if not cleaned:
            try:
                cfg = _get_research_config()
                # Prefer Brave HTML fallback (static markup), then Bing as last resort
                fallback = _search_brave_html(q, max_results, cfg.get("user_agent", DEFAULT_USER_AGENT))
                if not fallback:
                    fallback = _search_bing_html(q, max_results, cfg.get("user_agent", DEFAULT_USER_AGENT))
            except Exception:
                fallback = []
            cleaned = _clean(fallback)

        # Enforce max_results after dedupe
        cleaned = cleaned[: max(1, int(max_results or 10))]

        lines = [f"URL Retrieval Results for: '{query}'", ""]
        for i, r in enumerate(cleaned, start=1):
            title = r.get("title") or "(no title)"
            href = r.get("href") or ""
            body = r.get("body") or ""
            date = r.get("date") or ""
            lines.append(f"{i}. {title}")
            lines.append(f"   URL: {href}")
            if date:
                lines.append(f"   Date: {date}")
            if include_snippets and body:
                snippet = body.strip().replace("\n", " ")
                if len(snippet) > 300:
                    snippet = snippet[:297] + "..."
                lines.append(f"   Snippet: {snippet}")
            lines.append("")
        output = "\n".join(lines).rstrip()
        # Populate cache
        if ttl > 0:
            _URL_CACHE[sig] = {"ts": now, "text": output}
        return output
    except Exception as e:
        logger.error(f"URL retrieval failed: {e}")
        return f"URL Retrieval Error: {e}"


def _parse_html_to_markdown(html: str) -> Tuple[str, str]:
    """Return tuple of (title, markdown) from raw HTML using Readability then markdownify; fallback to visible text."""
    title = ""
    if Document is not None:
        try:
            doc = Document(html)
            title = doc.short_title() or ""
            main_html = doc.summary(html_partial=True)
            if html_to_md is not None:
                md = html_to_md(main_html or html, heading_style="ATX", code_language="")
                return title, md
        except Exception as e:  # pragma: no cover - exercised in integration not unit focus
            logger.debug(f"Readability parse failed, falling back: {e}")

    # Fallback: BeautifulSoup visible text
    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(html, "html.parser")
            # Try to keep pre/code blocks
            texts: List[str] = []
            for pre in soup.find_all(["pre", "code"]):
                content = pre.get_text("\n", strip=False)
                if content:
                    texts.append("```\n" + content + "\n```")
            # Also collect headings and paragraphs
            for tag in soup.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
                t = tag.get_text(" ", strip=True)
                if t:
                    if tag.name.startswith("h"):
                        level = int(tag.name[1])
                        texts.append("#" * max(1, min(6, level)) + f" {t}")
                    elif tag.name == "li":
                        texts.append(f"- {t}")
                    else:
                        texts.append(t)
            md = "\n\n".join(texts) if texts else soup.get_text("\n", strip=True)
            # Title fallback
            if not title:
                if soup.title and soup.title.string:
                    title = soup.title.string.strip()
            return title, md
        except Exception as e:  # pragma: no cover
            logger.debug(f"Soup parse failed: {e}")

    # Last resort: raw text
    return title, html


def _parse_pdf_to_markdown(content: bytes) -> Tuple[str, str]:
    if pypdf is None:
        return "", "PDF parsing unavailable (pypdf not installed)"
    try:
        reader = pypdf.PdfReader(io.BytesIO(content))  # type: ignore
        parts: List[str] = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        text = "\n\n".join(parts)
        # Light markdownization
        return "", text
    except Exception as e:  # pragma: no cover
        logger.error(f"PDF parsing failed: {e}")
        return "", "(Failed to parse PDF)"


def _page_parser_single(
    url: str,
    cfg: Dict[str, Any],
    save: bool = False,
    max_tokens: Optional[int] = None,
    format: str = "markdown",
    use_cache: bool = True,
    force: bool = False,
) -> str:
    """Internal: fetch and parse a single URL to Markdown and optional vault save."""
    if not url or not url.strip():
        return "Error: URL cannot be empty"

    save_dir = Path(cfg["save_dir"]) if cfg.get("save_dir") else Path(SAVE_DIR_DEFAULT)
    user_agent = cfg["user_agent"]
    token_model = cfg["model_for_tokens"]
    max_tok = int(max_tokens or cfg["max_tokens"] or DEFAULT_MAX_TOKENS)

    canon_url = _canonicalize_url(url.strip())
    domain = _extract_domain(canon_url)

    # Basic GET with retries
    session = _build_session(user_agent)
    try:
        resp = session.get(canon_url, timeout=15, allow_redirects=True)
    except Exception as e:
        logger.error(f"Fetch failed for {canon_url}: {e}")
        return f"Page Parser Error: Failed to fetch URL: {e}"

    status = resp.status_code
    content_type = resp.headers.get("Content-Type", "")
    fetched_at = datetime.utcnow().isoformat() + "Z"

    if status >= 400:
        return f"Page Parser Error: HTTP {status} for URL: {canon_url}"

    title = ""
    body_md = ""
    truncated = False

    if _is_pdf_response(resp):
        title, body_md = _parse_pdf_to_markdown(resp.content)
    else:
        title, body_md = _parse_html_to_markdown(resp.text)

    title = title or domain or ""

    # Token counting and truncation
    full_tokens = count_tokens(body_md, token_model)
    output_md = body_md
    if full_tokens > max_tok:
        output_md = truncate_text_to_tokens(body_md, max_tok, token_model)
        truncated = True

    meta = {
        "url": canon_url,
        "title": title,
        "domain": domain,
        "fetched_at": fetched_at,
        "status_code": status,
        "content_type": content_type,
        "token_count": min(full_tokens, max_tok),
        "truncated": truncated,
    }

    saved_path: Optional[Path] = None
    if save or truncated:
        try:
            saved_path = _save_webresult(Path(save_dir), meta, body_md)
            meta["saved_path"] = str(saved_path)
            # Opportunistic immediate indexing for searchability
            try:
                # Semantic embedding (single-chunk) if embeddings system is available
                from layer3_longterm.embeddings import embed as _embed
                project_root = Path(__file__).parent.parent
                rel_path = str(saved_path.relative_to(project_root)) if saved_path.is_absolute() else str(saved_path)
                md = {
                    "type": "file_chunk",
                    "file_path": rel_path,
                    "timestamp": time.time(),
                    "content_length": len(body_md),
                    "extension": ".md",
                    "start_line": 1,
                    "end_line": len(body_md.splitlines()) or 1,
                    "chunk_id": f"{rel_path}:1-{len(body_md.splitlines()) or 1}",
                }
                _embed(body_md, metadata=md, file_path=rel_path)
                try:
                    from layer3_longterm.embeddings import get_embedding_manager
                    get_embedding_manager().flush()
                except Exception:
                    pass
            except Exception as e:
                logger.debug(f"Immediate semantic indexing skipped/failed: {e}")

            try:
                # Fast BM25 indexing if available
                from layer3_longterm.fast_file_indexer import fast_index_document, save_fast_index
                doc_id = md.get("chunk_id") if 'md' in locals() else str(saved_path)
                fast_index_document(doc_id, body_md, md if 'md' in locals() else {})
                try:
                    save_fast_index()
                except Exception:
                    pass
            except Exception as e:
                logger.debug(f"Immediate BM25 indexing skipped/failed: {e}")
        except Exception as e:
            logger.error(f"Failed to save webresult: {e}")

    # Compose return markdown with a small metadata header (not YAML frontmatter)
    header_lines = [
        f"# {title}",
        "",
        f"Source: {canon_url}",
        f"Domain: {domain}",
        f"Fetched: {fetched_at}",
        f"Status: {status}",
        (f"Saved: {meta.get('saved_path')}" if meta.get("saved_path") else ""),
        ("(truncated to token limit)" if truncated else ""),
        "",
        "---",
        "",
    ]
    header = "\n".join([l for l in header_lines if l != ""]) + "\n"
    return header + output_md


def page_parser(
    url: Union[str, List[str]],
    save: bool = False,
    max_tokens: Optional[int] = None,
    format: str = "markdown",
    use_cache: bool = True,
    force: bool = False,
) -> str:
    """
    Fetch and parse one or multiple web pages into clean Markdown. If content exceeds
    token limit or save=True, write to vault. Accepts either a single URL string or
    a list of URL strings.

    Returns concatenated markdown blocks (each with provenance header).
    """
    cfg = _get_research_config()
    # Single string
    if isinstance(url, str):
        return _page_parser_single(url, cfg, save=save, max_tokens=max_tokens, format=format, use_cache=use_cache, force=force)
    # List of URLs
    if isinstance(url, list):
        outputs: List[str] = []
        for u in url:
            outputs.append(_page_parser_single(str(u), cfg, save=save, max_tokens=max_tokens, format=format, use_cache=use_cache, force=force))
        return "\n\n".join(outputs)
    return "Error: URL must be a string or a list of strings"


def parse_page_title(markdown: str) -> str:
    """Extract the first Markdown H1 title line (without leading '#')."""
    try:
        for line in (markdown or "").splitlines():
            if line.startswith('# '):
                return line[2:].strip()
    except Exception:
        pass
    return ""
