# The Groups API, as far as it is documented

Notes taken while building this client, kept so the research does not have to be repeated.
Everything here is either quoted from Meta's own reference or marked as not documented. No
vendor page, BSP blog or aggregator is cited anywhere: on this API they are unanimous,
uncited, and wrong about at least one thing that matters.

**Checked against Meta's documentation on 30 September 2026.** Meta's own WhatsApp changelog
stops earlier than that, so the absence of a change entry proves nothing — re-check before
relying on any of it.

## Getting access

The Groups API requires the business phone number to be an **Official Business Account**.
It is unavailable for WhatsApp Business app numbers and for numbers onboarded to
Multi-solution Conversations. Anyone cloning this repository without an OBA cannot run it
against the real API at all, which is why the demo runs offline.

Meta's five criteria for the status:

1. compliance with the WhatsApp Business Messaging Policy
2. registered on the WhatsApp Business Platform for at least 30 days
3. the owning business portfolio verified through Business Verification
4. two-step verification enabled on the business phone number
5. display name approved

Press coverage is not among them, whatever the vendor pages say. A rejected request means
waiting 30 days before submitting another, so it is worth submitting complete.

Status is queried on the phone number node with `?fields=official_business_account`, which
returns `official_business_account` with `oba_status` nested inside it.

Also required: the number active on Cloud API, a configured webhook server, subscription to
the four group webhook fields, the app subscribed to the WABA, and the
`whatsapp_business_messaging` permission.

Eligibility is a property of a phone number, and Meta states it does not grant the status to
test accounts — so there is no sandbox for this API.

> **Dated observation, not a documented behaviour.** Probing a non-OBA number on
> 21 September 2026: `GET /<PHONE_NUMBER_ID>/groups` answered `200` with an empty list, while
> `POST /<PHONE_NUMBER_ID>/groups` failed with code `131215`, "This phone number is not
> eligible to access Groups APIs". Meta documents neither: `131215` appears nowhere in the
> public error reference, on the current or the legacy path, and the Groups reference
> documents only generic 400/401/500. This client recognises the code as one signal among
> several and depends on none of them — see `errors.classify`.

## Limits

| Limit | Value | Source |
| --- | --- | --- |
| Participants per group | 8 | "Max group participants: 8" |
| Groups per business number | 10,000 | "Max groups you can create: 10,000" |
| Cloud API businesses per group | 1 | "Max Cloud API businesses per group: 1" |
| `subject` | 128 characters, required, trimmed | request body table |
| `description` | 2048 characters, optional | request body table |
| Page size on the list endpoint | 1 to 1024, default 25 | request parameters |
| Icon | `image/jpeg`, square, at least 192x192, at most 5 MB | `profile_picture_file` |

**Whether the business number occupies one of the eight places is not documented**, and
Meta's own reference points both ways. The business "is always added to the group as the
creator and admin", and `total_participant_count` is defined as the count "excluding your
business". Every vendor page asserts that seven guests fit; none cites a source.

This client therefore enforces eight, warns at eight, and encodes no constant for seven. The
warning exists because the two ways of being wrong are not equal: if the business does occupy
a place, the eighth invitation is refused, and there is no endpoint that adds a participant
afterwards.

Meta's own sample request for the icon uploads a `.png`, contradicting the `image/jpeg` rule
stated on the same page. This client reads the format from the file's bytes, so neither the
extension nor the example decides.

## Endpoints

Covered by this client:

| What | Call |
| --- | --- |
| Create a group | `POST /<BUSINESS_PHONE_NUMBER_ID>/groups` |
| Update subject, description or icon | `POST /<GROUP_ID>` |
| Get the invite link | `GET /<GROUP_ID>/invite_link` |
| Reset the invite link | `POST /<GROUP_ID>/invite_link` |
| List who is waiting to join | `GET /<GROUP_ID>/join_requests` |
| Approve join requests | `POST /<GROUP_ID>/join_requests` |
| Reject join requests | `DELETE /<GROUP_ID>/join_requests` |
| Remove participants | `DELETE /<GROUP_ID>/participants` |
| Group metadata | `GET /<GROUP_ID>?fields=…` |
| List active groups | `GET /<BUSINESS_PHONE_NUMBER_ID>/groups` |
| Delete a group | `DELETE /<GROUP_ID>` |
| Send to a group | `POST /<PHONE_NUMBER_ID>/messages` with `recipient_type: "group"` |

