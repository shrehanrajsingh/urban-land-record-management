"""Training pipeline for the match probability model."""

from __future__ import annotations

import os
import pickle
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    log_loss,
    precision_score,
    recall_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ml.calibration.calibrate import ProbabilityCalibrator
from ml.matcher.features import FEATURE_COLUMNS, features_to_vector


def generate_training_data(
    scenario_seeds: list[int] = list(range(1, 21)),
    exclude_seeds: list[int] = [42],
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Generate labelled training pairs from benchmark scenarios.

    For each seed, generate the scenario, find spatial candidates within 50 m,
    compute pair features, and label from ground-truth correspondences.

    Returns (X, y, feature_names).
    """
    from benchmark.scenario import generate_scenario
    from geospatial.geometry.features import build_pair_features

    all_X: list[list[float]] = []
    all_y: list[int] = []

    seeds = [s for s in scenario_seeds if s not in exclude_seeds]

    for seed in seeds:
        scenario = generate_scenario(seed=seed)
        legacy_gdf = scenario["legacy"]
        survey_gdf = scenario["survey"]
        correspondences = scenario["ground_truth"]["correspondences"]

        # Build lookup: legacy_id -> set of reference_ids it maps to
        legacy_to_ref: dict[str, set[str]] = {}
        for corr in correspondences:
            lid = corr["legacy_id"]
            # Legacy parcels in the GDF are prefixed with "L-"
            legacy_key = f"L-{lid}" if not lid.startswith("L-") else lid
            ref_ids = set(corr.get("reference_ids") or [])
            legacy_to_ref.setdefault(legacy_key, set()).update(ref_ids)

        # For each legacy parcel, find survey candidates within 50m
        for _, legacy_row in legacy_gdf.iterrows():
            legacy_id = legacy_row["parcel_id"]
            legacy_geom = legacy_row.geometry
            legacy_attrs = {
                k: v for k, v in legacy_row.items() if k not in ("geometry",)
            }

            for _, survey_row in survey_gdf.iterrows():
                survey_id = survey_row["parcel_id"]
                survey_geom = survey_row.geometry

                dist = legacy_geom.distance(survey_geom)
                if dist > 50.0:
                    continue

                survey_attrs = {
                    k: v for k, v in survey_row.items() if k not in ("geometry",)
                }

                feats = build_pair_features(
                    legacy_geom,
                    survey_geom,
                    legacy_attrs,
                    survey_attrs,
                    gnss_support=0.0,
                    building_support=0.0,
                )

                vec = features_to_vector(feats)
                all_X.append(vec)

                # Label: 1 if survey_id is in the correspondence set for this legacy parcel
                ref_set = legacy_to_ref.get(legacy_id, set())
                label = 1 if survey_id in ref_set else 0
                all_y.append(label)

    X = np.array(all_X, dtype=float)
    y = np.array(all_y, dtype=int)
    return X, y, list(FEATURE_COLUMNS)


def _reliability_bins(
    y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10
) -> list[dict[str, Any]]:
    """Compute reliability diagram bins."""
    bins = []
    edges = np.linspace(0, 1, n_bins + 1)
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (y_prob >= lo) & (y_prob < hi)
        if not mask.any():
            continue
        bins.append({
            "bin": f"{lo:.1f}-{hi:.1f}",
            "count": int(mask.sum()),
            "mean_predicted": float(y_prob[mask].mean()),
            "mean_observed": float(y_true[mask].mean()),
        })
    return bins


def train_and_evaluate(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: list[str],
    output_path: str = "data/models/matcher.pkl",
) -> dict:
    """Train, compare, calibrate, evaluate, and save the match model.

    1. Stratified 60/20/20 split
    2. Train LogisticRegression and HistGradientBoostingClassifier with 5-fold CV
    3. Compare validation log-loss; keep GBM only if >5% better
    4. Apply Platt calibration on calibration split
    5. Report on test split
    6. Save model
    7. Return metrics dict
    """
    n = len(y)
    rng = np.random.default_rng(42)
    idx = np.arange(n)
    rng.shuffle(idx)

    n_train = int(0.6 * n)
    n_cal = int(0.2 * n)
    train_idx = idx[:n_train]
    cal_idx = idx[n_train : n_train + n_cal]
    test_idx = idx[n_train + n_cal :]

    X_train, y_train = X[train_idx], y[train_idx]
    X_cal, y_cal = X[cal_idx], y[cal_idx]
    X_test, y_test = X[test_idx], y[test_idx]

    # --- Logistic Regression ---
    lr_pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(class_weight="balanced", max_iter=1000, random_state=42)),
    ])
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    lr_cv_scores = cross_val_score(
        lr_pipe, X_train, y_train, cv=cv, scoring="neg_log_loss"
    )
    lr_cv_logloss = float(-lr_cv_scores.mean())
    lr_pipe.fit(X_train, y_train)

    # --- HistGradientBoosting ---
    gbm_pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", HistGradientBoostingClassifier(
            max_iter=200, learning_rate=0.1, max_depth=5,
            random_state=42,
        )),
    ])
    gbm_cv_scores = cross_val_score(
        gbm_pipe, X_train, y_train, cv=cv, scoring="neg_log_loss"
    )
    gbm_cv_logloss = float(-gbm_cv_scores.mean())
    gbm_pipe.fit(X_train, y_train)

    # --- Compare: keep GBM only if >5% better ---
    use_gbm = gbm_cv_logloss < lr_cv_logloss * 0.95
    chosen_pipe = gbm_pipe if use_gbm else lr_pipe
    chosen_name = "HistGradientBoosting" if use_gbm else "LogisticRegression"

    # --- Platt calibration on calibration split ---
    cal_probs = chosen_pipe.predict_proba(X_cal)[:, 1]
    calibrator = ProbabilityCalibrator(method="platt")
    calibrator.fit(cal_probs, y_cal)

    # --- Evaluate on test split ---
    test_probs_raw = chosen_pipe.predict_proba(X_test)[:, 1]
    test_probs = calibrator.transform(test_probs_raw)
    test_preds = (test_probs >= 0.5).astype(int)

    brier = float(brier_score_loss(y_test, test_probs))
    acc = float(accuracy_score(y_test, test_preds))
    prec = float(precision_score(y_test, test_preds, zero_division=0))
    rec = float(recall_score(y_test, test_preds, zero_division=0))
    reliability = _reliability_bins(y_test, test_probs)

    # --- Feature importances / coefficients ---
    if use_gbm:
        clf = chosen_pipe.named_steps["clf"]
        importances = dict(zip(feature_names, [float(v) for v in clf.feature_importances_]))
    else:
        clf = chosen_pipe.named_steps["clf"]
        importances = dict(zip(feature_names, [float(v) for v in clf.coef_[0]]))

    # --- Save ---
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        pickle.dump(
            {
                "pipeline": chosen_pipe,
                "calibrator": calibrator,
                "version": f"matcher-2.0-{chosen_name.lower()}",
                "cols": feature_names,
                "model_type": chosen_name,
            },
            f,
        )

    metrics = {
        "chosen_model": chosen_name,
        "lr_cv_logloss": lr_cv_logloss,
        "gbm_cv_logloss": gbm_cv_logloss,
        "use_gbm": use_gbm,
        "brier_score": brier,
        "accuracy": acc,
        "precision": prec,
        "recall": rec,
        "reliability_bins": reliability,
        "feature_importances": importances,
        "n_train": len(y_train),
        "n_cal": len(y_cal),
        "n_test": len(y_test),
        "positive_rate_train": float(y_train.mean()),
        "positive_rate_test": float(y_test.mean()),
    }
    return metrics
