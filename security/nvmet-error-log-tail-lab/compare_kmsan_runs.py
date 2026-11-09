#!/usr/bin/env python3
"""Compare unpatched and candidate-fixed KMSAN/response-tail logs."""
from pathlib import Path
import re
import sys

RELEVANT = re.compile(
    r"nvmet_execute_get_log_page_error|nvmet_try_send_data|"
    r"nvmet_tcp|nvmet_setup_c2h_data_pdu",
    re.IGNORECASE,
)
SGL_ORIGIN = re.compile(
    r"Uninit was created at:[\\s\\S]{0,1200}"
    r"sgl_alloc_order[\\s\\S]{0,500}nvmet_tcp_map_data",
    re.IGNORECASE,
)


def reports(path: Path) -> list[str]:
    if not path.is_file():
        return []
    text = path.read_text(errors="replace")
    return [chunk[:16000] for chunk in text.split("BUG: KMSAN:")[1:]]


def relevant_report(path: Path) -> bool:
    return any(RELEVANT.search(report) for report in reports(path))


def sgl_allocation_origin_report(path: Path) -> bool:
    return any(SGL_ORIGIN.search(report) for report in reports(path))


def response_signal(path: Path) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(errors="replace")
    return any(token in text for token in (
        "TEST_MARKER_REAPPEARED", "NONZERO_TAIL_OBSERVED"
    ))


def summary(path: Path) -> dict[str, bool]:
    return {
        "kmsan_nvmet_report": relevant_report(path),
        "kmsan_sgl_allocation_origin": sgl_allocation_origin_report(path),
        "nonzero_or_marker_tail": response_signal(path),
        "any_kmsan_report": bool(reports(path)),
    }


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <unpatched.log> <patched.log>")
        return 64

    original_path, fixed_path = map(Path, sys.argv[1:])
    missing = [str(path) for path in (original_path, fixed_path) if not path.is_file()]
    if missing:
        print(f"KMSAN_COMPARISON=INCOMPLETE_MISSING_LOGS: {', '.join(missing)}")
        return 0

    original = summary(original_path)
    fixed = summary(fixed_path)
    print(f"UNPATCHED={original}")
    print(f"PATCHED={fixed}")

    original_signal = (
        original["kmsan_nvmet_report"] or
        original["kmsan_sgl_allocation_origin"] or
        original["nonzero_or_marker_tail"]
    )
    fixed_signal = (
        fixed["kmsan_nvmet_report"] or
        fixed["kmsan_sgl_allocation_origin"] or
        fixed["nonzero_or_marker_tail"]
    )

    if (original["kmsan_sgl_allocation_origin"] and
            not fixed["any_kmsan_report"] and
            not fixed["nonzero_or_marker_tail"]):
        print("KMSAN_COMPARISON=UNINITIALIZED_SGL_PAGE_ORIGIN_CONFIRMED_AND_SUPPRESSED")
    elif original_signal and not fixed_signal:
        print("KMSAN_COMPARISON=ORIGINAL_DISCLOSURE_SIGNAL_SUPPRESSED_BY_CANDIDATE_FIX")
    elif original_signal and fixed_signal:
        print("KMSAN_COMPARISON=CANDIDATE_FIX_DID_NOT_SUPPRESS_SIGNAL")
        return 1
    else:
        print("KMSAN_COMPARISON=INCONCLUSIVE_NO_ORIGINAL_DISCLOSURE_SIGNAL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
