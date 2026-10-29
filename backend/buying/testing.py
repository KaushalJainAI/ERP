"""Test helper: a fresh supplier bill number (Purchase Invoices need one to submit)."""

import itertools

_seq = itertools.count(1)


def next_bill_no() -> str:
    return f"BILL/{next(_seq):05d}"
