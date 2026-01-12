class RunError(Exception):
    """Base class for all runner exceptions."""
    pass

class ConfigError(RunError):
    """Configuration related errors."""
    pass

class CompilationError(RunError):
    """Compilation failure."""
    pass

class ExecutionError(RunError):
    """Execution failure."""
    pass
