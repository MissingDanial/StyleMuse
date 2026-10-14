"""Offline, topic-clustered analysis for paired RAG experiments."""
import itertools
import statistics

import numpy as np

WEIGHTS = {"style": 0.35, "naturalness": 0.25, "topic": 0.20, "originality": 0.20}
MARGIN = 3.0


def quality_score(scores):
    return round(sum(scores[key] * weight for key, weight in WEIGHTS.items()), 4)


def cluster_effect(topic_deltas, seed=20261014, bootstrap_samples=20000):
    """Resample topics, preserving correlated replicates and judge ratings."""
    values = np.asarray([statistics.mean(deltas) for deltas in topic_deltas.values()], dtype=float)
    if not len(values):
        return {"topics": 0, "mean": None, "ci95": None, "exact_signflip_p": None, "verdict": "no_data"}
    mean = float(values.mean())
    draws = np.random.default_rng(seed).choice(values, size=(bootstrap_samples, len(values)), replace=True).mean(axis=1)
    low, high = map(float, np.quantile(draws, [0.025, 0.975]))
    signs = np.asarray(list(itertools.product((-1, 1), repeat=len(values))))
    permutation_means = (signs * values).mean(axis=1)
    p = float(np.mean(np.abs(permutation_means) >= abs(mean) - 1e-10))
    if len(values) < 4:
        verdict = "insufficient_topics"
    elif low > 0 and mean >= MARGIN:
        verdict = "benefit"
    elif high < 0 and mean <= -MARGIN:
        verdict = "harm"
    elif low >= -MARGIN and high <= MARGIN:
        verdict = "within_practical_margin"
    else:
        verdict = "inconclusive"
    return {"topics": len(values), "pairs": sum(len(d) for d in topic_deltas.values()),
            "mean": round(mean, 4), "ci95": [round(low, 4), round(high, 4)],
            "exact_signflip_p": round(p, 5), "verdict": verdict,
            "topic_mean_deltas": {k: round(statistics.mean(v), 4) for k,v in topic_deltas.items()}}


def validate_judgment(value, candidate_ids):
    rows = value.get("candidates") if isinstance(value, dict) else None
    if not isinstance(rows, list) or len(rows) != len(candidate_ids):
        raise ValueError("Judge must score every candidate")
    if {row.get("id") for row in rows} != set(candidate_ids):
        raise ValueError("Judge candidate IDs mismatch")
    for row in rows:
        for key in WEIGHTS:
            if type(row.get(key)) is not int or not 0 <= row[key] <= 100:
                raise ValueError("Invalid judge dimension")
        if not isinstance(row.get("reason"), str):
            raise ValueError("Invalid judge explanation")
    return rows


def analyze(samples, panels):
    complete = [s for s in samples if len(s.get("judgments", [])) == 2 and s["complete"]]
    for row in complete:
        row["quality"] = round(statistics.mean(quality_score(j) for j in row["judgments"]), 4)
        row["judge_mean"] = {key: round(statistics.mean(j[key] for j in row["judgments"]), 4) for key in WEIGHTS}
        row["order_score_gap"] = abs(quality_score(row["judgments"][0]) - quality_score(row["judgments"][1]))
    grouped = {}
    for s in complete:
        grouped.setdefault(s["panel"], {})[s["condition"]] = s
    effects = {}
    contrasts = [("rag-off", "rag", "off"), ("rag-random", "rag", "random"), ("random-off", "random", "off")]
    for stratum in ("hit", "empty"):
        for name, left, right in contrasts:
            deltas = {}
            for panel in panels:
                rows = grouped.get(panel["id"], {})
                if panel["stratum"] == stratum and len(rows) == 3:
                    deltas.setdefault(panel["topic"], []).append(rows[left]["quality"] - rows[right]["quality"])
            effects[stratum + ":" + name] = cluster_effect(deltas)
    summaries = []
    for stratum in ("hit", "empty", "all"):
        for condition in ("off", "rag", "random"):
            group = [s for s in complete if s["condition"] == condition and (stratum == "all" or s["stratum"] == stratum)]
            if not group:
                continue
            summaries.append({"stratum": stratum, "condition": condition, "n": len(group),
                "quality": round(statistics.mean(s["quality"] for s in group), 3),
                **{key: round(statistics.mean(s["judge_mean"][key] for s in group), 3) for key in WEIGHTS},
                "chars": round(statistics.mean(s["chars"] for s in group), 2),
                "length_pass": sum(s["length_pass"] for s in group),
                "copy_fail": sum(s["plagiarism"].get("passed") is False for s in group),
                "prompt_copy_fail": sum(s["prompt_overlap"] > 15 for s in group),
                "max_source_overlap": max(s["plagiarism"]["max_common"] for s in group),
                "llm_seconds": round(statistics.mean(s["llm_seconds"] for s in group), 3),
                "retrieval_ms": round(statistics.mean(s["retrieval_ms"] for s in group), 3),
                "end_to_end_seconds": round(statistics.mean(s["end_to_end_seconds"] for s in group), 3),
                "input_tokens": round(statistics.mean(s["usage"]["input_tokens"] for s in group), 2),
                "output_tokens": round(statistics.mean(s["usage"]["output_tokens"] for s in group), 2)})
    within_margin = wins = losses = 0
    for panel in panels:
        rows = grouped.get(panel["id"], {})
        if panel["stratum"] == "hit" and len(rows) == 3:
            difference = rows["rag"]["quality"] - rows["off"]["quality"]
            wins += difference >= MARGIN
            losses += difference <= -MARGIN
            within_margin += abs(difference) < MARGIN
    empty_hash_equal = [rows["off"]["prompt_sha256"] == rows["rag"]["prompt_sha256"]
                        for panel_id, rows in grouped.items() if len(rows) == 3 and rows["off"]["stratum"] == "empty"]
    return {"complete_samples": len(complete), "expected_samples": len(panels) * 3,
            "groups": summaries, "effects": effects,
            "practical_outcomes_hit": {"rag_wins": wins, "off_wins": losses, "within_3_points": within_margin},
            "empty_prompts_identical": all(empty_hash_equal) if empty_hash_equal else None,
            "mean_order_score_gap": round(statistics.mean(s["order_score_gap"] for s in complete), 3) if complete else None}
