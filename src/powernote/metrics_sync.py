from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg

from .models import FitnessEntry, NutritionEntry
from .storage import DiaryStorage


logger = logging.getLogger(__name__)


NATURAL_FOOD_HINTS = (
    "овощ",
    "салат",
    "рыб",
    "тунец",
    "куриц",
    "яйц",
    "творог",
    "йогурт без сахара",
    "овсян",
    "греч",
    "ягод",
    "чай",
    "зелень",
)

LOW_QUALITY_FOOD_HINTS = (
    "чипс",
    "шоколад",
    "сахар",
    "конфет",
    "печень",
    "алког",
    "пиво",
    "кола",
    "фастфуд",
)


def week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def clamp(value: float, minimum: float = 0, maximum: float = 100) -> float:
    return max(minimum, min(maximum, value))


def text_for_nutrition(entries: list[NutritionEntry]) -> str:
    return " ".join(
        [
            " ".join(item for entry in entries for item in entry.items),
            " ".join(entry.raw_text for entry in entries),
            " ".join(entry.score_reason for entry in entries),
        ]
    ).lower()


def daily_nutrition_score(
    entries: list[NutritionEntry],
    totals: dict[str, float],
    calorie_target: float,
    protein_target: float,
    fiber_target: float,
) -> float:
    if not entries:
        return 0

    calories = totals["calories_kcal"]
    protein_ratio = totals["protein_g"] / max(protein_target, 1)
    fiber_ratio = totals["fiber_g"] / max(fiber_target, 1)
    calorie_ratio = calories / max(calorie_target, 1)
    weighted_entry_score = totals["health_score"]

    score = weighted_entry_score * 0.35 + 35

    if 0.85 <= calorie_ratio <= 1.08:
        score += 15
    elif 0.7 <= calorie_ratio < 0.85 or 1.08 < calorie_ratio <= 1.2:
        score += 6
    elif calorie_ratio > 1.2:
        score -= 12
    else:
        score -= 5

    if protein_ratio >= 1:
        score += 18
    elif protein_ratio >= 0.8:
        score += 10
    elif protein_ratio >= 0.6:
        score += 2
    else:
        score -= 14

    if fiber_ratio >= 1:
        score += 12
    elif fiber_ratio >= 0.7:
        score += 6
    else:
        score -= 8

    text = text_for_nutrition(entries)
    score += min(8, sum(1 for hint in NATURAL_FOOD_HINTS if hint in text) * 2)
    score -= min(18, sum(1 for hint in LOW_QUALITY_FOOD_HINTS if hint in text) * 5)
    return clamp(score)


def ensure_schema(connection: psycopg.Connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            create table if not exists nutrition_daily (
                day date primary key,
                calories_kcal double precision not null,
                protein_g double precision not null,
                fat_g double precision not null,
                carbs_g double precision not null,
                fiber_g double precision not null,
                meal_count integer not null,
                entry_score double precision not null,
                quality_score double precision not null,
                calorie_target double precision not null,
                protein_target double precision not null,
                fiber_target double precision not null,
                updated_at timestamptz not null default now()
            )
            """
        )
        cursor.execute(
            """
            create table if not exists fitness_weekly (
                week_start date primary key,
                active_minutes double precision not null,
                strength_sessions integer not null,
                cardio_sessions integer not null,
                estimated_calories_kcal double precision not null,
                effort_score double precision not null,
                success_percent double precision not null,
                active_minutes_target double precision not null,
                strength_sessions_target integer not null,
                cardio_sessions_target integer not null,
                updated_at timestamptz not null default now()
            )
            """
        )
    connection.commit()


def sync_once(storage: DiaryStorage, connection: psycopg.Connection) -> None:
    storage.ensure_initialized()
    profile = storage.read_profile()

    nutrition_by_day: dict[date, list[NutritionEntry]] = defaultdict(list)
    for entry in storage.read_nutrition_entries():
        nutrition_by_day[entry.datetime.date()].append(entry)

    fitness_by_week: dict[date, list[FitnessEntry]] = defaultdict(list)
    for entry in storage.read_fitness_entries():
        fitness_by_week[week_start(entry.datetime.date())].append(entry)

    with connection.cursor() as cursor:
        cursor.execute("truncate table nutrition_daily")
        for day, entries in sorted(nutrition_by_day.items()):
            totals = storage.nutrition_totals(entries)
            quality_score = daily_nutrition_score(
                entries=entries,
                totals=totals,
                calorie_target=profile.nutrition_targets.calories_kcal,
                protein_target=profile.nutrition_targets.protein_g,
                fiber_target=profile.nutrition_targets.fiber_g,
            )
            cursor.execute(
                """
                insert into nutrition_daily (
                    day, calories_kcal, protein_g, fat_g, carbs_g, fiber_g,
                    meal_count, entry_score, quality_score, calorie_target,
                    protein_target, fiber_target, updated_at
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                """,
                (
                    day,
                    totals["calories_kcal"],
                    totals["protein_g"],
                    totals["fat_g"],
                    totals["carbs_g"],
                    totals["fiber_g"],
                    len(entries),
                    totals["health_score"],
                    quality_score,
                    profile.nutrition_targets.calories_kcal,
                    profile.nutrition_targets.protein_g,
                    profile.nutrition_targets.fiber_g,
                ),
            )

        cursor.execute("truncate table fitness_weekly")
        for start, entries in sorted(fitness_by_week.items()):
            totals = storage.fitness_totals(entries, profile)
            cursor.execute(
                """
                insert into fitness_weekly (
                    week_start, active_minutes, strength_sessions, cardio_sessions,
                    estimated_calories_kcal, effort_score, success_percent,
                    active_minutes_target, strength_sessions_target,
                    cardio_sessions_target, updated_at
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                """,
                (
                    start,
                    totals["active_minutes"],
                    totals["strength_sessions"],
                    totals["cardio_sessions"],
                    totals["estimated_calories_kcal"],
                    totals["effort_score"],
                    totals["success_percent"],
                    profile.fitness_targets.weekly_active_minutes,
                    profile.fitness_targets.weekly_strength_sessions,
                    profile.fitness_targets.weekly_cardio_sessions,
                ),
            )
    connection.commit()
    logger.info(
        "Synced metrics: nutrition_days=%s fitness_weeks=%s",
        len(nutrition_by_day),
        len(fitness_by_week),
    )


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    data_dir = Path(os.getenv("DATA_DIR", "/app/data"))
    database_url = os.environ["DATABASE_URL"]
    interval_seconds = int(os.getenv("METRICS_SYNC_INTERVAL_SECONDS", "60"))
    storage = DiaryStorage(data_dir)

    while True:
        try:
            with psycopg.connect(database_url) as connection:
                ensure_schema(connection)
                sync_once(storage, connection)
        except Exception:
            logger.exception("Metrics sync failed")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
