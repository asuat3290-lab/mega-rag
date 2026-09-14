"""Resolve optional utility paths without relying on the developer's machine."""
from pathlib import Path


def configured_path(key: str, fallback: str) -> Path:
    import yaml
    root = Path(__file__).resolve().parent
    with (root / 'config.yaml').open(encoding='utf-8') as handle:
        config = yaml.safe_load(handle)
    value = Path(config.get('paths', {}).get(key, fallback)).expanduser()
    return value.resolve() if value.is_absolute() else (root / value).resolve()
