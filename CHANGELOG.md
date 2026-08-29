# Changelog

## 1.1.0-rc.1 - 2026-08-29

### Added

- Repository/Unit of Work abstraction with a SQLite WAL backend.
- Verified JSON-to-SQLite migration, recovery export, performance, concurrency, and fault probes.
- Accessible workflow navigation, guarded UI actions, and hardened container delivery.
- Multi-architecture GHCR release automation with SBOM, provenance, and security gates.

### Changed

- Extracted business persistence from the FastAPI composition root into focused services.
- Made scoring, rescore, ground-truth, calibration, configuration, cleanup, and project lifecycle
  writes compensating and recoverable.
- Unified application, OpenAPI, health, package, image, and release-candidate version metadata.

### Security

- Production containers run as an unprivileged user with a read-only root filesystem, no Linux
  capabilities, no-new-privileges, fail-closed API-key loading, and authenticated metrics.
- Updated FastAPI, Starlette, python-multipart, and development Pygments dependencies to remove
  known fixable vulnerabilities while preserving the certified OpenAPI contract.

### Compatibility

- Public paths, HTTP methods, request/response DTOs, authentication behavior, scoring algorithms,
  and storage schemas remain compatible with 1.0.0. Only release metadata changes to 1.1.0rc1.
