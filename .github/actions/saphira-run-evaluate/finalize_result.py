import sys
from pathlib import Path

from saphirapy.github_actions_contracts import finalize_result_payload


def main() -> int:
    if len(sys.argv) not in (6, 7):
        raise SystemExit(
            "Usage: finalize_result.py <result_json_path> <production_class> <exit_code> "
            "<duration_seconds> <restaged_file_default> [casted_production_name]"
        )

    result_json_path = Path(sys.argv[1])
    production_class = sys.argv[2]
    exit_code = int(sys.argv[3])
    duration_seconds = float(sys.argv[4])
    restaged_file_default = sys.argv[5]
    casted_production_name = sys.argv[6] if len(sys.argv) == 7 else None

    finalize_result_payload(
        result_json_path,
        production_class,
        exit_code,
        duration_seconds,
        restaged_file_default,
        casted_production_name,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
