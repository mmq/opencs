"""Wire layer: number codecs, strings, frame build/parse, incremental frame and line readers.

Must not know about sockets or what any message means (DESIGN §3).
"""

from opencs.wire.errors import WireError

__all__ = ["WireError"]
