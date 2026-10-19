from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LoadStatus = Literal[
    "available", "booked", "dispatched", "in_transit", "delivered", "cancelled"
]


class Load(BaseModel):
    id: int
    ref: str
    status: LoadStatus
    lane_id: str | None = None
    orig_metro: str
    dest_metro: str
    equipment: str
    commodity: str
    weight_lbs: int
    pickup_at: datetime
    delivery_at: datetime
    eta: datetime | None = None
    carrier_dot: int | None = None
    assigned_rep: str
    customer_name: str
    customer_rate: Decimal | None = None
    carrier_rate: Decimal | None = None
    updated_at: datetime
    late: bool
    margin: Decimal | None = None
    as_of: datetime | None = None


class LoadEvent(BaseModel):
    id: int
    load_id: int
    type: str
    at: datetime
    city: str | None = None
    state: str | None = None
    lat: float | None = None
    lon: float | None = None
    source_integration: str
    detail: str | None = None


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None
    as_of: datetime


class Carrier(BaseModel):
    dot_number: int
    mc_number: str | None = None
    legal_name: str
    dba_name: str | None = None
    city: str | None = None
    state: str | None = None
    lat: float | None = None
    lon: float | None = None
    home_metro: str | None = None
    equipment: str
    power_units: int | None = None
    drivers: int | None = None
    safety_rating: str
    safety_rating_date: date | None = None
    recordable_crash_rate: Decimal | None = None
    authority_since: date | None = None
    hazmat: bool
    is_fictional: bool
    as_of: datetime


class Compliance(BaseModel):
    authority_status: str
    insurance_expires: date
    insurance_valid: bool
    source_integration: str
    observed_at: datetime
    as_of: datetime


class Scorecard(BaseModel):
    on_time_pct: Decimal
    loads_hauled: int
    notes: str | None = None
    notes_updated_at: datetime | None = None
    as_of: datetime


class CapacityPost(BaseModel):
    id: int
    dot_number: int
    legal_name: str
    safety_rating: str
    equipment: str
    orig_metro: str
    dest_metro: str | None = None
    available_date: date
    source_integration: str
    posted_at: datetime


class Lane(BaseModel):
    lane_id: str
    orig: str
    dest: str
    equipment: str
    miles: int
    annual_loads_2024: int


class RateHistoryPoint(BaseModel):
    week: date
    rpm: Decimal
    availability: str
    n_obs: int


class TenantLaneHistory(BaseModel):
    delivered_count_90d: int
    avg_carrier_rate_90d: Decimal | None = None


class LaneDetail(BaseModel):
    orig: str
    dest: str
    equipment: str
    lane: Lane | None = None
    miles: int
    derived: bool
    market: str
    rate_history: list[RateHistoryPoint]
    tenant_history: TenantLaneHistory
    as_of: datetime


class RateIndexRow(BaseModel):
    week: date
    market: str
    reefer_rpm: Decimal
    dry_van_rpm: Decimal
    availability: str
    n_obs: int


QuoteStatus = Literal["draft", "withdrawn"]
QuoteEquipment = Literal["reefer", "dry_van"]


class Quote(BaseModel):
    id: str
    tenant_id: str
    created_by: str
    customer_name: str
    orig_metro: str
    dest_metro: str
    equipment: QuoteEquipment
    pickup_date: date
    rate_per_mile: Decimal
    miles: int
    total: Decimal
    notes: str | None = None
    status: QuoteStatus
    version: int
    created_at: datetime
    updated_at: datetime


class QuoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_name: str = Field(min_length=1, max_length=200)
    orig_metro: str = Field(pattern=r"^[A-Z]{3}$")
    dest_metro: str = Field(pattern=r"^[A-Z]{3}$")
    equipment: QuoteEquipment
    pickup_date: date
    rate_per_mile: Decimal = Field(gt=0, le=20, max_digits=6, decimal_places=2)
    notes: str | None = Field(default=None, max_length=1000)


class QuoteUpdate(BaseModel):
    """All fields optional; at least one required. `status` may only revert to draft."""

    model_config = ConfigDict(extra="forbid")
    rate_per_mile: Decimal | None = Field(
        default=None, gt=0, le=20, max_digits=6, decimal_places=2
    )
    pickup_date: date | None = None
    notes: str | None = Field(default=None, max_length=1000)
    status: Literal["draft"] | None = None

    @model_validator(mode="after")
    def _check(self):
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        for f in ("rate_per_mile", "pickup_date", "status"):
            if f in self.model_fields_set and getattr(self, f) is None:
                raise ValueError(f"{f} cannot be null")
        return self
