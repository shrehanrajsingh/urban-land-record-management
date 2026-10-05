"""Evaluation metrics using ground-truth labels."""

from __future__ import annotations

from typing import Any


def conflation_precision(predicted_correct: int, predicted_total: int) -> float:
    if predicted_total == 0:
        return 0.0
    return predicted_correct / predicted_total


def conflict_recall(detected: int, known: int) -> float:
    if known == 0:
        return 1.0
    return detected / known


def silent_error_rate(unauthorized_publishes: int) -> float:
    return float(unauthorized_publishes)


def relation_accuracy(
    predicted_relations: list[dict[str, Any]],
    ground_truth_correspondences: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Compute per-relation-type accuracy comparing predicted matches
    against ground truth correspondences.
    """
    gt_lookup: dict[str, dict] = {}
    for gt in ground_truth_correspondences:
        legacy_id = gt.get("legacy_id", "")
        gt_lookup[legacy_id] = gt

    correct = 0
    total = 0
    by_type: dict[str, dict] = {}

    for pred in predicted_relations:
        source_id = pred.get("source_parcel_id", "")
        pred_relation = pred.get("relation", "")
        pred_targets = set(pred.get("target_parcel_ids", []))

        gt = gt_lookup.get(source_id)
        if gt is None:
            continue

        total += 1
        gt_relation = gt.get("relation_type", "")
        gt_targets = set(gt.get("reference_ids", []))

        # Check relation type match
        type_match = pred_relation == gt_relation
        # Check target match (at least one target in common)
        target_match = bool(pred_targets & gt_targets) if pred_targets and gt_targets else pred_relation == "NO_MATCH" and gt_relation == "NO_MATCH"

        is_correct = type_match and target_match
        if is_correct:
            correct += 1

        if gt_relation not in by_type:
            by_type[gt_relation] = {"correct": 0, "total": 0}
        by_type[gt_relation]["total"] += 1
        if is_correct:
            by_type[gt_relation]["correct"] += 1

    per_type = {}
    for rtype, counts in by_type.items():
        per_type[rtype] = {
            "accuracy": counts["correct"] / counts["total"] if counts["total"] > 0 else 0.0,
            "correct": counts["correct"],
            "total": counts["total"],
        }

    return {
        "overall_accuracy": correct / total if total > 0 else 0.0,
        "correct": correct,
        "total": total,
        "per_relation_type": per_type,
    }


def conflict_detection_metrics(
    detected_conflicts: list[dict[str, Any]],
    injected_conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare detected conflicts against known injected conflicts."""
    injected_ids = {c.get("parcel_id", "") for c in injected_conflicts}
    detected_ids = set()
    for c in detected_conflicts:
        for pid in c.get("parcel_ids", []):
            detected_ids.add(pid)

    true_pos = injected_ids & detected_ids
    false_neg = injected_ids - detected_ids
    false_pos = detected_ids - injected_ids

    precision = len(true_pos) / (len(true_pos) + len(false_pos)) if (len(true_pos) + len(false_pos)) > 0 else 0
    recall = len(true_pos) / len(injected_ids) if len(injected_ids) > 0 else 1.0

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(2 * precision * recall / (precision + recall), 4) if (precision + recall) > 0 else 0.0,
        "true_positives": sorted(true_pos),
        "false_negatives": sorted(false_neg),
        "false_positive_count": len(false_pos),
    }


def registration_quality(
    regional_stats: dict[str, Any],
    uncertainty_threshold: float = 0.5,
) -> dict[str, Any]:
    """Evaluate registration quality from regional statistics."""
    if not regional_stats:
        return {"pass": False, "reason": "No regional stats available"}

    failing_regions = []
    passing_regions = []
    for region, stats in regional_stats.items():
        p95 = stats.get("p95", 999)
        if p95 > uncertainty_threshold * 1.5:
            failing_regions.append({"region": region, "p95": p95})
        else:
            passing_regions.append({"region": region, "p95": p95})

    return {
        "pass": len(failing_regions) == 0,
        "passing_regions": len(passing_regions),
        "failing_regions": failing_regions,
        "worst_p95": max((s.get("p95", 0) for s in regional_stats.values()), default=0),
    }


def summarize_benchmark(results: dict[str, Any]) -> dict[str, Any]:
    return {
        "conflation_precision_auto_accept": results.get("conflation_precision"),
        "conflict_recall": results.get("conflict_recall"),
        "silent_error_rate": results.get("silent_error_rate", 0),
        "manual_effort_reduction": results.get("manual_effort_reduction"),
        "throughput_parcels_per_min": results.get("throughput"),
        "relation_accuracy": results.get("relation_accuracy"),
        "conflict_detection": results.get("conflict_detection"),
        "registration_quality": results.get("registration_quality"),
        "targets": {
            "conflation_precision": 0.95,
            "conflict_recall": 0.95,
            "silent_error_rate": 0,
            "manual_effort_reduction": 0.50,
        },
    }
