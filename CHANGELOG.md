# Changelog

## Unreleased

- Configured pytest to import the integration from the repository root in Linux CI.
- Documented all service events, meter information, options, and diagnostics; added a meter-information automation example.
- Added the MIT license and project license metadata.
- Added local integration brand assets.
- Added minimum (2026.8.0) and latest Home Assistant test environments to CI.
- Fixed the last-updated sensor and diagnostics by using the timestamp-aware coordinator.
- Updated device lookup for the current HA registry API while retaining legacy compatibility.
- Added Ruff checks and formatting to CI; validated the integration with hassfest.
- Removed HA-provided requests/certifi dependencies from the custom integration manifest.
- Reorganized the integration around a coordinator, shared helpers, and typed runtime data.
- Added account and meter device targeting for service calls.
- Preserved existing account device and billing sensor identities during migration.
- Added Home Assistant monetary and water-meter sensor metadata.
- Closed portal sessions when initial setup fails.
- Improved error classification and safe GET retries with numeric or HTTP-date `Retry-After` values.
- Redacted account and meter identifiers from diagnostics.
- Added reconfiguration, localization, and validation workflows.
- Prevented duplicate account entries from merging legacy account devices.

## 0.4.0

- Added coordinator-based polling and typed config entry runtime data.
- Added meter device metadata and services that can target a selected account or meter.
- Added diagnostics with sensitive data redaction.
- Added bounded retries for safe API reads and clearer connection/authentication errors.
- Added reauthentication, reconfiguration, and options flows.
