import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 6:
        raise SystemExit(
            "Usage: finalize_result.py <result_json_path> <production_class> <exit_code> <duration_seconds> <restaged_file_default>"
        )

    result_json_path = Path(sys.argv[1])
    production_class = sys.argv[2]
    exit_code = int(sys.argv[3])
    duration_seconds = float(sys.argv[4])
    restaged_file_default = sys.argv[5]

    payload = {}
    if result_json_path.exists():
        try:
            payload = json.loads(result_json_path.read_text())
        except Exception:
            payload = {}

    payload.setdefault("production_class", production_class)
    payload.setdefault("restaged_file", restaged_file_default)
    payload["exit_code"] = exit_code
    payload["duration_seconds"] = duration_seconds

    result_json_path.parent.mkdir(parents=True, exist_ok=True)
    result_json_path.write_text(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
