#!/usr/bin/env python3
"""Automatically run the daily MLB workflow on a fixed interval."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs" / "auto_runner"
STATE_PATH = ROOT / "data" / "auto_runner_status.json"


def now_local() -> datetime:
    return datetime.now().astimezone()


def today_str() -> str:
    return now_local().date().isoformat()


def append_log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{today_str()}.log"
    stamp = now_local().strftime("%Y-%m-%d %H:%M:%S %z")
    line = f"[{stamp}] {message}"
    encoding = sys.stdout.encoding or "utf-8"
    safe_line = line.encode(encoding, errors="replace").decode(encoding, errors="replace")
    print(safe_line, flush=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def print_console(text: str, end: str = "\n") -> None:
    encoding = sys.stdout.encoding or "utf-8"
    safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    print(safe_text, end=end, flush=True)


def write_state(**updates: object) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = {}
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
    state.update(updates)
    state["updated_at_tw"] = now_local().isoformat(timespec="seconds")
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def run_workflow(args: argparse.Namespace) -> int:
    target_date = args.date or today_str()
    command = [
        sys.executable,
        "scripts/run_daily_workflow.py",
        "--date",
        target_date,
        "--unit",
        str(args.unit),
        "--min-edge",
        str(args.min_edge),
        "--all-predictions",
        "--skip-backtest-refresh",
    ]
    if args.skip_odds_fetch:
        command.append("--skip-odds-fetch")

    append_log(f"start workflow date={target_date}")
    append_log("command: " + " ".join(command))
    write_state(
        mode="running",
        target_date=target_date,
        last_started_at_tw=now_local().isoformat(timespec="seconds"),
        last_command=command,
    )

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    child_log = LOG_DIR / f"{target_date}.workflow.log"
    with child_log.open("a", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            print_console(line, end="")
            log_file.write(line)
        return_code = process.wait()

    status = "success" if return_code == 0 else "failed"
    append_log(f"finish workflow date={target_date} status={status} return_code={return_code}")
    write_state(
        mode=status,
        target_date=target_date,
        last_finished_at_tw=now_local().isoformat(timespec="seconds"),
        last_return_code=return_code,
        dashboard=str(ROOT / "docs" / "index.html"),
        daily_predictions=str(ROOT / "docs" / "daily_predictions.html"),
        betting_ticket=str(ROOT / "docs" / "betting_ticket.html"),
        log=str(child_log),
    )
    return return_code


def open_dashboard() -> None:
    index_path = ROOT / "docs" / "index.html"
    if index_path.exists():
        webbrowser.open(index_path.resolve().as_uri())
        append_log(f"opened dashboard {index_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-run MLB daily workflow.")
    parser.add_argument("--date", default=None, help="Force a date in YYYY-MM-DD. Default: today.")
    parser.add_argument("--interval-minutes", type=int, default=60, help="Loop interval. Default: 60.")
    parser.add_argument("--once", action="store_true", help="Run once and exit.")
    parser.add_argument("--start-now", action="store_true", help="Run immediately before sleeping.")
    parser.add_argument("--open-dashboard", action="store_true", help="Open docs/index.html after successful run.")
    parser.add_argument("--unit", type=float, default=100.0)
    parser.add_argument("--min-edge", type=float, default=0.0)
    parser.add_argument("--skip-odds-fetch", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    interval = max(5, args.interval_minutes)

    if args.once:
        return_code = run_workflow(args)
        if return_code == 0 and args.open_dashboard:
            open_dashboard()
        raise SystemExit(return_code)

    append_log(f"auto runner started interval_minutes={interval}")
    if args.start_now:
        return_code = run_workflow(args)
        if return_code == 0 and args.open_dashboard:
            open_dashboard()

    while True:
        next_run = now_local() + timedelta(minutes=interval)
        write_state(mode="sleeping", next_run_at_tw=next_run.isoformat(timespec="seconds"))
        append_log(f"next run at {next_run.strftime('%Y-%m-%d %H:%M:%S %z')}")
        time.sleep(interval * 60)
        run_workflow(args)


if __name__ == "__main__":
    main()
