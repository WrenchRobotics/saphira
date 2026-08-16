
import inspect
from saphirapy.productions.relationships import RELATED_PRODUCTIONS


def identify_base_class_arguments(base_class: type) -> list[str]:
    """
    **Description**

    Given a base production class, identify the argument names that are used within that
    base class.

    This will be used to determine which arguments are commonly used across all productions that belong to the same base class, 
    which can help us design more consistent APIs for our productions.

    **Arguments**

    base_class: type
        The base production class for which to identify common argument names.

    **Returns**
    common_arguments: list[str]
        A list of argument names that are commonly used across all productions that belong to the given base class.
    """
    # Complain if the input is not a base class in RELATED_PRODUCTIONS
    if base_class not in RELATED_PRODUCTIONS:
        raise ValueError(f"Input base class {base_class.__name__} is not a key in RELATED_PRODUCTIONS. Please input a valid base class.")
    
    # Collect all argument names from the __init__ methods of all productions that belong to the given base class
    inspected_constructor = inspect.signature(base_class.__init__)
    for param in inspected_constructor.parameters.values():
        print(f"Parameter name: {param.name}, default value: {param.default}")
        print(f"  Kind: {param.kind}, annotation: {param.annotation}")

    return []