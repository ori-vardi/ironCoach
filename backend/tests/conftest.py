"""Shared test configuration."""
import sys
from pathlib import Path

# Ensure server module is importable
sys.path.insert(0, str(Path(__file__).parent.parent))
