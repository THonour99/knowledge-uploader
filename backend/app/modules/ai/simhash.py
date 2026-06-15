from __future__ import annotations

import hashlib
import re

MASK_64 = (1 << 64) - 1
SIGN_BIT_64 = 1 << 63
MASK_16 = (1 << 16) - 1
SIGN_BIT_16 = 1 << 15
BIT_MARGIN = 2


def compute_simhash(text: str) -> int:
    tokens = tokenize(text)
    if not tokens:
        return 0
    vector = [0] * 64
    for token in tokens:
        hashed = _hash_token(token)
        weight = _token_weight(token)
        for bit_index in range(64):
            bit = 1 << bit_index
            vector[bit_index] += weight if hashed & bit else -weight
    fingerprint = 0
    for bit_index, score in enumerate(vector):
        if score > BIT_MARGIN:
            fingerprint |= 1 << bit_index
    return to_signed_64(fingerprint)


def tokenize(text: str) -> list[str]:
    units = re.findall(r"[a-z0-9][a-z0-9_-]*|[\u4e00-\u9fff]+", text.lower())
    tokens: list[str] = []
    for unit in units:
        if _is_cjk(unit):
            tokens.extend(_character_ngrams(unit, size=2))
            tokens.extend(_character_ngrams(unit, size=3))
            if len(unit) == 1:
                tokens.append(unit)
        else:
            tokens.append(unit)
    tokens.extend(f"{tokens[index]} {tokens[index + 1]}" for index in range(len(tokens) - 1))
    return tokens


def hamming_distance(left: int, right: int) -> int:
    return ((left ^ right) & MASK_64).bit_count()


def simhash_bands(fingerprint: int) -> tuple[int, int, int, int]:
    unsigned = fingerprint & MASK_64
    return (
        _to_signed_16(unsigned & MASK_16),
        _to_signed_16((unsigned >> 16) & MASK_16),
        _to_signed_16((unsigned >> 32) & MASK_16),
        _to_signed_16((unsigned >> 48) & MASK_16),
    )


def to_signed_64(value: int) -> int:
    unsigned = value & MASK_64
    if unsigned >= SIGN_BIT_64:
        return unsigned - (1 << 64)
    return unsigned


def _to_signed_16(value: int) -> int:
    unsigned = value & MASK_16
    if unsigned >= SIGN_BIT_16:
        return unsigned - (1 << 16)
    return unsigned


def _is_cjk(value: str) -> bool:
    return all("\u4e00" <= char <= "\u9fff" for char in value)


def _character_ngrams(value: str, *, size: int) -> list[str]:
    if len(value) < size:
        return []
    return [value[index : index + size] for index in range(len(value) - size + 1)]


def _hash_token(token: str) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=False)


def _token_weight(token: str) -> int:
    if " " in token:
        return 2
    if len(token) >= 4:
        return 2
    return 1
