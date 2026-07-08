from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from openai import AsyncOpenAI

from .models import DiaryEntry, ExtractionResult


SYSTEM_PROMPT = """Ты модуль личного дневника фактов.

Твоя задача: из сырого текста пользователя извлечь только факты и события, которые действительно были сказаны пользователем.

Не добавляй интерпретации. Не делай психологических выводов. Не превращай предположения пользователя в установленные факты. Если пользователь говорит "кажется", "возможно", "я думаю", сохрани это как предположение пользователя.

Разделяй разные темы на отдельные записи. Для каждой записи выбери подходящие теги из списка существующих тегов. Новый тег создай только если ни один существующий тег не подходит.

Правила:
1. Извлекай только факты и явно сказанные события.
2. Не добавляй выводы от себя.
3. Сохраняй имена, суммы, даты, места, предметы и отношения между людьми.
4. Разделяй разные темы на разные записи.
5. Сохраняй неопределенность как предположение пользователя.
6. Не превращай эмоции в факты о других людях.
7. Если фактов нет, верни пустой массив entries.
8. Теги должны быть на русском языке, в нижнем регистре, без эмодзи, одно-два слова максимум.
9. Для "сегодня", "вчера" и явно названных дат вычисли дату относительно message_datetime.
10. datetime_hint верни в ISO 8601 с часовым поясом. Если дата факта не отличается от даты сообщения, верни null.

Верни строго JSON без markdown:
{
  "entries": [
    {
      "datetime_hint": null,
      "tags": ["семья"],
      "facts": ["Пользователь разговаривал с мамой."]
    }
  ],
  "new_tags": []
}
"""

ANSWER_SYSTEM_PROMPT = """Ты отвечаешь на вопросы пользователя по его личному дневнику фактов.

Используй только записи, переданные в контексте. Не добавляй сведения от себя и не подменяй предположения установленными фактами.

Правила ответа:
1. Сначала определи, что именно спрашивает пользователь.
2. Выбери из контекста только факты, относящиеся к вопросу.
3. Дай краткий прямой ответ на русском языке.
4. Если важны даты, суммы, имена или места, сохрани их точно.
5. Если записи содержат предположение, явно сохрани неопределенность.
6. Если данных недостаточно, прямо скажи, что в дневнике нет достаточной информации.
7. Не перечисляй все найденные записи и не описывай процесс поиска.
"""


class DiaryAI:
    def __init__(
        self,
        api_key: str,
        transcribe_model: str,
        fact_model: str,
        embedding_model: str,
    ) -> None:
        self.client = AsyncOpenAI(api_key=api_key)
        self.transcribe_model = transcribe_model
        self.fact_model = fact_model
        self.embedding_model = embedding_model

    async def transcribe(self, audio_path: Path) -> str:
        with audio_path.open("rb") as audio_file:
            transcript = await self.client.audio.transcriptions.create(
                model=self.transcribe_model,
                file=audio_file,
            )
        return transcript.text.strip()

    async def extract_facts(
        self,
        raw_text: str,
        existing_tags: list[str],
        message_datetime: datetime,
    ) -> ExtractionResult:
        user_payload = {
            "existing_tags": existing_tags,
            "message_datetime": message_datetime.isoformat(),
            "raw_text": raw_text,
        }
        response = await self.client.chat.completions.create(
            model=self.fact_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
        )
        content = response.choices[0].message.content or '{"entries":[],"new_tags":[]}'
        return ExtractionResult.model_validate_json(content)

    async def create_embeddings(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self.client.embeddings.create(
            model=self.embedding_model,
            input=texts,
        )
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]

    async def answer_question(
        self,
        question: str,
        entries: list[DiaryEntry],
    ) -> str:
        context = [
            {
                "datetime": entry.datetime.isoformat(),
                "tags": entry.tags,
                "facts": entry.facts,
            }
            for entry in entries
        ]
        response = await self.client.chat.completions.create(
            model=self.fact_model,
            messages=[
                {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"question": question, "diary_entries": context},
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        return (
            response.choices[0].message.content
            or "В дневнике нет достаточной информации для ответа."
        ).strip()
