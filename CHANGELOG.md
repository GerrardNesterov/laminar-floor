# Changelog

## v6-preview - 23 November 2026

Public release of the existing executable built on 19 November. Application code is unchanged for publication.

- Left/right rotation axis after orienting the selected DXF edge upward.
- Annular support contact instead of filling the support surface with disks.
- Ribs extend to actual support contact regions; isolated remnants are excluded.
- Airflow screening excludes the complete external support footprint, separating surface openness from through-open channels.
- Reinforcement stops before closing the final 10% of available passage around the support footprint. Unmet strength conditions remain visible in the report.
- Finite STL geometry can be exported even when the report advises against export.

Earlier v5 changes: additional outer flange 0-10 mm, revised nominal thickness reference, removal of JSON project save/load and wheel-load selection from the native interface.

This experimental preview was not retested on clean Windows. Previous clean-Windows evidence applies to an earlier iteration.
