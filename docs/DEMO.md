# The demo

```bash
uv sync            # or: pip install -e ".[dotenv]"
wa-groups demo
```

No Meta account, no credentials, no network call. The API responses come from
`fixtures/recorded.json` and the writes are withheld, but everything between those two ends
is the real client: the guest-list guard, the phone normalisation, the idempotent create, the
pagination, the response parsing and the dry-run recording.

## What each step is showing

**1. A guest list that cannot fit is refused before anything exists.** Ten guests for a group
documented to hold eight. The error arrives with no group created, which is the point: there
is no endpoint that adds a participant, so discovering the problem after the group exists
means somebody stays outside it.

**2. Three numbers written three different ways become three sets of digits.** Brackets,
spaces, a dash and a leading `00` country prefix all disappear. A cell holding an extension or
a truncated number would have been refused rather than silently sent somewhere.

**3. Eight guests is accepted, with a warning rather than a refusal.** Meta publishes "Max
group participants: 8" and never says whether the business number occupies one of those
places. Every vendor page says it does; none cites a source. So the client enforces the number
Meta publishes and hands the uncertainty to the caller instead of resolving it quietly.

**4. Creating twice produces one group.** The first call scans, finds nothing and creates.
The second finds the group in the index, confirms it still exists, and returns it. A
provisioning run gets re-run — a retry after a timeout, a double click, a queue redelivering —
and the second group is the one nobody is watching.

**5, 6, 7. Reading.** Group metadata, the invite link, and who is waiting for approval.
`people 3 besides the business` comes from Meta's `total_participant_count`, which is defined
as the count excluding the business, so nothing is subtracted from it.

**8. One write was withheld.** The count is the whole claim the dry run makes, and the
withheld request is printed with its field *names* and not its values — enough to review a
provisioning script, not enough to leak a client's data into a log.

## The webhook, without a webhook

```bash
wa-groups webhook-replay fixtures/webhooks/participants-update.json --tamper
wa-groups webhook-replay fixtures/webhooks/aggregated-statuses.json
```

The first signs a saved payload with a throwaway secret, runs it through the same verification
and parsing a delivery from Meta meets, and then re-runs it signed with a *different* secret,
which must be refused with 403.

The second is the one worth looking at. It is a single delivery carrying six statuses: one
message read by three participants and delivered to a fourth, plus a second message delivered
to two. Meta aggregates, and code that reads the first element of `statuses` reports one read
where there were three — with nothing about the number looking wrong. See
[WEBHOOKS.md](WEBHOOKS.md).

## Trying it for real

```bash
export WA_PHONE_NUMBER_ID=... WA_TOKEN=...
wa-groups limits          # what this build enforces
wa-groups list            # reads are real; WA_DRY_RUN is still true, so writes are not
```

Leaving the dry run on is the useful middle state: reads go to the live API while every write
is recorded and withheld, so a provisioning script can be pointed at production and read
before it is trusted. `WA_DRY_RUN=false` is the only thing that changes that.

The Groups API needs a phone number with Official Business Account status, which Meta does not
grant to test accounts. Without it, `GET` may answer and `POST /groups` will not — see
[GROUPS_API.md](GROUPS_API.md) for what that failure looks like and why this client treats it
as a stop rather than something to retry.

## The fixtures

`fixtures/recorded.json` maps `"<METHOD> <path>"` to a queue of responses; a key with several
entries replays them in order and repeats the last, so a caller that polls does not exhaust
it. An entry of the form `{"__error__": {"status": 404, "body": "..."}}` raises instead of
returning, because a fixture set that can only express success can only test the half of a
client that rarely goes wrong.

Every phone number is in a range reserved for examples, every group id is invented, and the
invite link goes nowhere.
