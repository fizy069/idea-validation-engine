"""OASIS backend package.

The package intentionally keeps imports lightweight at module import
time. API handlers should import concrete entry points directly:

  - ``from oasis_validator.pipeline import run_market_validation``
  - ``from oasis_validator.simulator import run_simulation``
  - ``from oasis_validator.scorer import build_market_artifacts``
"""

__all__: list[str] = []
