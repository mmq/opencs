"""Registry, tags, trailing bytes and the DecodeError-only contract (I §4.1, I §4.2, I §8)."""

from __future__ import annotations

import contextlib
import random
from dataclasses import dataclass
from typing import ClassVar, Self

import pytest

from opencs.messages import WirePos, WireXZ, client, server
from opencs.messages.base import (
    _CLIENT,
    _SERVER,
    ClientMessage,
    DecodeError,
    ServerMessage,
    _Registry,
    client_payload,
    decode_client,
    decode_server,
    message,
    server_payload,
)
from opencs.wire.codec import Reader
from opencs.wire.errors import WireError
from opencs.wire.frame import MAX_PAYLOAD, build_frame

TAG = 0x2A


def test_uppercase_server_types_carry_the_tag_and_lowercase_do_not() -> None:
    assert server_payload(server.ReleaseDrop(7), TAG) == b"A" + bytes([TAG, 0x17])
    chat = server.Chat(1, 2, b"x")
    assert server_payload(chat, TAG) == b"a\x11\x12\x11x"  # no tag byte, whatever TAG is
    assert server_payload(chat, 0x99) == server_payload(chat, TAG)


def test_every_uppercase_server_type_is_tagged_and_every_lowercase_is_not() -> None:
    for cls in set(_SERVER.classes.values()):
        assert issubclass(cls, ServerMessage)
        assert cls.is_tagged() == cls.TYPE.isupper()
    assert {c.TYPE for c in _SERVER.classes.values() if not c.TYPE.isupper()} == {"a", "b", "c"}


def test_client_payload_never_has_a_tag() -> None:
    # Uppercase client types ('A', 'E', 'I', ...) have none: the byte after the type is a field.
    assert client_payload(client.Flags(5)) == b"S\x15"
    assert client_payload(client.DropMe()) == b"A"
    assert client_payload(client.LegArrived(250)) == b"K" + bytes([0xB2, 0x20])
    assert client_payload(client.Ping()) == b"EN"


@pytest.mark.parametrize("tag", [0x10, 0x11, 0x7F, 0xFF])
def test_tag_round_trips(tag: int) -> None:
    msg, got = decode_server(server_payload(server.Delete(3), tag))
    assert (msg, got) == (server.Delete(3), tag)


@pytest.mark.parametrize("tag", [-1, 0, 0x0F, 0x100])
def test_tag_outside_the_legal_bytes_is_refused_on_encode(tag: int) -> None:
    with pytest.raises(WireError):
        server_payload(server.Delete(3), tag)


def test_tag_below_0x10_is_refused_on_decode() -> None:
    with pytest.raises(DecodeError):
        decode_server(b"H\x0f\x13")
    assert decode_server(b"H\x10\x13") == (server.Delete(3), 0x10)


def test_lowercase_server_decode_has_no_tag() -> None:
    assert decode_server(b"a\x10\x10\x11x") == (server.Chat(0, 0, b"x"), None)


def test_server_trailing_bytes_are_rejected() -> None:
    good = server_payload(server.Delete(3), TAG)
    assert decode_server(good)[0] == server.Delete(3)
    with pytest.raises(DecodeError):
        decode_server(good + b"\x10")


def test_client_trailing_bytes_are_accepted_and_ignored() -> None:
    assert decode_client(b"S\x15") == client.Flags(5)
    assert decode_client(b"S\x15\x10\x10") == client.Flags(5)
    assert decode_client(b"A\x10") == client.DropMe()


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\x00",
        b"Z",
        b"z",
        b"\xff",
        b"E",
        b"Q",
        b"D",
        b"T",
        b"Q\x77",
        b"D?",
        b"EZ",
        b"EV",
        b"Ev",
    ],
)
def test_client_unknown_or_empty_raises_decode_error(payload: bytes) -> None:
    with pytest.raises(DecodeError):
        decode_client(payload)


