from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .models import DiaryEntry, EmbeddingRecord, FailedMessage, FitnessEntry, NutritionEntry, UserProfile

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

FRUIT_JUICE_RE = re.compile(r"\b(?:сок(?:а|ом|и)?|juice)\b", re.IGNORECASE)
SWEETENED_JUICE_RE = re.compile(
    r"\b(?:нектар(?!ин)|сокосодержащ\w*|juice drink)\b",
    re.IGNORECASE,
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
        self.diary_jsonl_path = data_dir / "diary.jsonl"
        self.failed_messages_path = data_dir / "failed_messages.jsonl"
        self.embeddings_path = data_dir / "embeddings.jsonl"
        self.profile_path = data_dir / "profile.json"
        self.nutrition_jsonl_path = data_dir / "nutrition.jsonl"
        self.fitness_jsonl_path = data_dir / "fitness.jsonl"

    def ensure_initialized(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.diary_jsonl_path.touch(exist_ok=True)
        self.failed_messages_path.touch(exist_ok=True)
        self.embeddings_path.touch(exist_ok=True)
        self.nutrition_jsonl_path.touch(exist_ok=True)
        self.fitness_jsonl_path.touch(exist_ok=True)
        diary_changed = self._migrate_jsonl(self.diary_jsonl_path, "diary")
        self._migrate_jsonl(self.nutrition_jsonl_path, "nutrition")
        self._migrate_jsonl(self.fitness_jsonl_path, "fitness")
        if diary_changed:
            self.embeddings_path.write_text("", encoding="utf-8")
        for legacy_name in ("diary.log", "nutrition.log", "fitness.log", "tags.json", "raw_transcripts.log"):
            (self.data_dir / legacy_name).unlink(missing_ok=True)
        if not self.profile_path.exists():
            self.write_profile(UserProfile())

    @staticmethod
    def _migrate_jsonl(path: Path, kind: str) -> bool:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        changed = False
        migrated: list[dict[str, object]] = []
        previous_source_key: tuple[str, str] | None = None
        message_group = -1

        for index, line in enumerate(lines):
            payload = json.loads(line)
            if "occurred_at" not in payload:
                payload["occurred_at"] = payload.pop("datetime")
                changed = True
            if kind == "diary" and "tags" in payload:
                payload.pop("tags")
                changed = True

            source_key = (str(payload.get("source", "")), str(payload.get("raw_text", "")))
            if source_key != previous_source_key:
                message_group += 1
                previous_source_key = source_key
            if "message_id" not in payload:
                payload["message_id"] = str(
                    uuid5(NAMESPACE_URL, f"powernote:{kind}:legacy-message:{message_group}:{source_key}")
                )
                changed = True
            if "created_at" not in payload:
                payload["created_at"] = payload["occurred_at"]
                changed = True
            if "id" not in payload:
                identity = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                payload["id"] = str(uuid5(NAMESPACE_URL, f"powernote:{kind}:legacy-entry:{index}:{identity}"))
                changed = True
            migrated.append(payload)

        if changed:
            temp_path = path.with_suffix(path.suffix + ".tmp")
            with temp_path.open("w", encoding="utf-8") as file:
                for payload in migrated:
                    file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            temp_path.replace(path)
        return changed

    def read_profile(self) -> UserProfile:
        self.ensure_initialized()
        return UserProfile.model_validate_json(self.profile_path.read_text(encoding="utf-8"))

    def write_profile(self, profile: UserProfile) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.profile_path.write_text(
            profile.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

    def append_failed_message(self, message: FailedMessage) -> None:
        self.ensure_initialized()
        with self.failed_messages_path.open("a", encoding="utf-8") as file:
            file.write(message.model_dump_json() + "\n")

    def append_entries(self, entries: list[DiaryEntry]) -> None:
        self.ensure_initialized()
        if not entries:
            return

        with self.diary_jsonl_path.open("a", encoding="utf-8") as jsonl_file:
            for entry in entries:
                jsonl_file.write(entry.model_dump_json() + "\n")

    def append_nutrition_entries(self, entries: list[NutritionEntry]) -> None:
        self.ensure_initialized()
        if not entries:
            return

        with self.nutrition_jsonl_path.open("a", encoding="utf-8") as jsonl_file:
            for entry in entries:
                jsonl_file.write(entry.model_dump_json() + "\n")

    def append_fitness_entries(self, entries: list[FitnessEntry]) -> None:
        self.ensure_initialized()
        if not entries:
            return

        with self.fitness_jsonl_path.open("a", encoding="utf-8") as jsonl_file:
            for entry in entries:
                jsonl_file.write(entry.model_dump_json() + "\n")

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
        return [entry for entry in self.read_entries() if entry.occurred_at.date() == target_date]

    def nutrition_for_date(self, target_date: date) -> list[NutritionEntry]:
        return [
            entry
            for entry in self.read_nutrition_entries()
            if entry.occurred_at.date() == target_date
        ]

    def fitness_for_week(self, week_date: date) -> list[FitnessEntry]:
        week_start = week_date - timedelta(days=week_date.weekday())
        week_end = week_start + timedelta(days=7)
        return [
            entry
            for entry in self.read_fitness_entries()
            if week_start <= entry.occurred_at.date() < week_end
        ]

    def fitness_for_date(self, target_date: date) -> list[FitnessEntry]:
        return [
            entry
            for entry in self.read_fitness_entries()
            if entry.occurred_at.date() == target_date
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

        if entry.occurred_at.hour >= 21:
            if entry.calories_kcal >= 600:
                score -= 10
            elif entry.calories_kcal >= 350:
                score -= 7
            elif entry.calories_kcal >= 150:
                score -= 3
            if entry.occurred_at.hour >= 23 and entry.calories_kcal >= 100:
                score -= 2

        text = cls.text_for_nutrition([entry])
        low_quality_hints = LOW_QUALITY_FOOD_HINTS
        if entry.added_sugar_g <= 8:
            low_quality_hints = tuple(hint for hint in LOW_QUALITY_FOOD_HINTS if hint != "сахар")

        score += min(5, sum(1 for hint in NATURAL_FOOD_HINTS if hint in text) * 1.5)
        score -= min(10, sum(1 for hint in low_quality_hints if hint in text) * 3)

        if FRUIT_JUICE_RE.search(text):
            score -= 5
            if entry.fiber_g < 2:
                score -= 2
            if SWEETENED_JUICE_RE.search(text):
                score -= 6

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
    def nutrition_day_progress(cls, target_date: date, as_of: datetime | None = None) -> float:
        if as_of is None or target_date != as_of.date():
            return 1.0

        hour = as_of.hour + as_of.minute / 60
        anchors = (
            (0.0, 0.25),
            (10.0, 0.35),
            (14.0, 0.60),
            (18.0, 0.80),
            (21.0, 0.95),
            (23.0, 1.00),
        )
        for (start_hour, start_progress), (end_hour, end_progress) in zip(anchors, anchors[1:]):
            if hour <= end_hour:
                position = (hour - start_hour) / (end_hour - start_hour)
                return start_progress + max(0, position) * (end_progress - start_progress)
        return 1.0

    @classmethod
    def daily_nutrition_score(
        cls,
        entries: list[NutritionEntry],
        totals: dict[str, float],
        profile: UserProfile,
        as_of: datetime | None = None,
    ) -> float:
        if not entries:
            return 0

        calories = totals["calories_kcal"]
        target_date = entries[-1].occurred_at.date()
        day_progress = cls.nutrition_day_progress(target_date, as_of)
        fiber_progress = max(0.25, day_progress * 0.85)
        protein_ratio = totals["protein_g"] / max(profile.nutrition_targets.protein_g * day_progress, 1)
        fiber_ratio = totals["fiber_g"] / max(profile.nutrition_targets.fiber_g * fiber_progress, 1)
        fruit_veg_ratio = totals["fruit_veg_g"] / max(profile.nutrition_targets.fruit_veg_g * day_progress, 1)
        sugar_ratio = totals["added_sugar_g"] / max(profile.nutrition_targets.added_sugar_g, 1)
        calorie_ratio = calories / max(profile.nutrition_targets.calories_kcal * day_progress, 1)
        weighted_entry_score = totals["health_score"]
        has_quality_metadata = any(nutrition_quality_metadata_present(entry) for entry in entries)
        late_calories = sum(entry.calories_kcal for entry in entries if entry.occurred_at.hour >= 21)
        if late_calories >= 700:
            late_penalty = 8
        elif late_calories >= 400:
            late_penalty = 5
        elif late_calories >= 200:
            late_penalty = 3
        else:
            late_penalty = 0

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
            score -= late_penalty
            if day_progress < 1:
                score = min(score, 80 + day_progress * 20)
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
        score -= late_penalty
        if day_progress < 1:
            score = min(score, 80 + day_progress * 20)
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
        activity_type = entry.activity_type.strip().lower()
        if activity_type in {"recovery", "восстановление"}:
            return "recovery"
        if activity_type in {"strength", "силовая", "силовые"}:
            return "strength"
        if activity_type in {"active_walk", "active walk", "активная прогулка"}:
            return "active_walk"
        if activity_type in {"cardio", "кардио"}:
            return "cardio"

        type_markers = (
            ("recovery", ("recovery", "восстанов")),
            ("strength", ("strength", "сил", "gym", "зал", "weights", "гантел", "штанг")),
            ("active_walk", ("active_walk", "active walk", "walking", "прогул", "ходь", "пеш")),
            ("cardio", ("cardio", "кардио", "run", "бег", "bike", "bicycle", "вел", "плав", "swim")),
        )
        for category, markers in type_markers:
            if any(marker in activity_type for marker in markers):
                return category

        fallback_text = " ".join(
            [
                entry.intensity,
                " ".join(entry.muscle_groups),
                entry.raw_text,
            ]
        ).lower()
        if any(marker in fallback_text for marker in ("баня", "сауна", "steam", "sauna", "recovery", "восстанов")):
            return "recovery"
        if any(marker in fallback_text for marker in ("strength", "сил", "gym", "зал", "weights", "гантел", "штанг", "отжим", "присед")):
            return "strength"
        if any(marker in fallback_text for marker in ("active_walk", "walk", "walking", "прогул", "ходь", "пеш")):
            return "active_walk"
        if any(marker in fallback_text for marker in ("run", "бег", "bike", "bicycle", "вел", "cardio", "кардио", "плав", "swim")):
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

        active_minutes = active_walk_minutes + cardio_minutes + strength_minutes + sum(
            entry.duration_minutes
            for entry in entries
            if cls.fitness_activity_category(entry) == "activity"
        )
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

    @staticmethod
    def entry_key(entry: DiaryEntry) -> str:
        return entry.id

    @staticmethod
    def embedding_text(entry: DiaryEntry) -> str:
        return " ".join(entry.facts)

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
    def _write_jsonl(path: Path, entries: list[DiaryEntry] | list[NutritionEntry] | list[FitnessEntry] | list[EmbeddingRecord]) -> None:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as file:
            for entry in entries:
                file.write(entry.model_dump_json() + "\n")
        temp_path.replace(path)

    def undo_last_saved_entry(self, embedding_model: str | None = None) -> dict[str, object] | None:
        self.ensure_initialized()
        diary_entries = self.read_entries()
        nutrition_entries = self.read_nutrition_entries()
        fitness_entries = self.read_fitness_entries()

        all_entries: list[tuple[str, DiaryEntry | NutritionEntry | FitnessEntry]] = [
            *(("diary", entry) for entry in diary_entries),
            *(("nutrition", entry) for entry in nutrition_entries),
            *(("fitness", entry) for entry in fitness_entries),
        ]
        if not all_entries:
            return None

        _, last_entry = max(all_entries, key=lambda candidate: candidate[1].created_at)
        message_id = last_entry.message_id
        removed_diary = [entry for entry in diary_entries if entry.message_id == message_id]
        removed_nutrition = [entry for entry in nutrition_entries if entry.message_id == message_id]
        removed_fitness = [entry for entry in fitness_entries if entry.message_id == message_id]

        if removed_diary:
            self._write_jsonl(self.diary_jsonl_path, [entry for entry in diary_entries if entry.message_id != message_id])
            if embedding_model:
                self.delete_embeddings(embedding_model, removed_diary)
        if removed_nutrition:
            self._write_jsonl(
                self.nutrition_jsonl_path,
                [entry for entry in nutrition_entries if entry.message_id != message_id],
            )
        if removed_fitness:
            self._write_jsonl(
                self.fitness_jsonl_path,
                [entry for entry in fitness_entries if entry.message_id != message_id],
            )

        removed_by_kind = {
            "diary": len(removed_diary),
            "nutrition": len(removed_nutrition),
            "fitness": len(removed_fitness),
        }
        kinds = [kind for kind, count in removed_by_kind.items() if count]

        return {
            "kinds": kinds,
            "count": sum(removed_by_kind.values()),
            "last_datetime": last_entry.occurred_at,
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
