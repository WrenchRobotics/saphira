import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

@dataclass
class BromRestagingManager:
    output_dir: Path

    """
    A transformer for Brom Benchmark data.
    This class is used to transform Brom Benchmark data into a format suitable for analysis.
    """

    def find_production_instances(self, tree: ast.AST) -> List[Dict[str,str|ast.AST]]:
        # Walk through the AST to find all variable assignments
        prod_aliases: set[str] = set()
        prod_names: set[str] = set()

        # Collect how brom_drake.productions.all is imported.
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "brom_drake.productions.all":
                        prod_aliases.add(alias.asname or alias.name.split(".")[-1])
            elif isinstance(node, ast.ImportFrom):
                if node.module == "brom_drake.productions.all":
                    for alias in node.names:
                        prod_names.add(alias.asname or alias.name)

        production_instances: List[Dict[str,str|ast.AST]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                func = node.value.func
                class_name = None

                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    if func.value.id in prod_aliases:
                        class_name = func.attr
                elif isinstance(func, ast.Name):
                    if func.id in prod_names:
                        class_name = func.id

                if class_name and node.targets and isinstance(node.targets[0], ast.Name):
                    production_instances.append({
                        "variable": node.targets[0].id,
                        "class": class_name,
                        "node": node,
                    })

        return production_instances

    def generate_restaged_code(self, target_file: Path) -> List[Path]:
        # Setup

        # Read contents of the target file and put it into a syntax tree
        target_file_contents = target_file.read_text()
        parsed_tree = ast.parse(target_file_contents)

        # Find the Production instantiations in the syntax tree
        production_instantiations = self.find_production_instances(parsed_tree)
        pass

    def replace_production_in_ast(self, tree: ast.AST, old_production_name: str, new_production_name: str) -> ast.AST:
        pass

    def transform(self, data: str) -> dict:
        """
        Transforms the Brom Benchmark data from a string representation to a dictionary.

        Args:
            data (str): The Brom Benchmark data as a string.

        Returns:
            dict: The transformed data as a dictionary.
        """
        return ast.literal_eval(data)