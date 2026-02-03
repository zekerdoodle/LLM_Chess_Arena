"""
Configuration Loader
# Test comment - Added during Mega Test of Doom system verification

This module provides configuration loading and validation functionality:
- Loads configuration from config.yaml
- Substitutes environment variables
- Merges provider API keys from .env/environment variables
- Validates required fields
- Provides default values for missing fields
- Logs configuration loading events

Uses yaml.safe_load and dotenv for secure configuration management.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

PROJECT_ROOT = Path(__file__).parent.parent

# Optional dotenv support to avoid hard test dependency
try:
    from dotenv import load_dotenv  # type: ignore

    # Eagerly load project-level .env without overriding explicit shell exports
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)
except Exception:
    # Proceed without dotenv if unavailable
    pass

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Custom exception for configuration errors."""


# Supported providers mapped to their candidate environment variable names.
# The first populated variable in the list wins.
API_KEY_ENV_MAP = {
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "google": ["GOOGLE_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "xai": ["XAI_API_KEY"],
}


def substitute_env_vars(value: str) -> str:
    """
    Substitute environment variables in a string value.

    Args:
        value: String that may contain ${ENV_VAR} placeholders

    Returns:
        String with environment variables substituted
    """
    if not isinstance(value, str):
        return value

    # Find all ${VAR} patterns and substitute them
    import re

    pattern = r"\$\{([^}]+)\}"

    def replace_var(match):
        var_name = match.group(1)
        env_value = os.getenv(var_name)
        if env_value is None:
            logger.warning(f"Environment variable {var_name} not found")
            return match.group(0)  # Keep original placeholder
        return env_value

    return re.sub(pattern, replace_var, value)


def _merge_api_keys_from_env(config: Dict[str, Any]) -> Dict[str, str]:
    """Populate config['api_keys'] using .env / environment variables."""

    raw_keys = dict(config.get("api_keys") or {})
    merged: Dict[str, str] = {}

    for provider, env_candidates in API_KEY_ENV_MAP.items():
        explicit = raw_keys.get(provider)
        if isinstance(explicit, str) and explicit and not (
            explicit.startswith("${") and explicit.endswith("}")
        ):
            merged[provider] = explicit
            continue

        # First non-empty environment variable wins
        for env_name in env_candidates:
            env_value = os.getenv(env_name, "").strip()
            if env_value:
                merged[provider] = env_value
                break

    # Preserve any additional providers explicitly defined in config
    for provider, value in raw_keys.items():
        if provider in merged:
            continue
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if not stripped:
            continue
        if stripped.startswith("${") and stripped.endswith("}"):
            continue
        merged[provider] = stripped

    config["api_keys"] = merged
    return merged


def _validate_and_fix_image_model(config: Dict[str, Any]) -> None:
    """
    Validate and auto-correct the image_analysis_model config field.
    
    Ensures the image_analysis_model is an OpenAI model. If missing or invalid,
    auto-corrects to the default (gpt-5-mini) and logs a warning.
    
    Args:
        config: Configuration dictionary to validate and potentially modify
    """
    current_model = config.get("image_analysis_model", "").strip()
    
    # Valid OpenAI model prefixes
    valid_prefixes = ("gpt-", "o1-", "o3-", "o4-")
    
    is_valid = current_model and any(current_model.startswith(prefix) for prefix in valid_prefixes)
    
    if not is_valid:
        default_model = "gpt-5-mini"
        if current_model:
            logger.warning(
                f"image_analysis_model '{current_model}' is not a valid OpenAI model. "
                f"Auto-correcting to '{default_model}'"
            )
        else:
            logger.info(
                f"image_analysis_model not configured. Using default: '{default_model}'"
            )
        config["image_analysis_model"] = default_model


def validate_config(config: Dict[str, Any]) -> None:
    """
    Validate the loaded configuration.

    Args:
        config: Configuration dictionary to validate

    Raises:
        ConfigError: If configuration is invalid
    """
    # Core required fields. `system_instructions` is optional and may be
    # provided at runtime or via defaults in other parts of the system.
    required_fields = [
        "primary_model",
        "fallback_model",
        "api_keys",
        "max_token_budget",
    ]

    for field in required_fields:
        if field not in config:
            raise ConfigError(f"Missing required configuration field: {field}")

    # Validate API keys for providers actually in use
    providers_required = set()
    try:
        from layer1_chatbot.model_selector import ModelSelector  # lazy import to avoid heavy deps at module import
        ms = ModelSelector(config)

        def _add_model(field: str) -> None:
            model_name = str(config.get(field) or "").strip()
            if not model_name:
                return
            try:
                provider = ms.get_provider_from_model(model_name)
            except Exception:
                provider = None
            if provider:
                providers_required.add(provider)

        _add_model("primary_model")
        _add_model("fallback_model")
        _add_model("image_analysis_model")
    except ImportError:
        ms = None  # type: ignore[assignment]
    except Exception as selector_err:
        logger.debug(f"Config validation - provider detection skipped: {selector_err}")
        ms = None  # type: ignore[assignment]

    # Always require OpenAI because core functionality depends on it
    providers_required.add("openai")

    missing_required: list[str] = []
    critical_providers = {"openai"}
    for provider in sorted(providers_required):
        if provider in {"local", ""}:
            continue
        key_value = (config.get("api_keys") or {}).get(provider, "")
        missing = not key_value or (
            isinstance(key_value, str)
            and key_value.startswith("${")
            and key_value.endswith("}")
        )
        if missing:
            if provider in critical_providers:
                logger.warning(
                    "API key for provider '%s' is missing but required by configured models",
                    provider,
                )
                missing_required.append(provider)
            else:
                logger.warning(
                    "API key for provider '%s' is missing; related models will be unavailable until configured",
                    provider,
                )

    if missing_required:
        raise ConfigError(
            "Missing required API key(s): " + ", ".join(sorted(missing_required))
        )

    # Validate token budget
    if config["max_token_budget"] <= 0:
        raise ConfigError("max_token_budget must be positive")

    # Optional: validate prompt allocations if provided
    allocations = config.get("prompt_allocations")
    if isinstance(allocations, dict):
        keys = ["theo_memory", "human_memory", "verbatim_memory", "chat_history"]
        if all(k in allocations for k in keys):
            total = sum(
                int(allocations[k]) for k in keys
            )
            if total != 100:
                raise ConfigError(
                    "Prompt allocations must sum to 100%"
                )


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Load and validate configuration from YAML file.

    Args:
        config_path: Path to configuration file (defaults to config.yaml)

    Returns:
        Validated configuration dictionary

    Raises:
        ConfigError: If configuration file is invalid or missing
        FileNotFoundError: If configuration file doesn't exist
    """
    if config_path is None:
        # Always use absolute path relative to project root
        config_path = PROJECT_ROOT / "config.yaml"

    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    try:
        logger.debug(f"Loading configuration from {config_path}")

        with open(config_file, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        if config is None:
            raise ConfigError("Configuration file is empty")

        # Substitute environment variables in string values
        def substitute_recursive(obj):
            if isinstance(obj, dict):
                return {k: substitute_recursive(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [substitute_recursive(item) for item in obj]
            elif isinstance(obj, str):
                return substitute_env_vars(obj)
            else:
                return obj

        config = substitute_recursive(config)

        # Populate API keys from environment before validation
        _merge_api_keys_from_env(config)

        # Validate and fix image_analysis_model
        _validate_and_fix_image_model(config)

        # Validate configuration
        validate_config(config)

        # Demote noisy startup log to DEBUG to avoid flooding streamed logs
        logger.debug("Configuration loaded successfully")
        logger.debug(f"Loaded config keys: {list(config.keys())}")

        return config

    except yaml.YAMLError as e:
        logger.error(f"Failed to parse YAML configuration: {e}")
        raise ConfigError(f"Invalid YAML in configuration file: {e}")
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        raise


def get_config_value(
    config: Dict[str, Any], key_path: str, default: Any = None
) -> Any:
    """
    Get a configuration value using dot notation.

    Args:
        config: Configuration dictionary
        key_path: Dot-separated path to the value (e.g., 'api_keys.openai')
        default: Default value if key doesn't exist

    Returns:
        Configuration value or default
    """
    keys = key_path.split(".")
    value = config

    try:
        for key in keys:
            value = value[key]
        return value
    except (KeyError, TypeError):
        logger.debug(
            f"Configuration key '{key_path}' not found, using default: {default}"
        )
        return default


def is_config_complete(config: Dict[str, Any]) -> bool:
    """
    Check if configuration is complete (all required API keys are set).

    Args:
        config: Configuration dictionary

    Returns:
        True if configuration is complete, False otherwise
    """
    try:
        api_keys = config.get("api_keys", {})
        required_keys = ["openai", "anthropic", "google", "xai"]
        # Conditionally require Groq if configured models route to Groq
        try:
            from layer1_chatbot.model_selector import ModelSelector
            ms = ModelSelector(config)
            providers = set()
            for k in ("primary_model", "fallback_model"):
                mv = str(config.get(k) or "")
                if mv:
                    providers.add(ms.get_provider_from_model(mv))
            if "groq" in providers:
                required_keys.append("groq")
        except Exception:
            pass

        for key in required_keys:
            value = api_keys.get(key, "")
            if not value or (isinstance(value, str) and value.startswith("${") and value.endswith("}")):
                logger.warning(f"API key '{key}' is not set")
                return False

        return True
    except Exception as e:
        logger.error(f"Error checking config completeness: {e}")
        return False
