"""OASIS-powered idea validation toolkit.

The submodules are intentionally not eagerly imported here so that the
scoring/reporting layer can be imported without requiring the OASIS
package itself (camel-oasis pins ``Python <3.12`` and brings heavy
dependencies). Import the specific entry point you need:

  - ``from oasis_validator.pipeline import run_validation``  (full run)
  - ``from oasis_validator.simulator import run_simulation`` (simulate only)
  - ``from oasis_validator.scorer import score_run``         (score a DB)
  - ``from oasis_validator.report import render_console``    (render output)
"""

__all__: list[str] = []
