from dataclasses import dataclass
from typing import List


@dataclass
class CastMemberOption:
    """A registered alternative implementation for a cast member role."""

    name: str
    import_statements: List[str]
    instantiation_code: str
    variable_name: str
    method_to_call: str