@pytest.mark.parametrize(
    "payload",
    [b"", b"Z\x10", b"z", b"E", b"E\x10", b"E\x10Z", b"D\x10", b"D\x10?", b"B\x10", b"\xc0\x10"],
)
def test_server_unknown_or_truncated_raises_decode_error(payload: bytes) -> None:
    with pytest.raises(DecodeError):
        decode_server(payload)


def test_truncation_at_every_length_raises_decode_error() -> None:
    sample = server_payload(
        server.Introduce(1, b"JOINGEN2", 1, 0, WirePos(0, 0, 75, 240, 75), 450, 0, 0, 0),
        TAG,
    )
    for n in range(len(sample)):
        with pytest.raises(DecodeError):
            decode_server(sample[:n])
    pod = client_payload(client.PodState(WirePos(1, 1, 5, 6, 7), 1, 2, 3, 4, 5, 6, 7, 8, 9, 10))
    for n in range(len(pod)):
        with pytest.raises(DecodeError):
            decode_client(pod[:n])


def test_bytes_below_0x10_are_not_scalars() -> None:
    # Reader.b10() would return -1 for 0x0f; no encoder emits it, so it is not a message.
    with pytest.raises(DecodeError):
        decode_server(b"A" + bytes([TAG, 0x0F]))
    with pytest.raises(DecodeError):
        decode_client(b"S\x05")


def test_bias88_bytes_below_0x10_decode_except_terminators() -> None:
    # bias88 runs down to -136, byte 0x00 (I §4.3): a client 'b' reversing at -121 is a message.
    pod = decode_client(b"b\x10\x10\x10\x10\x10" + b"\x0f\x00\x10\x10\x14\x10\x10\x10\x10")
    assert isinstance(pod, client.PodState)
    assert (pod.fwd, pod.fwd_target) == (-121, -136)
    for term in (0x0A, 0x0D, 0x0E):  # -126, -123, -122: never a value (I §4.1)
        with pytest.raises(DecodeError):
            decode_client(
                b"b\x10\x10\x10\x10\x10" + bytes([term]) + b"\x88\x10\x10\x14\x10\x10\x10\x10"
            )
        with pytest.raises(DecodeError):
            decode_client(b"DB" + b"\x11" * 9 + bytes([term]))


