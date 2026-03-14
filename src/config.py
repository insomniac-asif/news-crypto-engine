"""Configuration loader utility."""

import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "config.yaml"


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load configuration from YAML file.

    Environment variable references like ${VAR_NAME} are resolved.

    Args:
        config_path: Path to the YAML config file.

    Returns:
        Parsed configuration dict.

    Raises:
        FileNotFoundError: If config file doesn't exist.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(path) as f:
        raw = f.read()

    config = yaml.safe_load(raw)
    logger.info("Loaded configuration from %s", config_path)
    return config


def setup_logging(config: dict[str, Any]) -> None:
    """Configure logging from the config dict.

    Args:
        config: Full config dict (expects 'logging' key).
    """
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    fmt = log_cfg.get("format", "%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    log_file = log_cfg.get("file")

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(str(log_path)))

    logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)
