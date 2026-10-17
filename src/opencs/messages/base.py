"""Message base classes, the registry, and the encode/decode entry points.

A message class is a frozen, slotted dataclass built by the ``@message`` decorator, which also
registers it under ``(type letter, sub byte)``. It knows its own bytes and nothing of what it causes
(DESIGN §3). Tags are never stored on a message: ``server_payload`` takes one at the send boundary
(DESIGN §3.2, I §4.2).

Decoding raises only ``DecodeError``. Server messages validate their fields in ``validate``
(II §8, DESIGN §5): no instance can exist that would put a crash value on a player's
screen, so a bug in our code raises in our code. Client messages validate nothing but decodability.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Self, dataclass_transform

from opencs.wire.codec import BYTE_MAX, BYTE_MIN, Reader
from opencs.wire.errors import WireError
from opencs.wire.frame import MAX_PAYLOAD, TERMINATORS


class DecodeError(WireError):
    """Bytes that are not a message we can decode: unknown type or sub, truncated, or a value no
    legal message holds. The only exception ``decode_client`` and ``decode_server`` raise."""


#: Registry key for the class that takes every sub byte nothing else claims (``'Q'`` destroy,
#: single-cell ``'T'``, I §8).
ANY_SUB = -1


class Message(ABC):
    """One protocol message. ``TYPE`` is its type letter; ``SUBS`` the raw sub bytes it answers to
    (empty if its type has no sub), ``TAKES_ANY_SUB`` if it also takes every unclaimed sub."""

    __slots__ = ()

    TYPE: ClassVar[str]
    SUBS: ClassVar[tuple[int, ...]] = ()
    TAKES_ANY_SUB: ClassVar[bool] = False

    @abstractmethod
    def body(self) -> bytes:
        """The fields after the type letter (and the tag, for a server message); for a dispatching
        type this starts with the sub byte."""

    @classmethod
    @abstractmethod
    def read(cls, r: Reader, sub: int) -> Self:
        """Build the message from the fields after the sub byte. ``sub`` is the raw sub byte the
        dispatcher consumed, or 0 for a type without one."""


class ServerMessage(Message):
    """A server to client message. Validated on construction; uppercase types carry a tag."""

    __slots__ = ()

    @classmethod
    def is_tagged(cls) -> bool:
        """True for an uppercase type, which carries a tag byte (I §4.2)."""
        return cls.TYPE.isupper()

    def validate(self) -> None:
        """Raise ``ValueError`` for a field the client cannot take. Override per message."""

    def __post_init__(self) -> None:
        self.validate()
        body = self.body()  # raises WireError (a ValueError) for an unencodable field
        size = 1 + (1 if self.is_tagged() else 0) + len(body)
        if size > MAX_PAYLOAD:
            raise ValueError(f"payload of {size} bytes exceeds {MAX_PAYLOAD} (I §4.1)")
        if any(b in TERMINATORS for b in body):
            raise ValueError("payload holds 0x0a, 0x0d or 0x0e (I §4.1)")


class ClientMessage(Message):
    """A client to server message. The client is the authority on its own format, so nothing is
    validated here; sessions decide what to do with odd values."""

    __slots__ = ()


class _Registry:
    """Messages of one direction by ``(type letter, sub byte)``."""

    def __init__(self) -> None:
        self.classes: dict[tuple[str, int | None], type[Message]] = {}
        self.dispatching: dict[str, bool] = {}

    def add(self, cls: type[Message]) -> None:
        letter = cls.TYPE
        if len(letter) != 1 or not letter.isascii():
            raise TypeError(f"{cls.__name__}: TYPE must be one ASCII letter")
        keys: list[tuple[str, int | None]]
        if cls.SUBS or cls.TAKES_ANY_SUB:
            keys = [(letter, s) for s in cls.SUBS]
            if cls.TAKES_ANY_SUB:
                keys.append((letter, ANY_SUB))
        else:
            keys = [(letter, None)]
        dispatching = keys[0][1] is not None
        if self.dispatching.setdefault(letter, dispatching) != dispatching:
            raise TypeError(f"{cls.__name__}: type {letter!r} is both dispatching and not")
        for key in keys:
            if key in self.classes:
                raise TypeError(f"{cls.__name__}: {key} already taken by {self.classes[key]}")
            self.classes[key] = cls


_SERVER = _Registry()
_CLIENT = _Registry()


@dataclass_transform(frozen_default=True)
def message[M: Message](cls: type[M]) -> type[M]:
    """Make ``cls`` a frozen, slotted dataclass and register it for decoding."""
    cls = dataclass(frozen=True, slots=True)(cls)
    registry = _SERVER if issubclass(cls, ServerMessage) else _CLIENT
    registry.add(cls)
    return cls


def sub_letters(letters: str) -> tuple[int, ...]:
    """Raw sub bytes for a string of literal sub letters, e.g. ``sub_letters("RWH")``."""
    return tuple(ord(c) for c in letters)


def _decode(registry: _Registry, r: Reader, letter: str) -> Message:
    dispatching = registry.dispatching.get(letter)
    if dispatching is None:
        raise DecodeError(f"unknown message type {letter!r}")
    sub = 0
    if dispatching:
        sub = r.raw(1)[0]
        cls = registry.classes.get((letter, sub)) or registry.classes.get((letter, ANY_SUB))
        if cls is None:
            raise DecodeError(f"unknown sub {sub:#04x} of type {letter!r}")
    else:
        cls = registry.classes[(letter, None)]
    return cls.read(r, sub)


def client_payload(msg: ClientMessage) -> bytes:
    """The payload for a client message: the type letter and the body, never a tag (SPEC A,
    conventions)."""
    return msg.TYPE.encode("ascii") + msg.body()


def server_payload(msg: ServerMessage, tag: int) -> bytes:
    """The payload for a server message: the type letter, the tag if the type is uppercase, and the
    body (I §4.2). Lowercase types ignore ``tag``."""
    head = msg.TYPE.encode("ascii")
    if msg.is_tagged():
        if not BYTE_MIN <= tag <= BYTE_MAX:
            raise WireError(f"tag {tag:#x} outside {BYTE_MIN:#x}..{BYTE_MAX:#x} (I §4.2)")
        head += bytes([tag])
    payload = head + msg.body()
    if len(payload) > MAX_PAYLOAD:
        raise WireError(f"payload {len(payload)} bytes exceeds {MAX_PAYLOAD} (I §4.1)")
    return payload


def decode_client(payload: bytes) -> ClientMessage:
    """Decode a payload the client sent. Trailing bytes after the last field are ignored: the client
    is the authority on its own format (I §4.1). Raises only ``DecodeError``."""
    try:
        r = Reader(payload)
        letter = chr(r.raw(1)[0])
        msg = _decode(_CLIENT, r, letter)
    except ValueError as exc:  # WireError and every validation error are ValueErrors
        raise DecodeError(str(exc)) from exc
    assert isinstance(msg, ClientMessage)
    return msg


def decode_server(payload: bytes) -> tuple[ServerMessage, int | None]:
    """Decode a payload the server sent, as a test or the fake client reads it: the message and
    its tag (None for a lowercase type). Trailing bytes are an error: they would be our bug.
    Raises only ``DecodeError``."""
    tag: int | None = None
    try:
        r = Reader(payload)
        first = r.raw(1)[0]
        letter = chr(first)
        if 0x41 <= first <= 0x5A:  # an uppercase ASCII type carries a tag (I §4.2)
            tag = r.raw(1)[0]
            if tag < BYTE_MIN:
                raise DecodeError(f"tag {tag:#x} below {BYTE_MIN:#x}")
        msg = _decode(_SERVER, r, letter)
        if not r.at_end():
            raise DecodeError(f"{len(r.rest())} trailing bytes after {type(msg).__name__}")
    except ValueError as exc:
        raise DecodeError(str(exc)) from exc
    assert isinstance(msg, ServerMessage)
    return msg, tag
