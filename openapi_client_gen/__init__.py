"""Generate a small, typed, single-module httpx client from an OpenAPI 3.x spec."""

from .generator import Generator, generate
from .spec import SpecError, load_spec

__all__ = ["Generator", "SpecError", "generate", "load_spec"]
__version__ = "0.1.0"
