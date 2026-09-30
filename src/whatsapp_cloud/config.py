"""Settings, and the one default that matters.

``dry_run`` defaults to **True**. A library that talks to a messaging platform on behalf of a
business gets one chance to send the wrong thing to a real person, so the default is the safe
one and turning it off is an explicit act. Everything else defaults to something harmless.

The Graph API version is configuration, not a constant. Meta retires versions on a schedule,
and a client that pins one in its source is wrong from the day it is published; a client that
reads one from the environment is merely out of date, which the operator can fix without a
release.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from .errors import ConfigurationError

#: Used when nothing else is configured. Named here so a reader can find it in one place.
#: Graph versions expire on a published schedule, so check Meta's changelog before a real
#: deployment and set ``WA_GRAPH_VERSION`` rather than waiting for a release of this package.
DEFAULT_GRAPH_VERSION = "v26.0"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_RETRY_ATTEMPTS = 4
DEFAULT_RETRY_BASE_SECONDS = 1.0
DEFAULT_RETRY_MAX_SECONDS = 60.0

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Config:
    """Everything the client needs, and nothing it does not.

    ``app_secret`` and ``verify_token`` are only used by the webhook side; a process that
    only sends may leave them empty.
    """

    phone_number_id: str
    token: str = field(repr=False)
    app_secret: str = field(default="", repr=False)
    verify_token: str = field(default="", repr=False)
    graph_version: str = DEFAULT_GRAPH_VERSION
    dry_run: bool = True
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    retry_attempts: int = DEFAULT_RETRY_ATTEMPTS
    retry_base_seconds: float = DEFAULT_RETRY_BASE_SECONDS
    retry_max_seconds: float = DEFAULT_RETRY_MAX_SECONDS

    def __post_init__(self) -> None:
        if not self.phone_number_id.strip():
            raise ConfigurationError("phone_number_id is required")
        if not self.graph_version.startswith("v"):
            raise ConfigurationError(
                f"graph_version should look like 'v23.0'; got {self.graph_version!r}"
            )
        if self.retry_attempts < 1:
            raise ConfigurationError("retry_attempts must be at least 1")

    @property
    def base_url(self) -> str:
        return f"https://graph.facebook.com/{self.graph_version}"

    def redacted(self) -> dict[str, object]:
        """A form safe to print or log: every secret becomes its length."""
        return {
            "phone_number_id": self.phone_number_id,
            "token": f"<{len(self.token)} chars>" if self.token else "<empty>",
            "app_secret": f"<{len(self.app_secret)} chars>" if self.app_secret else "<empty>",
            "verify_token": f"<{len(self.verify_token)} chars>" if self.verify_token else "<empty>",
            "graph_version": self.graph_version,
            "dry_run": self.dry_run,
        }


def _flag(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    lowered = value.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigurationError(
        f"Expected a boolean like 'true' or 'false'; got {value!r}. "
        "An unrecognised value is refused rather than guessed, because guessing wrong here "
        "means sending for real."
    )


def _number(value: str | None, default: float, name: str) -> float:
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be a number; got {value!r}") from error
    if parsed <= 0:
        raise ConfigurationError(f"{name} must be positive; got {parsed}")
    return parsed


def from_env(
    env: Mapping[str, str] | None = None,
    *,
    require_credentials: bool = True,
    load_dotenv: bool = True,
) -> Config:
    """Reads the environment.

    ``require_credentials=False`` builds a config good enough to inspect and to run the
    offline demo, which is how the package stays usable before a number exists.
    """
    if load_dotenv and env is None:
        _load_dotenv_if_available()
    source: Mapping[str, str] = os.environ if env is None else env

    def get(name: str) -> str:
        return source.get(name, "").strip()

    phone_number_id = get("WA_PHONE_NUMBER_ID")
    token = get("WA_TOKEN")

    if require_credentials:
        missing = [
            n for n, v in (("WA_PHONE_NUMBER_ID", phone_number_id), ("WA_TOKEN", token)) if not v
        ]
        if missing:
            raise ConfigurationError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                "Copy .env.example to .env and fill them in, or call with "
                "require_credentials=False to inspect without credentials."
            )
    elif not phone_number_id:
        # Config requires one, and the demo has no real number: a clearly fake value keeps
        # the object constructible without any chance of being mistaken for a real id.
        phone_number_id = "000000000000000"

    return Config(
        phone_number_id=phone_number_id,
        token=token,
        app_secret=get("WA_APP_SECRET"),
        verify_token=get("WA_VERIFY_TOKEN"),
        graph_version=get("WA_GRAPH_VERSION") or DEFAULT_GRAPH_VERSION,
        dry_run=_flag(source.get("WA_DRY_RUN"), default=True),
        timeout_seconds=_number(
            source.get("WA_TIMEOUT_SECONDS"), DEFAULT_TIMEOUT_SECONDS, "WA_TIMEOUT_SECONDS"
        ),
        retry_attempts=int(
            _number(source.get("WA_RETRY_ATTEMPTS"), DEFAULT_RETRY_ATTEMPTS, "WA_RETRY_ATTEMPTS")
        ),
        retry_base_seconds=_number(
            source.get("WA_RETRY_BASE_SECONDS"), DEFAULT_RETRY_BASE_SECONDS, "WA_RETRY_BASE_SECONDS"
        ),
        retry_max_seconds=_number(
            source.get("WA_RETRY_MAX_SECONDS"), DEFAULT_RETRY_MAX_SECONDS, "WA_RETRY_MAX_SECONDS"
        ),
    )


def _load_dotenv_if_available() -> None:
    """Loads a .env when python-dotenv is installed, and says nothing when it is not.

    An optional dependency: a library should not force one on an application that already
    manages its own configuration.
    """
    try:
        from dotenv import load_dotenv as _load
    except ImportError:
        return
    _load()
