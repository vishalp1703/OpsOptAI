"""
dashboard/metrics.py

Derived metrics that aren't a straight column read from the DB:
  - Operational Risk Score
  - AI Confidence health

Both are HEURISTICS (v1) — the original Phase 5 spec named these metrics but
didn't define the exact formula, so I made a reasonable, documented,
easy-to-tune choice rather than guessing silently. Adjust the weights below
freely; nothing else in the dashboard depends on the internals, only on the
0-100 score this returns.
"""

import pandas as pd

# Higher = more severe. Tune to match your actual Severity enum values if they differ.
SEVERITY_WEIGHTS = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}


def _normalize_severity(s):
    if pd.isna(s):
        return None
    return str(s).strip().lower().replace("severity.", "")


def compute_risk_score(df: pd.DataFrame) -> dict:
    """
    Operational Risk Score (0-100), computed only over classified rows
    (unclassified rows have no severity to score).

    Formula:
      base = mean(severity_weight) / max_weight            -> 0-1, how severe on average
      volume_factor = min(1, classified_count / 50)         -> ramps up 0-1 as more data comes in,
                                                                 so 2-3 rows don't swing the score wildly
      risk_score = round(base * volume_factor * 100)

    This intentionally UNDER-reports risk when there's very little classified
    data yet (your case: 12/320 rows) rather than overstating confidence in a
    tiny sample. As more of the 320 complaints get classified, volume_factor
    climbs toward 1 and the score reflects the true severity mix.
    """
    if df.empty or "severity" not in df.columns:
        return {"score": None, "n_classified": 0, "note": "No severity data available yet."}

    sev = df["severity"].map(_normalize_severity).dropna()
    sev = sev[sev.isin(SEVERITY_WEIGHTS.keys())]
    n = len(sev)
    if n == 0:
        return {"score": None, "n_classified": 0, "note": "No classified complaints yet."}

    weights = sev.map(SEVERITY_WEIGHTS)
    base = weights.mean() / max(SEVERITY_WEIGHTS.values())
    volume_factor = min(1.0, n / 50)
    score = round(base * volume_factor * 100, 1)

    return {
        "score": score,
        "n_classified": n,
        "note": f"Based on {n} classified complaint(s); score ramps up as more data is classified.",
    }


def confidence_health(df: pd.DataFrame) -> dict:
    """Simple distribution stats over the AI confidence field."""
    if df.empty or "confidence" not in df.columns:
        return {"mean": None, "min": None, "low_confidence_count": 0}
    conf = pd.to_numeric(df["confidence"], errors="coerce").dropna()
    if conf.empty:
        return {"mean": None, "min": None, "low_confidence_count": 0}
    low_thresh = 0.7
    return {
        "mean": round(conf.mean(), 3),
        "min": round(conf.min(), 3),
        "low_confidence_count": int((conf < low_thresh).sum()),
        "low_thresh": low_thresh,
    }