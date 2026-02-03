"""Dynamic Context Optimizer utilities.

This module asks a lightweight mini-model to decide how much context Theo
should load for an upcoming turn. The model returns a compact JSON payload,
which we translate into concrete token allocations for chat history and the
various long-term memory stores.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Set

try:  # pragma: no cover - optional dependency for CI environments
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover - keep import optional when SDK missing
    OpenAI = None  # type: ignore

from utils.config_loader import get_config_value, load_config as _load_config
from utils.token_counter import truncate_text_to_tokens
from utils.logger import get_logger

logger = get_logger(__name__)

# Re-export so tests can monkeypatch utils.dynamic_optimizer.load_config
load_config = _load_config

_CONTEXT_MULTIPLIERS: Dict[str, float] = {
    "min": 0.05,
    "small": 0.10,
    "medium": 0.25,
    "large": 0.50,
    "maximum": 1.00,
}

_DEFAULT_MEMORY_FRACTIONS: Dict[str, float] = {
    "verbatim_memory": 0.40,
    "theo_memory": 0.30,
    "human_memory": 0.30,
}

_ALLOWED_REASONING: Set[str] = {"minimal", "low", "medium", "high"}


def get_last_total_allocation(config: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Compatibility helper for tooling that inspects the last allocation."""
    try:
        if isinstance(config, dict):
            alloc = (config.get("_last_optimization") or {}).get("allocated_tokens", {})
            total = alloc.get("total")
            if isinstance(total, int) and total > 0:
                return total
    except Exception:
        pass
    return None


def _determine_provider(model: Optional[str]) -> str:
    """Infer the provider from a model name without importing heavy modules."""
    if not model:
        return "openai"

    model_lower = str(model).lower()

    groq_keywords = [
        "openai/gpt-oss",
        "gpt-oss-",
        "meta-llama/",
        "whisper-large-v3",
        "whisper-large-v3-turbo",
        "deepseek",
        "qwen/",
        "moonshotai/",
        "playai-tts",
        "compound-beta",
    ]
    if (
        model_lower.startswith("groq:")
        or model_lower.startswith("openai/")
        or model_lower.startswith("gpt-oss-")
        or any(keyword in model_lower for keyword in groq_keywords)
    ):
        return "groq"

    if (
        model_lower.startswith("gpt-")
        or model_lower.startswith("o3")
        or model_lower.startswith("o4")
    ):
        return "openai"

    if "claude" in model_lower or model_lower.startswith("anthropic"):
        return "anthropic"

    if model_lower.startswith("gemini-") or model_lower.startswith("google"):
        return "google"

    if model_lower.startswith("grok-") or model_lower.startswith("xai:") or model_lower.startswith("xai/"):
        return "xai"

    local_prefixes = [
        "gemma3:",
        "gemma3-tools",
        "petros/",
        "petrosstav/",
        "llama:",
        "mistral:",
        "qwen:",
        "local-",
        "mistral-small:",
        "deepseek:",
    ]
    if any(model_lower.startswith(prefix) for prefix in local_prefixes) or model_lower.startswith("llama-"):
        return "local"

    return "openai"


