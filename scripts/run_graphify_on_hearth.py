"""Build a graphify knowledge graph of the Hearth codebase.

Standalone wrapper around graphify's library API. Outputs land in
`graphify-out/` at the repo root: a clickable HTML, a markdown report,
and the full graph JSON for ad-hoc queries.
"""
from __future__ import annotations

import time
from pathlib import Path

from graphify import detect, extract, build, analyze, report, export
from graphify import cluster as graph_cluster


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "graphify-out"


def main() -> int:
    print(f"[graphify] root: {ROOT}")
    print(f"[graphify] out:  {OUT}")
    OUT.mkdir(exist_ok=True)

    # 1. Detect — graphify.detect.detect(root) walks the directory and
    # returns a dict with {files: {code: [...], document: [...], ...}}.
    # Exclude sibling repos so the graph stays focused on Hearth.
    extra_excludes = [
        "jan-main", "claude-code-main", "hermes-agent-main", "openhuman-main",
        "ollama-main", "openclaw-main", "awesome-openclaw-skills-main",
        "oh-my-openagent-dev", "superpowers-main", "clawdbot-main",
        "graphify-7", "graphify-out", "Windows Terminal",
        ".venv", "venv", "node_modules", "dist", "build", "__pycache__",
        ".git", ".pytest_cache", ".mypy_cache",
    ]
    t = time.perf_counter()
    manifest = detect.detect(ROOT, extra_excludes=extra_excludes)
    files_by_kind = manifest["files"]
    total = sum(len(v) for v in files_by_kind.values())
    print(f"[graphify] detect: {total} files in {(time.perf_counter()-t)*1000:.0f}ms "
          f"(code={len(files_by_kind['code'])}, "
          f"docs={len(files_by_kind['document'])}, "
          f"papers={len(files_by_kind['paper'])}, "
          f"images={len(files_by_kind['image'])}, "
          f"video={len(files_by_kind['video'])})")
    print(f"[graphify] corpus: ~{manifest.get('total_words', 0):,} words")

    # 2. Extract — graphify.extract.extract takes a LIST of paths and does
    # both per-file structural extraction (classes, functions, imports) AND
    # cross-file import resolution in one shot. We focus on code; docs/papers
    # are content-like and graphify's doc extractor needs an LLM, which we'd
    # have to wire up separately.
    code_files = [Path(p) for p in files_by_kind.get("code", [])]
    t = time.perf_counter()
    extraction = extract.extract(code_files, cache_root=ROOT)
    n_ext_nodes = len(extraction.get("nodes", []))
    n_ext_edges = len(extraction.get("edges", []))
    print(f"[graphify] extract: {n_ext_nodes:,} nodes, {n_ext_edges:,} edges "
          f"from {len(code_files)} code files in {time.perf_counter()-t:.1f}s")

    # 3. Build the NetworkX graph — build.build takes a list of extractions
    t = time.perf_counter()
    g = build.build([extraction])
    print(f"[graphify] build:   {g.number_of_nodes()} nodes, {g.number_of_edges()} edges "
          f"in {time.perf_counter()-t:.1f}s")

    # 3a. Cluster (Leiden community detection). Returns {community_id: [nodes]}.
    # Community labels are derived from the most frequent token in node names.
    t = time.perf_counter()
    communities = graph_cluster.cluster(g)
    # Derive a human label per community = most common word in member node names.
    community_labels = {}
    import re as _re
    from collections import Counter as _Counter
    for cid, members in communities.items():
        words = []
        for nid in members:
            words.extend(_re.findall(r"[A-Za-z]{4,}", str(nid).lower()))
        common = _Counter(words).most_common(1)
        community_labels[cid] = common[0][0] if common else f"community-{cid}"
    print(f"[graphify] cluster: {len(communities)} communities "
          f"in {time.perf_counter()-t:.1f}s "
          f"(largest: {community_labels[0]!r} with {len(communities[0])} nodes)")

    # 4. Analyze — god nodes (highest degree), cycles, surprising connections,
    # suggested questions. Each is its own function in the analyze module.
    t = time.perf_counter()
    gods = analyze.god_nodes(g)
    cycles = analyze.find_import_cycles(g)
    surprises = analyze.surprising_connections(g, communities=communities)
    questions = analyze.suggest_questions(g, communities, community_labels)
    analysis = {
        "god_nodes": gods,
        "cycles": cycles,
        "surprising_connections": surprises,
        "suggested_questions": questions,
        "communities": communities,
        "community_labels": community_labels,
    }
    print(f"[graphify] analyze: {len(gods)} god-nodes, {len(cycles)} cycles, "
          f"{len(surprises)} surprises, {len(questions)} questions "
          f"in {time.perf_counter()-t:.1f}s")

    # 4a. Cohesion score per community — higher = tighter cluster.
    cohesion_scores = graph_cluster.score_all(g, communities)

    # 5. Report — needs the full bundle of analysis artefacts.
    md = report.generate(
        g,
        communities=communities,
        cohesion_scores=cohesion_scores,
        community_labels=community_labels,
        god_node_list=gods,
        surprise_list=surprises,
        detection_result=manifest,
        token_cost={"total_input_tokens": 0, "total_output_tokens": 0,
                    "input_cost_usd": 0.0, "output_cost_usd": 0.0,
                    "total_cost_usd": 0.0},
        root=str(ROOT),
        suggested_questions=questions,
    )
    report_path = OUT / "GRAPH_REPORT.md"
    report_path.write_text(md, encoding="utf-8")
    print(f"[graphify] report:  {report_path.name} ({len(md):,} chars)")

    # 6. Export — JSON for queries, HTML for click-through
    t = time.perf_counter()
    json_path = OUT / "graph.json"
    export.to_json(g, communities, str(json_path), force=True)
    print(f"[graphify] json:    {json_path.name} ({json_path.stat().st_size:,} bytes)")
    html_path = OUT / "graph.html"
    export.to_html(g, communities, str(html_path), community_labels=community_labels)
    print(f"[graphify] html:    {html_path.name} ({html_path.stat().st_size:,} bytes) "
          f"in {time.perf_counter()-t:.1f}s")

    # 7. Final hint
    print()
    print(f"OPEN  : {html_path}    (click + filter the Hearth codebase)")
    print(f"READ  : {report_path}  (god-nodes, cycles, surprises, suggested questions)")
    print(f"QUERY : {json_path}    (full graph data for ad-hoc analysis)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
