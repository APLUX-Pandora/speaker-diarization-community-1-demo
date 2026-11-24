"""Resolve a local package directory. The native loader validates its contents."""
from pathlib import Path


def resolve_model_package(models):
    root = Path(models).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Model directory does not exist: {root}")
    return root
