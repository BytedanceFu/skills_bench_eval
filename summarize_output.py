import argparse
import json
from pathlib import Path


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def summarize(output_dir: Path) -> dict:
    tasks: list[str] = []

    total_input_tokens = 0
    total_output_tokens = 0
    completed = 0
    passed = 0
    errors = 0
    total_score = 0.0
    total_duration_seconds = 0.0

    for entry in sorted(output_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        result_path = entry / "result.json"
        if not result_path.exists():
            continue

        try:
            data = _load_json(result_path)
        except Exception:
            tasks.append(entry.name)
            errors += 1
            continue

        task_name = data.get("task") or entry.name
        tasks.append(task_name)

        usage = data.get("usage") or {}
        in_tok = usage.get("input_tokens") or 0
        out_tok = usage.get("output_tokens") or 0
        if isinstance(in_tok, str) and in_tok.isdigit():
            in_tok = int(in_tok)
        if isinstance(out_tok, str) and out_tok.isdigit():
            out_tok = int(out_tok)
        if isinstance(in_tok, int):
            total_input_tokens += in_tok
        if isinstance(out_tok, int):
            total_output_tokens += out_tok

        if data.get("status") == "completed":
            completed += 1
        if data.get("error") not in (None, "", {}):
            errors += 1

        verification = data.get("verification") or {}
        if verification.get("passed") is True:
            passed += 1
        score = verification.get("test_score")
        if _is_number(score):
            total_score += float(score)

        start = data.get("start_time")
        end = data.get("end_time")
        if _is_number(start) and _is_number(end) and end >= start:
            total_duration_seconds += float(end - start)

    total_tasks = len(tasks)
    pass_rate = (passed / total_tasks) if total_tasks else 0.0
    avg_duration_seconds = (total_duration_seconds / total_tasks) if total_tasks else 0.0

    avg_input_tokens = (total_input_tokens / total_tasks) if total_tasks else 0.0
    avg_output_tokens = (total_output_tokens / total_tasks) if total_tasks else 0.0
    avg_total_tokens = avg_input_tokens + avg_output_tokens

    return {
        "total_tasks": total_tasks,
        "completed": completed,
        "passed": passed,
        "errors": errors,
        "total_usage": {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "total_tokens": total_input_tokens + total_output_tokens,
        },
        "avg_usage": {
            "input_tokens": avg_input_tokens,
            "output_tokens": avg_output_tokens,
            "total_tokens": avg_total_tokens,
        },
        "pass_rate": pass_rate,
        "score": total_score,
        "timing": {
            "total_seconds": total_duration_seconds,
            "avg_seconds": avg_duration_seconds,
        },
        "tasks": tasks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--summary-path", default=None)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent
    output_dir = (repo_root / args.output_dir).resolve()
    summary_path = (
        Path(args.summary_path).resolve()
        if args.summary_path is not None
        else (output_dir / "summary.json")
    )

    summary = summarize(output_dir)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
