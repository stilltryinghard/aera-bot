# Preliminary verification, 26 October 2026

This is evidence for the next reviewer, not a completed comprehensive security audit.

## Confirmed

- 226 application/script/asset/migration/dependency files copied from the live application; all SHA-256 hashes rechecked against source, zero mismatches. 144 Python files in the source snapshot.
- Bot, runtime API, node-sync, site and support processes active; runtime readiness HTTP 200. These are point-in-time health checks, not end-to-end business validation.
- Python syntax compilation passed for app/scripts/alembic; AST import inventory found no unresolved `from app.*` modules. Dynamic imports and external integrations require further review.
- Alembic upgrade to head passed against a fresh disposable SQLite DB; model table/column comparison found no missing tables/columns. Indexes, FK behavior, constraints, downgrade and PostgreSQL were not comprehensively verified.
- 29 added critical-contract tests passed under isolated Linux/Python 3.12. They cover the exact 15-minute checkout boundary, paid/reversed receipt protection, malformed provider amounts, signed panel token tampering/identity binding, stale/future panel snapshots, HWID normalization/parent binding, and rejection of unverified/mismatched trial-to-paid purchases.
- Targeted credential-pattern scan found no real Telegram token, private key or literal client URI. URI matches were builders using variables, not issued credentials. This is not a formal guarantee that arbitrary secrets cannot exist.
- No production source changes, payment simulations, client messages, subscription changes or service restarts were performed for this snapshot.

## Known gaps / must not hide

- Existing repository suite on the fresh code: **176 passed, 52 failed, 8 collection errors**, one warning. See LEGACY_TEST_BASELINE.txt. Several errors are stale expectations/imports for removed checkout, FreeKassa, manual-bank and old bot flows. Other failures must be individually triaged; do not dismiss all failures as obsolete.
- Ruff app/scripts: **676 findings**: I001=92, E501=396, E701=116, E702=51, E401=8, E402=6, F401=7. Most are style/import issues; not a security severity score.
- Existing CI and historical helper `scripts.verify` were not rewritten to mask baseline failures. `scripts.verify` also references docker-compose.yml absent in production/repository and should be reviewed as tooling drift.
- `.env.example`, tests, CI and SECURITY.md are repository audit/development metadata, not byte-for-byte production source. Source files listed in SOURCE_SHA256.json were kept unchanged.
- No live provider payment, Telegram Android multi-recipient action, INCY/Happ device behavior, node-provisioning failure/recovery, real concurrent checkout or refund was executed. No dependency CVE scan, penetration test or complete line-by-line human review has been completed by the snapshot preparer.
- Website, support application and remote forced-command bridge implementations are external audit boundaries. Their processes being healthy does not validate their code.
- Marketing copy such as “100% приватно” or “Сервис №1” is not evidence of anonymity, security, availability or ranking.

## Priority review areas

Trace actual registered routes first. Check Platega callbacks and provider reconciliation, Telegram Stars pre-checkout/successful-payment validation, late/repeated receipts, timeout reserves, concurrent issuance, trial recycling and trial-to-paid UUID ownership, recovery after partial node updates, blocked/expired access, HWID limits, authenticated internal APIs, first-purchase commissions and withdrawal reservation concurrency. See the exhaustive request in AUDIT_FOR_CLAUDE.md.
