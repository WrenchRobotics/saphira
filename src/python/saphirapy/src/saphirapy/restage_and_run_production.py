import argparse
import ast
import importlib.util
import inspect
import json
import sys
import traceback
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import ModuleType

import brom_drake.productions.all as brom_all
from saphirapy.productions.evaluators.offline_kinematic_motion_planning import (
    OfflineKinematicMotionPlanningEvaluator,
)
from saphirapy.restaging import BromRestagingManager
from saphirapy.syntax_tree.cast_member_option import CastMemberOption


def _yaml_scalar(value: object) -> str:
    return json.dumps(value)


def installed_package_version(distribution_name: str) -> str:
    """Return the installed distribution version, or ``unknown`` if unavailable."""
    try:
        return version(distribution_name)
    except PackageNotFoundError:
        return "unknown"


def write_restaging_manifest(
    manifest_path: Path,
    target_file: Path,
    production_class_name: str,
    restaged_file: Path,
    cast_production_name: str,
    current_actors: list[dict[str, str | int | None]],
    actor_variant: dict | None,
    exit_code: int,
) -> None:
    """Write the exact restaging inputs and generated file location as YAML."""
    lines = [
        f"cast_production_name: {_yaml_scalar(cast_production_name)}",
        f"source_file: {_yaml_scalar(str(target_file))}",
        f"production_class: {_yaml_scalar(production_class_name)}",
        f"restaged_file: {_yaml_scalar(str(restaged_file))}",
        f"exit_code: {exit_code}",
        "package_versions:",
        f"  brom_drake: {_yaml_scalar(installed_package_version('brom_drake'))}",
        f"  saphirapy: {_yaml_scalar(installed_package_version('saphirapy'))}",
        f"current_actors: {_yaml_scalar(current_actors)}",
    ]
    if actor_variant is None:
        lines.append("actor_variant: null")
    else:
        lines.append("actor_variant:")
        for key, value in actor_variant.items():
            lines.append(f"  {key}: {_yaml_scalar(value)}")

    manifest_path.write_text("\n".join(lines) + "\n")


def _append_production_return_to_main(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            node.body.append(ast.parse("return locals().get('production')").body[0])
            return ast.fix_missing_locations(tree)

    return tree


def _load_module_from_path(module_path: Path) -> ModuleType:
    module_name = f"_saphira_restaged_{module_path.stem}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _invoke_restaged_main(restaged_module: ModuleType):
    main_fn = getattr(restaged_module, "main", None)
    if not callable(main_fn):
        raise ValueError("Restaged module must define a callable main()")

    signature = inspect.signature(main_fn)
    kwargs = {}
    for name, parameter in signature.parameters.items():
        if name == "meshcat_port_number":
            kwargs[name] = -1
            continue

        if parameter.default is inspect._empty and parameter.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            raise ValueError(
                f"Unsupported restaged main() parameter {name!r}; only defaulted parameters are supported."
            )

    return main_fn(**kwargs)


def _run_restaged_module(restaged_file: Path):
    try:
        restaged_module = _load_module_from_path(restaged_file)
        return _invoke_restaged_main(restaged_module), 0
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1 if exc.code else 0
        return None, int(exit_code)
    except Exception:
        traceback.print_exc()
        return None, 1


def _collect_additional_metrics(production) -> dict[str, float]:
    if production is None or not isinstance(
        production, brom_all.KinematicMotionPlanningProduction
    ):
        return {}

    watcher = getattr(production, "watcher", None)
    if watcher is not None:
        if hasattr(watcher, "_save_figures"):
            watcher._save_figures()
        if hasattr(watcher, "_save_raw_data"):
            watcher._save_raw_data()

    metrics = OfflineKinematicMotionPlanningEvaluator(production=production).evaluate()
    return {metric_name: float(metric_value) for metric_name, metric_value in metrics.items()}


def restage_and_run_one(
    target_file: Path,
    production_class_name: str,
    output_dir: Path,
    actor_variant: dict | None = None,
    cast_production_name: str | None = None,
) -> dict:
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
    cast_calls = manager.find_easy_cast_and_build_calls(parsed_tree)
    current_actors = [
        {
            "cast_call_index": index,
            "expression": cast_call.cast_member_expr,
            "class_name": manager._extract_class_name_from_instantiation(
                cast_call.instantiation_node
            ),
            "actor_type": cast_call.cast_member_type,
        }
        for index, cast_call in enumerate(cast_calls)
    ]
    if actor_variant is not None:
        cast_call_index = int(actor_variant["cast_call_index"])
        if cast_call_index >= len(cast_calls):
            raise ValueError(f"No cast call exists at index {cast_call_index}.")
        actor_option = CastMemberOption(
            name=str(actor_variant["name"]),
            import_statements=list(actor_variant["import_statements"]),
            instantiation_code=str(actor_variant["instantiation_code"]),
            variable_name=str(actor_variant["variable_name"]),
            method_to_call=str(actor_variant["method_to_call"]),
        )
        new_tree = manager.replace_cast_member_in_ast(new_tree, cast_calls[cast_call_index], actor_option)
    new_tree = _append_production_return_to_main(new_tree)

    output_dir.mkdir(parents=True, exist_ok=True)
    file_cast_production_name = cast_production_name or production_class_name
    restaged_file = output_dir / f"{target_file.stem}_{file_cast_production_name}.py"
    restaged_file.write_text(ast.unparse(new_tree))

    production, exit_code = _run_restaged_module(restaged_file)
    manifest_path = output_dir / f"{file_cast_production_name}.yaml"
    write_restaging_manifest(
        manifest_path,
        target_file,
        production_class_name,
        restaged_file,
        file_cast_production_name,
        current_actors,
        actor_variant,
        exit_code,
    )
    result = {
        "production_class": production_class_name,
        "restaged_file": str(restaged_file),
        "restaging_manifest": str(manifest_path),
        "exit_code": int(exit_code),
    }
    if cast_production_name is not None:
        result["cast_production_name"] = cast_production_name
    if actor_variant is not None:
        result["actor_variant"] = actor_variant["name"]

    metrics = _collect_additional_metrics(production)
    if metrics:
        result["metrics"] = metrics

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate and run one restaged cast production."
    )
    parser.add_argument("--target-file", type=Path, required=True)
    parser.add_argument("--production-class", type=str, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--result-json", type=Path, required=False)
    parser.add_argument("--actor-variant-json", type=str, required=False)
    parser.add_argument("--cast-production-name", type=str, required=False)
    args = parser.parse_args()

    result = restage_and_run_one(
        target_file=args.target_file.resolve(),
        production_class_name=args.production_class,
        output_dir=args.output_dir.resolve(),
        actor_variant=(json.loads(args.actor_variant_json) if args.actor_variant_json else None),
        cast_production_name=args.cast_production_name,
    )

    if args.result_json is not None:
        args.result_json.parent.mkdir(parents=True, exist_ok=True)
        args.result_json.write_text(json.dumps(result, indent=2))

    print(json.dumps(result))
    return 0 if result["exit_code"] == 0 else result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