def _normalize_optimizer_response(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize mini-model output for downstream calculations."""
    if not isinstance(raw, dict):
        raise ValueError("Optimizer response must be a dictionary")

    normalized = dict(raw)

    valid_contexts = {"min", "small", "medium", "large", "maximum"}
    synonyms = {
        "max": "maximum",
        "full": "maximum",
        "all": "maximum",
        "med": "medium",
        "minimum": "min",
        "sm": "small",
        "lg": "large",
    }
    raw_context = str(normalized.get("total_context", "") or "").strip().lower()
    context_value = synonyms.get(raw_context, raw_context)
    if context_value not in valid_contexts:
        logger.warning(
            "L6.optimizer [validation] - Invalid total_context '%s'; defaulting to medium",
            raw_context,
        )
        context_value = "medium"

    history_pct_value: Optional[float] = None
    if "history_percentage" in normalized:
        history_pct_value = float(normalized.get("history_percentage") or 0)
    elif "history_importance" in normalized or "memory_importance" in normalized:
        history_imp = float(normalized.get("history_importance") or 0)
        memory_imp = float(normalized.get("memory_importance") or max(0.0, 100.0 - history_imp))
        total_imp = history_imp + memory_imp
        if total_imp <= 0:
            raise ValueError("Optimizer response importances must sum to a positive value")
        history_pct_value = (history_imp / total_imp) * 100.0
    else:
        raise ValueError("Missing required field: history_percentage")

    history_pct_value = max(0.0, min(100.0, history_pct_value))
    history_percentage = int(round(history_pct_value))
    memory_percentage = 100 - history_percentage

    history_importance = normalized.get("history_importance")
    memory_importance = normalized.get("memory_importance")
    if history_importance is None and memory_importance is None:
        history_importance = history_percentage
        memory_importance = memory_percentage
    else:
        history_importance = int(max(0, min(100, int(history_importance or 0))))
        memory_importance = int(
            max(0, min(100, int(memory_importance or (100 - history_importance))))
        )

    reasoning_effort = str(normalized.get("reasoning_effort", "low") or "low").lower()
    if reasoning_effort not in _ALLOWED_REASONING:
        reasoning_effort = "low"

    normalized.update({
        "total_context": context_value,
        "history_percentage": history_percentage,
        "history_importance": history_importance,
        "memory_importance": memory_importance,
        "reasoning_effort": reasoning_effort,
    })

    return normalized


def _extract_response_text(response: Any) -> str:
    """Extract the first text chunk from a Responses API object."""
    if response is None:
        raise ValueError("Mini-model response is empty")

    output = getattr(response, "output", None)
    if output:
        try:
            first_output = output[0]
            contents = getattr(first_output, "content", None)
            if contents:
                first_piece = contents[0]
                text_value = getattr(first_piece, "text", None)
                if isinstance(text_value, str) and text_value.strip():
                    return text_value
        except Exception:
            pass

    for attr in ("output_text", "text"):
        text_value = getattr(response, attr, None)
        if isinstance(text_value, str) and text_value.strip():
            return text_value

    raise ValueError("Mini-model response did not include text content")


def optimize_context(
    query: str,
    config: Optional[Dict[str, Any]] = None,
    optimizer_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the dynamic optimizer and return the computed allocation."""
    logger.info("L6.optimizer [analysis] - Starting context optimization")

    config = config or load_config()

    if not get_config_value(config, "dynamic_context_optimizer.enabled", True):
        logger.info("L6.optimizer [config] - Dynamic optimizer disabled, using fallback")
        return _get_fallback_allocation(config)

    optimizer_model = get_config_value(config, "optimizer_model", "gpt-5-nano")
    provider = _determine_provider(optimizer_model)

    key_map = {
        "openai": "api_keys.openai",
        "google": "api_keys.google",
        "groq": "api_keys.groq",
        "xai": "api_keys.xai",
        "anthropic": "api_keys.anthropic",
        "local": None,
    }
    key_path = key_map.get(provider, "api_keys.openai")
    api_key = get_config_value(config, key_path) if key_path else "local"
    if provider != "local" and not api_key:
        logger.error(f"L6.optimizer [config] - API key not found for provider: {provider}")
        return _get_fallback_allocation(config)

    max_budget = int(get_config_value(config, "max_token_budget", 10000) or 10000)
    try:
        budget_pct = float(
            get_config_value(config, "dynamic_context_optimizer.mini_model_budget_percent", 10)
        )
    except Exception:
        budget_pct = 10.0
    if budget_pct <= 0:
        budget_pct = 10.0
    mini_model_budget = max(16, int(max_budget * (budget_pct / 100.0)))
    logger.debug(
        "L6.optimizer [budget] - Mini-model budget: %s tokens (%.1f%% of %s)",
        mini_model_budget,
        budget_pct,
        max_budget,
    )

    user_input = None
    if isinstance(optimizer_prompt, str) and optimizer_prompt.strip():
        try:
            primary_model = get_config_value(config, "primary_model", "gpt-5")
            user_input = truncate_text_to_tokens(optimizer_prompt, mini_model_budget, primary_model)
        except Exception:
            user_input = optimizer_prompt

    base_query = query if query is not None else ""
    call_kwargs: Dict[str, Any] = {}
    if user_input is not None:
        call_kwargs["user_input"] = user_input

    try:
        raw_result = _call_mini_model(
            base_query,
            api_key,
            mini_model_budget,
            optimizer_model,
            **call_kwargs,
        )
    except Exception as exc:
        logger.error(f"L6.optimizer [api] - Mini-model call failed: {exc}")
        return _get_fallback_allocation(config)

    logger.info(f"L6.optimizer [api] - Mini-model response: {raw_result}")

    try:
        normalized_result = _normalize_optimizer_response(raw_result)
    except Exception as exc:
        logger.error(f"L6.optimizer [validation] - Invalid optimizer response: {exc}")
        return _get_fallback_allocation(config)

    allocation_result = _calculate_token_allocation(normalized_result, config)
    logger.info(f"L6.optimizer [calc] - Allocation result: {allocation_result}")

    context_size = allocation_result.get("total_context", "medium")
    alloc_total = max(1, allocation_result["allocated_tokens"]["total"])
    hist_tokens = allocation_result["allocated_tokens"]["history"]
    mem_tokens = allocation_result["allocated_tokens"]["memory"]
    history_rel = (hist_tokens / alloc_total) * 100
    memory_rel = (mem_tokens / alloc_total) * 100

    logger.info(
        "L6.optimizer [allocation] - Optimized: %s context → history %.0f%% / memory %.0f%% (reasoning=%s)",
        context_size,
        history_rel,
        memory_rel,
        allocation_result.get("reasoning_effort", "low"),
    )

    return allocation_result


def _call_mini_model(
    query: Optional[str],
    api_key: str,
    budget: int,
    model: str = "gpt-5-nano",
    user_input: Optional[str] = None,
) -> Dict[str, Any]:
    """Invoke the optimizer mini-model with multi-provider support."""
    provider = _determine_provider(model)

    system_prompt = """
You are Theo's dynamic context optimizer (not Theo). Your job is to choose the optimal context size, the history allocation, and the reasoning effort for the next turn.

You always receive the real tool inventory in the Available Tools section (for example: add_working_memory, update_working_memory, remove_working_memory, snapshot_working_memory, bash, create_diff, web_search, url_retrieval, page_parser, code_agent, generate_image, add_memory, manually_send_message). Treat that list as the ground truth for what Theo can call, even if you only see a concise summary.

Return ONE JSON object with EXACTLY these fields (no prose):
  - total_context: one of min | small | medium | large | maximum
  - history_percentage: integer 0-100
  - reasoning_effort: one of minimal | low | medium | high

Inputs: You may see sections like "Current Message", optional "Recent Chat History", optional "Memory Bank", and an "Available Tools" section. Use them to assess how much context is useful and whether tools are needed.

Decision policy (select the smallest that comfortably fits, but stay proportional to signals):
- total_context (fraction of global budget):
  - min (~5%): greetings, acknowledgements, very short or trivial requests.
  - small (~10%): single-turn tasks with little dependency on prior context.
  - medium (~25%): typical requests; some short history helps; light tool use is fine.
  - large (~50%): clearly multi-step or analysis-heavy work, or when history/memory content is needed substantially.
  - maximum (~100%): long-running plans, heavy tool usage, or high uncertainty requiring full context.

- history_percentage (0-100) — portion of the chosen allocation for recent chat:
  - 80-95 when the request depends strongly on the immediate conversation (e.g., "continue...", "as we discussed...", step-by-step workflows, coding sessions).
  - 50-70 for balanced/typical tasks where history helps but is not dominant.
  - 10-30 when the request is primarily about cross-chat knowledge or personal facts (favor memory over history).

- reasoning_effort (4-tier) — Choose based on current message complexity and thinking depth needed. Below a description of each level:
  - minimal: basic understanding; suitable for simple tasks, light conversation, or straightforward instructions.
  - low: much better reasoning than minimal; good for tasks, normal conversation, and moderately complex requests.
  - medium: another step up in reasoning; great for complex tasks, multi-step workflows, nuanced understanding.
  - high: a slight step up from medium, but is costly. good for highly complex, ambiguous, or critical tasks.

Return only the JSON object with the three fields above—no explanations.
    """

    user_prompt = (
        user_input if user_input is not None else f"Analyze this query for optimal context allocation: {query}"
    )

    try:
        qlen = len(user_prompt)
    except Exception:
        qlen = 0
    logger.debug(
        "L6.optimizer [mini_model:%s] - Analyzing context requirements (query length: %s chars)",
        model,
        qlen,
    )

    schema = {
        "type": "object",
        "properties": {
            "total_context": {"type": "string", "enum": ["min", "small", "medium", "large", "maximum"]},
            "history_percentage": {"type": "integer", "minimum": 0, "maximum": 100},
            "reasoning_effort": {"type": "string", "enum": ["minimal", "low", "medium", "high"]},
        },
        "required": ["total_context", "history_percentage", "reasoning_effort"],
        "additionalProperties": False,
    }

    # Provider-specific API calls with structured outputs
    result_dict = None

    if provider == "openai":
        if OpenAI is None:
            raise RuntimeError("OpenAI SDK is not installed; cannot call optimizer mini-model")
        
        client = OpenAI(api_key=api_key)
        max_output_tokens = max(64, int(max(1, budget) * 0.5))

        try:
            response = client.responses.create(  # type: ignore[call-arg]
                model=model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "dynamic_context_optimizer_output",
                        "schema": schema,
                    },
                },
                max_output_tokens=max_output_tokens,
                reasoning={"effort": "minimal"} if str(model).lower().startswith("gpt-5") else None,
            )
            text = _extract_response_text(response)
            result_dict = json.loads(text)
        except Exception as exc:
            logger.error(f"L6.optimizer [mini_model.openai] - API call failed: {exc}")
            raise

    elif provider == "groq":
        try:
            from groq import Groq
        except ImportError:
            raise RuntimeError("Groq SDK is not installed; cannot call optimizer mini-model")

        try:
            client = Groq(api_key=api_key)
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "context_optimization",
                    "schema": schema,
                },
            }
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                response_format=response_format,
                reasoning_effort="low",  # Optimizer always uses low effort for Groq
            )
            choice = (resp.choices or [None])[0]
            msg = getattr(choice, "message", None) or {}
            txt = getattr(msg, "content", None)
            if isinstance(txt, str) and txt.strip():
                result_dict = json.loads(txt.strip())
            else:
                raise ValueError("Groq response did not include content")
        except Exception as exc:
            logger.error(f"L6.optimizer [mini_model.groq] - API call failed: {exc}")
            raise

    elif provider == "google":
        try:
            from google import genai
            from google.genai import types as gtypes
        except ImportError:
            raise RuntimeError("Google Generative AI SDK is not installed; cannot call optimizer mini-model")

        try:
            client = genai.Client(api_key=api_key)
            # Enforce BLOCK_NONE safety across all categories for optimizer calls
            try:
                cats = [
                    gtypes.HarmCategory.HARASSMENT,
                    gtypes.HarmCategory.HATE_SPEECH,
                    gtypes.HarmCategory.SEXUALLY_EXPLICIT,
                    gtypes.HarmCategory.DANGEROUS_CONTENT,
                    gtypes.HarmCategory.CIVIC_INTEGRITY,
                ]
                safety = [
                    gtypes.SafetySetting(category=c, threshold=gtypes.HarmBlockThreshold.BLOCK_NONE)
                    for c in cats
                ]
            except Exception:
                safety = None
            
            cfg = gtypes.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                safety_settings=safety,
            )
            resp = client.models.generate_content(
                model=model,
                contents=f"{system_prompt}\n\n{user_prompt}",
                config=cfg,
            )
            # Prefer parsed
            parsed_val = getattr(resp, "parsed", None)
            if isinstance(parsed_val, dict):
                result_dict = parsed_val
            else:
                txt = getattr(resp, "text", None)
                if isinstance(txt, str) and txt.strip():
                    result_dict = json.loads(txt.strip())
                else:
                    raise ValueError("Google response did not include text or parsed content")
        except Exception as exc:
            logger.error(f"L6.optimizer [mini_model.google] - API call failed: {exc}")
            raise

    elif provider == "xai":
        try:
            from xai_sdk import Client as XClient
            from xai_sdk.chat import system as xsystem, user as xuser
        except ImportError:
            raise RuntimeError("xAI SDK is not installed; cannot call optimizer mini-model")

        try:
            # Use JSON mode fallback for xAI (structured outputs may not be available)
            xclient = XClient(api_key=api_key)
            chat = xclient.chat.create(model=model)
            chat.append(xsystem(system_prompt))
            chat.append(xuser(user_prompt))
            resp = chat.run()
            # Extract text from response
            if hasattr(resp, "content") and isinstance(resp.content, str):
                txt = resp.content
            elif hasattr(resp, "text") and isinstance(resp.text, str):
                txt = resp.text
            else:
                txt = str(resp)
            
            if txt.strip():
                result_dict = json.loads(txt.strip())
            else:
                raise ValueError("xAI response did not include text content")
        except Exception as exc:
            logger.error(f"L6.optimizer [mini_model.xai] - API call failed: {exc}")
            raise

    elif provider == "anthropic":
        # Anthropic does not support strict structured outputs; use fallback JSON parsing
        logger.warning("L6.optimizer [mini_model.anthropic] - Anthropic does not support structured outputs; using JSON mode")
        try:
            from anthropic import Anthropic
        except ImportError:
            raise RuntimeError("Anthropic SDK is not installed; cannot call optimizer mini-model")

        try:
            client = Anthropic(api_key=api_key)
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                messages=[
                    {"role": "user", "content": f"{system_prompt}\n\n{user_prompt}"}
                ],
            )
            # Extract text from response
            if hasattr(response, "content") and len(response.content) > 0:
                txt = response.content[0].text
            else:
                raise ValueError("Anthropic response did not include content")
            
            if txt.strip():
                result_dict = json.loads(txt.strip())
            else:
                raise ValueError("Anthropic response text was empty")
        except Exception as exc:
            logger.error(f"L6.optimizer [mini_model.anthropic] - API call failed: {exc}")
            raise

    else:
        raise RuntimeError(f"Provider {provider} not supported for optimizer mini-model")

    if result_dict is None:
        raise RuntimeError(f"Failed to get response from {provider} mini-model")

    return _normalize_optimizer_response(result_dict)


