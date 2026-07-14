from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ExtractedEntry(BaseModel):
    datetime_hint: str | None = None
    tags: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    entries: list[ExtractedEntry] = Field(default_factory=list)
    new_tags: list[str] = Field(default_factory=list)


class DiaryEntry(BaseModel):
    datetime: datetime
    tags: list[str]
    facts: list[str]
    source: str
    raw_text: str


class EmbeddingRecord(BaseModel):
    entry_key: str
    model: str
    embedding: list[float]


class NutritionTargets(BaseModel):
    calories_kcal: float = 1900
    protein_g: float = 130
    fat_g: float = 60
    carbs_g: float = 180
    fiber_g: float = 30


class FitnessTargets(BaseModel):
    weekly_active_minutes: int = 180
    weekly_strength_sessions: int = 3
    weekly_cardio_sessions: int = 2


class UserProfile(BaseModel):
    age: int = 40
    weight_kg: float = 76
    height_cm: float = 176
    lifestyle: str = "desk job, mostly sedentary"
    goal: str = "lose 5 kg of excess weight, improve nutrition, and build muscle"
    nutrition_targets: NutritionTargets = Field(default_factory=NutritionTargets)
    fitness_targets: FitnessTargets = Field(default_factory=FitnessTargets)


class NutritionEntry(BaseModel):
    datetime: datetime
    meal_name: str
    items: list[str] = Field(default_factory=list)
    calories_kcal: float = 0
    protein_g: float = 0
    fat_g: float = 0
    carbs_g: float = 0
    fiber_g: float = 0
    health_score: int = Field(default=50, ge=0, le=100)
    score_reason: str = ""
    source: str
    raw_text: str


class FitnessEntry(BaseModel):
    datetime: datetime
    activity_type: str
    duration_minutes: int = 0
    intensity: str = "unknown"
    muscle_groups: list[str] = Field(default_factory=list)
    estimated_calories_kcal: float = 0
    effort_score: int = Field(default=50, ge=0, le=100)
    score_reason: str = ""
    source: str
    raw_text: str


class ExtractedNutritionEntry(BaseModel):
    datetime_hint: str | None = None
    meal_name: str = "meal"
    items: list[str] = Field(default_factory=list)
    calories_kcal: float = 0
    protein_g: float = 0
    fat_g: float = 0
    carbs_g: float = 0
    fiber_g: float = 0
    health_score: int = Field(default=50, ge=0, le=100)
    score_reason: str = ""


class ExtractedFitnessEntry(BaseModel):
    datetime_hint: str | None = None
    activity_type: str = "activity"
    duration_minutes: int = 0
    intensity: str = "unknown"
    muscle_groups: list[str] = Field(default_factory=list)
    estimated_calories_kcal: float = 0
    effort_score: int = Field(default=50, ge=0, le=100)
    score_reason: str = ""


class HealthExtractionResult(BaseModel):
    is_nutrition: bool = False
    is_fitness: bool = False
    nutrition_entries: list[ExtractedNutritionEntry] = Field(default_factory=list)
    fitness_entries: list[ExtractedFitnessEntry] = Field(default_factory=list)
