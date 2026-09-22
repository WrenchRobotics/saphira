import json
import sys
from pathlib import Path

from saphirapy.github_actions_contracts import finalize_result_payload


def main() -> int:
    if len(sys.argv) not in (6, 7, 8):
        raise SystemExit(
            "Usage: finalize_result.py <result_json_path> <production_class> <exit_code> "
            "<duration_seconds> <restaged_file_default> [cast_production_name]"
        )

    result_json_path = Path(sys.argv[1])
    production_class = sys.argv[2]
    exit_code = int(sys.argv[3])
    duration_seconds = float(sys.argv[4])
    restaged_file_default = sys.argv[5]
    cast_production_name = sys.argv[6] if len(sys.argv) == 7 else None
    comparison_metadata_json = sys.argv[7] if len(sys.argv) == 8 else ""
    comparison_metadata = json.loads(comparison_metadata_json) if comparison_metadata_json else None

    finalize_result_payload(
        result_json_path,
        production_class,
        exit_code,
        duration_seconds,
        restaged_file_default,
        cast_production_name,
        comparison_metadata,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
