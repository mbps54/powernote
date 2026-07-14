from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

from .models import DiaryEntry, EmbeddingRecord, FitnessEntry, NutritionEntry, UserProfile


DEFAULT_TAGS = [
    "семья",
    "машина",
    "цены",
    "финансы",
    "работа",
    "здоровье",
    "документы",
    "покупки",
    "дом",
    "путешествия",
    "друзья",
    "обучение",
]


class DiaryStorage:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.diary_log_path = data_dir / "diary.log"
        self.diary_jsonl_path = data_dir / "diary.jsonl"
        self.tags_path = data_dir / "tags.json"
        self.raw_transcripts_path = data_dir / "raw_transcripts.log"
        self.embeddings_path = data_dir / "embeddings.jsonl"
        self.profile_path = data_dir / "profile.json"
        self.nutrition_log_path = data_dir / "nutrition.log"
        self.nutrition_jsonl_path = data_dir / "nutrition.jsonl"
        self.fitness_log_path = data_dir / "fitness.log"
        self.fitness_jsonl_path = data_dir / "fitness.jsonl"

    def ensure_initialized(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.diary_log_path.touch(exist_ok=True)
        self.diary_jsonl_path.touch(exist_ok=True)
        self.raw_transcripts_path.touch(exist_ok=True)
        self.embeddings_path.touch(exist_ok=True)
        self.nutrition_log_path.touch(exist_ok=True)
        self.nutrition_jsonl_path.touch(exist_ok=True)
        self.fitness_log_path.touch(exist_ok=True)
        self.fitness_jsonl_path.touch(exist_ok=True)
        if not self.profile_path.exists():
            self.write_profile(UserProfile())
        if not self.tags_path.exists():
            self.tags_path.write_text(
                json.dumps({"tags": DEFAULT_TAGS}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    def read_profile(self) -> UserProfile:
        self.ensure_initialized()
        return UserProfile.model_validate_json(self.profile_path.read_text(encoding="utf-8"))

    def write_profile(self, profile: UserProfile) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.profile_path.write_text(
            profile.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

    def get_tags(self) -> list[str]:
        self.ensure_initialized()
        payload = json.loads(self.tags_path.read_text(encoding="utf-8"))
        return sorted({str(tag).strip().lower() for tag in payload.get("tags", []) if str(tag).strip()})

    def update_tags(self, tags: list[str]) -> list[str]:
        existing = set(self.get_tags())
        normalized = {tag.strip().lower() for tag in tags if tag.strip()}
        merged = sorted(existing | normalized)
        self.tags_path.write_text(
            json.dumps({"tags": merged}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return merged

    def append_raw_transcript(self, message_datetime: datetime, source: str, raw_text: str) -> None:
        self.ensure_initialized()
        line = f"{message_datetime.isoformat()} [{source}] {raw_text.strip()}\n"
        with self.raw_transcripts_path.open("a", encoding="utf-8") as file:
            file.write(line)

    def append_entries(self, entries: list[DiaryEntry]) -> None:
        self.ensure_initialized()
        if not entries:
            return

        with self.diary_jsonl_path.open("a", encoding="utf-8") as jsonl_file:
            for entry in entries:
                jsonl_file.write(entry.model_dump_json() + "\n")

        with self.diary_log_path.open("a", encoding="utf-8") as log_file:
            for entry in entries:
                timestamp = entry.datetime.strftime("%Y-%m-%d %H:%M")
                tags = ", ".join(entry.tags)
                facts = " ".join(entry.facts)
                log_file.write(f"{timestamp} [{tags}]\n{facts}\n\n")

        self.update_tags([tag for entry in entries for tag in entry.tags])

    def append_nutrition_entries(self, entries: list[NutritionEntry]) -> None:
        self.ensure_initialized()
        if not entries:
            return

        with self.nutrition_jsonl_path.open("a", encoding="utf-8") as jsonl_file:
            for entry in entries:
                jsonl_file.write(entry.model_dump_json() + "\n")

        with self.nutrition_log_path.open("a", encoding="utf-8") as log_file:
            for entry in entries:
                timestamp = entry.datetime.strftime("%Y-%m-%d %H:%M")
                items = ", ".join(entry.items)
                log_file.write(
                    f"{timestamp} [{entry.meal_name}] score={entry.health_score}\n"
                    f"{items}\n"
                    f"kcal={entry.calories_kcal:.0f}, protein={entry.protein_g:.1f}g, "
                    f"fat={entry.fat_g:.1f}g, carbs={entry.carbs_g:.1f}g, "
                    f"fiber={entry.fiber_g:.1f}g\n"
                    f"{entry.score_reason}\n\n"
                )

    def append_fitness_entries(self, entries: list[FitnessEntry]) -> None:
        self.ensure_initialized()
        if not entries:
            return

        with self.fitness_jsonl_path.open("a", encoding="utf-8") as jsonl_file:
            for entry in entries:
                jsonl_file.write(entry.model_dump_json() + "\n")

        with self.fitness_log_path.open("a", encoding="utf-8") as log_file:
            for entry in entries:
                timestamp = entry.datetime.strftime("%Y-%m-%d %H:%M")
                muscles = ", ".join(entry.muscle_groups)
                log_file.write(
                    f"{timestamp} [{entry.activity_type}] score={entry.effort_score}\n"
                    f"duration={entry.duration_minutes} min, intensity={entry.intensity}, "
                    f"muscles={muscles}, kcal={entry.estimated_calories_kcal:.0f}\n"
                    f"{entry.score_reason}\n\n"
                )

    def read_entries(self) -> list[DiaryEntry]:
        self.ensure_initialized()
        entries: list[DiaryEntry] = []
        for line in self.diary_jsonl_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entries.append(DiaryEntry.model_validate_json(line))
        return entries

    def read_nutrition_entries(self) -> list[NutritionEntry]:
        self.ensure_initialized()
        entries: list[NutritionEntry] = []
        for line in self.nutrition_jsonl_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entries.append(NutritionEntry.model_validate_json(line))
        return entries

    def read_fitness_entries(self) -> list[FitnessEntry]:
        self.ensure_initialized()
        entries: list[FitnessEntry] = []
        for line in self.fitness_jsonl_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entries.append(FitnessEntry.model_validate_json(line))
        return entries

    def last_entries(self, limit: int = 5) -> list[DiaryEntry]:
        return self.read_entries()[-limit:]

    def entries_for_date(self, target_date: date) -> list[DiaryEntry]:
        return [entry for entry in self.read_entries() if entry.datetime.date() == target_date]

    def nutrition_for_date(self, target_date: date) -> list[NutritionEntry]:
        return [
            entry
            for entry in self.read_nutrition_entries()
            if entry.datetime.date() == target_date
        ]

    def fitness_for_week(self, week_date: date) -> list[FitnessEntry]:
        week_start = week_date - timedelta(days=week_date.weekday())
        week_end = week_start + timedelta(days=7)
        return [
            entry
            for entry in self.read_fitness_entries()
            if week_start <= entry.datetime.date() < week_end
        ]

    @staticmethod
    def nutrition_totals(entries: list[NutritionEntry]) -> dict[str, float]:
        calories = sum(entry.calories_kcal for entry in entries)
        weighted_score = sum(entry.health_score * max(entry.calories_kcal, 1) for entry in entries)
        score_weight = sum(max(entry.calories_kcal, 1) for entry in entries)
        return {
            "calories_kcal": calories,
            "protein_g": sum(entry.protein_g for entry in entries),
            "fat_g": sum(entry.fat_g for entry in entries),
            "carbs_g": sum(entry.carbs_g for entry in entries),
            "fiber_g": sum(entry.fiber_g for entry in entries),
            "health_score": weighted_score / score_weight if score_weight else 0,
        }

    @staticmethod
    def fitness_totals(entries: list[FitnessEntry], profile: UserProfile) -> dict[str, float]:
        active_minutes = sum(entry.duration_minutes for entry in entries)
        strength_sessions = sum(
            1
            for entry in entries
            if any(
                marker in entry.activity_type.lower()
                for marker in ("strength", "сил", "gym", "зал", "weights", "гантел")
            )
        )
        cardio_sessions = sum(
            1
            for entry in entries
            if any(
                marker in entry.activity_type.lower()
                for marker in ("run", "бег", "walk", "ход", "bike", "cardio", "кардио")
            )
        )
        effort_score = (
            sum(entry.effort_score for entry in entries) / len(entries)
            if entries
            else 0
        )
        minute_progress = active_minutes / max(profile.fitness_targets.weekly_active_minutes, 1)
        strength_progress = strength_sessions / max(profile.fitness_targets.weekly_strength_sessions, 1)
        cardio_progress = cardio_sessions / max(profile.fitness_targets.weekly_cardio_sessions, 1)
        success_percent = min(
            100,
            (minute_progress * 0.5 + strength_progress * 0.3 + cardio_progress * 0.2) * 100,
        )
        return {
            "active_minutes": active_minutes,
            "strength_sessions": strength_sessions,
            "cardio_sessions": cardio_sessions,
            "estimated_calories_kcal": sum(entry.estimated_calories_kcal for entry in entries),
            "effort_score": effort_score,
            "success_percent": success_percent,
        }

    def entries_by_tag(self, tag: str, limit: int = 10) -> list[DiaryEntry]:
        normalized = tag.strip().lower()
        entries = [entry for entry in self.read_entries() if normalized in entry.tags]
        return entries[-limit:]

    @staticmethod
    def entry_key(entry: DiaryEntry) -> str:
        payload = {
            "datetime": entry.datetime.isoformat(),
            "tags": entry.tags,
            "facts": entry.facts,
            "source": entry.source,
            "raw_text": entry.raw_text,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def embedding_text(entry: DiaryEntry) -> str:
        tags = ", ".join(entry.tags)
        facts = " ".join(entry.facts)
        return f"Теги: {tags}\nФакты: {facts}"

    def read_embeddings(self, model: str) -> dict[str, list[float]]:
        self.ensure_initialized()
        embeddings: dict[str, list[float]] = {}
        for line in self.embeddings_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = EmbeddingRecord.model_validate_json(line)
            if record.model == model:
                embeddings[record.entry_key] = record.embedding
        return embeddings

    def upsert_embeddings(
        self,
        model: str,
        entries: list[DiaryEntry],
        vectors: list[list[float]],
    ) -> None:
        if len(entries) != len(vectors):
            raise ValueError("Entries and embedding vectors count must match")

        self.ensure_initialized()
        records: dict[tuple[str, str], EmbeddingRecord] = {}
        for line in self.embeddings_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = EmbeddingRecord.model_validate_json(line)
            records[(record.model, record.entry_key)] = record

        for entry, vector in zip(entries, vectors, strict=True):
            record = EmbeddingRecord(
                entry_key=self.entry_key(entry),
                model=model,
                embedding=vector,
            )
            records[(model, record.entry_key)] = record

        temp_path = self.embeddings_path.with_suffix(".jsonl.tmp")
        with temp_path.open("w", encoding="utf-8") as file:
            for record in records.values():
                file.write(record.model_dump_json() + "\n")
        temp_path.replace(self.embeddings_path)

    @staticmethod
    def cosine_similarity(left: list[float], right: list[float]) -> float:
        if len(left) != len(right) or not left:
            return 0.0
        dot_product = sum(a * b for a, b in zip(left, right, strict=True))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return dot_product / (left_norm * right_norm)

    def semantic_search(
        self,
        entries: list[DiaryEntry],
        embeddings: dict[str, list[float]],
        query_embedding: list[float],
        limit: int,
        min_score: float,
    ) -> list[DiaryEntry]:
        ranked = [
            (
                self.cosine_similarity(query_embedding, embeddings[self.entry_key(entry)]),
                index,
                entry,
            )
            for index, entry in enumerate(entries)
            if self.entry_key(entry) in embeddings
        ]
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [entry for score, _, entry in ranked[:limit] if score >= min_score]
