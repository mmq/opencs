"""Session layer: a connection's phases (Login, Chamber, InGame) as explicit state machines.

Turns decoded client messages into calls on the lobby or a city. Must not know game rules
(DESIGN §3).
"""
