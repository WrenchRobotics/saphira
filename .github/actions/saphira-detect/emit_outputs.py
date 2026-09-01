import sys
from pathlib import Path

from saphirapy.github_actions_contracts import write_variant_outputs


def parse_boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"Expected 'true' or 'false', received {value!r}.")


def main() -> int:
    if len(sys.argv) != 6:
        raise SystemExit(
            "Usage: emit_outputs.py <detection_json> <production_file> "
            "<github_output_path> <include_alternative_productions> "
            "<include_alternative_actors>"
        )

    write_variant_outputs(
        sys.argv[1],
        Path(sys.argv[2]),
        Path(sys.argv[3]),
        parse_boolean(sys.argv[4]),
        parse_boolean(sys.argv[5]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())