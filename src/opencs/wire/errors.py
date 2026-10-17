"""The one exception the wire layer raises for malformed or unencodable data."""


class WireError(ValueError):
    """A value cannot be encoded, or bytes cannot be decoded, under the wire rules (I §4-§6).

    Decode functions raise only this on hostile input; callers drop the message and count it
    (DESIGN §5).
    """
