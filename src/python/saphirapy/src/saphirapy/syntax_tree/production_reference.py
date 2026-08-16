import ast
from typing import List, Optional, Tuple
from brom_drake.productions.types.base import BaseProduction

NodePath = List[Tuple[str, Optional[int]]]

class ProductionReference:
    """
    A class that represents a reference to a production in the syntax tree.
    """
    #: The name of the variable that is assigned the production instance (e.g., "attempt1")
    variable_name: str

    #: The class of the production (e.g., AttemptGraspWithStaticWrist) - this is the actual class object, not just a string
    production_class: type

    # Node in the syntax tree where the production is instantiated
    node: ast.AST

    # Path from AST root to node, represented as (field_name, index_or_none) steps
    node_path: NodePath

    def __init__(self, variable_name: str, production_class: type, node: ast.AST, node_path: NodePath):
        
        self.variable_name = variable_name
        
        # Verify that the production_class is indeed a class object (type)
        if not isinstance(production_class, type):
            raise TypeError(f"Expected a class type for production_class, got {type(production_class)}")
        
        # Also, verify that the production_class is a subclass of BaseProduction (this is a sanity check, we expect all production classes to be subclasses of BaseProduction)
        if not issubclass(production_class, BaseProduction):
            raise TypeError(f"Expected a subclass of BaseProduction for production_class, got {production_class}")

        self.production_class = production_class
        self.node = node
        self.node_path = node_path