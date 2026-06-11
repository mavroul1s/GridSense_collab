# api/models/postgres.py
from typing import Optional, Any, Literal
from datetime import date, datetime
from pydantic import BaseModel, Field


class ResidentialTariff(BaseModel):
    tariff_class:    Literal["residential"] = "residential"
    rate_per_kwh:    float = Field(..., gt=0)
    standing_charge: float = Field(..., ge=0)
    night_rate_kwh:  Optional[float] = None
    night_start:     Optional[str]   = None
    night_end:       Optional[str]   = None


class CommercialTariff(BaseModel):
    tariff_class:         Literal["commercial"] = "commercial"
    rate_per_kwh:         float = Field(..., gt=0)
    standing_charge:      float = Field(..., ge=0)
    demand_charge_kw:     float = Field(..., ge=0)
    power_factor_penalty: Optional[float] = None


class IndustrialTariff(BaseModel):
    tariff_class:         Literal["industrial"] = "industrial"
    rate_per_kwh:         float = Field(..., gt=0)
    standing_charge:      float = Field(..., ge=0)
    demand_charge_kw:     float = Field(..., ge=0)
    tou_peak_rate:        float = Field(..., gt=0)
    tou_offpeak_rate:     float = Field(..., gt=0)
    tou_peak_start:       str
    tou_peak_end:         str
    interruptible:        bool  = False
    regulatory_surcharge: float = 0.0


class ConsumerAccountIn(BaseModel):
    premise_id:  str            = Field(..., min_length=1, max_length=50)
    name:        str            = Field(..., min_length=1, max_length=200)
    address:     str            = Field(..., min_length=1)
    tariff_info: dict[str, Any] = Field(default_factory=dict)
    balance:     float          = Field(default=0.0)

    model_config = {"json_schema_extra": {
        "example": {
            "premise_id":  "PREM_1",
            "name":        "Maria Papadopoulou",
            "address":     "14 Aristotelous Street, Thessaloniki 54624",
            "tariff_info": {
                "tariff_class":    "residential",
                "rate_per_kwh":    0.18,
                "standing_charge": 0.35
            },
            "balance": 0.00
        }
    }}


class ConsumerAccountOut(BaseModel):
    premise_id:  str
    name:        str
    address:     str
    tariff_info: dict[str, Any]
    balance:     float
    created_at:  datetime
    updated_at:  datetime

    model_config = {"from_attributes": True}


class InvoiceLineItem(BaseModel):
    description: str
    units:       float
    unit_label:  str
    rate:        float
    amount:      float


class InvoiceIn(BaseModel):
    premise_id:      str                    = Field(..., min_length=1, max_length=50)
    period_start:    date                   = Field(...)
    period_end:      date                   = Field(...)
    consumption_kwh: float                  = Field(..., ge=0)
    line_items:      list[InvoiceLineItem]  = Field(default_factory=list)

    model_config = {"json_schema_extra": {
        "example": {
            "premise_id":      "PREM_1",
            "period_start":    "2026-05-01",
            "period_end":      "2026-05-31",
            "consumption_kwh": 312.5,
            "line_items": [
                {
                    "description": "Energy consumption",
                    "units":       312.5,
                    "unit_label":  "kWh",
                    "rate":        0.18,
                    "amount":      56.25
                },
                {
                    "description": "Standing charge",
                    "units":       31,
                    "unit_label":  "days",
                    "rate":        0.35,
                    "amount":      10.85
                }
            ]
        }
    }}


class InvoiceOut(BaseModel):
    invoice_id:      int
    premise_id:      str
    period_start:    date
    period_end:      date
    consumption_kwh: float
    amount_due:      float
    line_items:      Any
    status:          str
    issued_at:       datetime

    model_config = {"from_attributes": True}


class BalanceUpdate(BaseModel):
    premise_id: str
    delta:      float = Field(..., description="Positive to credit, negative to debit")
    reason:     str   = Field(..., description="INVOICE, PAYMENT, CREDIT, ADJUSTMENT")