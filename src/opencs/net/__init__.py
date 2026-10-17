"""Net layer: the asyncio listener and one Connection per socket (read pump, bounded outbox, write
pump).

Applies tags at the send boundary. Must not know rules or session phases (DESIGN §3).
"""
