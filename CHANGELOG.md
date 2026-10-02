# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-09-30

First public release: a client for the Groups API of Meta's WhatsApp Cloud API, extracted from a production customer-messaging project and rebuilt as a library with an offline demo.

### Added

- Group lifecycle behind one client: create, update subject, description and icon, get and reset the invite link, list, approve and reject join requests, remove participants, read metadata, list a number's groups, and delete a group.
- Idempotent creation. Creating the same subject twice returns the existing group rather than a second one, with an optional index (in memory or a JSON file, written atomically) as the fast path and a full scan as the fallback. An id the index returns is confirmed against the API before it is used, so a group deleted or suspended between runs is never handed back.
- Every documented limit checked before the request is built: subject and description lengths measured after trimming as Meta measures them, page size within the documented range, guest lists against the published cap of 8, and group icons validated from the file's bytes for format, squareness, minimum side and size.
- The undocumented part of the participant cap handled as undocumented. Meta publishes a maximum of 8 and never says whether the business number occupies one of those places, so the client enforces 8, warns at 8, and ships no constant for 7.
- Request shapes that are not the obvious ones, each pinned by a test: `messaging_product` in the body of create, update settings, invite-link reset and remove participants; `DELETE /<GROUP_ID>/participants` taking an array of objects keyed `user`; the list endpoint's page nested under `data.groups`; and defensive parsing of the create response, for which Meta documents no body.
- Four transports behind one protocol: the real HTTP one with retries on 429 and 5xx that honour `Retry-After`, a dry-run one that records withheld writes while passing reads through to the live API, a recorded one that replays captured responses and can also express a failure, and a failing one for testing error handling.
- Webhook handling as pure functions over bytes: HMAC-SHA256 verification over the raw body in constant time with no SHA-1 fallback, refusing everything when no secret is configured; the verification handshake with the challenge echoed unchanged; parsing that dispatches on `value.groups[].type` rather than on the subscription field; and aggregation-aware status parsing that returns every status with a key stable across the seven days Meta may retry.
- Webhook responses that mean what they say: 403 for an unverified signature, 400 for a body that cannot be accepted, 500 when a handler raised so the retry is used, and 200 only for received-and-handled.
- A standard-library webhook server, and a command line covering the group lifecycle plus `limits`, `check-icon`, `check-guests`, `cost`, `webhook-replay` and an offline `demo`.
- Typed boundary models that keep their raw payload, with `total_participant_count` used as Meta defines it rather than adjusted.
- `docs/GROUPS_API.md`, recording what Meta documents, what was observed on a dated probe, and what Meta does not document at all — including three claims repeated across vendor pages that appear in no Meta document.
- 153 offline tests, a Dependabot configuration, and CI that lints, tests, and asserts the demo's behaviour, the printed limits, a refused tampered webhook, a PNG caught by its bytes, and that the dry run is still the default.

[0.1.0]: https://github.com/fillipeml/whatsapp-cloud-api-client/releases/tag/v0.1.0
