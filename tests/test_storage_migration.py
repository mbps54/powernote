import json
from datetime import datetime, timezone

from powernote.models import DiaryEntry, FitnessEntry, NutritionEntry
from powernote.storage import DiaryStorage


def test_migrates_legacy_diary_and_removes_legacy_files(tmp_path) -> None:
    legacy = {
        "datetime": "2026-07-20T10:00:00+00:00",
        "tags": ["работа"],
        "facts": ["Пользователь завершил задачу."],
        "source": "text",
        "raw_text": "Завершил задачу",
    }
    (tmp_path / "diary.jsonl").write_text(json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")
    (tmp_path / "tags.json").write_text('{"tags":["работа"]}\n', encoding="utf-8")
    (tmp_path / "diary.log").write_text("legacy\n", encoding="utf-8")

    storage = DiaryStorage(tmp_path)
    storage.ensure_initialized()
    entry = storage.read_entries()[0]

    assert entry.occurred_at == datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
    assert entry.created_at == entry.occurred_at
    assert entry.id
    assert entry.message_id
    assert "tags" not in json.loads((tmp_path / "diary.jsonl").read_text(encoding="utf-8"))
    assert not (tmp_path / "tags.json").exists()
    assert not (tmp_path / "diary.log").exists()


def test_undo_uses_creation_time_and_removes_entire_message(tmp_path) -> None:
    storage = DiaryStorage(tmp_path)
    storage.ensure_initialized()
    old_created_at = datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc)
    new_created_at = datetime(2026, 8, 2, 10, 0, tzinfo=timezone.utc)

    storage.append_entries(
        [
            DiaryEntry(
                message_id="older-message",
                occurred_at=datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc),
                created_at=old_created_at,
                facts=["Старая запись"],
                source="text",
                raw_text="Старая запись",
            )
        ]
    )
    storage.append_nutrition_entries(
        [
            NutritionEntry(
                message_id="new-message",
                occurred_at=datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc),
                created_at=new_created_at,
                meal_name="завтрак",
                items=["каша"],
                source="text",
                raw_text="Вчера ел кашу и гулял",
            )
        ]
    )
    storage.append_fitness_entries(
        [
            FitnessEntry(
                message_id="new-message",
                occurred_at=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
                created_at=new_created_at,
                activity_type="active_walk",
                duration_minutes=30,
                source="text",
                raw_text="Вчера ел кашу и гулял",
            )
        ]
    )

    result = storage.undo_last_saved_entry()

    assert result is not None
    assert result["count"] == 2
    assert result["kinds"] == ["nutrition", "fitness"]
    assert len(storage.read_entries()) == 1
    assert storage.read_nutrition_entries() == []
    assert storage.read_fitness_entries() == []
