import argparse
import ast
import json
from pathlib import Path

from saphirapy.productions.relationships import RELATED_PRODUCTIONS
from saphirapy.restaging import BromRestagingManager


def detect_production_mapping(target_file: Path) -> dict:
    """
    Detect production mapping information from a target file.

    Returns a dictionary with keys:
    - status: one of "ok", "unmapped", "not_found"
    - detected_parent_class: parent class name from RELATED_PRODUCTIONS or None
    - detected_production_class: concrete detected production class name or None
    - related_productions: list of related production class names
    """
    parsed_tree = ast.parse(target_file.read_text())
    manager = BromRestagingManager(output_dir=target_file.parent)

    try:
        production_instances = manager.find_production_instances(parsed_tree)
    except AssertionError:
        return {
            "status": "not_found",
            "detected_parent_class": None,
            "detected_production_class": None,
            "related_productions": [],
        }

    if len(production_instances) == 0:
        return {
            "status": "not_found",
            "detected_parent_class": None,
            "detected_production_class": None,
            "related_productions": [],
        }
    if len(production_instances) > 1:
        raise ValueError(
            "Expected at most one production instance in target file, "
            + f"but found {len(production_instances)}."
        )

    detected_class = production_instances[0].production_class
    detected_class_name = detected_class.__name__
    for parent_class in RELATED_PRODUCTIONS:
        if issubclass(detected_class, parent_class):
            return {
                "status": "ok",
                "detected_parent_class": parent_class.__name__,
                "detected_production_class": detected_class_name,
                "related_productions": [
                    prod.__name__ for prod in RELATED_PRODUCTIONS[parent_class]
                ],
            }

    return {
        "status": "unmapped",
        "detected_parent_class": None,
        "detected_production_class": detected_class_name,
        "related_productions": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Find a production instance in a Python file and emit a JSON "
            "payload containing detected parent and related productions."
        )
    )
    parser.add_argument("target_file", type=Path, help="Path to the Python file to inspect")
    args = parser.parse_args()

    target_file = args.target_file.resolve()
    if not target_file.exists():
        raise FileNotFoundError(f"Target file not found: {target_file}")

    mapping = detect_production_mapping(target_file)
    print(json.dumps(mapping))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
