from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import psycopg

from .models import FitnessEntry, NutritionEntry
from .storage import DiaryStorage


logger = logging.getLogger(__name__)


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
                fruit_veg_g double precision not null default 0,
                added_sugar_g double precision not null default 0,
                ultra_processed_score double precision not null default 0,
                meal_count integer not null,
                entry_score double precision not null,
                quality_score double precision not null,
                calorie_target double precision not null,
                protein_target double precision not null,
                fiber_target double precision not null,
                fruit_veg_target double precision not null default 400,
                added_sugar_limit double precision not null default 35,
                updated_at timestamptz not null default now()
            )
            """
        )
        cursor.execute("alter table nutrition_daily add column if not exists fruit_veg_g double precision not null default 0")
        cursor.execute("alter table nutrition_daily add column if not exists added_sugar_g double precision not null default 0")
        cursor.execute("alter table nutrition_daily add column if not exists ultra_processed_score double precision not null default 0")
        cursor.execute("alter table nutrition_daily add column if not exists fruit_veg_target double precision not null default 400")
        cursor.execute("alter table nutrition_daily add column if not exists added_sugar_limit double precision not null default 35")
        cursor.execute(
            """
            create table if not exists fitness_daily (
                day date primary key,
                active_minutes double precision not null,
                active_walk_minutes double precision not null,
                cardio_minutes double precision not null,
                strength_minutes double precision not null,
                strength_sessions integer not null,
                estimated_calories_kcal double precision not null,
                effort_score double precision not null,
                health_score double precision not null,
                active_minutes_target double precision not null default 110,
                active_walk_minutes_target double precision not null default 60,
                cardio_minutes_target double precision not null default 30,
                strength_minutes_target double precision not null default 20,
                updated_at timestamptz not null default now()
            )
            """
        )
        cursor.execute("alter table fitness_daily add column if not exists active_minutes_target double precision not null default 110")
        cursor.execute("alter table fitness_daily add column if not exists active_walk_minutes_target double precision not null default 60")
        cursor.execute("alter table fitness_daily add column if not exists cardio_minutes_target double precision not null default 30")
        cursor.execute("alter table fitness_daily add column if not exists strength_minutes_target double precision not null default 20")
    connection.commit()


def sync_once(storage: DiaryStorage, connection: psycopg.Connection) -> None:
    storage.ensure_initialized()
    profile = storage.read_profile()

    nutrition_by_day: dict[date, list[NutritionEntry]] = defaultdict(list)
    for entry in storage.read_nutrition_entries():
        nutrition_by_day[entry.datetime.date()].append(entry)

    fitness_by_day: dict[date, list[FitnessEntry]] = defaultdict(list)
    for entry in storage.read_fitness_entries():
        fitness_by_day[entry.datetime.date()].append(entry)

    with connection.cursor() as cursor:
        cursor.execute("truncate table nutrition_daily")
        for day, entries in sorted(nutrition_by_day.items()):
            totals = storage.nutrition_totals(entries)
            quality_score = storage.daily_nutrition_score(entries, totals, profile)
            cursor.execute(
                """
                insert into nutrition_daily (
                    day, calories_kcal, protein_g, fat_g, carbs_g, fiber_g,
                    fruit_veg_g, added_sugar_g, ultra_processed_score,
                    meal_count, entry_score, quality_score, calorie_target,
                    protein_target, fiber_target, fruit_veg_target,
                    added_sugar_limit, updated_at
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                """,
                (
                    day,
                    totals["calories_kcal"],
                    totals["protein_g"],
                    totals["fat_g"],
                    totals["carbs_g"],
                    totals["fiber_g"],
                    totals["fruit_veg_g"],
                    totals["added_sugar_g"],
                    totals["ultra_processed_score"],
                    len(entries),
                    totals["health_score"],
                    quality_score,
                    profile.nutrition_targets.calories_kcal,
                    profile.nutrition_targets.protein_g,
                    profile.nutrition_targets.fiber_g,
                    profile.nutrition_targets.fruit_veg_g,
                    profile.nutrition_targets.added_sugar_g,
                ),
            )

        cursor.execute("truncate table fitness_daily")
        for day, entries in sorted(fitness_by_day.items()):
            totals = storage.fitness_daily_totals(entries, profile)
            cursor.execute(
                """
                insert into fitness_daily (
                    day, active_minutes, active_walk_minutes, cardio_minutes,
                    strength_minutes, strength_sessions, estimated_calories_kcal,
                    effort_score, health_score, active_minutes_target,
                    active_walk_minutes_target, cardio_minutes_target,
                    strength_minutes_target, updated_at
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                """,
                (
                    day,
                    totals["active_minutes"],
                    totals["active_walk_minutes"],
                    totals["cardio_minutes"],
                    totals["strength_minutes"],
                    int(totals["strength_sessions"]),
                    totals["estimated_calories_kcal"],
                    totals["effort_score"],
                    totals["health_score"],
                    profile.fitness_targets.daily_active_minutes,
                    profile.fitness_targets.daily_active_walk_minutes,
                    profile.fitness_targets.daily_cardio_minutes,
                    profile.fitness_targets.daily_strength_minutes,
                ),
            )
    connection.commit()
    logger.info(
        "Synced metrics: nutrition_days=%s fitness_days=%s",
        len(nutrition_by_day),
        len(fitness_by_day),
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
