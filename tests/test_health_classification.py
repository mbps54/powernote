from datetime import datetime, timezone

from powernote.ai import HEALTH_SYSTEM_PROMPT
from powernote.bot import format_daily_nutrition_assessment, format_nutrition_remaining
from powernote.models import FitnessEntry, NutritionEntry, UserProfile
from powernote.storage import DiaryStorage


def fitness_entry(activity_type: str) -> FitnessEntry:
    now = datetime.now(timezone.utc)
    return FitnessEntry(
        message_id="test-message",
        occurred_at=now,
        created_at=now,
        activity_type=activity_type,
        duration_minutes=60,
        source="test",
        raw_text="Сегодня бегал час и занимался силовыми час",
    )


def nutrition_entry(items: list[str], fiber_g: float, health_score: int = 70) -> NutritionEntry:
    now = datetime.now(timezone.utc)
    return NutritionEntry(
        message_id="test-message",
        occurred_at=now,
        created_at=now,
        meal_name="перекус",
        items=items,
        calories_kcal=90,
        protein_g=1,
        fiber_g=fiber_g,
        fruit_veg_g=0,
        added_sugar_g=0,
        ultra_processed_score=10,
        health_score=health_score,
        source="test",
        raw_text=" ".join(items),
    )


def test_canonical_fitness_type_wins_over_shared_raw_text() -> None:
    assert DiaryStorage.fitness_activity_category(fitness_entry("cardio")) == "cardio"
    assert DiaryStorage.fitness_activity_category(fitness_entry("strength")) == "strength"


def test_juice_scores_lower_than_whole_fruit() -> None:
    juice = nutrition_entry(["апельсиновый сок 250 мл"], fiber_g=0)
    orange = nutrition_entry(["апельсин 250 г"], fiber_g=6)

    assert DiaryStorage.meal_nutrition_score(juice) < DiaryStorage.meal_nutrition_score(orange)


def test_good_breakfast_is_not_judged_against_full_day_targets() -> None:
    breakfast = NutritionEntry(
        message_id="test-breakfast",
        occurred_at=datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 8, 3, 8, 1, tzinfo=timezone.utc),
        meal_name="завтрак",
        items=["яйца", "овсяная каша", "овощи"],
        calories_kcal=520,
        protein_g=34,
        fiber_g=8,
        fruit_veg_g=180,
        added_sugar_g=0,
        ultra_processed_score=5,
        health_score=85,
        source="test",
        raw_text="Хороший завтрак",
    )
    totals = DiaryStorage.nutrition_totals([breakfast])
    morning = DiaryStorage.daily_nutrition_score(
        [breakfast],
        totals,
        UserProfile(),
        datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc),
    )
    full_day = DiaryStorage.daily_nutrition_score([breakfast], totals, UserProfile())

    assert morning >= 75
    assert morning > full_day


def test_large_late_meal_scores_lower_than_same_early_meal() -> None:
    early = nutrition_entry(["ужин"], fiber_g=3, health_score=70)
    early.occurred_at = datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc)
    early.calories_kcal = 650
    late = early.model_copy(update={"occurred_at": datetime(2026, 8, 3, 23, 0, tzinfo=timezone.utc)})

    assert DiaryStorage.meal_nutrition_score(late) < DiaryStorage.meal_nutrition_score(early)


def test_nutrition_result_always_shows_consumed_against_target() -> None:
    result = format_nutrition_remaining(
        UserProfile(),
        {"calories_kcal": 2100, "protein_g": 130},
    )

    assert result == "Результат: 2100/1800 ккал, белок 130/120 г"


def test_daily_assessment_names_foods_that_reduce_score() -> None:
    entry = nutrition_entry(["чипсы 50 г", "шоколад 20 г"], fiber_g=2, health_score=35)
    entry.added_sugar_g = 10
    totals = DiaryStorage.nutrition_totals([entry])
    assessment = format_daily_nutrition_assessment(
        UserProfile(),
        totals,
        [entry],
        entry.occurred_at,
    )

    assert "score снижают: чипсы, шоколад" in assessment
    assert "сладкая газировка" not in assessment
    assert "были продукты" not in assessment


def test_nutrition_prompt_does_not_copy_main_food_quantity_to_addition() -> None:
    assert '"100 г макарон с маслом"' in HEALTH_SYSTEM_PROMPT
    assert '"сливочное масло ≈5 г (оценка)"' in HEALTH_SYSTEM_PROMPT
    assert "а не 100 г масла" in HEALTH_SYSTEM_PROMPT
