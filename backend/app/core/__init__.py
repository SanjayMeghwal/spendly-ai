"""Cross-cutting concerns: configuration, security primitives, shared constants.

Nothing here may import from `api`, `services`, or `models` - this is the
innermost layer and must not depend on anything above it.
"""
