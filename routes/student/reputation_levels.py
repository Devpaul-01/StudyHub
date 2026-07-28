"""
routes/student/reputation_levels.py — COMPATIBILITY SHIM

The actual implementation moved to services/reputation_levels.py as part of
the Phase 1 service-extraction remediation (services/* must never be
imported by, nor import from, routes/student/* — this module's real home
is the service layer since it's a pure lookup table with no Flask/route
dependency).

This shim re-exports the same names from the new location so any remaining
`from routes.student.reputation_levels import ...` call site keeps working
during the transition. Update callers to import from `services.reputation_levels`
directly; this file should be deleted once all callers are confirmed updated.
"""
from services.reputation_levels import (
    REPUTATION_LEVELS,
    get_reputation_level,
    get_reputation_level_name,
)
