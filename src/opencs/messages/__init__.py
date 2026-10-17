"""Messages layer: one typed, immutable class per protocol message, with a registry.

Each message encodes and decodes itself; it must not know what a message causes (DESIGN §3).
"""
