import argparse
import json
from collections import defaultdict


def _score_changes(changes: dict) -> list[tuple[str, int]]:
    scored = [(name, len(values)) for name, values in changes.items()]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def analyze(log_path: str, top_n: int) -> None:
    attr_changes = defaultdict(set)
    text_changes = defaultdict(set)

    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            for item in record.get("items", []):
                key = "|".join([
                    item.get("tag", ""),
                    item.get("id", ""),
                    item.get("attrs", {}).get("data-tid", ""),
                    item.get("attrs", {}).get("aria-label", ""),
                ])
                attr_changes[f"{key}::className"].add(item.get("className", ""))
                attrs = item.get("attrs", {})
                for name, value in attrs.items():
                    attr_changes[f"{key}::{name}"].add(value)
                text = item.get("text", "")
                if text:
                    text_changes[key].add(text)

    print("Top changing attributes:")
    for name, count in _score_changes(attr_changes)[:top_n]:
        print(f"  {name} changes={count}")

    print("\nTop changing text nodes:")
    for name, count in _score_changes(text_changes)[:top_n]:
        print(f"  {name} changes={count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze Teams DOM probe log")
    parser.add_argument("--log", default="dom_probe.log")
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()
    analyze(args.log, args.top)
