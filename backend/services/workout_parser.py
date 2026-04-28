"""Parse structured workout syntax into plan-compatible data.

Supports a plain-text workout format that coaching agents can generate.
The format is inspired by intervals.icu syntax but simplified for IronCoach.

Example:
    Bike Threshold Workout

    Warmup
    - 10m Z1
    - 5m build Z2-Z3

    Main Set 3x
    - 3m 88-94% FTP 90rpm
    - 2m easy recovery

    Cooldown
    - 10m Z1

Parsed into structured sections with total duration and description.
"""

import re

_DURATION_RE = re.compile(
    r'(\d+)\s*(?:h\s*(\d+)\s*m|h|m(?:in)?|s(?:ec)?|km|mi)',
    re.IGNORECASE
)

_TIME_RE = re.compile(r'^(\d+):(\d{2})$')


def _parse_duration_seconds(text: str) -> int | None:
    """Parse a duration string like '10m', '1h30m', '5:00', '30s' into seconds."""
    text = text.strip()

    # Try MM:SS format
    tm = _TIME_RE.match(text)
    if tm:
        return int(tm.group(1)) * 60 + int(tm.group(2))

    # Try Xh, Xm, Xs patterns
    total = 0
    found = False
    for m in re.finditer(r'(\d+)\s*(h|m(?:in)?|s(?:ec)?)', text, re.IGNORECASE):
        found = True
        val = int(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith('h'):
            total += val * 3600
        elif unit.startswith('m'):
            total += val * 60
        elif unit.startswith('s'):
            total += val
    return total if found else None


def _parse_workout_syntax(text: str) -> dict:
    """Parse structured workout text into a plan-ready dict.

    Returns:
        {
            "title": str,
            "sections": [{"name": str, "repeats": int, "steps": [{"duration_s": int, "target": str}]}],
            "total_duration_min": int,
            "description": str (human-readable summary)
        }
    """
    lines = [l.rstrip() for l in text.strip().splitlines()]
    if not lines:
        return {"title": "", "sections": [], "total_duration_min": 0, "description": ""}

    # First non-empty line without a dash is the title
    title = ""
    start_idx = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("-"):
            title = stripped
            start_idx = i + 1
            break

    sections = []
    current_section = None

    for line in lines[start_idx:]:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("-"):
            # Step line
            step_text = stripped.lstrip("- ").strip()
            dur = None
            target = step_text

            # Extract duration from start of step
            dur_match = re.match(r'^(\d+\s*(?:h\s*\d+\s*m|h|m(?:in)?|s(?:ec)?|\:\d{2}))\s*(.*)', step_text, re.IGNORECASE)
            if dur_match:
                dur = _parse_duration_seconds(dur_match.group(1))
                target = dur_match.group(2).strip() or step_text

            step = {"duration_s": dur or 0, "target": target}
            if current_section:
                current_section["steps"].append(step)
            else:
                current_section = {"name": "Main", "repeats": 1, "steps": [step]}
                sections.append(current_section)
        else:
            # Section header (e.g., "Main Set 3x", "Warmup", "Cooldown")
            repeats = 1
            rm = re.search(r'(\d+)\s*x', stripped, re.IGNORECASE)
            if rm:
                repeats = int(rm.group(1))
                name = stripped[:rm.start()].strip() or stripped
            else:
                name = stripped
            current_section = {"name": name, "repeats": repeats, "steps": []}
            sections.append(current_section)

    # Compute total duration
    total_s = 0
    for sec in sections:
        sec_dur = sum(s["duration_s"] for s in sec["steps"]) * sec["repeats"]
        total_s += sec_dur

    # Build human-readable description
    desc_parts = []
    for sec in sections:
        if sec["steps"]:
            step_strs = []
            for s in sec["steps"]:
                dur_m = s["duration_s"] // 60 if s["duration_s"] else 0
                step_strs.append(f"{dur_m}min {s['target']}" if dur_m else s["target"])
            repeat_str = f" {sec['repeats']}x" if sec["repeats"] > 1 else ""
            desc_parts.append(f"{sec['name']}{repeat_str}: {', '.join(step_strs)}")

    return {
        "title": title,
        "sections": sections,
        "total_duration_min": round(total_s / 60),
        "description": " | ".join(desc_parts),
    }
