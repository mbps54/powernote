from __future__ import annotations

import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from .ai import DiaryAI
from .config import Settings
from .models import DiaryEntry, FitnessEntry, NutritionEntry, UserProfile
from .storage import DiaryStorage, nutrition_quality_metadata_present

logger = logging.getLogger(__name__)

EntryMode = Literal["auto", "diary", "nutrition", "fitness"]

DIARY_BUTTON_TEXT = "Дневник"
NUTRITION_BUTTON_TEXT = "Питание"
FITNESS_BUTTON_TEXT = "Фитнес"
SEARCH_BUTTON_TEXT = "Поиск"
UNDO_BUTTON_TEXT = "Удалить последнее"
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text=DIARY_BUTTON_TEXT),
            KeyboardButton(text=NUTRITION_BUTTON_TEXT),
            KeyboardButton(text=FITNESS_BUTTON_TEXT),
        ],
        [
            KeyboardButton(text=SEARCH_BUTTON_TEXT),
            KeyboardButton(text=UNDO_BUTTON_TEXT),
        ],
    ],
    resize_keyboard=True,
    input_field_placeholder="Выберите режим или отправьте запись",
)


class SearchState(StatesGroup):
    waiting_for_query = State()


class EntryModeState(StatesGroup):
    waiting_for_diary = State()
    waiting_for_nutrition = State()
    waiting_for_fitness = State()


class ProfileSetupState(StatesGroup):
    waiting_for_age = State()
    waiting_for_weight = State()
    waiting_for_height = State()
    waiting_for_lifestyle = State()
    waiting_for_goal = State()


