"""Systematic Reed–Solomon over GF(256), polynomial 0x11d.

Each byte position is an independent stripe. A Vandermonde generator is
normalised so its first k rows are the identity. This is erasure decoding:
corruption must be rejected with trusted hashes before decoding.
"""
from functools import lru_cache
from itertools import islice

EXP = [0] * 512
LOG = [0] * 256
x = 1
for i in range(255):
    EXP[i] = x
    LOG[x] = i
    x <<= 1
    if x & 256:
        x ^= 0x11d
for i in range(255, 512):
    EXP[i] = EXP[i - 255]


def mul(a, b):
    return EXP[LOG[a] + LOG[b]] if a and b else 0


def inverse(matrix):
    n = len(matrix)
    rows = [list(row) + [int(i == j) for j in range(n)] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = next((i for i in range(col, n) if rows[i][col]), None)
        if pivot is None:
            raise ValueError('singular matrix')
        rows[col], rows[pivot] = rows[pivot], rows[col]
        scale = EXP[255 - LOG[rows[col][col]]]
        rows[col] = [mul(v, scale) for v in rows[col]]
        for i in range(n):
            if i != col:
                factor = rows[i][col]
                rows[i] = [a ^ mul(factor, b) for a, b in zip(rows[i], rows[col])]
    return [row[n:] for row in rows]


@lru_cache(maxsize=16)
def generator(k, n):
    if not 1 <= k <= n <= 255:
        raise ValueError('require 1 <= k <= n <= 255')
    vand = [[1] + [EXP[(LOG[i + 1] * j) % 255] for j in range(1, k)] for i in range(n)]
    inv = inverse(vand[:k])
    return tuple(tuple(_dot(row, [inv[t][j] for t in range(k)]) for j in range(k)) for row in vand)


def _dot(a, b):
    out = 0
    for x, y in zip(a, b):
        out ^= mul(x, y)
    return out


@lru_cache(maxsize=256)
def _table(factor):
    return bytes(mul(factor, b) for b in range(256))


def _combine(coefficients, shards):
    # translate performs GF multiplication in C; integer XOR handles whole stripes.
    result = 0
    for factor, shard in zip(coefficients, shards):
        if factor:
            result ^= int.from_bytes(shard if factor == 1 else shard.translate(_table(factor)), 'little')
    return result.to_bytes(len(shards[0]), 'little')


def encode(data: bytes, k: int, n: int) -> tuple[bytes, ...]:
    matrix = generator(k, n)
    size = max(1, (len(data) + k - 1) // k)
    padded = data.ljust(size * k, b'\0')
    source = [padded[i * size:(i + 1) * size] for i in range(k)]
    return tuple(_combine(row, source) for row in matrix)


def decode(fragments: dict[int, bytes], k: int, n: int, original_size: int) -> bytes:
    matrix = generator(k, n)
    if len(fragments) < k:
        raise ValueError('insufficient distinct fragments')
    if any(type(i) is not int or not 0 <= i < n for i in fragments):
        raise ValueError('invalid fragment index')
    chosen = list(islice(sorted(fragments.items()), k))
    shards = [value for _, value in chosen]
    if not shards[0] or len({len(value) for value in fragments.values()}) != 1:
        raise ValueError('inconsistent fragment sizes')
    if not 0 <= original_size <= k * len(shards[0]):
        raise ValueError('invalid original size')
    inv = inverse([matrix[index] for index, _ in chosen])
    return b''.join(_combine(row, shards) for row in inv)[:original_size]
