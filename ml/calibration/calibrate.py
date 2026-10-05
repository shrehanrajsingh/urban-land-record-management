"""Probability calibration via Platt scaling or isotonic regression."""

from __future__ import annotations

from typing import Literal

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression


class ProbabilityCalibrator:
    def __init__(self, method: Literal["platt", "isotonic"] = "platt"):
        self.method = method
        self._iso: IsotonicRegression | None = None
        self._platt: LogisticRegression | None = None

    def fit(self, scores: np.ndarray, y: np.ndarray) -> None:
        scores = scores.reshape(-1, 1) if scores.ndim == 1 else scores
        if self.method == "isotonic":
            self._iso = IsotonicRegression(out_of_bounds="clip")
            self._iso.fit(scores.ravel(), y)
        else:
            self._platt = LogisticRegression(max_iter=1000)
            self._platt.fit(scores, y)

    def transform(self, scores: np.ndarray) -> np.ndarray:
        if self.method == "isotonic" and self._iso is not None:
            return self._iso.transform(scores.ravel())
        if self._platt is not None:
            s = scores.reshape(-1, 1) if scores.ndim == 1 else scores
            return self._platt.predict_proba(s)[:, 1]
        return scores.ravel()