def resolve_entry_datetime(datetime_hint: str | None, fallback: datetime) -> datetime:
    if not datetime_hint:
        return fallback
    try:
        resolved = datetime.fromisoformat(datetime_hint.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Ignoring invalid datetime_hint from AI: %s", datetime_hint)
        return fallback
    if resolved.tzinfo is None:
        return resolved.replace(tzinfo=fallback.tzinfo)
    return resolved.astimezone(fallback.tzinfo)


def floor_to_quarter_hour(value: datetime) -> datetime:
    return value.replace(minute=value.minute - value.minute % 15, second=0, microsecond=0)


def format_entries(entries: list[DiaryEntry], empty_text: str = "Записей пока нет.") -> str:
    if not entries:
        return empty_text

    chunks: list[str] = []
    for entry in entries:
        timestamp = entry.datetime.strftime("%Y-%m-%d %H:%M")
        tags = ", ".join(entry.tags)
        facts = " ".join(entry.facts)
        chunks.append(f"{timestamp} [{tags}]\n{facts}")
    return "\n\n".join(chunks)


def build_profile(age: int, weight_kg: float, height_cm: float, lifestyle: str, goal: str) -> UserProfile:
    bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
    activity_factor = 1.2
    maintenance = bmr * activity_factor
    calorie_target = max(1400, maintenance - 200)
    protein_target = max(90, weight_kg * 1.58)
    fat_target = max(45, weight_kg * 0.75)
    carbs_target = max(100, (calorie_target - protein_target * 4 - fat_target * 9) / 4)
    profile = UserProfile(
        age=age,
        weight_kg=weight_kg,
        height_cm=height_cm,
        lifestyle=lifestyle,
        goal=goal,
    )
    profile.nutrition_targets.calories_kcal = round(calorie_target)
    profile.nutrition_targets.protein_g = round(protein_target)
    profile.nutrition_targets.fat_g = round(fat_target)
    profile.nutrition_targets.carbs_g = round(carbs_target)
    profile.nutrition_targets.fiber_g = 30
    profile.nutrition_targets.fruit_veg_g = 400
    profile.nutrition_targets.added_sugar_g = 35
    profile.nutrition_targets.ultra_processed_score = 20
    profile.fitness_targets.daily_active_minutes = 110
    profile.fitness_targets.daily_active_walk_minutes = 60
    profile.fitness_targets.daily_cardio_minutes = 30
    profile.fitness_targets.daily_strength_minutes = 20
    profile.fitness_targets.weekly_active_minutes = 180
    profile.fitness_targets.weekly_strength_sessions = 3
    profile.fitness_targets.weekly_cardio_sessions = 2
    return profile


def format_profile(profile: UserProfile) -> str:
    return (
        "Профиль здоровья:\n"
        f"- возраст: {profile.age}\n"
        f"- вес: {profile.weight_kg:g} кг\n"
        f"- рост: {profile.height_cm:g} см\n"
        f"- образ жизни: {profile.lifestyle}\n"
        f"- цель: {profile.goal}\n\n"
        "Цели питания в день:\n"
        f"- лимит калорий: {profile.nutrition_targets.calories_kcal:g} ккал\n"
        f"- белки: {profile.nutrition_targets.protein_g:g} г\n"
        f"- жиры: {profile.nutrition_targets.fat_g:g} г\n"
        f"- углеводы: {profile.nutrition_targets.carbs_g:g} г\n"
        f"- клетчатка: {profile.nutrition_targets.fiber_g:g} г\n"
        f"- овощи/фрукты: {profile.nutrition_targets.fruit_veg_g:g} г\n"
        f"- добавленный сахар: до {profile.nutrition_targets.added_sugar_g:g} г\n\n"
        "Цели активности в день:\n"
        f"- всего: {profile.fitness_targets.daily_active_minutes} мин\n"
        f"- active walk: {profile.fitness_targets.daily_active_walk_minutes} мин\n"
        f"- кардио: {profile.fitness_targets.daily_cardio_minutes} мин\n"
        f"- силовые: {profile.fitness_targets.daily_strength_minutes} мин"
    )


def format_nutrition_totals(prefix: str, totals: dict[str, float]) -> str:
    return (
        f"{prefix}: "
        f"{totals['calories_kcal']:.0f} ккал, "
        f"белок {totals['protein_g']:.0f}, "
        f"клетчатка {totals['fiber_g']:.0f}, "
        f"score {totals['health_score']:.0f}/100"
    )


def format_nutrition_remaining(profile: UserProfile, totals: dict[str, float]) -> str:
    calorie_target = profile.nutrition_targets.calories_kcal
    protein_target = profile.nutrition_targets.protein_g
    calories_left = calorie_target - totals["calories_kcal"]
    protein_left = max(0, protein_target - totals["protein_g"])
    calorie_text = (
        f"{calories_left:.0f}/{calorie_target:.0f} ккал"
        if calories_left >= 0
        else f"+{-calories_left:.0f} ккал сверх {calorie_target:.0f}"
    )
    return f"Осталось: {calorie_text}, белок +{protein_left:.0f} г"


def format_nutrition_quality_factors(profile: UserProfile, totals: dict[str, float]) -> str:
    return (
        "Факторы: "
        f"овощи/фрукты {totals['fruit_veg_g']:.0f}/{profile.nutrition_targets.fruit_veg_g:.0f}, "
        f"сахар {totals['added_sugar_g']:.0f}/{profile.nutrition_targets.added_sugar_g:.0f} лимит, "
        f"обработка {totals['ultra_processed_score']:.0f}/100 лимит {profile.nutrition_targets.ultra_processed_score:.0f}"
    )


def format_nutrition_summary(entries: list[NutritionEntry]) -> str:
    items: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        for item in entry.items:
            normalized = item.strip().lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                items.append(item.strip())
    if not items:
        fallback = entries[-1].raw_text.strip() if entries else "еда"
        summary = " ".join(fallback.split())
    else:
        summary = ", ".join(items[:4])
        if len(items) > 4:
            summary += " и еще"
    if len(summary) > 80:
        summary = summary[:77].rstrip() + "..."
    return summary or "еда"


def format_nutrition_comment(
    profile: UserProfile,
    added: dict[str, float],
    day: dict[str, float],
    entries: list[NutritionEntry],
    meal_datetime: datetime,
) -> str:
    parts: list[str] = []
    hour = meal_datetime.hour
    protein_left = profile.nutrition_targets.protein_g - day["protein_g"]
    fiber_left = profile.nutrition_targets.fiber_g - day["fiber_g"]
    calories_left = profile.nutrition_targets.calories_kcal - day["calories_kcal"]
    fruit_veg_left = profile.nutrition_targets.fruit_veg_g - day["fruit_veg_g"]
    items_text = " ".join(item.lower() for entry in entries for item in entry.items)
    raw_text = " ".join(entry.raw_text.lower() for entry in entries)
    combined_text = f"{items_text} {raw_text}"

    if added["protein_g"] >= 25:
        parts.append("хорошая белковая часть")
    elif added["protein_g"] >= 12:
        parts.append("белок есть, но порция умеренная")
    else:
        parts.append("белка мало")

    if added["fiber_g"] >= 6:
        parts.append("клетчатка хорошая")
    elif added["fiber_g"] >= 3:
        parts.append("клетчатка средняя")
    else:
        parts.append("клетчатки мало")

    if added["fruit_veg_g"] >= 150:
        parts.append("овощей/фруктов хорошая порция")
    elif added["fruit_veg_g"] >= 50:
        parts.append("овощи/фрукты есть, но немного")
    elif fruit_veg_left > 250:
        parts.append("овощей/фруктов за день мало")

    if any(marker in combined_text for marker in ("овощ", "салат", "зелень", "ягод", "фрукт", "суп", "греч", "овсян")):
        parts.append("качество еды скорее хорошее")
    if added["added_sugar_g"] > 12 or any(marker in combined_text for marker in ("чипс", "сахар", "шоколад", "печень", "конфет", "кола", "алког", "фастфуд")):
        parts.append("есть продукт, снижающий качество приема")
    if added["ultra_processed_score"] >= 60:
        parts.append("еда заметно обработанная")

    if hour >= 21 and added["calories_kcal"] > 500:
        parts.append("для позднего времени объем тяжеловат")
    elif hour >= 21:
        parts.append("для позднего времени объем спокойный")
    elif 6 <= hour <= 11 and added["protein_g"] < 15:
        parts.append("для утра белка лучше больше")

    if protein_left > 35:
        parts.append(f"за день белок сильно ниже цели: осталось {protein_left:.0f}")
    elif protein_left > 0:
        parts.append(f"до цели белка осталось {protein_left:.0f}")
    if fiber_left > 10:
        parts.append(f"клетчатка за день низкая: осталось {fiber_left:.0f}")
    if calories_left < 0:
        parts.append(f"калории уже выше цели на {-calories_left:.0f}")
    elif calories_left < 250:
        parts.append(f"калорийный запас небольшой: {calories_left:.0f}")

    return "Комментарий: " + "; ".join(parts[:5]) + "."


def format_nutrition_meals(entries: list[NutritionEntry]) -> str:
    lines = ["Приемы пищи:"]
    for entry in sorted(entries, key=lambda item: item.datetime):
        lines.append(
            f"- {entry.datetime.strftime('%H:%M')} {format_nutrition_summary([entry])}: "
            f"{entry.calories_kcal:.0f} ккал, белок {entry.protein_g:.0f}, "
            f"клетчатка {entry.fiber_g:.0f}, score {DiaryStorage.meal_nutrition_score(entry):.0f}/100"
        )
    return "\n".join(lines)


def format_daily_nutrition_assessment(profile: UserProfile, totals: dict[str, float], entries: list[NutritionEntry]) -> str:
    parts: list[str] = []
    protein_left = profile.nutrition_targets.protein_g - totals["protein_g"]
    fiber_left = profile.nutrition_targets.fiber_g - totals["fiber_g"]
    calories_left = profile.nutrition_targets.calories_kcal - totals["calories_kcal"]
    fruit_veg_left = profile.nutrition_targets.fruit_veg_g - totals["fruit_veg_g"]
    sugar_over = totals["added_sugar_g"] - profile.nutrition_targets.added_sugar_g
    combined_text = " ".join(
        [
            " ".join(item.lower() for entry in entries for item in entry.items),
            " ".join(entry.raw_text.lower() for entry in entries),
            " ".join(entry.score_reason.lower() for entry in entries),
        ]
    )
    late_calories = sum(entry.calories_kcal for entry in entries if entry.datetime.hour >= 21)

    if totals["health_score"] >= 75:
        parts.append("день по качеству хороший")
    elif totals["health_score"] >= 60:
        parts.append("день средний по качеству")
    else:
        parts.append("качество дня слабое")

    if protein_left > 35:
        parts.append(f"сильно не хватает белка: осталось {protein_left:.0f}")
    elif protein_left > 0:
        parts.append(f"белок ниже цели на {protein_left:.0f}")
    else:
        parts.append("цель по белку закрыта")

    if fiber_left > 10:
        parts.append(f"клетчатка низкая: осталось {fiber_left:.0f}")
    elif fiber_left > 0:
        parts.append(f"клетчатку можно добрать: {fiber_left:.0f}")
    else:
        parts.append("клетчатка закрыта")

    if calories_left < 0:
        parts.append(f"калории выше цели на {-calories_left:.0f}")
    elif calories_left < 250:
        parts.append(f"калорийный запас небольшой: {calories_left:.0f}")
    else:
        parts.append(f"калорий осталось {calories_left:.0f}")

    if any(nutrition_quality_metadata_present(entry) for entry in entries):
        if fruit_veg_left > 150:
            parts.append(f"овощей/фруктов мало: осталось {fruit_veg_left:.0f}")
        elif fruit_veg_left > 0:
            parts.append(f"овощи/фрукты почти добраны: осталось {fruit_veg_left:.0f}")
        else:
            parts.append("цель по овощам/фруктам закрыта")

        if sugar_over > 0:
            parts.append(f"сахар выше лимита на {sugar_over:.0f}")
        elif totals["added_sugar_g"] > profile.nutrition_targets.added_sugar_g * 0.7:
            parts.append("сахар близко к лимиту")

        if totals["ultra_processed_score"] > 45:
            parts.append("слишком много обработанной еды")

    if any(marker in combined_text for marker in ("чипс", "сахар", "шоколад", "печень", "конфет", "кола", "алког", "фастфуд")):
        parts.append("были продукты, снижающие score")
    elif any(marker in combined_text for marker in ("овощ", "салат", "зелень", "ягод", "фрукт", "суп", "греч", "овсян", "рыб", "куриц")):
        parts.append("по составу есть качественная еда")

    if late_calories > 500:
        parts.append("поздний объем еды великоват")

    return "Итоговая оценка: " + "; ".join(parts[:6]) + "."


def format_fitness_totals(prefix: str, totals: dict[str, float]) -> str:
    return (
        f"{prefix}: "
        f"{totals['active_minutes']:.0f} мин, "
        f"прогулка {totals['active_walk_minutes']:.0f} мин, "
        f"кардио {totals['cardio_minutes']:.0f} мин, "
        f"силовые {totals['strength_minutes']:.0f} мин, "
        f"{totals['estimated_calories_kcal']:.0f} ккал, "
        f"health score {totals['health_score']:.0f}/100"
    )


def format_undo_result(result: dict[str, object] | None) -> str:
    if not result:
        return "Удалять пока нечего."
    labels = {
        "diary": "дневник",
        "nutrition": "питание",
        "fitness": "фитнес",
    }
    kind = labels.get(str(result["kind"]), str(result["kind"]))
    timestamp = result["last_datetime"]
    timestamp_text = timestamp.strftime("%Y-%m-%d %H:%M") if isinstance(timestamp, datetime) else str(timestamp)
    raw_text = str(result.get("raw_text") or "").strip()
    if len(raw_text) > 160:
        raw_text = raw_text[:157].rstrip() + "..."
    return (
        f"Удалено: {kind}, записей: {result['count']}.\n"
        f"Время записи: {timestamp_text}\n"
        f"Текст: {raw_text or 'нет текста'}"
    )


NUTRITION_HINTS = (
    "ел",
    "ела",
    "съел",
    "съела",
    "поел",
    "поела",
    "завтрак",
    "обед",
    "ужин",
    "перекус",
    "пил",
    "пила",
    "выпил",
    "выпила",
    "гр",
    "грамм",
    "мл",
    "ккал",
    "калор",
    "белк",
    "жир",
    "углев",
    "овсян",
    "каша",
    "молок",
    "кофе",
    "сахар",
    "макарон",
    "паста",
    "рис",
    "овощ",
    "тунец",
    "тунц",
    "куриц",
    "рыб",
    "мяс",
    "творог",
    "йогурт",
    "банан",
    "яблок",
    "салат",
    "суп",
    "хлеб",
    "meal",
    "breakfast",
    "lunch",
    "dinner",
    "snack",
)

FITNESS_HINTS = (
    "трен",
    "спорт",
    "фитнес",
    "бег",
    "бегал",
    "бегала",
    "ходил",
    "ходила",
    "прогул",
    "зал",
    "гантел",
    "штанг",
    "отжим",
    "присед",
    "кардио",
    "йога",
    "плав",
    "велосип",
    "workout",
    "fitness",
    "run",
    "walk",
    "gym",
    "cardio",
)


def infer_auto_mode(raw_text: str) -> EntryMode:
    normalized = raw_text.lower().replace("ё", "е")
    nutrition_score = sum(1 for hint in NUTRITION_HINTS if hint in normalized)
    fitness_score = sum(1 for hint in FITNESS_HINTS if hint in normalized)
    if nutrition_score >= 2 and nutrition_score >= fitness_score:
        return "nutrition"
    if fitness_score >= 2 and fitness_score > nutrition_score:
        return "fitness"
    return "auto"


def is_allowed(message: Message, settings: Settings) -> bool:
    if not settings.allowed_telegram_user_ids:
        return True
    return bool(message.from_user and message.from_user.id in settings.allowed_telegram_user_ids)


def build_router(settings: Settings, storage: DiaryStorage, diary_ai: DiaryAI) -> Router:
    router = Router()

    async def ensure_embeddings(entries: list[DiaryEntry]) -> dict[str, list[float]]:
        embeddings = storage.read_embeddings(diary_ai.embedding_model)
        missing_entries = [
            entry for entry in entries if storage.entry_key(entry) not in embeddings
        ]
        if missing_entries:
            vectors = await diary_ai.create_embeddings(
                [storage.embedding_text(entry) for entry in missing_entries]
            )
            storage.upsert_embeddings(
                diary_ai.embedding_model,
                missing_entries,
                vectors,
            )
            embeddings.update(
                {
                    storage.entry_key(entry): vector
                    for entry, vector in zip(missing_entries, vectors, strict=True)
                }
            )
        return embeddings

    async def reject_if_needed(message: Message) -> bool:
        if is_allowed(message, settings):
            return False
        await message.answer("Доступ к этому дневнику ограничен.")
        return True

    async def answer_search_query(message: Message, query: str) -> None:
        entries = storage.read_entries()
        if not entries:
            await message.answer("В дневнике пока нет записей.")
            return

        await message.answer("Ищу ответ в дневнике.")
        try:
            embeddings = await ensure_embeddings(entries)
            query_embedding = (await diary_ai.create_embeddings([query]))[0]
        except Exception:
            logger.exception("Semantic search failed")
            await message.answer("Не удалось выполнить поиск. Попробуйте позже.")
            return

        results = storage.semantic_search(
            entries=entries,
            embeddings=embeddings,
            query_embedding=query_embedding,
            limit=settings.semantic_search_limit,
            min_score=settings.semantic_search_min_score,
        )
        if not results:
            await message.answer("В дневнике нет достаточной информации для ответа.")
            return

        try:
            answer = await diary_ai.answer_question(query, results)
        except Exception:
            logger.exception("Failed to answer question from diary entries")
            await message.answer("Не удалось сформировать ответ. Попробуйте позже.")
            return
        await message.answer(answer)

    async def process_health(
        message: Message,
        raw_text: str,
        source: str,
        message_datetime: datetime,
        mode: EntryMode = "auto",
    ) -> bool:
        profile = storage.read_profile()
        try:
            health = await diary_ai.extract_health(raw_text, profile, message_datetime, mode)
        except Exception:
            logger.exception("Health extraction failed")
            return False

        nutrition_entries = [
            NutritionEntry(
                datetime=floor_to_quarter_hour(resolve_entry_datetime(item.datetime_hint, message_datetime)),
                meal_name=(item.meal_name or "").strip() or "meal",
                items=[entry.strip() for entry in item.items if entry.strip()],
                calories_kcal=max(item.calories_kcal, 0),
                protein_g=max(item.protein_g, 0),
                fat_g=max(item.fat_g, 0),
                carbs_g=max(item.carbs_g, 0),
                fiber_g=max(item.fiber_g, 0),
                fruit_veg_g=max(item.fruit_veg_g, 0),
                added_sugar_g=max(item.added_sugar_g, 0),
                ultra_processed_score=item.ultra_processed_score,
                health_score=item.health_score,
                score_reason=(item.score_reason or "").strip(),
                source=source,
                raw_text=raw_text,
            )
            for item in health.nutrition_entries
            if mode != "fitness" and health.is_nutrition and (item.items or item.calories_kcal > 0)
        ]
        fitness_entries = [
            FitnessEntry(
                datetime=resolve_entry_datetime(item.datetime_hint, message_datetime),
                activity_type=(item.activity_type or "").strip() or "activity",
                duration_minutes=max(item.duration_minutes, 0),
                intensity=(item.intensity or "").strip() or "unknown",
                muscle_groups=[group.strip() for group in item.muscle_groups if group.strip()],
                estimated_calories_kcal=max(item.estimated_calories_kcal, 0),
                effort_score=item.effort_score,
                score_reason=(item.score_reason or "").strip(),
                source=source,
                raw_text=raw_text,
            )
            for item in health.fitness_entries
            if mode != "nutrition" and health.is_fitness and (item.duration_minutes > 0 or (item.activity_type or "").strip())
        ]

        if not nutrition_entries and not fitness_entries:
            return False

        chunks: list[str] = []
        if nutrition_entries:
            storage.append_nutrition_entries(nutrition_entries)
            added = storage.nutrition_totals(nutrition_entries)
            meal_datetime = nutrition_entries[-1].datetime
            day_entries = storage.nutrition_for_date(meal_datetime.date())
            day = storage.nutrition_totals(day_entries)
            day["health_score"] = storage.daily_nutrition_score(day_entries, day, profile)
            meal_summary = format_nutrition_summary(nutrition_entries)
            chunks.append(
                f"Питание записано: {meal_summary}\n"
                f"{format_nutrition_totals('Добавлено', added)}\n"
                f"{format_nutrition_totals('Сегодня', day)}\n"
                f"{format_nutrition_remaining(profile, day)}\n"
                f"{format_nutrition_comment(profile, added, day, nutrition_entries, meal_datetime)}"
            )

        if fitness_entries:
            storage.append_fitness_entries(fitness_entries)
            added_fitness = storage.fitness_daily_totals(fitness_entries, profile)
            day_entries = storage.fitness_for_date(message_datetime.date())
            day = storage.fitness_daily_totals(day_entries, profile)
            chunks.append(
                "Фитнес записан.\n"
                f"{format_fitness_totals('Добавлено', added_fitness)}\n"
                f"{format_fitness_totals('Сегодня', day)}\n"
                f"Комментарий: {fitness_entries[-1].score_reason or 'Оценка сохранена.'}"
            )

        await message.answer("\n\n".join(chunks))
        return True

    async def process_text(message: Message, raw_text: str, source: str, mode: EntryMode = "auto") -> None:
        if await reject_if_needed(message):
            return

        message_datetime = datetime.now(settings.timezone)
        storage.append_raw_transcript(message_datetime, source, raw_text)
        effective_mode = infer_auto_mode(raw_text) if mode == "auto" else mode
        if mode in ("auto", "nutrition", "fitness"):
            if await process_health(message, raw_text, source, message_datetime, effective_mode):
                return
            if mode == "nutrition":
                await message.answer("Не удалось распознать питание в сообщении. Попробуйте описать еду и порции подробнее.")
                return
            if mode == "fitness":
                await message.answer("Не удалось распознать фитнес-активность. Попробуйте указать вид активности и длительность.")
                return

        try:
            extraction = await diary_ai.extract_facts(raw_text, storage.get_tags(), message_datetime)
        except Exception:
            logger.exception("Failed to extract facts from diary message")
            await message.answer(
                "Не удалось сохранить запись: сервис анализа текста сейчас недоступен. "
                "Сырой текст сохранен во внутреннем логе, но запись в дневник не добавлена. "
                "Попробуйте отправить сообщение еще раз после восстановления OpenAI-квоты."
            )
            return

        entries = [
            DiaryEntry(
                datetime=resolve_entry_datetime(item.datetime_hint, message_datetime),
                tags=[tag.strip().lower() for tag in item.tags if tag.strip()],
                facts=[fact.strip() for fact in item.facts if fact.strip()],
                source=source,
                raw_text=raw_text,
            )
            for item in extraction.entries
            if item.facts
        ]

        all_tags = [tag for entry in entries for tag in entry.tags] + extraction.new_tags
        storage.update_tags(all_tags)
        storage.append_entries(entries)
        if entries:
            try:
                await ensure_embeddings(entries)
            except Exception:
                logger.exception("Failed to index new diary entries")

        if entries:
            await message.answer(f"Сохранено записей: {len(entries)}.\n\n{format_entries(entries)}")
        else:
            await message.answer("Фактических событий для сохранения не найдено.")

    async def transcribe_voice_message(message: Message, bot: Bot) -> str | None:
        if not message.voice:
            return None
        await message.answer("Голосовое получено, расшифровываю.")
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "voice.ogg"
            await bot.download(message.voice.file_id, destination=audio_path)
            try:
                raw_text = await diary_ai.transcribe(audio_path)
            except Exception:
                logger.exception("Failed to transcribe voice message")
                await message.answer(
                    "Не удалось расшифровать голосовое: сервис распознавания сейчас недоступен. "
                    "Попробуйте позже или отправьте текстом."
                )
                return None
        if not raw_text:
            await message.answer("Не удалось получить текст из голосового сообщения.")
            return None
        return raw_text

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        if await reject_if_needed(message):
            return
        await message.answer(
            "Это личный дневник фактов, питания и фитнеса.\n\n"
            "Можно просто отправить сообщение, и я попробую определить тип автоматически. "
            "Надежнее выбрать кнопку: Дневник, Питание или Фитнес, а затем отправить текст или voice.\n\n"
            "Команды:\n"
            "/note <text> - сохранить в дневник\n"
            "/food <text> - сохранить питание\n"
            "/fitness <text> - сохранить фитнес\n"
            "/last - последние 5 записей\n"
            "/today - записи за сегодня\n"
            "/tags - список тегов\n"
            "/tag <tag> - записи по тегу\n"
            "/search <query> - поиск по смыслу\n"
            "/profile - профиль питания и фитнеса\n"
            "/profile_setup - настроить профиль\n"
            "/nutrition_today - питание за сегодня\n"
            "/fitness_week - фитнес за сегодня\n"
            "/undo_last - удалить последнюю запись\n"
            "/cancel - отменить ввод вопроса",
            reply_markup=MAIN_KEYBOARD,
        )

    @router.message(Command("last"))
    async def last(message: Message) -> None:
        if await reject_if_needed(message):
            return
        await message.answer(format_entries(storage.last_entries(5)))

    @router.message(Command("today"))
    async def today(message: Message) -> None:
        if await reject_if_needed(message):
            return
        today_date = datetime.now(settings.timezone).date()
        await message.answer(format_entries(storage.entries_for_date(today_date), "За сегодня записей нет."))

    @router.message(Command("tags"))
    async def tags(message: Message) -> None:
        if await reject_if_needed(message):
            return
        await message.answer("\n".join(storage.get_tags()) or "Тегов пока нет.")

    @router.message(Command("note"))
    async def note_command(message: Message, command: CommandObject, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        raw_text = (command.args or "").strip()
        if not raw_text:
            await state.set_state(EntryModeState.waiting_for_diary)
            await message.answer("Отправьте текст или voice для дневника.")
            return
        await process_text(message, raw_text, "text", "diary")

    @router.message(Command("food"))
    async def food_command(message: Message, command: CommandObject, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        raw_text = (command.args or "").strip()
        if not raw_text:
            await state.set_state(EntryModeState.waiting_for_nutrition)
            await message.answer("Отправьте текст или voice с питанием.")
            return
        await process_text(message, raw_text, "text", "nutrition")

    @router.message(Command("fitness"))
    async def fitness_command(message: Message, command: CommandObject, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        raw_text = (command.args or "").strip()
        if not raw_text:
            await state.set_state(EntryModeState.waiting_for_fitness)
            await message.answer("Отправьте текст или voice с фитнес-активностью.")
            return
        await process_text(message, raw_text, "text", "fitness")

    @router.message(Command("profile"))
    async def profile(message: Message) -> None:
        if await reject_if_needed(message):
            return
        await message.answer(format_profile(storage.read_profile()))

    @router.message(Command("nutrition_today"))
    async def nutrition_today(message: Message) -> None:
        if await reject_if_needed(message):
            return
        today_date = datetime.now(settings.timezone).date()
        entries = storage.nutrition_for_date(today_date)
        if not entries:
            await message.answer("За сегодня питание еще не записано.")
            return
        profile = storage.read_profile()
        totals = storage.nutrition_totals(entries)
        totals["health_score"] = storage.daily_nutrition_score(entries, totals, profile)
        await message.answer(
            f"{format_nutrition_meals(entries)}\n\n"
            f"{format_nutrition_totals('Итого', totals)}\n"
            f"{format_nutrition_remaining(profile, totals)}\n"
            f"{format_nutrition_quality_factors(profile, totals)}\n"
            f"{format_daily_nutrition_assessment(profile, totals, entries)}"
        )

    @router.message(Command("fitness_week"))
    async def fitness_week(message: Message) -> None:
        if await reject_if_needed(message):
            return
        today_entries = storage.fitness_for_date(datetime.now(settings.timezone).date())
        if not today_entries:
            await message.answer("За сегодня фитнес еще не записан.")
            return
        profile = storage.read_profile()
        await message.answer(format_fitness_totals("Фитнес сегодня", storage.fitness_daily_totals(today_entries, profile)))

    @router.message(Command("profile_setup"))
    async def profile_setup(message: Message, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        await state.set_state(ProfileSetupState.waiting_for_age)
        await message.answer("Сколько вам лет? Например: 40")

    @router.message(ProfileSetupState.waiting_for_age, F.text, ~F.text.startswith("/"))
    async def profile_age(message: Message, state: FSMContext) -> None:
        try:
            age = int((message.text or "").strip())
        except ValueError:
            await message.answer("Введите возраст числом.")
            return
        await state.update_data(age=age)
        await state.set_state(ProfileSetupState.waiting_for_weight)
        await message.answer("Ваш вес в кг? Например: 76")

    @router.message(ProfileSetupState.waiting_for_weight, F.text, ~F.text.startswith("/"))
    async def profile_weight(message: Message, state: FSMContext) -> None:
        try:
            weight = float((message.text or "").replace(",", ".").strip())
        except ValueError:
            await message.answer("Введите вес числом.")
            return
        await state.update_data(weight_kg=weight)
        await state.set_state(ProfileSetupState.waiting_for_height)
        await message.answer("Ваш рост в см? Например: 176")

    @router.message(ProfileSetupState.waiting_for_height, F.text, ~F.text.startswith("/"))
    async def profile_height(message: Message, state: FSMContext) -> None:
        try:
            height = float((message.text or "").replace(",", ".").strip())
        except ValueError:
            await message.answer("Введите рост числом.")
            return
        await state.update_data(height_cm=height)
        await state.set_state(ProfileSetupState.waiting_for_lifestyle)
        await message.answer("Опишите образ жизни и работу. Например: работа за компьютером, мало движения.")

    @router.message(ProfileSetupState.waiting_for_lifestyle, F.text, ~F.text.startswith("/"))
    async def profile_lifestyle(message: Message, state: FSMContext) -> None:
        await state.update_data(lifestyle=(message.text or "").strip())
        await state.set_state(ProfileSetupState.waiting_for_goal)
        await message.answer("Какая цель? Например: скинуть 5 кг и укрепить мышцы.")

    @router.message(ProfileSetupState.waiting_for_goal, F.text, ~F.text.startswith("/"))
    async def profile_goal(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        profile = build_profile(
            age=int(data["age"]),
            weight_kg=float(data["weight_kg"]),
            height_cm=float(data["height_cm"]),
            lifestyle=str(data["lifestyle"]),
            goal=(message.text or "").strip(),
        )
        storage.write_profile(profile)
        await state.clear()
        await message.answer("Профиль обновлен.\n\n" + format_profile(profile), reply_markup=MAIN_KEYBOARD)

    @router.message(Command("tag"))
    async def tag(message: Message, command: CommandObject) -> None:
        if await reject_if_needed(message):
            return
        tag_name = (command.args or "").strip().lower()
        if not tag_name:
            await message.answer("Укажите тег: /tag семья")
            return
        await message.answer(format_entries(storage.entries_by_tag(tag_name), f"Записей с тегом {tag_name} нет."))

    @router.message(Command("search"))
    async def search(message: Message, command: CommandObject, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        query = (command.args or "").strip()
        if not query:
            await state.set_state(SearchState.waiting_for_query)
            await message.answer("Что вы хотите узнать из дневника?")
            return
        await state.clear()
        await answer_search_query(message, query)

    @router.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        await state.clear()
        await message.answer("Ввод вопроса отменен.", reply_markup=MAIN_KEYBOARD)

    @router.message(Command("undo_last"))
    async def undo_last(message: Message, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        await state.clear()
        result = storage.undo_last_saved_entry(diary_ai.embedding_model)
        await message.answer(format_undo_result(result), reply_markup=MAIN_KEYBOARD)

    @router.message(F.text == DIARY_BUTTON_TEXT)
    async def diary_button(message: Message, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        await state.set_state(EntryModeState.waiting_for_diary)
        await message.answer("Режим: дневник. Отправьте текст или voice для сохранения фактов.")

    @router.message(F.text == NUTRITION_BUTTON_TEXT)
    async def nutrition_button(message: Message, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        await state.set_state(EntryModeState.waiting_for_nutrition)
        await message.answer("Режим: питание. Отправьте текст или voice с едой или напитками.")

    @router.message(F.text == FITNESS_BUTTON_TEXT)
    async def fitness_button(message: Message, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        await state.set_state(EntryModeState.waiting_for_fitness)
        await message.answer("Режим: фитнес. Отправьте текст или voice с активностью.")

    @router.message(F.text == SEARCH_BUTTON_TEXT)
    async def search_button(message: Message, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        await state.set_state(SearchState.waiting_for_query)
        await message.answer("Что вы хотите узнать из дневника?")

    @router.message(F.text == UNDO_BUTTON_TEXT)
    async def undo_button(message: Message, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        await state.clear()
        result = storage.undo_last_saved_entry(diary_ai.embedding_model)
        await message.answer(format_undo_result(result), reply_markup=MAIN_KEYBOARD)

    @router.message(SearchState.waiting_for_query, F.text)
    async def search_query(message: Message, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        query = (message.text or "").strip()
        await state.clear()
        if not query:
            await message.answer("Вопрос не должен быть пустым.")
            return
        await answer_search_query(message, query)

    @router.message(EntryModeState.waiting_for_diary, F.text, ~F.text.startswith("/"))
    async def diary_mode_text(message: Message, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        await state.clear()
        await process_text(message, message.text or "", "text", "diary")

    @router.message(EntryModeState.waiting_for_nutrition, F.text, ~F.text.startswith("/"))
    async def nutrition_mode_text(message: Message, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        await state.clear()
        await process_text(message, message.text or "", "text", "nutrition")

    @router.message(EntryModeState.waiting_for_fitness, F.text, ~F.text.startswith("/"))
    async def fitness_mode_text(message: Message, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        await state.clear()
        await process_text(message, message.text or "", "text", "fitness")

    @router.message(EntryModeState.waiting_for_diary, F.voice)
    async def diary_mode_voice(message: Message, bot: Bot, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        await state.clear()
        raw_text = await transcribe_voice_message(message, bot)
        if raw_text:
            await process_text(message, raw_text, "voice", "diary")

    @router.message(EntryModeState.waiting_for_nutrition, F.voice)
    async def nutrition_mode_voice(message: Message, bot: Bot, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        await state.clear()
        raw_text = await transcribe_voice_message(message, bot)
        if raw_text:
            await process_text(message, raw_text, "voice", "nutrition")

    @router.message(EntryModeState.waiting_for_fitness, F.voice)
    async def fitness_mode_voice(message: Message, bot: Bot, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        await state.clear()
        raw_text = await transcribe_voice_message(message, bot)
        if raw_text:
            await process_text(message, raw_text, "voice", "fitness")

    @router.message(F.voice)
    async def voice(message: Message, bot: Bot) -> None:
        if await reject_if_needed(message):
            return
        raw_text = await transcribe_voice_message(message, bot)
        if raw_text:
            await process_text(message, raw_text, "voice")

    @router.message(F.text)
    async def text(message: Message) -> None:
        if not message.text or message.text.startswith("/"):
            return
        await process_text(message, message.text, "text")

    return router


async def run_bot(settings: Settings) -> None:
    storage = DiaryStorage(settings.data_dir)
    storage.ensure_initialized()
    diary_ai = DiaryAI(
        api_key=settings.openai_api_key,
        transcribe_model=settings.openai_transcribe_model,
        fact_model=settings.openai_fact_model,
        embedding_model=settings.openai_embedding_model,
    )

    bot = Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(settings, storage, diary_ai))

    logger.info("Starting Powernote bot")
    await dispatcher.start_polling(bot)
