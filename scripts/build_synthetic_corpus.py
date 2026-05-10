"""Generate a synthetic Algerian reference corpus for the eval harness.

Synthetic only (CLAUDE.md hard rule #7). Phone numbers, NIN, NIF, RIB and
IBAN-DZ entries are generated with valid checksums where applicable so
detector accuracy is measured against actually-correct identifiers. The
output is a JSONL file consumable by `scripts/eval_corpus.py`.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from src.substitution.synthetic import _digit_string_for, _rib_for


def _nin(seed: str) -> str:
    body = _digit_string_for(seed, length=18)
    body = ("1" if random.random() < 0.5 else "2") + body[1:]
    body = body[:1] + "1985" + body[5:]
    return body


def _nif(seed: str) -> str:
    return _digit_string_for(seed, length=15)


def _build_record(idx: int) -> dict[str, object]:
    seed = f"corpus-{idx}"
    nin = _nin(seed)
    nif = _nif(seed + "n")
    rib = _rib_for(seed + "r")
    fragments = []
    spans = []
    cursor = 0

    def push(prefix: str, value: str, etype: str) -> None:
        nonlocal cursor
        fragments.append(prefix)
        cursor += len(prefix)
        start = cursor
        fragments.append(value)
        spans.append({"start": start, "end": start + len(value), "entity_type": etype})
        cursor += len(value)
        fragments.append(". ")
        cursor += 2

    push("NIN ", nin, "national_id")
    push("NIF ", nif, "tax_id")
    push("RIB ", rib, "bank_account")
    push("Tel ", "+213 555 00 00 0" + str(idx % 10), "phone")
    return {"id": f"synth-{idx:04d}", "country": "DZ", "text": "".join(fragments), "spans": spans}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--count", type=int, default=100)
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for i in range(args.count):
            rec = _build_record(i)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
