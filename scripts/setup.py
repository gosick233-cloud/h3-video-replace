"""Two-stage first-use setup: inspect supplied paths, then save reviewed defaults."""
import argparse

from common import read_json, write_json
from workflow_config import DEFAULT_PROFILE, inspect_workflow, validate_profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--comfyui", required=True)
    inspect.add_argument("--workflow", required=True)
    inspect.add_argument("--comfy-url")
    inspect.add_argument("--out", required=True)
    save = sub.add_parser("save")
    save.add_argument("--from", dest="source", required=True, help="User-reviewed profile JSON")
    save.add_argument("--out", default=str(DEFAULT_PROFILE))
    args = parser.parse_args()
    if args.command == "inspect":
        schemas = None
        if args.comfy_url:
            from local_h3 import LocalComfy
            schemas = LocalComfy(args.comfy_url).request("/object_info")
        write_json(args.out, inspect_workflow(args.comfyui, args.workflow, args.comfy_url, schemas))
        print("Inspection saved. Ask the user to confirm controls and defaults; configuration has not been activated.")
    else:
        supplied = read_json(args.source)
        profile = supplied.get("profile", supplied)
        validate_profile(profile)
        write_json(args.out, profile)
        print("Reviewed local defaults saved: " + args.out)


if __name__ == "__main__":
    main()
