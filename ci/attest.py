"""Render parity evidence; absent or inconsistent evidence fails closed."""
import argparse
import json
from pathlib import Path


def render(result):
    rows = result["results"]
    counts = {s: sum(r["status"] == s for r in rows) for s in ("PASS", "FAIL", "SKIP")}
    valid = (result["status"] == "PASS" and not result.get("error")
             and isinstance(result["drop"], str) and bool(result["drop"])
             and result["counts"] == counts
             and result["compared"] == counts["PASS"] + counts["FAIL"]
             and result["goldens"] == len(rows)
             and all(r["drop"] == result["drop"] for r in rows)
             and all(r["status"] in ("PASS", "SKIP") for r in rows)
             and len({r["table"] for r in rows}) == len(rows))
    lines = ["# DAIWT-01 parity", "",
             f"**{'PASS' if valid else 'FAIL'} {result['drop']}** — "
             f"PASS {counts['PASS']}, FAIL {counts['FAIL']}, SKIP {counts['SKIP']}; "
             f"compared {result['compared']}/{result['goldens']}", ""]
    if result["compared"] == 0:
        lines.extend(["**0 outputs compared — not parity evidence**", ""])
    lines.extend(["| Golden table | Drop | Status | Detail |", "| --- | --- | --- | --- |"])
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    for row in rows:
        lines.append("| " + " | ".join(cell(row.get(k, "")) for k in ("table", "drop", "status", "reason")) + " |")
    if result.get("error"):
        lines.extend(["", cell(result["error"])])
    return "\n".join(lines) + "\n", valid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("result", nargs="*", default=["out/parity.json"])
    parser.add_argument("--output", default="attestation.md")
    args = parser.parse_args()
    sections, ok = [], True
    for path in args.result:
        try:
            body, valid = render(json.loads(Path(path).read_text()))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            body, valid = f"# DAIWT-01 parity\n\n**FAIL** — missing or invalid result: {exc}\n", False
        sections.append(body)
        ok = ok and valid
    Path(args.output).write_text("\n".join(sections))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
