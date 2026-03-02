#!/usr/bin/env python3
"""
Training Data Export — Autonomous Factory
══════════════════════════════════════════
Exports DaC tags + learning log as JSONL for model fine-tuning.
Format: {"input": ..., "tag": ..., "output": ..., "metadata": {...}}
══════════════════════════════════════════

Usage:
    python scripts/export_training_data.py [--output data.jsonl] [--project PROJECT_ID]
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestration.database import ReadOnlyDB


def export_training_data(db_path: str, output_path: str,
                          project_id: str = None) -> int:
    """Export training data as JSONL."""
    db = ReadOnlyDB(db_path)
    count = 0

    with open(output_path, 'w') as f:
        # Export resolved DaC tags with learning log
        entries = db.get_training_export(project_id=project_id)
        for entry in entries:
            record = {
                "input": entry.get("context", ""),
                "tag": entry.get("tag_type", ""),
                "output": entry.get("resolution", entry.get("fix_applied", "")),
                "metadata": {
                    "source_step": entry.get("source_step"),
                    "source_worker": entry.get("source_worker"),
                    "project_type": entry.get("project_type"),
                    "complexity": entry.get("complexity"),
                    "bug_description": entry.get("bug_description"),
                    "root_cause": entry.get("root_cause"),
                    "prevention_strategy": entry.get("prevention_strategy"),
                    "keywords": entry.get("keywords"),
                },
            }
            f.write(json.dumps(record) + "\n")
            count += 1

        # Export training_data table entries
        training = db.get_learning_log(limit=10000)
        for entry in training:
            record = {
                "input": entry.get("bug_description", ""),
                "tag": "LEARNING",
                "output": entry.get("fix_applied", ""),
                "metadata": {
                    "root_cause": entry.get("root_cause"),
                    "prevention_strategy": entry.get("prevention_strategy"),
                    "project_type": entry.get("project_type"),
                    "phase": entry.get("phase"),
                    "occurrence_count": entry.get("occurrence_count", 1),
                    "fixed_by": entry.get("fixed_by"),
                },
            }
            f.write(json.dumps(record) + "\n")
            count += 1

    return count


def main():
    parser = argparse.ArgumentParser(description="Export training data as JSONL")
    parser.add_argument("--output", "-o", default="training_data.jsonl",
                        help="Output JSONL file path")
    parser.add_argument("--project", "-p", default=None,
                        help="Filter by project ID")
    parser.add_argument("--db", default=None,
                        help="Database path (default: auto-detect)")
    args = parser.parse_args()

    # Auto-detect DB path
    db_path = args.db
    if not db_path:
        candidates = [
            os.path.expanduser("~/working/autonomous_factory/factory_state/factory.db"),
            "factory_state/factory.db",
        ]
        for c in candidates:
            if os.path.exists(c):
                db_path = c
                break

    if not db_path or not os.path.exists(db_path):
        print("Error: Database not found. Specify with --db flag.")
        sys.exit(1)

    count = export_training_data(db_path, args.output, args.project)
    print(f"Exported {count} training records to {args.output}")


if __name__ == "__main__":
    main()
