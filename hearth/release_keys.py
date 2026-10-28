"""The public keys a code patch must be signed with (Ed25519, raw 32 bytes, base64).

The private key never enters this repository: the person who builds releases
keeps it (scripts/patch_keys.py makes one and signs with it). Two keys can be
listed while one is being replaced, so a lost or retired key can be rotated out
in a release signed by the other.

Empty means this build refuses every patch: add the key before building.
"""
PUBLIC_KEYS: list = ["RiTndrxL0eKGUFGPIx9T48QnK90c+1WebtVzCaP3U24="]