def _calculate_token_allocation(
    optimization_result: Dict[str, Any], config: Dict[str, Any]
) -> Dict[str, Any]:
    """Convert optimizer output into concrete token allocations."""
    normalized = _normalize_optimizer_response(optimization_result)

    max_budget = int(get_config_value(config, "max_token_budget", 10000) or 10000)
    context_size = normalized.get("total_context", "medium")
    total_tokens = int(max_budget * _CONTEXT_MULTIPLIERS.get(context_size, 0.25))

    history_pct = float(normalized.get("history_percentage", 60))
    ceilings_cfg = get_config_value(config, "dynamic_context_optimizer.allocation_ceilings", {}) or {}

    try:
        max_history_percent = float(ceilings_cfg.get("max_history_percent", 100))
    except Exception:
        max_history_percent = 100.0
    try:
        max_memory_percent = float(ceilings_cfg.get("max_memory_percent", 100))
    except Exception:
        max_memory_percent = 100.0

    max_history_percent = max(0.0, min(100.0, max_history_percent))
    max_memory_percent = max(0.0, min(100.0, max_memory_percent))

    min_history_percent = max(0.0, 100.0 - max_memory_percent)
    if max_history_percent < min_history_percent:
        max_history_percent = min_history_percent

    history_pct = max(min_history_percent, min(max_history_percent, history_pct))

    history_ratio = history_pct / 100.0
    memory_ratio = 1.0 - history_ratio

    history_tokens = int(total_tokens * history_ratio)
    memory_tokens = max(0, total_tokens - history_tokens)

    fractions_cfg = get_config_value(config, "dynamic_context_optimizer.memory_fractions", {}) or {}
    verbatim_frac = float(
        fractions_cfg.get("verbatim_memory", _DEFAULT_MEMORY_FRACTIONS["verbatim_memory"] * 100)
    ) / 100
    theo_frac = float(
        fractions_cfg.get("theo_memory", _DEFAULT_MEMORY_FRACTIONS["theo_memory"] * 100)
    ) / 100
    human_frac = float(
        fractions_cfg.get("human_memory", _DEFAULT_MEMORY_FRACTIONS["human_memory"] * 100)
    ) / 100

    fraction_total = verbatim_frac + theo_frac + human_frac
    if fraction_total <= 0:
        verbatim_frac, theo_frac, human_frac = _DEFAULT_MEMORY_FRACTIONS.values()
        fraction_total = 1.0

    verbatim_frac /= fraction_total
    theo_frac /= fraction_total
    human_frac /= fraction_total

    verbatim_tokens = int(memory_tokens * verbatim_frac)
    theo_tokens = int(memory_tokens * theo_frac)
    human_tokens = memory_tokens - verbatim_tokens - theo_tokens

    normalized["history_percentage"] = int(round(history_pct))
    normalized.setdefault("history_importance", normalized["history_percentage"])
    normalized.setdefault("memory_importance", 100 - normalized["history_percentage"])

    allocation = {
        **normalized,
        "allocated_tokens": {
            "total": total_tokens,
            "history": history_tokens,
            "memory": memory_tokens,
            "memory_breakdown": {
                "verbatim_memory": verbatim_tokens,
                "theo_memory": theo_tokens,
                "human_memory": human_tokens,
            },
        },
    }

    logger.debug(
        "L6.optimizer [allocation] - Context %s => %s tokens (history=%s, memory=%s)",
        context_size,
        total_tokens,
        history_tokens,
        memory_tokens,
    )

    return allocation


def _get_fallback_allocation(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return a conservative allocation when optimization fails."""
    cfg = config or load_config()
    logger.warning("L6.optimizer [fallback] - Using fallback allocation due to optimization failure")
    seed = {
        "total_context": "medium",
        "history_percentage": 60,
        "history_importance": 60,
        "memory_importance": 40,
        "reasoning_effort": "medium",
    }
    return _calculate_token_allocation(seed, cfg)


def get_reasoning_kwargs(optimization_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build provider-agnostic reasoning kwargs from the optimizer output."""
    effort = "low"
    if isinstance(optimization_result, dict):
        candidate = str(optimization_result.get("reasoning_effort") or "").lower()
        if candidate in _ALLOWED_REASONING:
            effort = candidate

    try:
        cfg = load_config()
        max_budget = int(get_config_value(cfg, "max_token_budget", 10000) or 10000)
    except Exception:
        max_budget = 10000

    thinking_budget = -1 if max_budget >= 0 else None

    return {
        "reasoning": {"effort": effort, "summary": "auto"},
        "text": {"verbosity": "high"},
        "thinking_config": {
            "thinking_budget": thinking_budget,
            "include_thoughts": True,
        },
    }

