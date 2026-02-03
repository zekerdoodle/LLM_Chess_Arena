"""
Configuration Loader for LLM Chess Arena

Loads configuration from config.yaml and environment variables.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

PROJECT_ROOT = Path(__file__).parent.parent

# Optional dotenv support
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)
except ImportError:
    pass

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Custom exception for configuration errors."""


# Supported providers mapped to their environment variable names
API_KEY_ENV_MAP = {
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "google": ["GOOGLE_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "xai": ["XAI_API_KEY"],
}


def substitute_env_vars(value: str) -> str:
    """Substitute ${ENV_VAR} placeholders with environment values."""
    if not isinstance(value, str):
        return value

    pattern = r"\$\{([^}]+)\}"

    def replace_var(match):
        var_name = match.group(1)
        env_value = os.getenv(var_name)
        if env_value is None:
            return match.group(0)
        return env_value

    return re.sub(pattern, replace_var, value)


def _merge_api_keys_from_env(config: Dict[str, Any]) -> Dict[str, str]:
    """Populate config['api_keys'] using environment variables."""
    raw_keys = dict(config.get("api_keys") or {})
    merged: Dict[str, str] = {}

    for provider, env_candidates in API_KEY_ENV_MAP.items():
        explicit = raw_keys.get(provider)
        if isinstance(explicit, str) and explicit and not (
            explicit.startswith("${") and explicit.endswith("}")
        ):
            merged[provider] = explicit
            continue

        for env_name in env_candidates:
            env_value = os.getenv(env_name, "").strip()
            if env_value:
                merged[provider] = env_value
                break

    config["api_keys"] = merged
    return merged


def validate_config(config: Dict[str, Any]) -> None:
    """Validate the loaded configuration."""
    required_fields = ["max_token_budget"]

    for field in required_fields:
        if field not in config:
            raise ConfigError(f"Missing required configuration field: {field}")

    if config["max_token_budget"] <= 0:
        raise ConfigError("max_token_budget must be positive")


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load and validate configuration from YAML file."""
    if config_path is None:
        config_path = PROJECT_ROOT / "config.yaml"

    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        if config is None:
            raise ConfigError("Configuration file is empty")

        # Substitute environment variables
        def substitute_recursive(obj):
            if isinstance(obj, dict):
                return {k: substitute_recursive(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [substitute_recursive(item) for item in obj]
            elif isinstance(obj, str):
                return substitute_env_vars(obj)
            return obj

        config = substitute_recursive(config)
        _merge_api_keys_from_env(config)
        validate_config(config)

        return config

    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in configuration file: {e}")


def get_config_value(config: Dict[str, Any], key_path: str, default: Any = None) -> Any:
    """Get a configuration value using dot notation."""
    keys = key_path.split(".")
    value = config

    try:
        for key in keys:
            value = value[key]
        return value
    except (KeyError, TypeError):
        return default
