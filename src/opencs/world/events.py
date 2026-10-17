"""What a city says: a server message and who should receive it (I App. B, DESIGN §3.2).

The world never sends. A city method returns a list of ``Event``; the router (``opencs.routing``)
turns each audience into the connections that receive the message. Audiences are data, so the
spec's A.4 table is one small module, not logic spread over the rules.
"""

from __future__ import annotations

from dataclasses import dataclass

from opencs.messages import ServerMessage


@dataclass(frozen=True, slots=True)
class ToPlayer:
    """That client only (``'B'``, ``'I'`` to a victim, ``'E'``-``'N'``...)."""

    player_id: int


@dataclass(frozen=True, slots=True)
class ToAll:
    """Every client in the city (``'J'``, chat, ``'M'``...)."""


@dataclass(frozen=True, slots=True)
class ToAllExcept:
    """Every client in the city but one (relayed ``'b'``, ``'c'``)."""

    player_id: int


@dataclass(frozen=True, slots=True)
class ToTeam:
    """Every client whose player is on this team (team chat)."""

    team: int


@dataclass(frozen=True, slots=True)
class ToHolders:
    """Every client whose view holds this entity, i.e. has been told it exists (``'A'`` for a pod,
    a Muncher's ``'K'``; I App. B, I §7.3). ``except_player`` leaves one holder out: a relayed
    ``'b'`` goes to everyone who holds the pod but its owner, whose own echo would drag it to a
    stale position."""

    entity_id: int
    except_player: int | None = None


type Audience = ToPlayer | ToAll | ToAllExcept | ToTeam | ToHolders


@dataclass(frozen=True, slots=True)
class Event:
    """A server message and its audience."""

    message: ServerMessage
    audience: Audience
