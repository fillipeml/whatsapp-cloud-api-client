"""A typed Python client for the WhatsApp Cloud API's Groups API.

The short version of what this package is for: the Groups API is new, the documentation
leaves several things unsaid, and the failures land somewhere expensive — a group created
without its icon, a client outside the group for their own matter, two groups where there
should be one. So the limits are checked before the request is built, creation is idempotent,
the invite link is treated as a credential, and the default is a dry run.

Typical use::

    from whatsapp_cloud import Config, GroupsClient, HttpTransport

    config = Config(phone_number_id="...", token="...", dry_run=False)
    groups = GroupsClient(config.phone_number_id, HttpTransport(config))

    guests = GroupsClient.check_guest_list(["+55 62 90000-0000", "+55 62 90000-0001"])
    group = groups.create_idempotent("Example Ltd — matter 4471")
    groups.set_icon(group.id, "icons/acme.jpg")
    link = groups.invite_link(group.id)

Nothing above sends anything while ``dry_run`` is True, which it is unless you say otherwise.
"""

from __future__ import annotations

from .config import DEFAULT_GRAPH_VERSION, Config, from_env
from .errors import (
    ApiError,
    ConfigurationError,
    InvalidIconError,
    InvalidPhoneNumberError,
    LimitExceededError,
    NotEligibleForGroupsError,
    WhatsAppError,
)
from .groups import APPROVAL_REQUIRED, AUTO_APPROVE, GroupsClient
from .index import GroupIndex, JsonFileIndex, MemoryIndex, NoIndex
from .limits import (
    DOCUMENTED_LIMITS,
    MAX_DESCRIPTION_CHARS,
    MAX_PARTICIPANTS,
    MAX_SUBJECT_CHARS,
    capacity_warning,
)
from .messages import MessagesClient
from .models import CreatedGroup, Group, GroupInfo, InviteLink, Participant, SendResult
from .transport import (
    DryRunTransport,
    FailingTransport,
    HttpTransport,
    RecordedTransport,
    Request,
    Transport,
)
from .webhook import (
    GROUP_FIELDS,
    GroupEvent,
    ParsedPayload,
    StatusUpdate,
    WebhookResponse,
    handle_delivery,
    parse_events,
    sign,
    verify_challenge,
    verify_signature,
)

__version__ = "0.1.0"

__all__ = [
    "APPROVAL_REQUIRED",
    "AUTO_APPROVE",
    "ApiError",
    "Config",
    "ConfigurationError",
    "CreatedGroup",
    "DEFAULT_GRAPH_VERSION",
    "DOCUMENTED_LIMITS",
    "DryRunTransport",
    "FailingTransport",
    "GROUP_FIELDS",
    "Group",
    "GroupEvent",
    "GroupIndex",
    "GroupInfo",
    "GroupsClient",
    "HttpTransport",
    "InvalidIconError",
    "InvalidPhoneNumberError",
    "InviteLink",
    "JsonFileIndex",
    "LimitExceededError",
    "MAX_DESCRIPTION_CHARS",
    "MAX_PARTICIPANTS",
    "MAX_SUBJECT_CHARS",
    "MemoryIndex",
    "MessagesClient",
    "NoIndex",
    "NotEligibleForGroupsError",
    "ParsedPayload",
    "Participant",
    "RecordedTransport",
    "Request",
    "SendResult",
    "StatusUpdate",
    "Transport",
    "WebhookResponse",
    "WhatsAppError",
    "__version__",
    "from_env",
    "capacity_warning",
    "handle_delivery",
    "parse_events",
    "sign",
    "verify_challenge",
    "verify_signature",
]
