# whatsapp-cloud-api-client

A Python client for the **Groups API** of Meta's WhatsApp Cloud API: create a group, give it
an icon and a description, hand out the invite link, approve who asked to join, and receive
the events. The API is new, parts of it are undocumented, and its failures land somewhere
expensive — a group created without its icon, two groups where there should be one, a client
left outside the group for their own matter with no endpoint that can add them. So this
client checks every documented limit before the request is built, makes group creation
idempotent, treats the invite link as a credential, and defaults to a dry run that records
what it would have sent.

![CI](https://github.com/fillipeml/whatsapp-cloud-api-client/actions/workflows/ci.yml/badge.svg) ![Licence: MIT](https://img.shields.io/badge/licence-MIT-informational)

**Status:** extracted from a production customer-messaging project and rebuilt as a library ·
**Runs offline:** yes, the demo needs no Meta account, no credentials and no network

```
$ wa-groups demo
DEMO — recorded API responses, no network call, no credentials, nothing sent.

1. refuse a guest list that cannot fit
   refused before anything was created: 10 guests for a group documented to hold 8
   participants. Shorten the list or split the group before creating anything.

2. a list that fits, normalised: 5562900000001, 5562900000002, 5562900000003

3. accept a list at the documented maximum, and say what is uncertain about it
   8 guests is the documented maximum, but Meta does not say whether the business phone
   number occupies one of those places. If it does, the last invitation is refused, and
   there is no endpoint that adds a participant afterwards.

4. create the group, idempotently
  [dry run] POST 000000000000000/groups json=['description', 'join_approval_mode',
            'messaging_product', 'subject']
   created: dry-run-group-id
   run again -> already existed: dry-run-group-id

5. inspect the group that the recordings describe
   120363000000000001@g.us
     subject     Example Ltd — matter 4470
     joining     needs approval
     people      3 besides the business

6. the invite link, which is the only way in
   https://chat.whatsapp.com/ExampleInviteCode01

7. who is waiting for approval
   15550000004
   15550000005

8. writes withheld by the dry run: 1
   POST 000000000000000/groups json=['description', 'join_approval_mode',
        'messaging_product', 'subject']
```

## Install

```bash
pip install -e .          # or: uv sync
wa-groups demo            # offline, no credentials
wa-groups limits          # what this build enforces, and where each number came from
```

For a real run you need a phone number with Official Business Account status — the Groups API
is unavailable without it, and Meta does not grant it to test accounts, so there is no
sandbox. See [docs/GROUPS_API.md](docs/GROUPS_API.md).

```bash
export WA_PHONE_NUMBER_ID=... WA_TOKEN=...
wa-groups list                                   # still a dry run: reads are real, writes are not
export WA_DRY_RUN=false
wa-groups create --subject "Example Ltd — matter 4471"
```

```python
from whatsapp_cloud import Config, GroupsClient, HttpTransport, JsonFileIndex

config = Config(phone_number_id="...", token="...", dry_run=False)
groups = GroupsClient(config.phone_number_id, HttpTransport(config),
                      index=JsonFileIndex("groups.json"))

guests = GroupsClient.check_guest_list(["+55 62 90000-0000", "+55 62 90000-0001"])
group = groups.create_idempotent("Example Ltd — matter 4471",
                                 description="This group is for matter 4471 only.")
groups.set_icon(group.id, "icons/example.jpg")
link = groups.invite_link(group.id)
```

## What it covers

Create a group, update its subject, description and icon, get and reset the invite link,
list and approve and reject join requests, remove participants, read group metadata, list a
number's groups, delete a group, and send templates and text to a group or to one person.
Plus webhook verification, parsing and dispatch.

Documented and **not** covered: the dedicated group-invite-link template message flow, and
pinning or unpinning a message.

## Design decisions

**The dry run is a transport, not a flag.** A boolean consulted deep in the call stack is one
forgotten branch away from sending for real. `DryRunTransport` cannot reach the network
whatever the code above it does, it records every withheld write so a script can be inspected
rather than trusted, and it passes *reads* through to the live API — so a provisioning script
can be pointed at production and watched, and the only thing it cannot do is change anything.
It is also the default: sending for real has to be typed.

**Idempotent creation, with the index as an optimisation and never as the truth.** Create the
same subject twice and you get two groups, and the client ends up in the one nobody is
watching. The fix is to look before creating — but that lists every group the number owns,
and a number may own ten thousand. So an optional index maps subject to group id, and an id
it returns is *confirmed against the API* before it is used. A group can be deleted or
suspended between runs, and returning a stale id would be worse than the scan this avoids.

**The limits are checked before the request is built.** Not for speed. A group is created and
*then* given its icon, so an icon rejected by Meta leaves a half-provisioned group and a retry
that has to know it. Validating locally turns that into an error raised before anything
existed. Same for the guest list: discovering it is too long on the last invitation means
somebody is outside the group for their own matter, and there is no endpoint that adds them.

**Where Meta is silent, this client says so rather than choosing.** Every vendor page states
that a group holds the business plus seven people. Meta states "Max group participants: 8" and
separately defines `total_participant_count` as the count "excluding your business", and never
says which reading is right. So the client enforces eight, *warns* at eight, and encodes no
constant for seven — because a library that quietly bakes in an unverified number is how that
number becomes everyone's assumption.

**Errors branch on status first.** Meta documents HTTP statuses for these endpoints and does
not document the numeric codes, so branching the other way would rest the error model on the
least certain part. A 429 or a 5xx is about the moment and is retried, honouring `Retry-After`
when it is sent; a 400 will be a 400 next time and is not. The one recognised code, for a
number that is not eligible, is treated as a signal and not a contract, with the probe date
and its absence from the public reference recorded next to it.

**The webhook is functions over bytes.** The hard parts — verifying a signature over the raw
body, echoing an opaque challenge, telling an aggregated `statuses` array apart from one
status — are easier to get right and much easier to test as functions than as a route. The
standard-library server in `server.py` is the thin part and a worked example.

**No rates, no rate table, no prices.** Rates are per market and per category, Meta publishes
them as files that change, and a figure hardcoded in a library goes wrong silently. The client
reports that one group message is billed once per recipient, which is Meta's own worked
example, and points at the pricing page for the rest.

## The things that cost a day each

Collected in [docs/GROUPS_API.md](docs/GROUPS_API.md) with their sources, because none of them
is where you would look:

- Four writes require `messaging_product: "whatsapp"` in the body — including endpoints that
  are not under `/messages`. Leave it out and the request is refused by schema validation
  *before* eligibility is considered, so the failure reads as a permissions problem.
- `DELETE /<GROUP_ID>/participants` takes an array of **objects** keyed `user`, not an array
  of numbers, which is the shape every other participant field uses.
- The list endpoint nests its page under `data.groups`, not under `data`.
- Meta documents **no response body** for creating a group.
- `join_approval_mode` defaults to `auto_approve` when omitted: anyone with the link is in.
  This client defaults to the opposite and says so.
- There is no endpoint that adds a participant, and none that promotes an admin. Entry is
  always by invite link, which makes the link a credential and makes the whole provisioning
  flow follow from it.
- Delivery and read statuses arrive **aggregated** — one webhook can carry many participants'
  statuses for one message. Code that reads the first element under-counts reads, and nothing
  about the resulting number looks wrong. See [docs/WEBHOOKS.md](docs/WEBHOOKS.md).

## Tests

```bash
pytest          # 151 tests, all offline
ruff check .
ruff format --check .
```

Nothing in the suite touches the network. The transport tests run the real `httpx` client
against `respx`, with the sleep injected so the backoff arithmetic is checked without waiting
for it; everything above the transport runs against recorded responses, and a recording can
express a failure as well as a success, because error handling is half of what a client does.

Several tests exist specifically to pin a shape that is not the one you would guess — the
participant objects, the nested page, the four bodies that carry `messaging_product` — and say
so in a comment, so a future change that "simplifies" one of them fails with an explanation.

## Fictional data

Every phone number in the fixtures is in a range reserved for examples, every group id is
invented, and the invite link goes nowhere. No real number, token, app secret, business or
person appears anywhere in the repository.

## Built with

Python 3.12, httpx, Pillow, pytest and respx. Standard library only for the webhook server.
Developed with an AI coding assistant; the API facts were checked against Meta's own
documentation rather than taken from the model or from vendor pages, and what could not be
confirmed is marked as unconfirmed throughout.

## Licence

MIT — see [LICENSE](LICENSE).
