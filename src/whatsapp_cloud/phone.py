"""Turning whatever someone typed into the digits the API wants.

The Cloud API takes a phone number as digits only, in E.164 order, with no plus sign, spaces,
brackets or dashes. Everyone's spreadsheet has all four.

Stripping punctuation is the easy half. The half worth writing down is refusing what is left:
a cell holding an extension, a truncated number or a stray reference survives ``re.sub`` as a
short run of digits, and a group provisioned around it fails somewhere much less obvious than
here.
"""

from __future__ import annotations

import re

from .errors import InvalidPhoneNumberError
from .limits import MAX_PHONE_DIGITS, MIN_PHONE_DIGITS

_NON_DIGIT = re.compile(r"\D")
_LEADING_ZEROS = re.compile(r"^0+")


def normalise(number: str) -> str:
    """``'+55 (62) 98459-0000'`` becomes ``'5562984590000'``.

    Raises :class:`~whatsapp_cloud.errors.InvalidPhoneNumberError` rather than returning something
    unusable, because a wrong recipient is worse than a failed send.
    """
    if not isinstance(number, str):
        raise InvalidPhoneNumberError(f"Expected a string, got {type(number).__name__}")

    digits = _NON_DIGIT.sub("", number)
    if not digits:
        raise InvalidPhoneNumberError(f"No digits in {number!r}")

    # A leading zero is a national trunk prefix ("055…", "062…") and is never part of the
    # international form. Dropping it is safe; keeping it silently sends to the wrong place.
    trimmed = _LEADING_ZEROS.sub("", digits)
    if not trimmed:
        raise InvalidPhoneNumberError(f"Only zeros in {number!r}")

    if len(trimmed) < MIN_PHONE_DIGITS:
        raise InvalidPhoneNumberError(
            f"{number!r} has {len(trimmed)} digits; a number in international form has at "
            f"least {MIN_PHONE_DIGITS}. An extension or a truncated cell looks like this."
        )
    if len(trimmed) > MAX_PHONE_DIGITS:
        raise InvalidPhoneNumberError(
            f"{number!r} has {len(trimmed)} digits; E.164 allows at most {MAX_PHONE_DIGITS}."
        )
    return trimmed


def normalise_all(numbers: list[str]) -> list[str]:
    """Normalises a list, keeping order and dropping exact repeats.

    Order is kept because a caller's list usually has a meaning — the client first, then the
    team — and a set would throw that away.
    """
    seen: set[str] = set()
    out: list[str] = []
    for number in numbers:
        digits = normalise(number)
        if digits in seen:
            continue
        seen.add(digits)
        out.append(digits)
    return out


def looks_like_phone(number: str) -> bool:
    """Non-raising form, for filtering a column before deciding what to do with it."""
    try:
        normalise(number)
    except InvalidPhoneNumberError:
        return False
    return True
