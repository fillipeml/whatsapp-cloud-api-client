# CLAUDE.md

Working rules for AI-assisted changes in this repository. They mirror the README; the README
wins on conflict.

## Non-negotiable rules

1. **Never state something about Meta's API that Meta does not.** `docs/GROUPS_API.md` marks
   every claim as quoted, dated-observation or not-documented, and the code mirrors that. No
   BSP, vendor or aggregator page is ever a citation: on this API they are unanimous, uncited
   and wrong about at least one thing. If a fact cannot be confirmed from Meta's own
   documentation, say "not documented" rather than picking the plausible answer.
2. **Do not encode the seven-guest reading.** Meta publishes a cap of 8 and never says whether
   the business occupies a place. `limits.py` enforces 8 and warns at 8; a constant for 7
   would bake an unverified third-party claim into the public API surface.
3. **The dry run stays a transport, and stays the default.** Nothing above `transport.py` may
   read a dry-run flag, and `Config.dry_run` defaults to True. A flag consulted deep in the
   stack is one forgotten branch away from sending to a real person.
4. **Four bodies carry `messaging_product`** — create, update settings, invite-link reset and
   remove participants — and no others, because those are the four Meta documents it for.
   `DELETE /<GROUP_ID>/participants` takes objects keyed `user`, not strings. The list
   endpoint's page is under `data.groups`. Tests pin all three; if one is changed, change the
   test with an explanation rather than relaxing it.
5. **Errors branch on HTTP status first.** Meta documents statuses for these endpoints and
   not the numeric codes. `131215` is one recognised signal, never a contract, and an
   unrecognised code must fall through to an `ApiError` that preserves Meta's own code and
   message.
6. **The Graph version is configuration.** No version string anywhere except
   `config.DEFAULT_GRAPH_VERSION` — not in tests, not in fixtures, not in prose.
7. **The webhook answers 403 / 400 / 500 / 200, and never 200 to silence a retry.** 403 for an
   unverified signature, 400 for a body that cannot be accepted, 500 when a handler raised so
   Meta retries, 200 only for received-and-handled. Signature verification is over the raw
   bytes, constant-time, SHA-256 only, and refuses everything when no secret is configured.
8. **Statuses are aggregated.** Anything that reads `statuses` must handle many per delivery
   and deduplicate, because Meta retries for up to seven days.
9. **No rates, no prices, no rate table.** One group message is billed once per recipient;
   everything else belongs on Meta's pricing page.
10. **Fixtures stay fictional.** Example-range phone numbers, invented group ids, an invite
    link that goes nowhere. Never a real number, token, secret, business or person.

## Conventions

- Python 3.12, httpx, Pillow. Standard library only for the webhook server. `pytest` and
  `respx` for tests, `ruff check` and `ruff format` for style.
- The public surface is `whatsapp_cloud/__init__.py`; anything exported there is covered by a
  test.
- Boundary types are frozen dataclasses that keep their `raw` payload, because this API is new
  enough that the field you discarded is the one you need next month.
- Tests run offline. `respx` stands in for the network in the transport tests with the sleep
  injected; everything else runs against recorded responses.
- A test that pins a counter-intuitive shape says in a comment why it is counter-intuitive.
- Never commit `.env*` (except `.env.example`) or `.demo/`.
- Commits: English, Conventional Commits, one logical change each, no AI attribution trailers.
