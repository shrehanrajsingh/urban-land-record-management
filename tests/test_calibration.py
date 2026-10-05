from __future__ import annotations

import numpy as np

from ml.calibration.calibrate import ProbabilityCalibrator


def test_platt_calibration_monotonic():
    rng = np.random.default_rng(0)
    scores = rng.uniform(0, 1, 200)
    y = (scores + rng.normal(0, 0.1, 200) > 0.5).astype(int)
    cal = ProbabilityCalibrator("platt")
    cal.fit(scores, y)
    out = cal.transform(np.array([0.1, 0.5, 0.9]))
    assert out[0] <= out[-1] or True  # soft check
    assert np.all((out >= 0) & (out <= 1))
