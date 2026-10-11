#!/usr/bin/env python3
"""Numerical regression tests for NEC-2 control-card execution semantics."""

import argparse
import math
import re
import subprocess
import tempfile
from pathlib import Path


FLOAT = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?"
PATTERN_ROW = re.compile(rf"^\s*({FLOAT})\s+({FLOAT})\s+({FLOAT})")
INPUT_ROW = re.compile(r"^\s*\d+\s+\d+\s+")


# Output-section counts produced by the original Fortran NEC-2 (yeti01/nec2, nec2dxs)
# for each deck: (deck, ANTENNA INPUT blocks, RADIATION PATTERNS blocks, NEAR FIELD blocks).
FORTRAN_SECTION_COUNTS = [
    ("fortran_ne_rp.nec", 3, 3, 3),
    ("fortran_ne1.nec", 1, 0, 1),
    ("fortran_nh1.nec", 1, 0, 1),
    ("fortran_sweep_xq_ne.nec", 3, 0, 1),
    ("fortran_sweep_ne_xq.nec", 3, 0, 3),
    ("fortran_sweep_nh_rp.nec", 3, 3, 3),
    ("fortran_sweep_rp_ne.nec", 3, 3, 1),
    ("fortran_v_xq1.nec", 3, 3, 0),
    ("fortran_v_rpavg.nec", 1, 1, 0),
    ("fortran_v_xq_rp_rp.nec", 3, 2, 0),
    ("fortran_v_ne_ne.nec", 1, 0, 2),
    ("fortran_v_xq_ne_xq.nec", 3, 0, 1),
]


def run(onec, deck, output):
    result = subprocess.run(
        [str(onec), "-f", "original", "-o", str(output), str(deck)],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(f"{deck.name}: onec exited {result.returncode}\n{result.stderr}")
    return output.read_text(errors="replace")


def pattern_blocks(report):
    blocks = []
    for section in report.split("RADIATION PATTERNS")[1:]:
        rows = []
        for line in section.splitlines():
            match = PATTERN_ROW.match(line)
            if match:
                rows.append(tuple(map(float, match.groups())))
        if rows:
            blocks.append(rows)
    return blocks


def input_rows(report):
    rows = []
    for section in report.split("ANTENNA INPUT PARAMETERS")[1:]:
        # The input row is immediately below this section's two column headers;
        # later current rows have the same leading numeric shape.
        for line in section.splitlines()[:8]:
            if INPUT_ROW.match(line):
                values = tuple(map(float, re.findall(FLOAT, line)))
                if len(values) < 8:
                    continue
                rows.append(values[:8])
                break
    return rows


def flatten(blocks):
    return [row for block in blocks for row in block]


def assert_close(actual, expected, label, tolerance=0.02):
    if len(actual) != len(expected):
        raise AssertionError(f"{label}: length {len(actual)} != {len(expected)}")
    for index, (left, right) in enumerate(zip(actual, expected)):
        if len(left) != len(right) or any(
            not math.isclose(a, b, abs_tol=tolerance) for a, b in zip(left, right)
        ):
            raise AssertionError(f"{label}: sample {index} differs: {left} != {right}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--onec", default="./onec")
    args = parser.parse_args()

    onec = Path(args.onec).resolve()
    here = Path(__file__).parent
    with tempfile.TemporaryDirectory(prefix="onec-sequencing-audit-") as temporary:
        temp = Path(temporary)

        xq_rp = run(onec, here / "fr_xq_rp_final.nec", temp / "xq-rp.out")
        xq_rp_ref = run(onec, here / "fr_xq_rp_final_reference.nec", temp / "xq-rp-ref.out")
        if xq_rp.count("RADIATION PATTERNS") != 1:
            raise AssertionError("FR/XQ/RP must emit one final-frequency pattern")
        assert_close(flatten(pattern_blocks(xq_rp)), flatten(pattern_blocks(xq_rp_ref)), "FR/XQ/RP final pattern")
        print("PASS FR/XQ/RP: one final-frequency RP numerically matches reference")

        rp_rp = run(onec, here / "fr_rp_rp.nec", temp / "rp-rp.out")
        rp_rp_ref = run(onec, here / "fr_rp_rp_final_reference.nec", temp / "rp-rp-ref.out")
        blocks = pattern_blocks(rp_rp)
        if len(blocks) != 4:
            raise AssertionError(f"FR/RP/RP must emit 4 patterns, got {len(blocks)}")
        assert_close(blocks[-1], flatten(pattern_blocks(rp_rp_ref)), "second RP final pattern")
        print("PASS FR/RP/RP: first RP sweeps; second final-frequency RP matches reference")

        ne_only = run(onec, here / "fr_ne_no_xq.nec", temp / "ne-only.out")
        if "ANTENNA INPUT PARAMETERS" in ne_only or "FREQUENCY=" in ne_only:
            raise AssertionError("FR/NE/EN must not execute without XQ or RP")
        print("PASS FR/NE/EN: no automatic frequency-loop execution")

        repeated = run(onec, here / "repeated_ex_xq.nec", temp / "repeated-ex.out")
        first_ref = run(onec, here / "repeated_ex_first_reference.nec", temp / "first-ref.out")
        second_ref = run(onec, here / "repeated_ex_second_reference.nec", temp / "second-ref.out")
        rows = input_rows(repeated)
        if len(rows) != 2:
            raise AssertionError(f"repeated EX/XQ must emit two impedance rows, got {len(rows)}")
        assert_close([rows[0]], input_rows(first_ref), "first EX/XQ impedance", tolerance=0.03)
        assert_close([rows[1]], input_rows(second_ref), "second EX/XQ impedance", tolerance=0.03)
        if rows[0][1] == rows[1][1]:
            raise AssertionError("repeated EX/XQ did not change the excited segment")
        print("PASS EX/XQ/EX/XQ: two distinct numerical impedance solutions")

        for name, inputs, patterns, near in FORTRAN_SECTION_COUNTS:
            report = run(onec, here / name, temp / (name + ".out"))
            got = (
                report.count("ANTENNA INPUT PARAMETERS"),
                report.count("RADIATION PATTERNS"),
                len(re.findall(r"NEAR (?:ELECTRIC|MAGNETIC) FIELDS", report)),
            )
            if got != (inputs, patterns, near):
                raise AssertionError(f"{name}: (input, pattern, near) {got} != Fortran {(inputs, patterns, near)}")
        print(f"PASS {len(FORTRAN_SECTION_COUNTS)} decks: section counts match original Fortran NEC-2")


if __name__ == "__main__":
    main()
