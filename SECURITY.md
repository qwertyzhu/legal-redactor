# Security policy

## Supported versions

Only the latest tagged release receives security fixes during the early preview.

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/qwertyzhu/legal-redactor/security/advisories/new) once the repository is published.

Do **not** attach real contracts, case files, client identities, or redaction ledgers to a public issue.

## Data-handling boundary

- legal-redactor is designed for **local** processing.
- The `*.ledger.json` file is a deanonymization key. Treat it like a secret.
- Repository fixtures are fictional. Do not replace them with live matter files.
- Users remain responsible for model-provider policies, backups, and access control.

## Accidental disclosure

If a real ledger or client file is pushed:

1. rotate any exposed credentials;
2. rewrite git history (`git filter-repo`) and force-push only if you understand the impact;
3. contact GitHub Support for cached commit removal if needed;
4. follow your firm’s incident process for client data.
