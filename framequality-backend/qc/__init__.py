"""FrameQuality Pro — feature-film QC backend."""

__version__ = "1.0.0"

from .engine import run_scan  # noqa: F401
from .profiles import PROFILES, get_profile, list_profiles  # noqa: F401
