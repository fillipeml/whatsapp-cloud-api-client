"""How a request reaches Meta, and the three ways it can be made not to.

One protocol, four implementations:

``HttpTransport``      the real one: authentication, timeouts, retries.
``DryRunTransport``    records what would have been sent and answers plausibly.
``RecordedTransport``  replays responses captured from a real run, for the offline demo.
``FailingTransport``   fails on demand, so retry and error handling are testable.

Everything above this layer takes a transport as an argument and cannot tell which it has.
That is what lets the whole client be exercised — the limit checks, the idempotency, the
pagination, the error classification — with no credentials and no network.

The dry run is a transport rather than an ``if`` inside the HTTP call. A flag consulted deep
in the stack is one forgotten branch away from sending for real; a transport that cannot
reach the network cannot send for real however the code above it is written.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Protocol

import httpx

from .config import Config
from .errors import ApiError, classify

JsonDict = dict[str, Any]


@dataclass(frozen=True)
class Request:
    """One call, described before anything decides how to make it."""

    method: str
    path: str
    json: JsonDict | None = None
    params: JsonDict | None = None
    data: JsonDict | None = None
    #: multipart parts, as httpx takes them. Named separately because a body cannot be both.
    files: dict[str, tuple[str, BinaryIO, str]] | None = None

    @property
    def is_write(self) -> bool:
        return self.method.upper() != "GET"

    def describe(self) -> str:
        """A single line for a log, with no request body secrets in it."""
        parts = [self.method.upper(), self.path]
        if self.params:
            parts.append(f"params={sorted(self.params)}")
        if self.json:
            parts.append(f"json={sorted(self.json)}")
        if self.data:
            parts.append(f"data={sorted(self.data)}")
        if self.files:
            parts.append(f"files={sorted(self.files)}")
        return " ".join(parts)


class Transport(Protocol):
    """Everything the clients need from the outside world."""

    def send(self, request: Request) -> JsonDict: ...


# --------------------------------------------------------------------------------------
# The real one
# --------------------------------------------------------------------------------------


class HttpTransport:
    """Talks to the Graph API.

    Retries on 429 and 5xx only. A 400 means the request was wrong and will be wrong again,
    so retrying it wastes time and rate limit; a 403 on the Groups API usually means the
    number is not eligible, which retrying cannot fix either.
    """

    def __init__(
        self,
        config: Config,
        client: httpx.Client | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._client = client
        self._owns_client = client is None
        self._sleep = sleep

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.config.timeout_seconds)
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> HttpTransport:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def send(self, request: Request) -> JsonDict:
        url = f"{self.config.base_url}/{request.path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self.config.token}"}
        wait = self.config.retry_base_seconds

        for attempt in range(1, self.config.retry_attempts + 1):
            response = self.client.request(
                request.method,
                url,
                headers=headers,
                json=request.json,
                params=request.params,
                data=request.data,
                files=request.files,
            )
            if response.status_code < 400:
                return _decode(response)

            error = classify(response.status_code, response.text, request.path)
            if not error.is_transient or attempt == self.config.retry_attempts:
                raise error

            self._sleep(self._backoff(response, wait))
            wait = min(wait * 2, self.config.retry_max_seconds)

        raise AssertionError("unreachable")

    def _backoff(self, response: httpx.Response, wait: float) -> float:
        """Meta's own ``Retry-After`` wins over our schedule when it is present.

        Backing off less than the server asked for is how a rate limit becomes a ban.
        """
        header = response.headers.get("Retry-After")
        if header:
            try:
                asked = float(header)
            except ValueError:
                asked = 0.0
            if asked > 0:
                return min(asked, self.config.retry_max_seconds)
        return min(wait, self.config.retry_max_seconds)


def _decode(response: httpx.Response) -> JsonDict:
    if not response.content:
        return {}
    try:
        parsed = response.json()
    except ValueError as error:
        raise ApiError(
            response.status_code,
            f"response was not JSON: {response.text[:200]!r}",
            str(response.url),
        ) from error
    return parsed if isinstance(parsed, dict) else {"data": parsed}


# --------------------------------------------------------------------------------------
# The safe ones
# --------------------------------------------------------------------------------------

#: Obviously not a real id. A dry run that returned something id-shaped would let a caller
#: store it, and a stored fake id is a bug that surfaces days later.
DRY_RUN_GROUP_ID = "dry-run-group-id"


@dataclass
class DryRunTransport:
    """Records writes instead of making them.

    ``reads`` lets a dry run still see the real world: pointing it at an
    :class:`HttpTransport` means listing and inspecting work normally while nothing is
    created, updated or sent. That combination — real reads, recorded writes — is what makes
    a provisioning script safe to run against production before it is trusted.
    """

    reads: Transport | None = None
    calls: list[Request] = field(default_factory=list)
    log: Callable[[str], None] | None = None

    def send(self, request: Request) -> JsonDict:
        if not request.is_write:
            if self.reads is not None:
                return self.reads.send(request)
            return {"data": []}

        self.calls.append(request)
        if self.log is not None:
            self.log(f"[dry run] {request.describe()}")
        return {"dry_run": True, "id": DRY_RUN_GROUP_ID}

    @property
    def writes(self) -> list[Request]:
        """Every write that was withheld, in order. The point of the dry run."""
        return list(self.calls)


class RecordedTransport:
    """Replays responses captured from a real run.

    Keyed by method and path, with each key holding a queue: a sequence of calls to the same
    endpoint replays in the order it was recorded, which is what makes pagination and
    before/after comparisons reproducible offline.

    A call with no recording raises rather than returning an empty object, because a demo
    that silently answers nothing teaches a reader the wrong thing about the API.

    A recording of the form ``{"__error__": {"status": 404, "body": "..."}}`` raises instead
    of returning. Error handling is half of what a client does, and a fixture set that can
    only express success can only test the half that rarely goes wrong.
    """

    def __init__(self, recordings: dict[str, list[JsonDict]]):
        self._remaining = {key: list(value) for key, value in recordings.items()}
        self._used: list[str] = []

    @classmethod
    def from_file(cls, path: str | Path) -> RecordedTransport:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(loaded["responses"])

    @staticmethod
    def key(request: Request) -> str:
        return f"{request.method.upper()} {request.path}"

    def send(self, request: Request) -> JsonDict:
        key = self.key(request)
        queue = self._remaining.get(key)
        if not queue:
            raise ApiError(
                404,
                f"no recorded response for {key}. The offline demo answers only for the "
                f"recorded calls, and never invents one. Recorded keys: "
                f"{sorted(self._remaining)}",
                request.path,
            )
        self._used.append(key)
        # The last recording for a key repeats, so a caller that polls does not exhaust it.
        recording = queue.pop(0) if len(queue) > 1 else queue[0]
        failure = recording.get("__error__")
        if isinstance(failure, dict):
            raise classify(
                int(failure.get("status", 500)),
                str(failure.get("body", "")),
                request.path,
            )
        return recording

    @property
    def used(self) -> list[str]:
        return list(self._used)


@dataclass
class FailingTransport:
    """Fails the first ``failures`` calls, then delegates. For testing retry behaviour."""

    status: int = 429
    failures: int = 1
    body: str = '{"error": {"message": "rate limited", "code": 613}}'
    inner: Transport | None = None
    attempts: int = 0

    def send(self, request: Request) -> JsonDict:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise classify(self.status, self.body, request.path)
        if self.inner is not None:
            return self.inner.send(request)
        return {"ok": True}
