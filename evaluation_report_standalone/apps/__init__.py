"""
Standalone evaluation-report stack.

`apps.evaluation_report` is copied from the main Django project and keeps the same
imports (`apps.core.*`). This package provides a *thin* `apps.core` shim so the
feature can run alone for testing/optimization.

When embedding back into tablet_backend, discard this shim and use the real
`apps.core` (see docs/EMBEDDING.md).
"""
