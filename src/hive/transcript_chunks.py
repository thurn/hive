"""Bounded reads of native append-only transcripts, retaining incomplete tails."""

from dataclasses import dataclass
from typing import BinaryIO

MAX_LINE: int = 262_144
MAX_BATCH: int = 1_048_576


@dataclass(frozen=True)
class Line:
    offset: int
    data: bytes


@dataclass(frozen=True)
class Oversize:
    offset: int
    size: int
    first: bool = True


@dataclass(frozen=True)
class Chunk:
    records: tuple[Line | Oversize, ...]
    position: int
    skipping: bool
    incomplete: bool
    read_bytes: int


def read(stream: BinaryIO, position: int, skipping: bool, budget: int) -> Chunk:
    if not MAX_LINE < budget <= MAX_BATCH:
        raise ValueError(
            f"Chunk budget must be {MAX_LINE + 1} through {MAX_BATCH} bytes"
        )
    stream.seek(position)
    content = stream.read(budget)
    records: list[Line | Oversize] = []
    cursor = 0
    incomplete = False
    while cursor < len(content):
        ending = content.find(b"\n", cursor)
        if ending < 0:
            if skipping or len(content) - cursor > MAX_LINE:
                records.append(
                    Oversize(position + cursor, len(content) - cursor, not skipping)
                )
                skipping = True
                cursor = len(content)
            else:
                incomplete = True
            break
        if skipping:
            records.append(Oversize(position + cursor, ending - cursor, False))
        else:
            records.append(
                Oversize(position + cursor, ending - cursor)
                if ending - cursor > MAX_LINE
                else Line(position + cursor, content[cursor:ending])
            )
        skipping = False
        cursor = ending + 1
    return Chunk(tuple(records), position + cursor, skipping, incomplete, len(content))
