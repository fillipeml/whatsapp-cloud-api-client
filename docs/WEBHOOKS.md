# Receiving group events

Webhooks are not optional for the Groups API. Without a public HTTPS endpoint that answers
the verification challenge, none of the group events are delivered at all.

```bash
export WA_APP_SECRET=... WA_VERIFY_TOKEN=...
wa-groups webhook-serve --port 8000
```

That runs the standard-library endpoint in `whatsapp_cloud.server`. It binds to localhost,
because Meta needs a public HTTPS URL and a real deployment puts this behind a reverse proxy
or a tunnel — a default of `0.0.0.0` would quietly invite somebody to skip that step.

To run it inside an existing application instead, use the three functions the server is made
of. They are framework-agnostic and take bytes:

```python
from whatsapp_cloud import handle_delivery, verify_challenge

# GET: the one-time handshake
challenge = verify_challenge(request.query_params, verify_token)  # echo it, or refuse

# POST: everything else
result = handle_delivery(
    raw_body,
    request.headers.get("X-Hub-Signature-256"),
    app_secret,
    on_event=store_event,
    on_status=count_status,
)
return Response(result.body, status_code=result.status)
```

## Four things that are easy to get wrong

### The signature covers the bytes you received

Meta's page says the signature covers "the JSON payload", which is true and not precise
enough. A framework that parses the body and hands you a dictionary has thrown away the exact
bytes; re-serialising produces different ones, the signature stops matching, and the bug looks
exactly like a wrong app secret. Read the raw body first.

This client compares in constant time and supports only SHA-256, with no SHA-1 fallback. Both
are its own choices rather than something Meta asks for: an endpoint that leaks how much of a
signature was correct can be walked a byte at a time, and accepting a weaker signature as a
courtesy makes the weaker one the one that gets used.

A missing app secret means nothing can be authenticated, so **everything is refused**. The
alternative — treating an unset variable as "skip the check" — turns a public URL into an open
event injector the first time a deployment forgets a variable.

### All four subscriptions deliver the same envelope

`group_lifecycle_update`, `group_participants_update`, `group_settings_update` and
`group_status_update` all arrive as:

```json
{"object": "whatsapp_business_account",
 "entry": [{"changes": [{"field": "group_participants_update",
   "value": {"messaging_product": "whatsapp",
             "metadata": {"display_phone_number": "…", "phone_number_id": "…"},
             "groups": [{"timestamp": "…", "group_id": "…", "type": "participant_joined"}]}}]}]}
```

The thing that says what happened is **`value.groups[].type`**, not `changes[].field`. A
parser that dispatches on the field alone collapses distinct events into one bucket, and one
delivery can carry several entries in `groups[]` — a join and a leave arrive together.

`whatsapp_cloud.webhook.parse_events` returns one `GroupEvent` per entry, each carrying its
field, its type, its group and its timestamp.

### Delivery and read statuses are aggregated

They do not arrive on those four fields. They come in a `statuses` array, and Meta aggregates:
one payload may carry many participants' statuses for one message, or many messages' statuses
for one participant. Reading only the first element under-counts; assuming one recipient per
payload over-counts. Either way the number that reaches a dashboard is wrong, and nothing
about it looks wrong.

`parse_events` returns every status separately, and each one carries a `dedupe_key` of
`(message_id, recipient_id, status)`.

### The same event arrives more than once

Meta retries a delivery it did not get a 2xx for — immediately, then with decreasing
frequency, for up to seven days. Meta publishes no schedule, so this client hardcodes none.

Anything that counts, bills or notifies has to deduplicate. `GroupEvent.dedupe_key` and
`StatusUpdate.dedupe_key` are stable across redeliveries.

## What to answer

| Situation | Status | Why |
| --- | --- | --- |
| The signature does not verify | **403** | Not from Meta, and nothing is parsed |
| Authentic, and unreadable | **400** | Meta's guidance for this endpoint is a 400-level status for a request it cannot accept |
| Authentic, understood, and the handler raised | **500** | It might work next time; that is what the retries are for |
| Received and handled | **200** | Reserved for exactly that |

The 400 is worth dwelling on, because the opposite advice is widespread: answering 200 to a
payload you could not process, so that the retries stop. That reports a failure as a success,
and it is a community workaround rather than vendor guidance.

The 500 is the deliberate other half. A handler that failed because a database was briefly
unavailable *should* see the event again, and leaving it to Meta's retries is cheaper and more
reliable than building a queue to do the same thing.

## Which event is worth storing

`group_participants_update`. It carries the moment a person entered the group, and nothing the
API exposes can reconstruct that afterwards — so if onboarding time is a number anyone will
ever ask about, it has to be captured when it arrives.

## Trying it without a webhook

```bash
wa-groups webhook-replay fixtures/webhooks/participants-update.json --tamper
wa-groups webhook-replay fixtures/webhooks/aggregated-statuses.json
```

The payloads are signed with a throwaway secret and run through the same verification and
parsing path a real delivery meets. `--tamper` re-runs the same payload with a signature from
a different secret, which must be refused.
