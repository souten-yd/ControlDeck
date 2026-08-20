from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from app.addons.contract import (
    ADDON_CONTRACT_VERSION,
    BRIDGE_SCHEMA_VERSION,
    HEALTH_SCHEMA_VERSION,
    THEME_TOKEN_VERSION,
)
from app.addons.schema import load_manifest_file


def main() -> None:
    parser = argparse.ArgumentParser(prog="control-deck-ext")
    parser.add_argument("action", choices=("lint",))
    parser.add_argument("manifest")
    args = parser.parse_args()
    try:
        parsed = load_manifest_file(Path(args.manifest))
    except (ValueError, ValidationError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "valid": True,
        "api_version": parsed.manifest.api_version,
        "id": parsed.manifest.id,
        "warnings": list(parsed.warnings),
        "host_contract": {
            "addon": ADDON_CONTRACT_VERSION,
            "bridge": BRIDGE_SCHEMA_VERSION,
            "theme": THEME_TOKEN_VERSION,
            "health": HEALTH_SCHEMA_VERSION,
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
