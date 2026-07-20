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

MINIMALLY_PROCESSED_HINTS = (
    "яйц",
    "огур",
    "помид",
    "овощ",
    "салат",
    "зелень",
    "ягод",
    "фрукт",
    "рыб",
    "тунец",
    "куриц",
    "творог",
    "йогурт без сахара",
    "кефир",
    "молок",
    "сметан",
    "моцарел",
    "сыр",
    "овсян",
    "греч",
    "кофе",
    "чай",
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

ULTRA_PROCESSED_HINTS = (
    "чипс",
    "конфет",
    "печень",
    "кола",
    "газиров",
    "фастфуд",
    "бургер",
    "картошка фри",
    "сосиск",
    "колбас",
    "пиво",
    "алког",
)


def clamp(value: float, minimum: float = 0, maximum: float = 100) -> float:
    return max(minimum, min(maximum, value))


def nutrition_quality_metadata_present(entry: NutritionEntry) -> bool:
    return any(
        field in entry.model_fields_set
        for field in ("fruit_veg_g", "added_sugar_g", "ultra_processed_score")
    )


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
                    f"{timestamp} [{entry.meal_name}] score={self.meal_nutrition_score(entry):.0f}\n"
                    f"{items}\n"
                    f"kcal={entry.calories_kcal:.0f}, protein={entry.protein_g:.1f}g, "
                    f"fat={entry.fat_g:.1f}g, carbs={entry.carbs_g:.1f}g, "
                    f"fiber={entry.fiber_g:.1f}g, fruit_veg={entry.fruit_veg_g:.0f}g, "
                    f"added_sugar={entry.added_sugar_g:.1f}g, "
                    f"ultra_processed={entry.ultra_processed_score}/100\n"
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

    def fitness_for_date(self, target_date: date) -> list[FitnessEntry]:
        return [
            entry
            for entry in self.read_fitness_entries()
            if entry.datetime.date() == target_date
        ]

    @classmethod
    def meal_nutrition_score(cls, entry: NutritionEntry) -> float:
        if not nutrition_quality_metadata_present(entry):
            return float(entry.health_score)

        score = 50.0

        if entry.protein_g >= 30:
            score += 18
        elif entry.protein_g >= 20:
            score += 14
        elif entry.protein_g >= 12:
            score += 8
        elif entry.protein_g < 5:
            score -= 6

        if entry.fiber_g >= 8:
            score += 12
        elif entry.fiber_g >= 4:
            score += 7
        elif entry.fiber_g >= 2:
            score += 2
        else:
            score -= 4

        if entry.fruit_veg_g >= 200:
            score += 12
        elif entry.fruit_veg_g >= 100:
            score += 8
        elif entry.fruit_veg_g >= 50:
            score += 4

        if entry.added_sugar_g <= 3:
            score += 5
        elif entry.added_sugar_g <= 8:
            score += 2
        elif entry.added_sugar_g <= 15:
            score -= 4
        else:
            score -= 10

        if entry.ultra_processed_score <= 15:
            score += 8
        elif entry.ultra_processed_score <= 35:
            score += 2
        elif entry.ultra_processed_score <= 60:
            score -= 6
        else:
            score -= 14

        if 250 <= entry.calories_kcal <= 700:
            score += 5
        elif entry.calories_kcal > 900:
            score -= 8
        elif entry.calories_kcal < 150 and entry.protein_g < 10:
            score -= 4

        text = cls.text_for_nutrition([entry])
        low_quality_hints = LOW_QUALITY_FOOD_HINTS
        if entry.added_sugar_g <= 8:
            low_quality_hints = tuple(hint for hint in LOW_QUALITY_FOOD_HINTS if hint != "сахар")

        score += min(5, sum(1 for hint in NATURAL_FOOD_HINTS if hint in text) * 1.5)
        score -= min(10, sum(1 for hint in low_quality_hints if hint in text) * 3)

        calibrated_score = entry.health_score * 0.35 + score * 0.65
        return clamp(calibrated_score)

    @classmethod
    def meal_ultra_processed_score(cls, entry: NutritionEntry) -> float:
        if not nutrition_quality_metadata_present(entry):
            return 0

        text = cls.text_for_nutrition([entry])
        score = float(entry.ultra_processed_score)
        low_quality_count = sum(1 for hint in ULTRA_PROCESSED_HINTS if hint in text)
        minimally_processed_count = sum(1 for hint in MINIMALLY_PROCESSED_HINTS if hint in text)

        if low_quality_count == 0:
            score = min(score, 10)
            if entry.added_sugar_g <= 5:
                score -= 3
            if minimally_processed_count >= 2:
                score -= 4
            elif minimally_processed_count == 1:
                score -= 2
        else:
            score += min(18, low_quality_count * 6)
            if entry.added_sugar_g > 15:
                score += 5

        return clamp(score)

    @staticmethod
    def nutrition_totals(entries: list[NutritionEntry]) -> dict[str, float]:
        calories = sum(entry.calories_kcal for entry in entries)
        weighted_score = sum(DiaryStorage.meal_nutrition_score(entry) * max(entry.calories_kcal, 1) for entry in entries)
        score_weight = sum(max(entry.calories_kcal, 1) for entry in entries)
        processed_score = sum(
            DiaryStorage.meal_ultra_processed_score(entry) * max(entry.calories_kcal, 1) for entry in entries
        )
        return {
            "calories_kcal": calories,
            "protein_g": sum(entry.protein_g for entry in entries),
            "fat_g": sum(entry.fat_g for entry in entries),
            "carbs_g": sum(entry.carbs_g for entry in entries),
            "fiber_g": sum(entry.fiber_g for entry in entries),
            "fruit_veg_g": sum(entry.fruit_veg_g for entry in entries),
            "added_sugar_g": sum(entry.added_sugar_g for entry in entries),
            "ultra_processed_score": processed_score / score_weight if score_weight else 0,
            "health_score": weighted_score / score_weight if score_weight else 0,
        }

    @staticmethod
    def text_for_nutrition(entries: list[NutritionEntry]) -> str:
        return " ".join(
            [
                " ".join(item for entry in entries for item in entry.items),
                " ".join(entry.raw_text for entry in entries),
                " ".join(entry.score_reason for entry in entries),
            ]
        ).lower()

    @classmethod
    def daily_nutrition_score(
        cls,
        entries: list[NutritionEntry],
        totals: dict[str, float],
        profile: UserProfile,
    ) -> float:
        if not entries:
            return 0

        calories = totals["calories_kcal"]
        protein_ratio = totals["protein_g"] / max(profile.nutrition_targets.protein_g, 1)
        fiber_ratio = totals["fiber_g"] / max(profile.nutrition_targets.fiber_g, 1)
        fruit_veg_ratio = totals["fruit_veg_g"] / max(profile.nutrition_targets.fruit_veg_g, 1)
        sugar_ratio = totals["added_sugar_g"] / max(profile.nutrition_targets.added_sugar_g, 1)
        calorie_ratio = calories / max(profile.nutrition_targets.calories_kcal, 1)
        weighted_entry_score = totals["health_score"]
        has_quality_metadata = any(nutrition_quality_metadata_present(entry) for entry in entries)

        if not has_quality_metadata:
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

            text = cls.text_for_nutrition(entries)
            score += min(8, sum(1 for hint in NATURAL_FOOD_HINTS if hint in text) * 2)
            score -= min(18, sum(1 for hint in LOW_QUALITY_FOOD_HINTS if hint in text) * 5)
            return clamp(score)

        score = weighted_entry_score * 0.25 + 35

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

        if has_quality_metadata:
            if fruit_veg_ratio >= 1:
                score += 10
            elif fruit_veg_ratio >= 0.6:
                score += 6
            elif fruit_veg_ratio >= 0.3:
                score += 2
            else:
                score -= 5

            if totals["added_sugar_g"] <= 5:
                score += 4
            elif sugar_ratio <= 0.7:
                score += 1
            elif sugar_ratio <= 1:
                score -= 3
            else:
                score -= min(12, (sugar_ratio - 1) * 10 + 3)

            processed_score = totals["ultra_processed_score"]
            if processed_score <= profile.nutrition_targets.ultra_processed_score:
                score += 6
            elif processed_score <= 45:
                score -= 4
            else:
                score -= min(14, (processed_score - 45) / 55 * 14 + 4)

        text = cls.text_for_nutrition(entries)
        score += min(6, sum(1 for hint in NATURAL_FOOD_HINTS if hint in text) * 1.5)
        score -= min(10, sum(1 for hint in LOW_QUALITY_FOOD_HINTS if hint in text) * 3)
        return clamp(score)

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

    @staticmethod
    def fitness_activity_category(entry: FitnessEntry) -> str:
        text = " ".join(
            [
                entry.activity_type,
                entry.intensity,
                " ".join(entry.muscle_groups),
                entry.raw_text,
            ]
        ).lower()
        if any(marker in text for marker in ("strength", "сил", "gym", "зал", "weights", "гантел", "штанг", "отжим", "присед")):
            return "strength"
        if any(marker in text for marker in ("active_walk", "walk", "walking", "прогул", "ходь", "пеш")):
            return "active_walk"
        if any(marker in text for marker in ("run", "бег", "bike", "bicycle", "вел", "cardio", "кардио", "плав", "swim")):
            return "cardio"
        return "activity"

    @classmethod
    def fitness_daily_totals(cls, entries: list[FitnessEntry], profile: UserProfile | None = None) -> dict[str, float]:
        active_walk_minutes = 0
        cardio_minutes = 0
        strength_minutes = 0
        strength_sessions = 0

        for entry in entries:
            category = cls.fitness_activity_category(entry)
            if category == "strength":
                strength_minutes += entry.duration_minutes
                strength_sessions += 1
            elif category == "active_walk":
                active_walk_minutes += entry.duration_minutes
            elif category == "cardio":
                cardio_minutes += entry.duration_minutes

        active_minutes = sum(entry.duration_minutes for entry in entries)
        effort_score = (
            sum(entry.effort_score for entry in entries) / len(entries)
            if entries
            else 0
        )
        targets = profile.fitness_targets if profile else UserProfile().fitness_targets
        movement_score = min(30, active_walk_minutes / max(targets.daily_active_walk_minutes, 1) * 30)
        cardio_score = min(25, cardio_minutes / max(targets.daily_cardio_minutes, 1) * 25)
        strength_score = min(25, strength_minutes / max(targets.daily_strength_minutes, 1) * 25)
        consistency_score = min(20, active_minutes / max(targets.daily_active_minutes, 1) * 20)
        health_score = min(100, movement_score + cardio_score + strength_score + consistency_score)
        if entries:
            health_score = health_score * 0.75 + effort_score * 0.25

        return {
            "active_minutes": active_minutes,
            "active_walk_minutes": active_walk_minutes,
            "cardio_minutes": cardio_minutes,
            "strength_minutes": strength_minutes,
            "strength_sessions": strength_sessions,
            "estimated_calories_kcal": sum(entry.estimated_calories_kcal for entry in entries),
            "effort_score": effort_score,
            "health_score": health_score,
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

    def delete_embeddings(self, model: str, entries: list[DiaryEntry]) -> None:
        self.ensure_initialized()
        deleted_keys = {self.entry_key(entry) for entry in entries}
        records: list[EmbeddingRecord] = []
        for line in self.embeddings_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = EmbeddingRecord.model_validate_json(line)
            if record.model == model and record.entry_key in deleted_keys:
                continue
            records.append(record)

        self._write_jsonl(self.embeddings_path, records)

    @staticmethod
    def _same_source_message(left: DiaryEntry | NutritionEntry | FitnessEntry, right: DiaryEntry | NutritionEntry | FitnessEntry) -> bool:
        return left.raw_text == right.raw_text and left.source == right.source

    @staticmethod
    def _write_jsonl(path: Path, entries: list[DiaryEntry] | list[NutritionEntry] | list[FitnessEntry] | list[EmbeddingRecord]) -> None:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as file:
            for entry in entries:
                file.write(entry.model_dump_json() + "\n")
        temp_path.replace(path)

    def _rewrite_diary_log(self, entries: list[DiaryEntry]) -> None:
        with self.diary_log_path.open("w", encoding="utf-8") as log_file:
            for entry in entries:
                timestamp = entry.datetime.strftime("%Y-%m-%d %H:%M")
                tags = ", ".join(entry.tags)
                facts = " ".join(entry.facts)
                log_file.write(f"{timestamp} [{tags}]\n{facts}\n\n")

    def _rewrite_nutrition_log(self, entries: list[NutritionEntry]) -> None:
        with self.nutrition_log_path.open("w", encoding="utf-8") as log_file:
            for entry in entries:
                timestamp = entry.datetime.strftime("%Y-%m-%d %H:%M")
                items = ", ".join(entry.items)
                log_file.write(
                    f"{timestamp} [{entry.meal_name}] score={self.meal_nutrition_score(entry):.0f}\n"
                    f"{items}\n"
                    f"kcal={entry.calories_kcal:.0f}, protein={entry.protein_g:.1f}g, "
                    f"fat={entry.fat_g:.1f}g, carbs={entry.carbs_g:.1f}g, "
                    f"fiber={entry.fiber_g:.1f}g, fruit_veg={entry.fruit_veg_g:.0f}g, "
                    f"added_sugar={entry.added_sugar_g:.1f}g, "
                    f"ultra_processed={entry.ultra_processed_score}/100\n"
                    f"{entry.score_reason}\n\n"
                )

    def _rewrite_fitness_log(self, entries: list[FitnessEntry]) -> None:
        with self.fitness_log_path.open("w", encoding="utf-8") as log_file:
            for entry in entries:
                timestamp = entry.datetime.strftime("%Y-%m-%d %H:%M")
                muscles = ", ".join(entry.muscle_groups)
                log_file.write(
                    f"{timestamp} [{entry.activity_type}] score={entry.effort_score}\n"
                    f"duration={entry.duration_minutes} min, intensity={entry.intensity}, "
                    f"muscles={muscles}, kcal={entry.estimated_calories_kcal:.0f}\n"
                    f"{entry.score_reason}\n\n"
                )

    def _rewrite_tags_from_diary(self, entries: list[DiaryEntry]) -> None:
        tags = sorted(set(DEFAULT_TAGS) | {tag for entry in entries for tag in entry.tags})
        self.tags_path.write_text(
            json.dumps({"tags": tags}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def undo_last_saved_entry(self, embedding_model: str | None = None) -> dict[str, object] | None:
        self.ensure_initialized()
        diary_entries = self.read_entries()
        nutrition_entries = self.read_nutrition_entries()
        fitness_entries = self.read_fitness_entries()

        candidates: list[tuple[int, str]] = []
        if diary_entries:
            candidates.append((self.diary_jsonl_path.stat().st_mtime_ns, "diary"))
        if nutrition_entries:
            candidates.append((self.nutrition_jsonl_path.stat().st_mtime_ns, "nutrition"))
        if fitness_entries:
            candidates.append((self.fitness_jsonl_path.stat().st_mtime_ns, "fitness"))
        if not candidates:
            return None

        kind = max(candidates)[1]
        if kind == "diary":
            entries = diary_entries
            path = self.diary_jsonl_path
        elif kind == "nutrition":
            entries = nutrition_entries
            path = self.nutrition_jsonl_path
        else:
            entries = fitness_entries
            path = self.fitness_jsonl_path

        last_entry = entries[-1]
        split_at = len(entries) - 1
        while split_at > 0 and self._same_source_message(entries[split_at - 1], last_entry):
            split_at -= 1
        kept_entries = entries[:split_at]
        removed_entries = entries[split_at:]

        self._write_jsonl(path, kept_entries)
        if kind == "diary":
            self._rewrite_diary_log(kept_entries)
            self._rewrite_tags_from_diary(kept_entries)
            if embedding_model:
                self.delete_embeddings(embedding_model, removed_entries)
        elif kind == "nutrition":
            self._rewrite_nutrition_log(kept_entries)
        else:
            self._rewrite_fitness_log(kept_entries)

        return {
            "kind": kind,
            "count": len(removed_entries),
            "last_datetime": last_entry.datetime,
            "raw_text": last_entry.raw_text,
        }

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
