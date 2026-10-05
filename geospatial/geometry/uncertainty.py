"""Practical relative uncertainty model for MVP."""

from __future__ import annotations

import math


def relative_uncertainty(sigma1: float, sigma2: float, sigma_registration: float) -> float:
    """σ_relative = sqrt(σ1² + σ2² + σ_registration²)"""
    return math.sqrt(sigma1**2 + sigma2**2 + sigma_registration**2)


def search_radius(
    source_uncertainty: float,
    registration_uncertainty: float,
    local_error: float = 0.0,
    k: float = 3.0,
    min_radius: float = 1.0,
) -> float:
    base = relative_uncertainty(source_uncertainty, 0.0, registration_uncertainty)
    return max(min_radius, k * (base + local_error))


def within_uncertainty_envelope(observed_displacement: float, sigma_relative: float, k: float = 3.0) -> bool:
    return observed_displacement <= k * sigma_relative
