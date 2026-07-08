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
from .models import DiaryEntry
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

    async def process_text(message: Message, raw_text: str, source: str) -> None:
        if await reject_if_needed(message):
            return

        message_datetime = datetime.now(settings.timezone)
        storage.append_raw_transcript(message_datetime, source, raw_text)
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
