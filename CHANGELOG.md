# Changelog

## 2026-03-06
- Snapshot tag: `snapshot-2026-03-06-govern-suite`
- Commit: `5365357e4d42d75142149874b5066503498d35e3`

### Added
- Bearer-token protection for sensitive document routes
- Evidence package generation for downstream governance ingestion
- Handoff endpoint from AurorAI to CompassAI
- Source hashing, control checks, and audit trail enrichment

### Changed
- Improved fallback extraction behavior when the live LLM path is unavailable or invalid
- Preserved document-processing flow so evidence handoff still works under fallback

### Verified
- Local API works at `http://127.0.0.1:9206/`
- Upload -> extract -> evidence handoff works locally
- Evidence handoff into CompassAI succeeds in the current local stack

### Notes
- `EMERGENT_LLM_KEY` is loaded into the live process
- Richer remote extraction still needs a valid `OPENAI_API_KEY` or the correct `OPENAI_BASE_URL` for the Emergent-compatible endpoint
