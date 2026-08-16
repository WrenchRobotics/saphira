import sys
from pathlib import Path

from saphirapy.github_actions_contracts import write_detection_outputs


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(
            "Usage: emit_outputs.py <detection_json> <github_output_path>"
        )

    write_detection_outputs(sys.argv[1], Path(sys.argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())