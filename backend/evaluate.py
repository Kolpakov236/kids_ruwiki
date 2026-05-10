from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def sentences(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"(?<=[.!?…])\s+", text) if x.strip()]


def words(text: str) -> list[str]:
    return re.findall(r"[А-Яа-яA-Za-zЁё]+", text.lower())


def syllables_ru(word: str) -> int:
    return max(1, len(re.findall(r"[аеёиоуыэюя]", word.lower())))


def flesch_kincaid_ru(text: str) -> float:
    ws = words(text)
    ss = sentences(text)
    if not ws or not ss:
        return 0.0
    syllables = sum(syllables_ru(w) for w in ws)
    return 0.39 * (len(ws) / len(ss)) + 11.8 * (syllables / len(ws)) - 15.59


def lcs_len(a: list[str], b: list[str]) -> int:
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, start=1):
            cur.append(prev[j - 1] + 1 if x == y else max(prev[j], cur[-1]))
        prev = cur
    return prev[-1]


def rouge_l(pred: str, ref: str) -> float:
    p = words(pred)
    r = words(ref)
    if not p or not r:
        return 0.0
    lcs = lcs_len(p, r)
    precision = lcs / len(p)
    recall = lcs / len(r)
    return 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)


def evaluate_pair(original: str, simplified: str, reference: str | None = None) -> dict:
    out = {
        "original_grade": round(flesch_kincaid_ru(original), 2),
        "simplified_grade": round(flesch_kincaid_ru(simplified), 2),
    }
    out["grade_delta"] = round(out["original_grade"] - out["simplified_grade"], 2)
    if reference:
        out["rouge_l"] = round(rouge_l(simplified, reference), 4)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="JSONL with original, simplified, optional reference")
    args = parser.parse_args()

    rows = []
    for line in args.input.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        rows.append(evaluate_pair(item["original"], item["simplified"], item.get("reference")))

    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

