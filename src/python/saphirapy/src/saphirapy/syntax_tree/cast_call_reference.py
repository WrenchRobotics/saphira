import ast
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

NodePath = List[Tuple[str, Optional[int]]]


@dataclass
class CastCallReference:
    """Reference to a supported ``easy_cast_and_build()`` AST call."""

    production_var: str
    cast_member_arg_index: int
    cast_member_expr: str
    cast_member_type: Optional[str]
    base_variable_name: str
    instantiation_node: ast.Assign
    instantiation_node_path: NodePath
    call_args: List[ast.expr]
    call_kwargs: Dict[str, ast.expr]
    node: ast.Call
    node_path: NodePath
