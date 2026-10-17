"""World layer: one City per joinable game; pure rules, calls in and events out.

Must not know sockets, bytes, or the source of the clock; never does I/O and never awaits
(DESIGN §3).
"""
