"""Core domain layer: models, variable substitution, errors, schema, and auth."""

from .config import Config, ConfigError, ProviderConfig, load_config, load_config_file
from .auth import (
    ApiKeyAuth,
    Auth,
    AuthError,
    BasicAuth,
    BearerAuth,
    NoAuth,
    auth_from_dict,
)
from .errors import (
    CyclicVariableError,
    DuplicateProviderError,
    ProviderError,
    TimeoutError,
    TransportError,
    UndefinedVariableError,
    UnknownProviderError,
    UtcpError,
    ValidationError,
    VariableError,
)
from .models import Manual, Provider, Tool, ToolCall, TransportType
from .schema import (
    ArgumentValidationError,
    SchemaError,
    ensure_valid_arguments,
    validate_arguments,
)
from .variables import VariableResolver, parse_dotenv

__all__ = [
    "ApiKeyAuth",
    "ArgumentValidationError",
    "Auth",
    "AuthError",
    "Config",
    "ConfigError",
    "BasicAuth",
    "BearerAuth",
    "CyclicVariableError",
    "DuplicateProviderError",
    "Manual",
    "NoAuth",
    "Provider",
    "ProviderError",
    "SchemaError",
    "TimeoutError",
    "Tool",
    "ToolCall",
    "TransportError",
    "TransportType",
    "UndefinedVariableError",
    "UnknownProviderError",
    "UtcpError",
    "ValidationError",
    "VariableError",
    "ProviderConfig",
    "VariableResolver",
    "auth_from_dict",
    "load_config",
    "load_config_file",
    "ensure_valid_arguments",
    "parse_dotenv",
    "validate_arguments",
]