Documented and **not** covered: the dedicated group-invite-link template message flow, and
pinning or unpinning a message in a group.

### Four bodies carry `messaging_product`

Create, update settings, invite-link reset and remove participants all require
`messaging_product: "whatsapp"` in the body. It is easy to leave out of endpoints that are
not under `/messages`, and the request is then refused by schema validation before
eligibility is even considered — which makes the failure look like a permissions problem and
sends you looking in the wrong place.

### Remove participants takes objects, not strings

```json
{"messaging_product": "whatsapp", "participants": [{"user": "<PHONE_NUMBER or WA_ID>"}]}
```

Not a list of numbers, which is the shape every other participant field in this API uses.
At most 8, and never empty.

### The list endpoint nests its page

```json
{"data": {"groups": [{"id": "…", "subject": "…", "created_at": "…"}]}, "paging": {"cursors": {…}}}
```

The page is under `data.groups`, not under `data`.

### The create endpoint has no documented response body

Meta documents the create request body and the webhook it triggers, and shows no response.
This client parses defensively for a flat `{id}` and for the nested shape the rest of the
page uses, and raises with the raw body if neither matches — because failing *after* the
group exists is the expensive way to fail.

### `join_approval_mode`

`approval_required` or `auto_approve`, defaulting to **`auto_approve`** when omitted: anyone
holding the invite link is in.

This client defaults to `approval_required` instead. That is a deliberate divergence, not an
oversight. For a group that carries a client's business an unapproved join is a
confidentiality incident, and opening that door should be something somebody typed. It only
works if the join-request queue is read, which is why the two are in the same class.

## What the API does not do

**There is no endpoint that adds a participant.** Entry is always by invite link, and the
business phone number that created the group is always in it as creator and admin. There is
no endpoint that promotes or demotes an admin either. Every provisioning design follows from
this: create, set the icon, fetch the link, send the link, and wait.

Unsupported message types, in Meta's words: calling, disappearing messages, view-once,
authentication templates, commerce messages and interactive messages. Unsupported actions:
hiding the participant list, editing a message, deleting a message. Note that deleting a
*message* is unsupported while deleting a *group* is supported.

Supported: text, media, text templates and media templates. Meta does not report performance
metrics for a template used in a group, and advises creating templates specifically for
groups — reuse a one-to-one template and its numbers quietly stop describing the one-to-one
traffic you were reading them for.

## Cost

Meta charges each time a billable message is delivered to someone in the group, at the same
rates as one-to-one traffic. Its worked example is one template to a five-person group,
charged five times. **Group utility messages are excluded from volume tiers**, which is a
Groups-specific carve-out worth knowing before modelling a campaign.

This repository ships no rate table and no figures. Rates are per market and per category,
Meta publishes them as downloadable cards that change, and a number hardcoded in a library
goes wrong silently. Read Meta's pricing page.

The design consequence that does not depend on any rate: **a standing notice belongs in the
group's `description`**, which is not a message at all — no send, no template approval, and
visible to whoever joins tomorrow.

## Graph API versions

The version is configuration, never a constant in the source. Versions expire on a published
schedule, so a client that pins one is wrong from the day it is published, while one that
reads it from the environment is merely out of date and can be corrected without a release.

This client defaults to `v26.0`, current as of Meta's changelog on 30 September 2026. Set
`WA_GRAPH_VERSION` to something else and the requests follow.

## Where the vendor pages go wrong

Three claims that are repeated confidently across BSP and aggregator pages and are not in any
Meta document:

- **"8 participants including the business, so 7 humans."** Not stated anywhere, and Meta's
  own `total_participant_count` definition cuts against it.
- **A field named `is_official_business_account`.** It does not exist. Meta documents
  `official_business_account`, with `oba_status` nested inside it.
- **"1,000 free service conversations a month."** That quota belonged to the conversation
  pricing model, which was replaced by per-message pricing on 1 July 2025.

A fourth one is a trap for the reader rather than a vendor error: anything you read about
what is free is time-limited and changes on announced dates. Check the pricing page with a
date in hand.
