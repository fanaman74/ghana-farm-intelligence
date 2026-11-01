"""Pydantic request/response models for the API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

CropId = str


class GeometryPayload(BaseModel):
    type: str
    coordinates: Any = None
    geometries: Any = None


class FarmCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    geometry: dict[str, Any]
    region: str | None = None
    district: str | None = None
    crop_id: str | None = None
    crop_variety: str | None = Field(default=None, max_length=120)
    planting_date: str | None = None
    irrigation: Literal["rainfed", "supplementary", "full", "unknown"] | None = "rainfed"
    notes: str | None = Field(default=None, max_length=2000)
    source: Literal["drawn", "imported", "sample", "field"] = "drawn"
    is_demo: bool = False
    repair: bool = False

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()


class FarmUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    geometry: dict[str, Any] | None = None
    region: str | None = None
    district: str | None = None
    crop_id: str | None = None
    crop_variety: str | None = None
    planting_date: str | None = None
    irrigation: str | None = None
    notes: str | None = None
    repair: bool = False


class FieldCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    geometry: dict[str, Any]
    crop_id: str | None = None
    crop_variety: str | None = None
    planting_date: str | None = None
    irrigation: str | None = None
    repair: bool = False


class FieldUpdate(BaseModel):
    name: str | None = None
    geometry: dict[str, Any] | None = None
    crop_id: str | None = None
    crop_variety: str | None = None
    planting_date: str | None = None
    irrigation: str | None = None


class GeometryValidateRequest(BaseModel):
    geometry: dict[str, Any]
    repair: bool = False
    check_overlaps: bool = True


class GeometrySplitRequest(BaseModel):
    geometry: dict[str, Any]
    line: dict[str, Any]


class AnalysisRequest(BaseModel):
    field_id: str | None = None
    crop_id: str | None = None
    planting_date: str | None = None
    management_level: Literal["low", "medium", "high"] = "medium"
    force: bool = False


class SoilTestCreate(BaseModel):
    sampled_on: str
    lab_name: str | None = None
    field_id: str | None = None
    depth_from_cm: int | None = Field(default=None, ge=0, le=200)
    depth_to_cm: int | None = Field(default=None, ge=0, le=200)
    ph: float | None = Field(default=None, ge=0, le=14)
    soc_gkg: float | None = Field(default=None, ge=0, le=500)
    total_n_gkg: float | None = Field(default=None, ge=0, le=50)
    p_mgkg: float | None = Field(default=None, ge=0, le=1000)
    k_mgkg: float | None = Field(default=None, ge=0, le=2000)
    cec_cmolkg: float | None = Field(default=None, ge=0, le=200)
    clay_pct: float | None = Field(default=None, ge=0, le=100)
    sand_pct: float | None = Field(default=None, ge=0, le=100)
    silt_pct: float | None = Field(default=None, ge=0, le=100)
    texture_class: str | None = None
    source_url: str | None = None
    notes: str | None = Field(default=None, max_length=2000)


class RecordCreate(BaseModel):
    kind: Literal["observation", "action", "visit", "note"] = "observation"
    title: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=4000)
    payload: dict[str, Any] | None = None
    recorded_at: str | None = None
    field_id: str | None = None


class AdvisoryCreate(BaseModel):
    title: str
    body: str
    agency: str = "GMet"
    source_url: str
    published_at: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    regions: list[str] | None = None
    crop_ids: list[str] | None = None


class MarketPriceCreate(BaseModel):
    crop_id: str
    price_ghs: float = Field(gt=0)
    unit: str
    observed_on: str
    district: str | None = None
    market: str | None = None
    source_note: str | None = None
