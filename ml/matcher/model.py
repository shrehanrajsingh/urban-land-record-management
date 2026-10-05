"""Interpretable logistic regression matcher + optional GBM comparison."""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ml.calibration.calibrate import ProbabilityCalibrator
from ml.matcher.features import FEATURE_COLUMNS, features_to_vector


class MatchProbabilityModel:
    def __init__(self):
        self.pipeline: Pipeline | None = None
        self.calibrator: ProbabilityCalibrator | None = None
        self.version = "matcher-1.0-logreg"
        self._feature_names: list[str] = list(FEATURE_COLUMNS)
        self._model_type: str = "LogisticRegression"

    def fit(self, X: np.ndarray, y: np.ndarray) -> dict[str, Any]:
        self.pipeline = Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "clf",
                    LogisticRegression(
                        class_weight="balanced",
                        max_iter=1000,
                        random_state=42,
                    ),
                ),
            ]
        )
        self.pipeline.fit(X, y)
        train_acc = float(self.pipeline.score(X, y))
        return {"train_accuracy": train_acc, "n": int(len(y)), "version": self.version}

    def predict_proba(self, features: dict[str, float] | list[dict[str, float]]) -> np.ndarray:
        if self.pipeline is None:
            raise RuntimeError(
                "Model not trained or loaded. Call fit() or load() first."
            )
        if isinstance(features, dict):
            X = np.array([features_to_vector(features)])
        else:
            X = np.array([features_to_vector(f) for f in features])
        probs = self.pipeline.predict_proba(X)[:, 1]
        if self.calibrator is not None:
            probs = self.calibrator.transform(probs)
        return probs

    def feature_importances(self) -> dict[str, float]:
        """Return feature name -> importance (coefficients for LR, importances for GBM)."""
        if self.pipeline is None:
            return {}
        clf = self.pipeline.named_steps["clf"]
        if hasattr(clf, "feature_importances_"):
            values = clf.feature_importances_
        elif hasattr(clf, "coef_"):
            values = clf.coef_[0]
        else:
            return {}
        return dict(zip(self._feature_names, [float(v) for v in values]))

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(
                {
                    "pipeline": self.pipeline,
                    "calibrator": self.calibrator,
                    "version": self.version,
                    "cols": self._feature_names,
                    "model_type": self._model_type,
                },
                f,
            )

    def load(self, path: str | Path) -> None:
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.pipeline = data["pipeline"]
        self.calibrator = data.get("calibrator")
        self.version = data.get("version", self.version)
        self._feature_names = data.get("cols", list(FEATURE_COLUMNS))
        self._model_type = data.get("model_type", "LogisticRegression")


def generate_synthetic_training_pairs(
    n: int = 1000,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """Generate random feature vectors for bootstrapping the matcher model.

    Used for initial training when ground-truth labelled pairs are not yet
    available.  Each sample is a feature vector with len(FEATURE_COLUMNS) dims.
    """
    rng = np.random.default_rng(seed)
    n_features = len(FEATURE_COLUMNS)
    X = rng.random((n, n_features))

    # Simulate realistic ranges per column
    # centroid_distance: 0-50m
    X[:, 0] *= 50
    # boundary_distance: 0-30m
    X[:, 1] *= 30
    # iou: 0-1
    # intersection_area: 0-1200 sq m
    X[:, 3] *= 1200
    # area_ratio, perimeter_ratio, shape_sim, orientation_sim, attr_sim: 0-1
    # gnss_support, building_support: 0-1

    # Label: high IoU + low distance → match
    score = X[:, 2] * 0.4 - X[:, 0] / 50 * 0.3 + X[:, 4] * 0.15 + X[:, 8] * 0.15
    y = (score > rng.uniform(0.1, 0.4, n)).astype(int)

    return X, y
