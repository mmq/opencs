"""Messages layer: one typed, immutable class per protocol message, with a registry.

Each message encodes and decodes itself; it must not know what a message causes (DESIGN §3).
``server`` and ``client`` hold the classes (``server.PodState`` and ``client.PodState`` are the two
directions of ``'b'``); the entry points are re-exported here.
"""

from opencs.messages import client, server
from opencs.messages.base import (
    ClientMessage,
    DecodeError,
    Message,
    ServerMessage,
    client_payload,
    decode_client,
    decode_server,
    server_payload,
)
from opencs.messages.fields import WirePos, WireXZ

__all__ = [
    "ClientMessage",
    "DecodeError",
    "Message",
    "ServerMessage",
    "WirePos",
    "WireXZ",
    "client",
    "client_payload",
    "decode_client",
    "decode_server",
    "server",
    "server_payload",
]