def test_decoding_random_bytes_raises_only_decode_error() -> None:
    rng = random.Random(7)
    letters = [ord(c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"]
    for _ in range(20000):
        n = rng.randrange(0, 40)
        body = bytes(
            rng.randrange(0x10, 0x100) if rng.random() < 0.9 else rng.randrange(0x10)
            for _ in range(n)
        )
        payload = bytes([rng.choice(letters)]) + body
        for decode in (decode_client, decode_server):
            with contextlib.suppress(DecodeError):
                decode(payload)


def test_mutated_valid_payloads_raise_only_decode_error() -> None:
    rng = random.Random(8)
    seeds = [server_payload(m, TAG) for m in _sample_server_messages()]
    seeds += [client_payload(m) for m in _sample_client_messages()]
    for seed in seeds:
        for _ in range(300):
            data = bytearray(seed)
            for _ in range(rng.randrange(1, 4)):
                data[rng.randrange(len(data))] = rng.randrange(256)
            for decode in (decode_client, decode_server):
                with contextlib.suppress(DecodeError):
                    decode(bytes(data))


def _sample_server_messages() -> list[ServerMessage]:
    pos = WirePos(2, 3, 100, 72, -300)
    return [
        server.Session(3, 2, 1, b"ABC", b"NET", b"CITY", 8, 2, 3, 5, 6),
        server.SetBays((1, 2, 3, 4, 5, 0, 1, 2, 3), -7),
        server.Introduce(1, b"AB", 1, 0, pos, 450),
        server.PodState(5, pos, 1, 2, 3, 4, 5, 1, 7),
        server.Shot(3, pos, 1, 2, 9),
        server.SetBlocks(tuple(range(4)) * 16),
        server.SetBlock(3, 5, 2),
        server.SpawnTower(0, 9000, 3, pos, 1),
        server.Chat(100, 0, b"hello"),
    ]


def _sample_client_messages() -> list[ClientMessage]:
    pos = WirePos(2, 3, 100, 72, -300)
    return [
        client.PodState(pos, 1, 2, 3, 4, 5, 1, 7, 8, 9, 10),
        client.Shot(3, pos, 1, 2, 9),
        client.Grenade(pos, 1, 2),
        client.Bays((1, 2, 3, 4, 5, 0, 1, 2, 3), -7),
        client.ModuleRested(8300, pos),
        client.Drone(WireXZ(1, 2, 3, 4)),
        client.Chat(0, b"hello"),
    ]


def test_building_a_frame_from_every_sample_works() -> None:
    for m in _sample_server_messages():
        assert len(server_payload(m, TAG)) <= MAX_PAYLOAD
        build_frame(server_payload(m, TAG))


def test_duplicate_registration_raises() -> None:
    registry = _Registry()
    registry.add(server.ReleaseDrop)
    with pytest.raises(TypeError):
        registry.add(server.ReleaseDrop)
    registry.add(server.SetStatus)
    with pytest.raises(TypeError):  # same ('D', 'S') key from another class
        registry.add(client.Status)


def test_a_type_cannot_both_dispatch_and_not() -> None:
    registry = _Registry()
    registry.add(server.SetStatus)  # 'D' dispatches on a sub
    with pytest.raises(TypeError):
        registry.add(_Plain)


def test_message_decorator_rejects_a_duplicate_key_at_import() -> None:
    with pytest.raises(TypeError):

        @message
        class Twin(ServerMessage):  # pragma: no cover - the decorator raises
            TYPE: ClassVar[str] = "A"

            def body(self) -> bytes:
                return b""

            @classmethod
            def read(cls, r: Reader, sub: int) -> Self:
                return cls()

    # and the failed class did not displace the real one
    assert _SERVER.classes[("A", None)] is server.ReleaseDrop


@dataclass(frozen=True, slots=True)
class _Plain(ServerMessage):
    TYPE: ClassVar[str] = "D"

    def body(self) -> bytes:
        return b""

    @classmethod
    def read(cls, r: Reader, sub: int) -> Self:
        return cls()


def test_registries_are_separate_per_direction() -> None:
    assert _SERVER.classes[("A", None)] is server.ReleaseDrop
    assert _CLIENT.classes[("A", None)] is client.DropMe
    assert _SERVER.classes[("b", None)] is server.PodState
    assert _CLIENT.classes[("b", None)] is client.PodState


def test_construction_checks_encodability_size_and_terminators() -> None:
    @dataclass(frozen=True, slots=True)
    class Huge(ServerMessage):
        TYPE: ClassVar[str] = "z"

        def body(self) -> bytes:
            return b"x" * MAX_PAYLOAD  # with the type letter: one byte too many

        @classmethod
        def read(cls, r: Reader, sub: int) -> Self:
            return cls()

    @dataclass(frozen=True, slots=True)
    class Tagged(ServerMessage):
        TYPE: ClassVar[str] = "Z"

        def body(self) -> bytes:
            return b"x" * (MAX_PAYLOAD - 1)  # type + tag + body = 235 > 234

        @classmethod
        def read(cls, r: Reader, sub: int) -> Self:
            return cls()

    @dataclass(frozen=True, slots=True)
    class Terminated(ServerMessage):
        TYPE: ClassVar[str] = "y"
        value: int

        def body(self) -> bytes:
            return bytes([self.value])

        @classmethod
        def read(cls, r: Reader, sub: int) -> Self:
            return cls(0)

    with pytest.raises(ValueError, match="exceeds"):
        Huge()
    with pytest.raises(ValueError, match="exceeds"):
        Tagged()
    for bad in (0x0A, 0x0D, 0x0E):
        with pytest.raises(ValueError, match="0x0a"):
            Terminated(bad)
    assert Terminated(0x0B).body() == b"\x0b"
