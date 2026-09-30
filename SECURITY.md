# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's "Report a vulnerability" button on the repository's Security tab (private vulnerability reporting). Do not open a public issue.

You will get an acknowledgement within 72 hours and a fix or mitigation plan within 14 days for confirmed issues.

## Scope

This is a client library for a third party's messaging API. Three parts of it are security-relevant, and a report about any of them is welcome:

- **Webhook authenticity.** `verify_signature` computes HMAC-SHA256 over the raw request body and compares in constant time. It supports only SHA-256, with no SHA-1 fallback, and it refuses every delivery when no app secret is configured rather than treating a missing secret as permission to skip the check. `verify_challenge` compares the verification token in constant time too.
- **Credentials.** The token, the app secret and the verification token are read from the environment, never written to disk by this package, and kept out of `repr` and of `Config.redacted()`. `DryRunTransport` logs the *names* of the fields in a withheld write and never their values.
- **The invite link.** It is the only way into a group, so anyone holding it can ask to join. Treat it as a credential, and use `reset_invite_link` when one has been somewhere it should not.

The demo runs on fictional data only: phone numbers in ranges reserved for examples, invented group ids, and an invite link that goes nowhere. No real number, token, app secret, business or person appears anywhere in this repository. Reports about secrets or personal data accidentally committed are especially welcome and will be treated as critical.

## Not in scope

Claims about Meta's own API — its limits, its error codes, its pricing — are documented in `docs/GROUPS_API.md` with the date they were checked and a note wherever Meta does not document something. A correction there is a very welcome issue, not a vulnerability report.

## Supported versions

Only the `main` branch and the latest tagged release receive fixes.
