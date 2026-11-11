"""Shared ACE Python package.

Submodules are intentionally not imported here: most services do not need the
optional LLM, Kafka, or Cassandra dependencies.
"""

from .taxonomy import ECSF_ROLES

__version__ = "0.1.0"

__all__ = ["ECSF_ROLES", "__version__"]
