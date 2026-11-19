"""Reproducible hidden-baseline calibration with isolated data and real physics."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--character", required=True)
    parser.add_argument("--mode", choices=("thresholds", "verify", "theory"), default="verify")
    parser.add_argument("--hp", type=int)
    parser.add_argument("--hp-step", type=int, default=5,
                        help="HP grid for threshold calibration (default: multiples of five)")
    parser.add_argument("--count", type=int, default=2000)
    parser.add_argument("--start-seed", type=int, default=0)
    parser.add_argument("--max-sim-seconds", type=float, default=10_000)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    with tempfile.TemporaryDirectory(prefix="dango-benchmark-") as temporary:
        # Importing project configuration must never touch the player's home.
        os.environ["AEMEATH_DESK_PET_HOME"] = str(Path(temporary) / "home")
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import balance
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.roster import (
            BENCHMARK_ENTRY, ROSTER, find_entry,
        )
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.stats import DangoStats

        if args.character not in {entry.character_id for entry in ROSTER}:
            parser.error("--character must identify a public roster participant")
        if args.hp is not None and args.hp <= 0:
            parser.error("--hp must be positive")
        if args.hp_step <= 0:
            parser.error("--hp-step must be positive")
        if args.mode == "thresholds" and args.hp is not None:
            parser.error("threshold mode chooses HP; do not pass --hp")
        code = ROOT / "lib/script/gemes/packages/official/dango_showdown/code/dango_showdown_pkg"
        source_files = ("engine.py", "skills.py", "roster.py", "model.py", "events.py", "physics.py",
                        "combat.py", "constants.py", "balance.py")
        hashes = {name: hashlib.sha256((code / name).read_bytes()).hexdigest() for name in source_files}
        started = time.perf_counter()
        hp = args.hp if args.hp is not None else find_entry(args.character).max_hp
        report = {"character_id": args.character, "mode": args.mode,
                  "benchmark": asdict(BENCHMARK_ENTRY), "roster": [asdict(entry) for entry in ROSTER],
                  "source_sha256": hashes, "score_definition": "(wins + 0.5 * draws) / all matches",
                  "data_isolation": "Temporary independent home and stats; no player data",
                  "physics": "Production DangoMatch, BO1, fixed 60Hz, normal countdown"}
        if args.mode == "theory":
            try:
                report["theory"] = balance.theoretical_score(args.character, hp)
            except ValueError as error:
                parser.error(str(error))
        else:
            if args.count <= 0 or args.count % 2:
                parser.error("--count must be positive and even")
            if args.max_sim_seconds <= 0:
                parser.error("--max-sim-seconds must be positive")

            def progress(completed: int) -> None:
                if completed % 100 == 0:
                    print(json.dumps({"character": args.character, "completed": completed,
                                      "seconds": round(time.perf_counter()-started, 2)}), flush=True)

            stats = DangoStats(Path(temporary) / "stats.json")
            rows = balance.run_benchmark_series(
                stats, character_id=args.character, count=args.count, start_seed=args.start_seed,
                hp=None if args.mode == "thresholds" else hp,
                thresholds=args.mode == "thresholds", max_sim_seconds=args.max_sim_seconds,
                progress=progress,
            )
            if args.mode == "thresholds":
                hp = balance.choose_hp(rows, step=args.hp_step)
            report.update({"hp": hp, "start_seed": args.start_seed, "matches": rows,
                           "summary": balance.summarize(rows, candidate_hp=hp if args.mode == "thresholds" else None),
                           "interval_method": "Normal 95% interval for the mean of 0/0.5/1 scores, using sample variance"})
            if args.mode == "thresholds":
                report["hp_step"] = args.hp_step
                report["method"] = ("Training-only HP threshold fit from alive-until-baseline-death probes. "
                                    "Independent real-HP fights must validate the chosen HP.")
        if not all(hashlib.sha256((code/name).read_bytes()).hexdigest() == digest
                   for name, digest in hashes.items()):
            raise RuntimeError("Source changed during sampling; discard the run")
        report["wall_seconds"] = time.perf_counter()-started
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: report[key] for key in ("character_id", "mode", "hp", "summary", "theory", "wall_seconds")
                          if key in report}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
