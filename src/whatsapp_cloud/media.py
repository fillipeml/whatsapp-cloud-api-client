"""Checking a group icon before uploading it.

The check exists because of where the failure would otherwise land. A group is created, then
its icon is set; if the icon is rejected the group already exists, and a retry has to know
that. Validating locally turns a half-finished provisioning run into an error raised before
anything was created.

The contract is narrow and easy to miss: JPEG only, square only, at least 192 pixels a side,
at most 5 MB. A PNG exported from a design tool and a 191-pixel crop both look fine to a
human and are both refused by the API.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .errors import InvalidIconError
from .limits import ICON_FORMAT, ICON_MAX_BYTES, ICON_MIN_SIDE_PX


@dataclass(frozen=True)
class IconFacts:
    """What the file actually is, whether or not it is acceptable."""

    path: Path
    size_bytes: int
    image_format: str
    width: int
    height: int

    @property
    def is_square(self) -> bool:
        return self.width == self.height


def inspect(path: str | Path) -> IconFacts:
    """Reads the file's real properties, without judging them.

    Separate from :func:`validate` so a caller can report what is wrong with an image
    instead of only that something is.
    """
    icon = Path(path)
    if not icon.exists():
        raise InvalidIconError(f"Icon not found: {icon}")
    if not icon.is_file():
        raise InvalidIconError(f"Not a file: {icon}")

    try:
        with Image.open(icon) as image:
            return IconFacts(
                path=icon,
                size_bytes=icon.stat().st_size,
                image_format=image.format or "unknown",
                width=image.width,
                height=image.height,
            )
    except UnidentifiedImageError as error:
        raise InvalidIconError(f"Not an image this library can read: {icon}") from error


def validate(path: str | Path) -> IconFacts:
    """Raises :class:`~whatsapp_cloud.errors.InvalidIconError` unless the file satisfies the contract.

    Every failure names the value that was wrong, because "invalid icon" sends somebody back
    to the documentation while "1024x768, needs to be square" sends them to the crop tool.
    """
    facts = inspect(path)

    if facts.size_bytes > ICON_MAX_BYTES:
        raise InvalidIconError(
            f"{facts.path.name} is {facts.size_bytes:,} bytes; the limit is "
            f"{ICON_MAX_BYTES:,} ({ICON_MAX_BYTES // (1024 * 1024)} MB)."
        )
    if facts.image_format != ICON_FORMAT:
        raise InvalidIconError(
            f"{facts.path.name} is {facts.image_format}; only {ICON_FORMAT} is accepted. "
            "Re-export rather than renaming the file: the format is read from the bytes."
        )
    if not facts.is_square:
        raise InvalidIconError(
            f"{facts.path.name} is {facts.width}x{facts.height}; the icon must be square."
        )
    if facts.width < ICON_MIN_SIDE_PX:
        raise InvalidIconError(
            f"{facts.path.name} is {facts.width}px a side; the minimum is {ICON_MIN_SIDE_PX}px."
        )
    return facts


def problems(path: str | Path) -> list[str]:
    """Every reason the file would be refused, rather than only the first.

    For a pre-flight check over a folder of icons, where fixing them one round-trip at a
    time is the slow way to do it.
    """
    try:
        facts = inspect(path)
    except InvalidIconError as error:
        return [str(error)]

    found: list[str] = []
    if facts.size_bytes > ICON_MAX_BYTES:
        found.append(f"{facts.size_bytes:,} bytes exceeds {ICON_MAX_BYTES:,}")
    if facts.image_format != ICON_FORMAT:
        found.append(f"format is {facts.image_format}, not {ICON_FORMAT}")
    if not facts.is_square:
        found.append(f"{facts.width}x{facts.height} is not square")
    if min(facts.width, facts.height) < ICON_MIN_SIDE_PX:
        # The shorter side, not the width. `problems` promises every reason an icon would be
        # refused, and a wide, short image hid its undersized dimension behind "not square".
        found.append(
            f"{min(facts.width, facts.height)}px is below the {ICON_MIN_SIDE_PX}px minimum"
        )
    return found
