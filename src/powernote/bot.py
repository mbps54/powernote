from __future__ import annotations

import logging
import tempfile
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from .ai import DiaryAI
from .config import Settings
from .models import DiaryEntry, FitnessEntry, NutritionEntry, UserProfile
from .storage import DiaryStorage

logger = logging.getLogger(__name__)

SEARCH_BUTTON_TEXT = "Поиск"
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=SEARCH_BUTTON_TEXT)]],
    resize_keyboard=True,
    input_field_placeholder="Запись в дневник или вопрос",
)


class SearchState(StatesGroup):
    waiting_for_query = State()


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
    calorie_target = max(1400, maintenance - 350)
    protein_target = max(90, weight_kg * 1.7)
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
        f"- {profile.nutrition_targets.calories_kcal:g} ккал\n"
        f"- белки: {profile.nutrition_targets.protein_g:g} г\n"
        f"- жиры: {profile.nutrition_targets.fat_g:g} г\n"
        f"- углеводы: {profile.nutrition_targets.carbs_g:g} г\n"
        f"- клетчатка: {profile.nutrition_targets.fiber_g:g} г\n\n"
        "Цели фитнеса в неделю:\n"
        f"- активность: {profile.fitness_targets.weekly_active_minutes} мин\n"
        f"- силовые: {profile.fitness_targets.weekly_strength_sessions}\n"
        f"- кардио: {profile.fitness_targets.weekly_cardio_sessions}"
    )


def format_nutrition_totals(prefix: str, totals: dict[str, float]) -> str:
    return (
        f"{prefix}: "
        f"{totals['calories_kcal']:.0f} ккал, "
        f"Б {totals['protein_g']:.1f} г, "
        f"Ж {totals['fat_g']:.1f} г, "
        f"У {totals['carbs_g']:.1f} г, "
        f"клетчатка {totals['fiber_g']:.1f} г, "
        f"score {totals['health_score']:.0f}/100"
    )


def format_fitness_totals(prefix: str, totals: dict[str, float]) -> str:
    return (
        f"{prefix}: "
        f"{totals['active_minutes']:.0f} мин, "
        f"силовые {totals['strength_sessions']:.0f}, "
        f"кардио {totals['cardio_sessions']:.0f}, "
        f"{totals['estimated_calories_kcal']:.0f} ккал, "
        f"score {totals['effort_score']:.0f}/100, "
        f"успех {totals['success_percent']:.0f}%"
    )


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

    async def process_health(message: Message, raw_text: str, source: str, message_datetime: datetime) -> bool:
        profile = storage.read_profile()
        try:
            health = await diary_ai.extract_health(raw_text, profile, message_datetime)
        except Exception:
            logger.exception("Health extraction failed")
            return False

        nutrition_entries = [
            NutritionEntry(
                datetime=resolve_entry_datetime(item.datetime_hint, message_datetime),
                meal_name=item.meal_name.strip() or "meal",
                items=[entry.strip() for entry in item.items if entry.strip()],
                calories_kcal=max(item.calories_kcal, 0),
                protein_g=max(item.protein_g, 0),
                fat_g=max(item.fat_g, 0),
                carbs_g=max(item.carbs_g, 0),
                fiber_g=max(item.fiber_g, 0),
                health_score=item.health_score,
                score_reason=item.score_reason.strip(),
                source=source,
                raw_text=raw_text,
            )
            for item in health.nutrition_entries
            if health.is_nutrition and (item.items or item.calories_kcal > 0)
        ]
        fitness_entries = [
            FitnessEntry(
                datetime=resolve_entry_datetime(item.datetime_hint, message_datetime),
                activity_type=item.activity_type.strip() or "activity",
                duration_minutes=max(item.duration_minutes, 0),
                intensity=item.intensity.strip() or "unknown",
                muscle_groups=[group.strip() for group in item.muscle_groups if group.strip()],
                estimated_calories_kcal=max(item.estimated_calories_kcal, 0),
                effort_score=item.effort_score,
                score_reason=item.score_reason.strip(),
                source=source,
                raw_text=raw_text,
            )
            for item in health.fitness_entries
            if health.is_fitness and (item.duration_minutes > 0 or item.activity_type.strip())
        ]

        if not nutrition_entries and not fitness_entries:
            return False

        chunks: list[str] = []
        if nutrition_entries:
            storage.append_nutrition_entries(nutrition_entries)
            added = storage.nutrition_totals(nutrition_entries)
            day_entries = storage.nutrition_for_date(message_datetime.date())
            day = storage.nutrition_totals(day_entries)
            chunks.append(
                "Питание записано.\n"
                f"{format_nutrition_totals('Добавлено', added)}\n"
                f"{format_nutrition_totals('Сегодня', day)}\n"
                f"Комментарий: {nutrition_entries[-1].score_reason or 'Оценка сохранена.'}"
            )

        if fitness_entries:
            storage.append_fitness_entries(fitness_entries)
            added_fitness = storage.fitness_totals(fitness_entries, profile)
            week_entries = storage.fitness_for_week(message_datetime.date())
            week = storage.fitness_totals(week_entries, profile)
            chunks.append(
                "Фитнес записан.\n"
                f"{format_fitness_totals('Добавлено', added_fitness)}\n"
                f"{format_fitness_totals('Эта неделя', week)}\n"
                f"Комментарий: {fitness_entries[-1].score_reason or 'Оценка сохранена.'}"
            )

        await message.answer("\n\n".join(chunks))
        return True

    async def process_text(message: Message, raw_text: str, source: str) -> None:
        if await reject_if_needed(message):
            return

        message_datetime = datetime.now(settings.timezone)
        storage.append_raw_transcript(message_datetime, source, raw_text)
        if await process_health(message, raw_text, source, message_datetime):
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

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        if await reject_if_needed(message):
            return
        await message.answer(
            "Это личный дневник фактов.\n\n"
            "Отправьте голосовое или текстовое сообщение, а я сохраню только факты.\n\n"
            "Команды:\n"
            "/last - последние 5 записей\n"
            "/today - записи за сегодня\n"
            "/tags - список тегов\n"
            "/tag <tag> - записи по тегу\n"
            "/search <query> - поиск по смыслу\n"
            "/profile - профиль питания и фитнеса\n"
            "/profile_setup - настроить профиль\n"
            "/nutrition_today - питание за сегодня\n"
            "/fitness_week - фитнес за неделю\n"
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
        await message.answer(format_nutrition_totals("Питание сегодня", storage.nutrition_totals(entries)))

    @router.message(Command("fitness_week"))
    async def fitness_week(message: Message) -> None:
        if await reject_if_needed(message):
            return
        profile = storage.read_profile()
        week_entries = storage.fitness_for_week(datetime.now(settings.timezone).date())
        if not week_entries:
            await message.answer("На этой неделе фитнес еще не записан.")
            return
        await message.answer(format_fitness_totals("Фитнес за неделю", storage.fitness_totals(week_entries, profile)))

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

    @router.message(F.text == SEARCH_BUTTON_TEXT)
    async def search_button(message: Message, state: FSMContext) -> None:
        if await reject_if_needed(message):
            return
        await state.set_state(SearchState.waiting_for_query)
        await message.answer("Что вы хотите узнать из дневника?")

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

    @router.message(F.voice)
    async def voice(message: Message, bot: Bot) -> None:
        if await reject_if_needed(message):
            return
        if not message.voice:
            return

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
                return

        if not raw_text:
            await message.answer("Не удалось получить текст из голосового сообщения.")
            return
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
