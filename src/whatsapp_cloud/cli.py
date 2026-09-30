"""A command line for the endpoints, and a demo that needs no credentials.

Two audiences. Someone who has just been granted access to the Groups API and wants to prove
the chain works before any provisioning code depends on it — that is ``demo``'s real-world
twin, the commands below run one at a time. And someone who has cloned this repository and
wants to see what it does without a Meta account, which is what ``demo`` is for.

Nothing writes to the API unless ``WA_DRY_RUN=false`` is set. The banner says which mode is
in force on every run, because a tool whose destructive mode looks exactly like its safe mode
is one flag away from an incident.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import media
from .config import Config, from_env
from .errors import ConfigurationError, WhatsAppError
from .groups import GroupsClient
from .index import JsonFileIndex, MemoryIndex
from .limits import DOCUMENTED_LIMITS
from .messages import MessagesClient
from .models import GroupInfo
from .transport import DryRunTransport, HttpTransport, RecordedTransport, Transport
from .webhook import GROUP_FIELDS, handle_delivery, sign

RECORDINGS = Path(__file__).resolve().parent.parent.parent / "fixtures" / "recorded.json"
DEMO_PHONE_NUMBER_ID = "000000000000000"


def _out(line: str = "") -> None:
    print(line, flush=True)


# --------------------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------------------


def _transport(config: Config) -> Transport:
    """The dry run reads for real and withholds every write.

    That combination is the point: a provisioning script can be pointed at production and
    watched, and the only thing it cannot do is change anything.
    """
    live = HttpTransport(config)
    if not config.dry_run:
        _out("[live] writes WILL be sent to Meta.")
        return live
    _out(
        "[dry run] reads are real; every write is recorded and withheld. WA_DRY_RUN=false to send."
    )
    return DryRunTransport(reads=live, log=_out)


def _client(args: argparse.Namespace) -> GroupsClient:
    config = from_env()
    index = JsonFileIndex(args.index) if getattr(args, "index", None) else None
    return GroupsClient(config.phone_number_id, _transport(config), index=index)


# --------------------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------------------


def cmd_limits(_args: argparse.Namespace) -> None:
    """What this build enforces, and where each number came from."""
    width = max(len(limit.name) for limit in DOCUMENTED_LIMITS)
    for limit in DOCUMENTED_LIMITS:
        value = f"{limit.value:,}" if isinstance(limit.value, int) else limit.value
        unit = f" {limit.unit}" if limit.unit else ""
        _out(f"{limit.name.rjust(width)} : {value}{unit}")
        _out(f"{' '.rjust(width)}   {limit.source}")


def cmd_list(args: argparse.Namespace) -> None:
    client = _client(args)
    found = 0
    for group in client.list_groups(limit=args.limit):
        found += 1
        _out(f"{group.id}  {group.subject}")
    if not found:
        _out("no active groups on this number")


def cmd_create(args: argparse.Namespace) -> None:
    client = _client(args)
    description = (
        Path(args.description_file).read_text(encoding="utf-8") if args.description_file else None
    )
    result = client.create_idempotent(
        args.subject, description=description, require_approval=not args.auto_approve
    )
    _out(f"{'created' if result.created else 'already existed'}: {result.id}")


def cmd_icon(args: argparse.Namespace) -> None:
    client = _client(args)
    client.set_icon(args.group, args.file)
    _out("icon accepted")


def cmd_check_icon(args: argparse.Namespace) -> None:
    """Every reason an image would be refused, rather than only the first."""
    found = media.problems(args.file)
    if not found:
        facts = media.inspect(args.file)
        _out(f"ok: {facts.image_format} {facts.width}x{facts.height}, {facts.size_bytes:,} bytes")
        return
    for problem in found:
        _out(f"- {problem}")
    raise SystemExit(1)


def cmd_link(args: argparse.Namespace) -> None:
    client = _client(args)
    link = client.reset_invite_link(args.group) if args.reset else client.invite_link(args.group)
    if args.reset:
        _out("previous links no longer work")
    _out(link.url)


def cmd_info(args: argparse.Namespace) -> None:
    client = _client(args)
    _describe(client.info(args.group))


def cmd_requests(args: argparse.Namespace) -> None:
    client = _client(args)
    pending = client.join_requests(args.group)
    if not pending:
        _out("nobody is waiting")
        return
    for person in pending:
        _out(person.phone_number)


def cmd_approve(args: argparse.Namespace) -> None:
    client = _client(args)
    client.approve_join_requests(args.group, args.number)
    _out(f"approved: {', '.join(args.number)}")


def cmd_remove(args: argparse.Namespace) -> None:
    client = _client(args)
    client.remove_participants(args.group, args.number)
    _out(f"removed: {', '.join(args.number)}")


def cmd_check_guests(args: argparse.Namespace) -> None:
    """Refuses a guest list before anything is created."""
    digits = GroupsClient.check_guest_list(args.number)
    _out(f"{len(digits)} guest(s) fit:")
    for number in digits:
        _out(f"  {number}")


def cmd_cost(args: argparse.Namespace) -> None:
    """How many sends one group message is billed as."""
    billable = MessagesClient.billable_sends(args.recipients)
    _out(f"one message to {args.recipients} recipient(s) is billed as {billable} send(s)")
    _out(
        "group utility messages are excluded from volume tiers, so the per-message rate is the rate"
    )
    _out("a standing notice belongs in the group's description, which is not a message at all")


def cmd_webhook_serve(args: argparse.Namespace) -> None:
    from .server import log_event, serve

    config = from_env(require_credentials=False)
    if not config.app_secret or not config.verify_token:
        raise SystemExit(
            "WA_APP_SECRET and WA_VERIFY_TOKEN are required to receive webhooks. "
            "Without the secret nothing can be authenticated, and this endpoint refuses "
            "every delivery rather than trusting it."
        )
    serve(
        config.app_secret, config.verify_token, host=args.host, port=args.port, on_event=log_event
    )


def cmd_webhook_replay(args: argparse.Namespace) -> None:
    """Runs a saved payload through the real verification and parsing path.

    Signs it with a throwaway secret first, so the signature check being exercised is the
    same code a delivery from Meta meets.
    """
    body = Path(args.file).read_bytes()
    secret = "replay-secret"
    result = handle_delivery(body, sign(body, secret), secret, on_event=_print_event)
    _out(f"-> {result.status} {result.reason}")
    if args.tamper:
        tampered = handle_delivery(body, sign(body, "a-different-secret"), secret)
        _out(f"-> with a wrong signature: {tampered.status} {tampered.reason}")


def _print_event(event) -> None:  # noqa: ANN001 - GroupEvent, kept loose for the CLI
    _out(f"  {event.field_name}  group={event.group_id or '(not in payload)'}")


def cmd_demo(_args: argparse.Namespace) -> None:
    """The whole provisioning chain, offline, against recorded responses.

    Reads answer from the recordings and writes are withheld, so this exercises the real
    client — the limit checks, the idempotency, the pagination, the error classification —
    without a Meta account.
    """
    _out("DEMO — recorded API responses, no network call, no credentials, nothing sent.")
    _out()

    recorded = RecordedTransport.from_file(RECORDINGS)
    transport = DryRunTransport(reads=recorded, log=lambda line: _out(f"  {line}"))
    index = MemoryIndex()
    groups = GroupsClient(DEMO_PHONE_NUMBER_ID, transport, index=index)

    _out("1. refuse a guest list that cannot fit")
    too_many = [f"+55 62 9{n:04d}-0000" for n in range(10)]
    try:
        GroupsClient.check_guest_list(too_many)
    except WhatsAppError as error:
        _out(f"   refused before anything was created: {error}")
    _out()

    guests = GroupsClient.check_guest_list(
        ["+55 (62) 90000-0001", "+55 62 90000-0002", "005562900000003"]
    )
    _out(f"2. a list that fits, normalised: {', '.join(guests)}")
    _out()

    _out("3. accept a list at the documented maximum, and say what is uncertain about it")
    at_the_cap = [f"+55 62 9{n:04d}-1111" for n in range(8)]
    GroupsClient.check_guest_list(at_the_cap)
    _out(f"   {GroupsClient.guest_list_warning(at_the_cap)}")
    _out()

    _out("4. create the group, idempotently")
    first = groups.create_idempotent(
        "Example Ltd — matter 4471",
        description="Standing notice: this group is for matter 4471 only.",
    )
    _out(f"   {'created' if first.created else 'already existed'}: {first.id}")
    again = groups.create_idempotent("Example Ltd — matter 4471")
    _out(f"   run again -> {'created' if again.created else 'already existed'}: {again.id}")
    _out()

    _out("5. inspect the group that the recordings describe")
    _describe(groups.info("120363000000000001@g.us"), indent="   ")
    _out()

    _out("6. the invite link, which is the only way in")
    _out(f"   {groups.invite_link('120363000000000001@g.us').url}")
    _out()

    _out("7. who is waiting for approval")
    for person in groups.join_requests("120363000000000001@g.us"):
        _out(f"   {person.phone_number}")
    _out()

    _out(f"8. writes withheld by the dry run: {len(transport.writes)}")
    for call in transport.writes:
        _out(f"   {call.describe()}")


def _describe(info: GroupInfo, indent: str = "") -> None:
    _out(f"{indent}{info.id}")
    _out(f"{indent}  subject     {info.subject}")
    _out(f"{indent}  description {info.description or '(none)'}")
    _out(
        f"{indent}  joining     {'needs approval' if info.approval_required else 'open with the link'}"
    )
    _out(f"{indent}  people      {info.participant_count} besides the business")
    if info.suspended:
        _out(f"{indent}  SUSPENDED by moderation")
    if info.created_at:
        _out(f"{indent}  created     {info.created_at.date().isoformat()}")


# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wa-groups", description="WhatsApp Cloud API — Groups")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str, needs_index: bool = False) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        if needs_index:
            p.add_argument("--index", help="JSON file remembering subject -> group id")
        return p

    p = add("limits", "the limits this build enforces, with their provenance")
    p.set_defaults(func=cmd_limits)

    p = add("demo", "the whole chain offline, against recorded responses")
    p.set_defaults(func=cmd_demo)

    p = add("list", "list the number's active groups", needs_index=True)
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_list)

    p = add("create", "create a group, or return the one with this subject", needs_index=True)
    p.add_argument("--subject", required=True)
    p.add_argument("--description-file", help="a .txt file holding the description")
    p.add_argument(
        "--auto-approve",
        action="store_true",
        help="anyone with the link joins without approval (not the default, on purpose)",
    )
    p.set_defaults(func=cmd_create)

    p = add("icon", "set the group's picture", needs_index=True)
    p.add_argument("--group", required=True)
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_icon)

    p = add("check-icon", "report every reason an image would be refused")
    p.add_argument("file")
    p.set_defaults(func=cmd_check_icon)

    p = add("link", "get the invite link, or reset it", needs_index=True)
    p.add_argument("--group", required=True)
    p.add_argument("--reset", action="store_true", help="invalidate every previous link")
    p.set_defaults(func=cmd_link)

    p = add("info", "the group's metadata", needs_index=True)
    p.add_argument("--group", required=True)
    p.set_defaults(func=cmd_info)

    p = add("requests", "who is waiting to be let in", needs_index=True)
    p.add_argument("--group", required=True)
    p.set_defaults(func=cmd_requests)

    p = add("approve", "let waiting numbers in", needs_index=True)
    p.add_argument("--group", required=True)
    p.add_argument("--number", action="append", required=True)
    p.set_defaults(func=cmd_approve)

    p = add("remove", "remove participants", needs_index=True)
    p.add_argument("--group", required=True)
    p.add_argument("--number", action="append", required=True)
    p.set_defaults(func=cmd_remove)

    p = add("check-guests", "refuse a guest list before creating anything")
    p.add_argument("--number", action="append", required=True)
    p.set_defaults(func=cmd_check_guests)

    p = add("cost", "how many sends one group message is billed as")
    p.add_argument("--recipients", type=int, required=True)
    p.set_defaults(func=cmd_cost)

    p = add("webhook-serve", f"receive events ({', '.join(sorted(GROUP_FIELDS))})")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_webhook_serve)

    p = add("webhook-replay", "run a saved payload through verification and parsing")
    p.add_argument("file")
    p.add_argument("--tamper", action="store_true", help="also try it with a wrong signature")
    p.set_defaults(func=cmd_webhook_replay)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (WhatsAppError, ConfigurationError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except FileNotFoundError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
