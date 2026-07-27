import json
import sys
from pathlib import Path

work_order_path = Path(sys.argv[1]).resolve()
work_order = json.loads(work_order_path.read_text(encoding="utf-8"))
repo = Path(work_order["repo"]).resolve()
(repo / "reports").mkdir(parents=True, exist_ok=True)
(repo / "logs").mkdir(parents=True, exist_ok=True)
(repo / "logs" / "worker.log").write_text(
    "local worker wrote structured output\n",
    encoding="utf-8",
)
(repo / "reports" / "worker-output.json").write_text(
    json.dumps(
        {
            "schema": "tau.local_worker_output.v1",
            "status": "PASS",
            "summary": "worker emitted non-sensitive operational output",
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
result = {
    "schema": "tau.omp_worker_result.v1",
    "status": "PASS",
    "goal_hash": work_order["goal_hash"],
    "changed_files": ["reports/worker-output.json"],
    "artifacts": ["reports/worker-output.json", "logs/worker.log"],
    "tests_run": [
        {
            "name": "local-worker-script",
            "status": "PASS",
            "log_path": "logs/worker.log",
        }
    ],
    "findings": [],
    "next_recommended_route": "reviewer",
}
(repo / work_order["result_path"]).write_text(
    json.dumps(result, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(json.dumps({"status": "PASS", "result_path": str(repo / work_order["result_path"])}))
