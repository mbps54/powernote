from datetime import datetime, timezone

from powernote.models import FitnessEntry, NutritionEntry, UserProfile
from powernote.storage import DiaryStorage


def fitness_entry(activity_type: str) -> FitnessEntry:
    return FitnessEntry(
        datetime=datetime.now(timezone.utc),
        activity_type=activity_type,
        duration_minutes=60,
        source="test",
        raw_text="Сегодня бегал час и занимался силовыми час",
    )


def nutrition_entry(items: list[str], fiber_g: float, health_score: int = 70) -> NutritionEntry:
    return NutritionEntry(
        datetime=datetime.now(timezone.utc),
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
        datetime=datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc),
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
    early.datetime = datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc)
    early.calories_kcal = 650
    late = early.model_copy(update={"datetime": datetime(2026, 8, 3, 23, 0, tzinfo=timezone.utc)})

    assert DiaryStorage.meal_nutrition_score(late) < DiaryStorage.meal_nutrition_score(early)
