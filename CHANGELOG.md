# Changelog

## 0.2.0

- Added unified `diagnose` workflow combining doctor, evidence trace, profile lint, cleanup candidates, and recommended next actions.
- Added `explain` reports with diagnosis, evidence trace, and profile warnings.
- Added profile lint, cleanup patch preview, conservative cleanup dry-run/apply, and strict lint mode.
- Added profile-driven query/entity behavior and removed project-specific core matching assumptions.
- Added Pi slash command routing for diagnose, explain, doctor, profile refine/suggest/lint/cleanup workflows.
- Added `diagnose --save-artifacts` for Markdown, JSON, and cleanup patch outputs under `.loggraph/reports/`.
- Added multi-language/log parsing improvements, profile health checks, stale index detection, and release `scripts/check`.
