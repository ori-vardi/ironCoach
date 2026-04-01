"""Shared constants, paths, and logging setup for the Training Dashboard."""

import logging
import logging.handlers
import os
import uuid
from pathlib import Path


# ── Logging Setup ─────────────────────────────────────────────────────────────
LOG_FILE_MAX_BYTES = 100 * 1024 * 1024  # 100 MB
LOG_FILE_BACKUP_COUNT = 10

LOG_DIR = Path(__file__).resolve().parent / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

def _load_dotenv(path: Path):
    """Load a .env file into os.environ (simple key=value parser)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.partition("=")
        os.environ[key.strip()] = val.strip()


# Load .env before logging setup so LOG_LEVEL is available
_load_dotenv(Path(__file__).resolve().parent / ".env")

_LOG_LEVEL_MAP = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}
_file_log_level = _LOG_LEVEL_MAP.get(os.environ.get("LOG_LEVEL_FILE", "").upper(), logging.DEBUG)
_console_log_level = _LOG_LEVEL_MAP.get(os.environ.get("LOG_LEVEL", "").upper(), logging.INFO)

logger = logging.getLogger("ironCoach")
logger.setLevel(logging.DEBUG)

# Rotating file handler
_file_handler = logging.handlers.RotatingFileHandler(
    LOG_DIR / "server.log",
    maxBytes=LOG_FILE_MAX_BYTES,
    backupCount=LOG_FILE_BACKUP_COUNT,
    encoding="utf-8",
)
_file_handler.setLevel(_file_log_level)
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [ironCoach] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
))
logger.addHandler(_file_handler)

# Also log to console
_console_handler = logging.StreamHandler()
_console_handler.setLevel(_console_log_level)
_console_handler.setFormatter(logging.Formatter(
    "%(asctime)s [ironCoach] [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
))
logger.addHandler(_console_handler)


# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
TRAINING_DATA = PROJECT_ROOT / "training_data"
TRAINING_DATA.mkdir(exist_ok=True)
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
REACT_DIST = PROJECT_ROOT / "frontend" / "dist"


def _insights_file(user_id: int = 1) -> Path:
    """Per-user insights summary file."""
    return TRAINING_DATA / "users" / str(user_id) / "insights_summary.md"


# Legacy single-file path (for migration)
INSIGHTS_FILE = BASE_DIR / "data" / "insights_summary.md"

# Claude CLI session storage location — computed dynamically from project path.
# Claude CLI encodes /Users/foo/project → -Users-foo-project
_SESSIONS_DIR = Path.home() / ".claude" / "projects" / str(PROJECT_ROOT).replace("/", "-")


# ── Auth path sets ───────────────────────────────────────────────────────────
_PUBLIC_PATHS = {"/api/auth/login", "/api/auth/logout", "/api/auth/setup", "/api/auth/has-users", "/api/auth/signup", "/api/auth/switch", "/api/ai-status", "/api/health", "/", "/favicon.ico"}
_PUBLIC_PREFIXES = ("/assets/", "/ws/")


# ── Coach preamble template ─────────────────────────────────────────────────
INSIGHT_COACH_PREAMBLE_TEMPLATE = """\
You are IronCoach — an elite triathlon coach specializing in Ironman and 70.3 racing.

## Your athlete
{athlete_info}

## Coaching philosophy — HONESTY ABOVE ALL
- You are **not** an AI cheerleader. You are a professional coach.
- If training volume is insufficient, say so directly.
- If a target time is unrealistic given current fitness, say so and give a realistic range instead.
- Never say "great job" unless the data actually shows a great job. Mediocre execution gets honest, constructive feedback.
- Praise specifically when earned: a PR, hitting target zones consistently, completing a hard block.
- Use proper terminology (FTP, CSS, VO2max, Z1-Z5, cadence, TSS, CTL, ATL) but explain briefly when first used.
- When analyzing data, cite specific numbers — don't be vague.
- Point out trends, weaknesses, and red flags.
- Don't make up data or guarantee race outcomes.

