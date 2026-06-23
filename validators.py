import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ValidationResult:
    passed: bool
    checks: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)


def validate_schema(data: dict, required_fields: list) -> bool:
    return all(f in data and data[f] is not None for f in required_fields)


def validate_korean(data: dict, text_fields: list) -> bool:
    """Returns False if any text field is non-empty and contains no Korean characters."""
    pattern = re.compile(r'[가-힣]')
    for f in text_fields:
        val = data.get(f, '')
        if val and not pattern.search(str(val)):
            return False
    return True


def validate_freshness(data_ts: datetime, aired_at: datetime, max_hours: int = 24) -> bool:
    delta = abs((data_ts - aired_at).total_seconds())
    return delta <= max_hours * 3600


def validate_cross_source(text_a: str, text_b: str, keyword: str) -> float:
    """Keyword presence overlap between two sources. Returns 0.0, 0.5, or 1.0."""
    kw = keyword.lower()
    in_a = kw in text_a.lower()
    in_b = kw in text_b.lower()
    if in_a and in_b:
        return 1.0
    if in_a or in_b:
        return 0.5
    return 0.0


def run_all(data: dict, config: dict) -> ValidationResult:
    """
    config keys:
      required_fields: list[str]
      korean_text_fields: list[str]
      aired_at: datetime  (for freshness check)
      max_hours: int
      cross_source: {text_a, text_b, keyword}
    """
    errors: list[str] = []
    checks: dict[str, bool] = {}

    required = config.get('required_fields', [])
    schema_ok = validate_schema(data, required)
    checks['schema'] = schema_ok
    if not schema_ok:
        missing = [f for f in required if f not in data or data[f] is None]
        errors.append(f"Missing fields: {missing}")

    text_fields = config.get('korean_text_fields', [])
    if text_fields:
        korean_ok = validate_korean(data, text_fields)
        checks['korean'] = korean_ok
        if not korean_ok:
            errors.append("Korean text validation failed")

    data_ts = data.get('collected_at')
    aired_at = config.get('aired_at')
    if data_ts and aired_at:
        fresh_ok = validate_freshness(data_ts, aired_at, config.get('max_hours', 24))
        checks['freshness'] = fresh_ok
        if not fresh_ok:
            errors.append(f"Data too old (> {config.get('max_hours', 24)}h after airing)")

    cross_cfg = config.get('cross_source')
    if cross_cfg:
        score = validate_cross_source(
            cross_cfg['text_a'], cross_cfg['text_b'], cross_cfg['keyword']
        )
        checks['cross_source'] = score >= 0.5
        if score < 0.5:
            errors.append(f"Cross-source validation failed (score={score:.2f})")

    passed = all(checks.values()) if checks else False
    return ValidationResult(passed=passed, checks=checks, errors=errors)
