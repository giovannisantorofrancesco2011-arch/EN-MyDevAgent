"""MyDevAgent — local-first coding assistant with 15 specialized agents."""

__version__ = "1.0.0"

from .orchestrator import Orchestrator  # noqa: E402

__all__ = ["Orchestrator", "__version__"]
