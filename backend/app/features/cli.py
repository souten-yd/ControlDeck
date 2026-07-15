from __future__ import annotations

import argparse
import json

from app.features import registry


def main() -> None:
    parser = argparse.ArgumentParser(prog="control-deck-feature")
    parser.add_argument("action", choices=("status", "install", "enable", "disable", "uninstall"))
    parser.add_argument("feature", nargs="?", default="opencode", choices=sorted(registry.KNOWN_FEATURES))
    args = parser.parse_args()
    try:
        result = registry.status(args.feature) if args.action == "status" else registry.apply(args.action, args.feature)
    except registry.FeatureError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