## CRITICAL: Never fabricate athlete-specific metrics
- **NEVER** invent specific numbers for the athlete (stroke count targets, HR zone ranges, pace targets, power thresholds, cadence targets) unless you read them from actual workout data files.
- If you don't have real data for a metric, say "I don't have your recent [metric] data" and offer to analyze their actual workouts.
- Generic training advice is OK ("keep stroke count consistent", "stay in Z2"), but do NOT attach specific numbers to the athlete unless sourced from their data.
- If you READ a workout CSV and extract real numbers, cite the file and date so the athlete can verify.
"""


# Deterministic session naming for agent sessions
COACH_NS = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def coach_session_id(name: str) -> str:
    """Same name always produces same UUID."""
    return str(uuid.uuid5(COACH_NS, name))


# ── Model aliases — only short names that the CLI resolves per-provider ──────
VALID_MODEL_ALIASES = {"sonnet", "opus", "haiku"}


def normalize_model(raw: str) -> str | None:
    """Map any model string to a CLI-safe short alias, or empty to skip override.
    Returns None if the input doesn't match any known alias."""
    if not raw:
        return ""

    normalized = raw.strip().lower()
    for alias in VALID_MODEL_ALIASES:
        if alias in normalized:
            return alias

    return None


# ── Discipline constants ─────────────────────────────────────────────────────
_TRI_DISCIPLINES = {"swim", "bike", "run"}

# Alias — mergeable disciplines are exactly the tri disciplines
_MERGEABLE_DISCIPLINES = _TRI_DISCIPLINES

_BRICK_LABEL_MAP = {
    "swim": "Swim",
    "bike": "Bike",
    "run": "Run",
    "strength": "Strength",
    "other": "Other",
}

# ── HR Zone constants ────────────────────────────────────────────────────────
_HR_ZONES = [
    ("Z1", 0, 130),
    ("Z2", 130, 143),
    ("Z3", 143, 156),
    ("Z4", 156, 169),
    ("Z5", 169, 999),
]

_HR_ZONE_COLORS = {
    "Z1": "#3478B0",
    "Z2": "#2B8070",
    "Z3": "#7C9B2E",
    "Z4": "#B07028",
    "Z5": "#862248",
}

# ── Recovery (Banister) constants ────────────────────────────────────────────
_HR_REST = 55       # estimated resting HR (male, 37y, endurance-trained)
_HR_MAX = 182       # observed max from workout data
_HR_LTHR = 162      # lactate threshold HR (~89% of max, endurance-trained male)
_TAU_ATL = 7.0      # acute training load time constant (days)
_TAU_CTL = 42.0     # chronic training load time constant (days)
_TSB_SCALE = 0.7    # scales TSB into the 0-100 recovery range
_FLAT_TRIMP = {"swim": 1.5, "bike": 1.2, "run": 1.8, "strength": 1.0, "other": 0.8}

# ── Event type presets ───────────────────────────────────────────────────────
EVENT_TYPE_PRESETS = {
    "ironman": {"swim_km": 3.8, "bike_km": 180, "run_km": 42.2},
    "half_ironman": {"swim_km": 1.9, "bike_km": 90, "run_km": 21.1},
    "olympic_tri": {"swim_km": 1.5, "bike_km": 40, "run_km": 10},
    "sprint_tri": {"swim_km": 0.75, "bike_km": 20, "run_km": 5},
    "marathon": {"swim_km": 0, "bike_km": 0, "run_km": 42.195},
    "half_marathon": {"swim_km": 0, "bike_km": 0, "run_km": 21.1},
    "10k": {"swim_km": 0, "bike_km": 0, "run_km": 10},
    "5k": {"swim_km": 0, "bike_km": 0, "run_km": 5},
    "custom": {"swim_km": 0, "bike_km": 0, "run_km": 0},
}

# ── Insight constants ───────────────────────────────────────────────────────
INSIGHT_CUTOFF_DATE = "2026-02-01"

# ── Period insight constants ─────────────────────────────────────────────────
PERIOD_CATEGORIES = ["run", "swim", "bike", "nutrition", "recovery", "overall"]
_CATEGORY_AGENTS = {
    "run": "run-coach", "swim": "swim-coach", "bike": "bike-coach",
    "nutrition": "nutrition-coach", "recovery": "main-coach", "overall": "main-coach",
}
_CATEGORY_TYPES = {
    "run": ("Running", "Walking"), "swim": ("Swimming",), "bike": ("Cycling",),
}
