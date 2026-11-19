"""
scripts/benchmark_databases.py
Head-to-Head Relational vs. Graph Database Benchmark Harness (FR-0.4).

Executes identical simulated student cohorts side-by-side against:
  - Adapter A: StandaloneRelationalAdapter (SQLite / PostgreSQL)
  - Adapter B: StandaloneGraphAdapter (Property Graph)
  - Adapter C: MemoryReferenceAdapter (In-Memory Baseline)

Measures p50, p95, p99 latency and write throughput across 5 canonical workloads:
  1. Knowledge Frontier Scan (compute_knowledge_frontier)
  2. Multi-Hop FIRe Propagation (get_encompassed_ancestors + apply_submission_and_propagate_fire)
  3. Set-Cover Review Compression (compute_compressed_review_cover)
  4. Diagnostic Plus-Minus Propagation
  5. Concurrent Student Submission Writes
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT / "src"))

from trellis.domain.models import (
    InteractionAttempt,
    StudentProfile,
    StudentTopicState,
    XPLedgerEntry,
)
from trellis.engine.frontier_service import FrontierService
from trellis.engine.hsrs_fire_engine import HSRSFireEngine
from trellis.engine.session_scheduler import SessionScheduler
from trellis.engine.topology_service import TopologyService
from trellis.ports.storage_port import get_storage_adapter

GRAPH_PATH = WORKSPACE_ROOT / "data" / "clean_spec" / "trellis_knowledge_graph.json"


def percentile(data: List[float], p: float) -> float:
    """Calculates percentile p in [0, 100]."""
    if not data:
        return 0.0
    sorted_d = sorted(data)
    idx = int((len(sorted_d) - 1) * (p / 100.0))
    return sorted_d[idx]


def benchmark_adapter(
    adapter_name: str,
    graph_data: Dict[str, Any],
    num_iterations: int = 50,
) -> Dict[str, Any]:
    """Runs the 5 canonical workloads against a specific adapter."""
    print(f"\n---> Benchmarking Adapter: {adapter_name.upper()} ({num_iterations} iterations per workload) <---")

    adapter = get_storage_adapter(adapter_name)

    # Ingestion benchmark
    t0 = time.perf_counter()
    report = adapter.ingest_knowledge_graph(graph_data)
    ingestion_time_ms = (time.perf_counter() - t0) * 1000.0
    print(f"  [Ingestion] Ingested {report.nodes_ingested} nodes in {ingestion_time_ms:.1f} ms")

    courses = adapter.get_course_catalog()
    target_course_id = courses[0].course_id if courses else "CRS_FND_K2"

    # Setup simulated students
    sim_students = [f"usr_bench_{i:03d}" for i in range(10)]
    for sid in sim_students:
        adapter.upsert_student_profile(
            StudentProfile(
                student_id=sid,
                enrolled_course_id=target_course_id,
                xp_total=100,
                daily_xp_target=40,
            )
        )

    # -----------------------------------------------------------------------
    # Workload 1: Knowledge Frontier Scan
    # -----------------------------------------------------------------------
    latencies_w1 = []
    for i in range(num_iterations):
        sid = sim_students[i % len(sim_students)]
        t_start = time.perf_counter()
        frontier = adapter.compute_knowledge_frontier(sid, target_course_id)
        t_end = time.perf_counter()
        latencies_w1.append((t_end - t_start) * 1000.0)

    # -----------------------------------------------------------------------
    # Workload 2: Multi-Hop FIRe Propagation
    # -----------------------------------------------------------------------
    sample_nodes = adapter.get_course_subgraph(target_course_id)[:10]
    sample_node_ids = [n.id for n in sample_nodes] if sample_nodes else ["trl_fnd_num_001"]

    latencies_w2 = []
    for i in range(num_iterations):
        nid = sample_node_ids[i % len(sample_node_ids)]
        sid = sim_students[i % len(sim_students)]

        t_start = time.perf_counter()
        paths = adapter.get_encompassed_ancestors(nid, max_hops=2)
        # Apply FIRe credit update
        updated_states = []
        for p in paths:
            updated_states.append(
                StudentTopicState(
                    student_id=sid,
                    topic_id=p.ancestor_id,
                    status="MASTERED",
                    cumulative_fire_credit=p.compound_weight,
                )
            )
        now = datetime.now(timezone.utc)
        adapter.apply_submission_and_propagate_fire(
            attempt=InteractionAttempt(
                attempt_id=f"att_w2_{i}",
                student_id=sid,
                topic_id=nid,
                kp_id=f"{nid}#kp_0",
                submitted_answer="1",
                is_correct=True,
                duration_ms=2000,
                created_at=now,
            ),
            updated_states=updated_states,
            xp_entry=XPLedgerEntry(
                entry_id=f"xp_w2_{i}",
                student_id=sid,
                topic_id=nid,
                task_type="FRONTIER_LESSON",
                quality_tier="FULL_XP",
                delta_xp=3,
                reason="Workload 2 benchmark test",
                created_at=now,
            ),
        )
        t_end = time.perf_counter()
        latencies_w2.append((t_end - t_start) * 1000.0)

    # -----------------------------------------------------------------------
    # Workload 3: Set-Cover Review Compression
    # -----------------------------------------------------------------------
    # Seed 15 topics as DUE_FOR_REVIEW for benchmark student
    bench_sid = sim_students[0]
    for nid in sample_node_ids[:5]:
        adapter.upsert_student_topic_state(
            StudentTopicState(
                student_id=bench_sid,
                topic_id=nid,
                status="DUE_FOR_REVIEW",
            )
        )

    latencies_w3 = []
    for i in range(num_iterations):
        t_start = time.perf_counter()
        plan = adapter.compute_compressed_review_cover(bench_sid)
        t_end = time.perf_counter()
        latencies_w3.append((t_end - t_start) * 1000.0)

    # -----------------------------------------------------------------------
    # Workload 4: Diagnostic Plus-Minus Propagation
    # -----------------------------------------------------------------------
    latencies_w4 = []
    for i in range(num_iterations):
        nid = sample_node_ids[i % len(sample_node_ids)]
        sid = sim_students[i % len(sim_students)]
        t_start = time.perf_counter()
        parents = adapter.get_hard_parents(nid)
        postreqs = adapter.get_postrequisites(nid, max_hops=2)
        # Update signed balances
        for pid in parents:
            adapter.upsert_student_topic_state(
                StudentTopicState(
                    student_id=sid,
                    topic_id=pid,
                    diagnostic_plus_minus_balance=0.75,
                )
            )
        t_end = time.perf_counter()
        latencies_w4.append((t_end - t_start) * 1000.0)

    # -----------------------------------------------------------------------
    # Workload 5: Concurrent Student Submission Writes
    # -----------------------------------------------------------------------
    latencies_w5 = []
    now = datetime.now(timezone.utc)
    for i in range(num_iterations):
        sid = sim_students[i % len(sim_students)]
        nid = sample_node_ids[i % len(sample_node_ids)]
        t_start = time.perf_counter()
        adapter.apply_submission_and_propagate_fire(
            attempt=InteractionAttempt(
                attempt_id=f"att_w5_{i}",
                student_id=sid,
                topic_id=nid,
                kp_id=f"{nid}#kp_0",
                submitted_answer="42",
                is_correct=True,
                duration_ms=1800,
                created_at=now,
            ),
            updated_states=[
                StudentTopicState(
                    student_id=sid,
                    topic_id=nid,
                    status="IN_PROGRESS",
                    consecutive_correct=1,
                )
            ],
            xp_entry=XPLedgerEntry(
                entry_id=f"xp_w5_{i}",
                student_id=sid,
                topic_id=nid,
                task_type="FRONTIER_LESSON",
                quality_tier="FULL_XP",
                delta_xp=4,
                reason="Workload 5 write",
                created_at=now,
            ),
        )
        t_end = time.perf_counter()
        latencies_w5.append((t_end - t_start) * 1000.0)

    # -----------------------------------------------------------------------
    # Workload 6: Tier-4 Session Scheduler Refresh (FR-4.1 - FR-4.3)
    # -----------------------------------------------------------------------
    topo_w6 = TopologyService(adapter)
    frontier_svc_w6 = FrontierService(adapter, topology_service=topo_w6)
    hsrs_engine_w6 = HSRSFireEngine(adapter)
    scheduler_w6 = SessionScheduler(adapter, frontier_service=frontier_svc_w6, hsrs_engine=hsrs_engine_w6)

    w6_sid = f"usr_bench_w6_{adapter_name}"
    adapter.upsert_student_profile(
        StudentProfile(
            student_id=w6_sid,
            enrolled_course_id=target_course_id,
            xp_total=500,
            daily_xp_target=50,
        )
    )
    all_sample_nodes = adapter.get_course_subgraph(target_course_id)
    for idx, node in enumerate(all_sample_nodes[:15]):
        status = "DUE_FOR_REVIEW" if idx % 3 == 0 else "MASTERED"
        fire_credit = 0.40 if idx % 2 == 0 else 0.65
        mem_str = 0.45 if status == "DUE_FOR_REVIEW" else 0.65
        adapter.upsert_student_topic_state(
            StudentTopicState(
                student_id=w6_sid,
                topic_id=node.id,
                status=status,
                memory_strength=mem_str,
                cumulative_fire_credit=fire_credit,
                stability_interval_days=7.0,
                last_reviewed_at=datetime.now(timezone.utc) - timedelta(days=idx * 2 + 1),
            )
        )

    # Warmup scheduler caches
    scheduler_w6.refresh_dashboard(w6_sid)

    latencies_w6 = []
    for _ in range(num_iterations):
        t_start = time.perf_counter()
        scheduler_w6.refresh_dashboard(w6_sid)
        t_end = time.perf_counter()
        latencies_w6.append((t_end - t_start) * 1000.0)

    if hasattr(adapter, "close"):
        adapter.close()

    def stats(lats: List[float]) -> Dict[str, float]:
        return {
            "mean_ms": round(statistics.mean(lats), 3),
            "p50_ms": round(percentile(lats, 50), 3),
            "p95_ms": round(percentile(lats, 95), 3),
            "p99_ms": round(percentile(lats, 99), 3),
        }

    return {
        "adapter": adapter_name,
        "ingestion_time_ms": round(ingestion_time_ms, 2),
        "workloads": {
            "w1_frontier_scan": stats(latencies_w1),
            "w2_fire_propagation": stats(latencies_w2),
            "w3_review_compression": stats(latencies_w3),
            "w4_diagnostic_propagation": stats(latencies_w4),
            "w5_submission_writes": stats(latencies_w5),
            "w6_session_scheduler_refresh": stats(latencies_w6),
        },
    }


def main() -> None:
    if not GRAPH_PATH.exists():
        print(f"[ERROR] Graph file not found at: {GRAPH_PATH}", file=sys.stderr)
        sys.exit(1)

    print("Loading clean-room knowledge graph...")
    graph_data = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    print(f"Loaded graph with {len(graph_data.get('nodes', []))} nodes.")

    results = []
    # Benchmark Adapter C (Memory) and Adapter A (Relational) and Adapter B (Graph)
    for backend in ["memory", "relational", "graph"]:
        res = benchmark_adapter(backend, graph_data, num_iterations=40)
        results.append(res)

    print("\n" + "=" * 70)
    print("=== TRELLIS HEAD-TO-HEAD DATABASE BENCHMARK RESULTS (FR-0.4) ===")
    print("=" * 70)

    for r in results:
        print(f"\nADAPTER: {r['adapter'].upper()}")
        print(f"  Ingestion Latency: {r['ingestion_time_ms']} ms")
        for w_name, w_stats in r["workloads"].items():
            print(
                f"  {w_name:<26} -> p50: {w_stats['p50_ms']:>6.3f} ms | "
                f"p95: {w_stats['p95_ms']:>6.3f} ms | p99: {w_stats['p99_ms']:>6.3f} ms"
            )

    report_path = WORKSPACE_ROOT / "scripts" / "benchmark_results.json"
    report_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\n[PASS] Benchmark completed successfully. Saved full metrics to {report_path}")


if __name__ == "__main__":
    main()
