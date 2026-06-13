"""pyutcp-lab: a Python UTCP client, registry, and agent orchestrator.

The :mod:`pyutcp_lab.core` layer is the only part stable enough to re-export at
the top level for now; transports, registry, and agent layers land in later
milestones.
"""

from .agent import Checkpoint, Orchestrator
from .client import Budget, UtcpClient
from .core import (
    Manual,
    Provider,
    Tool,
    ToolCall,
    TransportType,
    UtcpError,
    VariableResolver,
)

__version__ = "0.1.0"

__all__ = [
    "Budget",
    "Checkpoint",
    "Manual",
    "Orchestrator",
    "Provider",
    "Tool",
    "ToolCall",
    "TransportType",
    "UtcpClient",
    "UtcpError",
    "VariableResolver",
    "__version__",
]
