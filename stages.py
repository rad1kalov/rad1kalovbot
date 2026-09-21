# stages.py
"""Стадии серии и переходы между ними."""

STAGES = ["friendship", "love", "marriage"]

STAGE_NAMES = {
    "friendship": "Дружба",
    "love": "Любовь",
    "marriage": "Брак",
}


def stage_name(stage: str) -> str:
    return STAGE_NAMES.get(stage, stage)


def next_stage(stage: str):
    try:
        i = STAGES.index(stage)
    except ValueError:
        return None
    return STAGES[i + 1] if i + 1 < len(STAGES) else None


def prev_stage(stage: str):
    try:
        i = STAGES.index(stage)
    except ValueError:
        return None
    return STAGES[i - 1] if i > 0 else None