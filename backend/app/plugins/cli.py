from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.plugins import registry


def main() -> None:
    parser = argparse.ArgumentParser(prog="control-deck-plugin")
    parser.add_argument("action", choices=("list", "validate", "install", "enable", "disable", "uninstall"))
    parser.add_argument("target", nargs="?")
    args = parser.parse_args()
    try:
        if args.action == "list":
            result = registry.list_plugins()
        elif args.action in {"validate", "install"}:
            if not args.target:
                parser.error(f"{args.action}にはmanifest pathが必要です")
            manifest = registry.validate_file(Path(args.target))
            result = manifest.model_dump(mode="json") if args.action == "validate" else registry.install(manifest)
        else:
            if not args.target:
                parser.error(f"{args.action}にはplugin IDが必要です")
            result = registry.uninstall(args.target) if args.action == "uninstall" else registry.set_enabled(args.target, args.action == "enable")
    except (registry.PluginError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
