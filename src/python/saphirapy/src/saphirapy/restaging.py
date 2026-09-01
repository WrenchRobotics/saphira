import ast
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from .syntax_tree.production_reference import ProductionReference
from typing import Dict, Iterator, List, Optional, Tuple
from .utils import find_all_alternative_productions
import brom_drake.productions.all as brom_all
NodePath = List[Tuple[str, Optional[int]]]

from .syntax_tree.cast_call_reference import CastCallReference
from .utils import identify_algorithm_type

class BromRestagingManager:
    """
    **Description*

    A transformer for Brom Benchmark data.
    This class is used to transform Brom Benchmark data into a format suitable for analysis.

    **Attributes**

    output_dir: Path
        The directory where the restaged code will be saved.
    """
    # The directory where the restaged code will be saved.
    def __init__(self, output_dir: Path|str):
        if isinstance(output_dir, str):
            output_dir = Path(output_dir)
            
        self.output_dir: Path = output_dir

    def _iter_nodes_with_paths(self, tree: ast.AST) -> Iterator[Tuple[ast.AST, NodePath]]:
        """
        Depth-first traversal yielding (node, path_from_root) pairs.
        """
        stack: List[Tuple[ast.AST, NodePath]] = [(tree, [])]
        while stack:
            node, path = stack.pop()
            yield node, path

            for field_name, value in ast.iter_fields(node):
                if isinstance(value, list):
                    for index in range(len(value) - 1, -1, -1):
                        child = value[index]
                        if isinstance(child, ast.AST):
                            stack.append((child, path + [(field_name, index)]))
                elif isinstance(value, ast.AST):
                    stack.append((value, path + [(field_name, None)]))

    def _node_at_path(self, tree: ast.AST, path: NodePath) -> ast.AST:
        """
        Resolve a previously captured AST path against a (possibly copied) tree.
        """
        node: ast.AST = tree
        for field_name, index in path:
            value = getattr(node, field_name)
            node = value if index is None else value[index]
        return node

    def find_easy_cast_and_build_calls(self, tree: ast.AST) -> List[CastCallReference]:
        """Find supported ``easy_cast_and_build()`` calls in ``tree``."""
        cast_calls: List[CastCallReference] = []

        for node, node_path in self._iter_nodes_with_paths(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "easy_cast_and_build":
                continue
            if not isinstance(node.func.value, ast.Name) or not node.args:
                continue

            cast_member_arg = node.args[0]
            if not isinstance(cast_member_arg, ast.Attribute):
                continue
            base_variable_name = self._extract_base_variable(cast_member_arg)
            if base_variable_name is None:
                continue

            instantiation_node, instantiation_node_path = self._find_variable_instantiation(
                tree, base_variable_name
            )
            if instantiation_node is None or instantiation_node_path is None:
                continue

            class_name = self._extract_class_name_from_instantiation(instantiation_node)
            if class_name is None:
                continue

            try:
                algorithm_type = identify_algorithm_type(class_name)
            except ValueError:
                algorithm_type = None

            call_kwargs = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
            cast_calls.append(
                CastCallReference(
                    production_var=node.func.value.id,
                    cast_member_arg_index=0,
                    cast_member_expr=ast.unparse(cast_member_arg),
                    cast_member_type=algorithm_type,
                    base_variable_name=base_variable_name,
                    instantiation_node=instantiation_node,
                    instantiation_node_path=instantiation_node_path,
                    call_args=node.args,
                    call_kwargs=call_kwargs,
                    node=node,
                    node_path=node_path,
                )
            )

        return cast_calls

    def _extract_base_variable(self, expression: ast.expr) -> Optional[str]:
        """Return the leftmost variable of a name or attribute expression."""
        if isinstance(expression, ast.Name):
            return expression.id
        if isinstance(expression, ast.Attribute):
            return self._extract_base_variable(expression.value)
        return None

    def _find_variable_instantiation(
        self, tree: ast.AST, variable_name: str
    ) -> Tuple[Optional[ast.Assign], Optional[NodePath]]:
        """Return the assignment that defines ``variable_name``, when present."""
        for node, node_path in self._iter_nodes_with_paths(tree):
            if not isinstance(node, ast.Assign):
                continue
            if any(isinstance(target, ast.Name) and target.id == variable_name for target in node.targets):
                return node, node_path
        return None, None

    def _extract_class_name_from_instantiation(self, assignment: ast.Assign) -> Optional[str]:
        """Return the constructor name for an assignment such as ``planner = Planner()``."""
        if not isinstance(assignment.value, ast.Call):
            return None
        if isinstance(assignment.value.func, ast.Name):
            return assignment.value.func.id
        if isinstance(assignment.value.func, ast.Attribute):
            return assignment.value.func.attr
        return None

    def find_all_imported_production_class_names(self, tree: ast.AST) -> List[str]:
        """
        **Description**

        Walk through the AST to find all class names that are imported from:
        - brom_drake.productions.all, or
        - any submodule of brom_drake.productions

        **Arguments**

        tree: ast.AST
            The abstract syntax tree of the target Python file.

        **Returns**

        imported_class_names: List[str]
            A list of class names that are imported from brom_drake.productions.all.
        """
        # Setup a list to store the names of imported classes from brom_drake.productions.all
        imported_class_names: List[str] = []

        # Walk through the AST to find all import statements
        for node in ast.walk(tree):
            # Check for both "import brom_drake.productions.all" and "from brom_drake.productions.all import ClassName"
            if isinstance(node, ast.ImportFrom) and node.module == "brom_drake.productions.all":
                for alias in node.names:
                    imported_class_names.append(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "brom_drake.productions.all":
                        # If the entire module is imported, we can't determine which classes are used without further analysis.
                        # For simplicity, we will assume that all classes from brom_drake.productions.all could be used.
                        # In a more advanced implementation, we could analyze attribute accesses to determine which classes are actually used.
                        return []  # Returning an empty list to indicate that all classes could be used.
            
            # Additionally, we should also check for imports from submodules of brom_drake.productions
            elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("brom_drake.productions"):
                for alias in node.names:
                    imported_class_names.append(alias.asname or alias.name)

        return imported_class_names

    def find_production_instances(self, tree: ast.AST) -> List[ProductionReference]:
        """
        **Description**
        
        Walk through the AST to find all variable assignments that instantiate a Production class.
        A Production class is any class that is:
        1. Imported from brom_drake.productions.all (either directly or via an alias).
        2. A subclass of BaseProduction.

        **Arguments**
        
        tree: ast.AST
            The abstract syntax tree of the target Python file.

        **Returns**

        production_instances: List[ProductionReference]
            A list of ProductionReference objects containing information about each production instance found.
        """
        # Walk through the AST to find all variable assignments
        prod_aliases: Dict[str, object] = {}
        prod_names: Dict[str, type] = {}

        # Collect how brom_drake.productions.all is imported.
        all_imported_production_names = self.find_all_imported_production_class_names(tree)
        assert len(all_imported_production_names) > 0, "No classes from brom_drake.productions.all are imported in the target file. Cannot find production instances without any imports from brom_drake.productions.all."

        production_instances: List[ProductionReference] = []
        for node, node_path in self._iter_nodes_with_paths(tree):
            # Find all variable assignments that instantiate one of the imported *Production* classes
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                func = node.value.func
                class_type: type|None = None

                # Check if the instantiation is of the form "ClassName()" where ClassName is directly imported from brom_drake.productions.all
                if isinstance(func, ast.Name) and func.id in all_imported_production_names:
                    class_type = getattr(brom_all, func.id, None)

                # Check if the instantiation is of the form "alias.ClassName()" where alias is an alias for brom_drake.productions.all
                elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    if func.value.id in all_imported_production_names:
                        class_type = getattr(brom_all, func.value.id, None)

                # If we have identified a class type and it is a subclass of BaseProduction, we consider it a production instance
                if class_type and issubclass(class_type, brom_all.BaseProduction) and node.targets and isinstance(node.targets[0], ast.Name):
                    production_instances.append(
                        ProductionReference(
                            variable_name=node.targets[0].id,
                            production_class=class_type,
                            node=node,
                            node_path=node_path,
                        )
                    )

        return production_instances

    def generate_restaged_code(self, target_file: Path) -> List[Path]:
        # Setup

        # Read contents of the target file and put it into a syntax tree
        target_file_contents = target_file.read_text()
        parsed_tree = ast.parse(target_file_contents)

        # Find the Production instantiations in the syntax tree
        production_instantiations = self.find_production_instances(parsed_tree)
        assert len(production_instantiations) == 1, f"Expected exactly one production instantiation in the target file, but found {len(production_instantiations)}. Found instantiations: {[instance.production_class.__name__ for instance in production_instantiations]}"

        # Replace the Production instantiations with new ones based on the RELATED_PRODUCTIONS mapping
        alternative_productions: List[type] = find_all_alternative_productions(production_instantiations[0].production_class)
        
        # Create a new syntax tree for each alternative production and write it to a new file
        new_files: List[Path] = []
        for detected_prod in production_instantiations:
            print(f"Detected production instantiation: {detected_prod.variable_name} = {detected_prod.production_class.__name__}()")
            
            print(f"Alternative productions to consider for restaging: {[prod.__name__ for prod in alternative_productions]}")
            for alt_prod in alternative_productions:
                new_tree = self.replace_production_in_ast(parsed_tree, production_instantiations[0], alt_prod)
                new_file_path = self.output_dir / f"{target_file.stem}_{alt_prod.__name__}.py"

                # Write the new syntax tree to a new file
                # - Make sure to create the output directory if it does not exist
                self.output_dir.mkdir(parents=True, exist_ok=True)

                # - Write file and save the written files to a list to return
                new_file_path.write_text(ast.unparse(new_tree))
                new_files.append(new_file_path)

        return new_files

    def replace_production_in_ast(self, tree: ast.AST, ref: ProductionReference, new_production: type) -> ast.AST:
        """
        **Description**

        Searches through the tree AST to find the node corresponding to the production reference and replaces it with a new production.

        **Arguments**

        tree: ast.AST
            The abstract syntax tree of the target Python file.

        ref: ProductionReference
            A reference to the production instance that we want to replace.

        new_production: type
            The new production class that we want to use to replace the original production.
        """
        # Copy the original tree to avoid modifying it in place
        out = deepcopy(tree)

        # First, we need to replace the import statement for the original production class with an import statement for the new production class if they are different.
        # We will look for import statements that import the original production class and modify them to import the new production class instead.
        for node in ast.walk(out):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("brom_drake.productions"):
                for alias in node.names:
                    if alias.asname == ref.production_class.__name__ or alias.name == ref.production_class.__name__:
                        alias.name = new_production.__name__
                        alias.asname = None  # Remove any aliasing to simplify

        # Second, find and replace using the captured path from the original tree.
        node = self._node_at_path(out, ref.node_path)
        print(f"Found node corresponding to production reference at path {ref.node_path}: {ast.dump(node)}")

        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func

            # Check if the instantiation is of the form "ClassName()"
            if isinstance(func, ast.Name) and func.id == ref.production_class.__name__:
                func.id = new_production.__name__

            # Check if the instantiation is of the form "alias.ClassName()"
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.attr == ref.production_class.__name__:
                    func.attr = new_production.__name__

            # Replace the original production class with the new production class in the node
            node.value.func = ast.Name(id=new_production.__name__, ctx=ast.Load())

        return out

    def restage_and_run(self, target_file: Path) -> None:
        """
        **Description**

        A convenience method that combines the generation of restaged code with running the generated code.

        **Arguments**

        target_file: Path
            The path to the target Python file to restage and run.
        """
        # 
        new_files = self.generate_restaged_code(target_file)
        processes: Dict[Path, subprocess.Popen] = {}

        for file in new_files:
            print(f"Starting restaged file in parallel process: {file}")
            processes[file] = subprocess.Popen([sys.executable, str(file)])

        failed_files: List[Tuple[Path, int]] = []
        for file, process in processes.items():
            return_code = process.wait()
            print(f"Finished restaged file: {file} (exit code: {return_code})")
            if return_code != 0:
                failed_files.append((file, return_code))

        if failed_files:
            failed_summary = ", ".join([f"{path} (code {code})" for path, code in failed_files])
            raise RuntimeError(f"One or more restaged files failed: {failed_summary}")