#!/usr/bin/env python3
"""Compile the bundled NEC-2 Fortran source and compare control-card results."""

import argparse
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


FLOAT = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?"
PATTERN_ROW = re.compile(rf"^\s*({FLOAT})\s+({FLOAT})\s+({FLOAT})")
INPUT_ROW = re.compile(r"^\s*\d+\s+\d+\s+")


def card(code, integers=(), floats=()):
    values = [*integers, *(0,) * (4 - len(integers)), *floats, *(0.0,) * (6 - len(floats))]
    return " ".join([code, *(f"{value:.9g}" for value in values)])


def geometry_card(code, tag, segments, values):
    """Geometry cards have two integer fields and seven real fields."""
    return " ".join([code, str(tag), str(segments), *(f"{value:.9g}" for value in values)])


def deck(lines):
    return "\n".join(["CM Fortran sequencing reference", "CE", *lines, "EN", ""])


COMMON = [
    geometry_card("GW", 1, 21, (-5.03, 0.0, 9.144, 5.03, 0.0, 9.144, 0.001)),
    card("GE", (0,)),
    card("EX", (0, 1, 11, 0), (1.0,)),
]

CASES = {
    "fr_xq_rp": deck([
        *COMMON,
        card("FR", (0, 3), (14.0, 0.1)),
        card("XQ", (0,)),
        card("RP", (0, 3, 2, 1000), (0.0, 0.0, 45.0, 180.0)),
    ]),
    "fr_rp_rp": deck([
        *COMMON,
        card("FR", (0, 3), (14.0, 0.1)),
        card("RP", (0, 3, 2, 1000), (0.0, 0.0, 45.0, 180.0)),
        card("RP", (0, 3, 2, 1000), (10.0, 0.0, 45.0, 180.0)),
    ]),
    "fr_ne_no_xq": deck([
        *COMMON,
        card("FR", (0, 3), (14.0, 0.1)),
        card("NE", (0, 2, 2, 2), (0.0, 0.0, 1.0, 1.0, 1.0, 1.0)),
    ]),
    "repeated_ex_xq": deck([
        *COMMON,
        card("FR", (0, 1), (14.0,)),
        card("XQ", (0,)),
        card("EX", (0, 1, 9, 0), (1.0,)),
        card("XQ", (0,)),
    ]),
}


def run_fortran(binary, source, output):
    result = subprocess.run(
        [str(binary)], input=f"{source}\n{output}\n", capture_output=True, text=True
    )
    if result.returncode:
        raise RuntimeError(f"Fortran NEC-2 failed: {result.stdout}\n{result.stderr}")
    return output.read_text(errors="replace")


def run_onec(onec, source, output):
    result = subprocess.run([str(onec), "-f", "original", "-o", str(output), str(source)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"OpenNEC failed: {result.stderr}")
    return output.read_text(errors="replace")


def summary(report):
    return (
        len(re.findall(r"FREQUENCY[ =:]", report)),
        report.count("ANTENNA INPUT PARAMETERS"),
        report.count("RADIATION PATTERNS"),
    )


def pattern_rows(report):
    rows = []
    for section in report.split("RADIATION PATTERNS")[1:]:
        for line in section.splitlines():
            match = PATTERN_ROW.match(line)
            if match:
                rows.append(tuple(map(float, match.groups())))
    return rows


def input_rows(report):
    rows = []
    for section in report.split("ANTENNA INPUT PARAMETERS")[1:]:
        for line in section.splitlines()[:8]:
            if INPUT_ROW.match(line):
                values = tuple(map(float, re.findall(FLOAT, line)))
                if len(values) >= 8:
                    rows.append(values[:8])
                    break
    return rows


def assert_close(actual, expected, name):
    if len(actual) != len(expected):
        raise AssertionError(f"{name}: {len(actual)} numerical rows != {len(expected)}")
    for index, (left, right) in enumerate(zip(actual, expected)):
        # NEC uses an implementation-defined sentinel at exact pattern nulls.
        if any(value < -300.0 for value in (*left, *right)):
            continue
        if len(left) != len(right) or any(
            not math.isclose(a, b, rel_tol=0.005, abs_tol=0.02) for a, b in zip(left, right)
        ):
            raise AssertionError(f"{name}: row {index} {left} != {right}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--onec", default="./onec")
    parser.add_argument("--gfortran", default=shutil.which("gfortran"))
    args = parser.parse_args()
    if not args.gfortran:
        print("SKIP Fortran reference: gfortran is not installed")
        return

    root = Path(__file__).resolve().parents[2]
    source = root / "tests" / "nec2-1.2.1.2.f"
    shim = Path(__file__).with_name("secnds.c")
    onec = Path(args.onec).resolve()
    with tempfile.TemporaryDirectory(prefix="onec-fortran-reference-") as temporary:
        temp = Path(temporary)
        binary = temp / "nec2-fortran"
        compile_result = subprocess.run(
            # The historical source relies on array aliasing that modern gfortran
            # miscompiles at -O2; Yeti's NEC2 build also uses -O0.
            [args.gfortran, "-std=legacy", "-ffixed-form", "-fallow-argument-mismatch", "-O0", str(source), str(shim), "-o", str(binary)],
            capture_output=True,
            text=True,
        )
        if compile_result.returncode:
            raise RuntimeError(f"Fortran compile failed:\n{compile_result.stderr}")

        for name, text in CASES.items():
            deck_path = temp / f"{name}.nec"
            deck_path.write_text(text)
            fortran_report = run_fortran(binary, deck_path, temp / f"{name}.fortran.out")
            onec_report = run_onec(onec, deck_path, temp / f"{name}.onec.out")
            if summary(fortran_report) != summary(onec_report):
                raise AssertionError(f"{name}: OpenNEC {summary(onec_report)} != Fortran {summary(fortran_report)}")
            assert_close(pattern_rows(onec_report), pattern_rows(fortran_report), f"{name} radiation pattern")
            assert_close(input_rows(onec_report), input_rows(fortran_report), f"{name} input impedance")
            print(f"PASS {name}: OpenNEC matches the compiled Fortran reference")


if __name__ == "__main__":
    main()
