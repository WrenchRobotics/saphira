import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

import brom_drake.productions.all as brom_all
from saphirapy.restaging import BromRestagingManager


def restage_and_run_one(target_file: Path, production_class_name: str, output_dir: Path) -> dict:
    manager = BromRestagingManager(output_dir=output_dir)
    parsed_tree = ast.parse(target_file.read_text())

    production_instances = manager.find_production_instances(parsed_tree)
    if len(production_instances) != 1:
        raise ValueError(
            "Expected exactly one production instance in target file, "
            + f"found {len(production_instances)}."
        )

    production_ref = production_instances[0]
    alt_production = getattr(brom_all, production_class_name, None)
    if alt_production is None:
        raise ValueError(f"Could not resolve production class named {production_class_name}.")

    new_tree = manager.replace_production_in_ast(parsed_tree, production_ref, alt_production)

    output_dir.mkdir(parents=True, exist_ok=True)
    restaged_file = output_dir / f"{target_file.stem}_{production_class_name}.py"
    restaged_file.write_text(ast.unparse(new_tree))

    completed = subprocess.run([sys.executable, str(restaged_file)], check=False)
    return {
        "production_class": production_class_name,
        "restaged_file": str(restaged_file),
        "exit_code": int(completed.returncode),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate and run one restaged production variant."
    )
    parser.add_argument("--target-file", type=Path, required=True)
    parser.add_argument("--production-class", type=str, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--result-json", type=Path, required=False)
    args = parser.parse_args()

    result = restage_and_run_one(
        target_file=args.target_file.resolve(),
        production_class_name=args.production_class,
        output_dir=args.output_dir.resolve(),
    )

    if args.result_json is not None:
        args.result_json.parent.mkdir(parents=True, exist_ok=True)
        args.result_json.write_text(json.dumps(result, indent=2))

    print(json.dumps(result))
    return 0 if result["exit_code"] == 0 else result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
