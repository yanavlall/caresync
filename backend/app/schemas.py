"""
Pydantic models for API I/O and LLM structured extraction.

The PCRExtraction model doubles as the JSON schema we give to Claude when
asking it to extract structured fields from a transcript.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class PCRExtraction(BaseModel):
    """Structured PCR fields extracted by the LLM from a transcript."""

    patient_name: Optional[str] = Field(None, description="Full name of the patient")
    patient_age: Optional[int] = Field(None, description="Age in years")
    patient_sex: Optional[Literal["F", "M", "O"]] = Field(None, description="Sex: F, M, or O")

    blood_pressure: Optional[str] = Field(None, description="Format: systolic/diastolic, e.g. 140/90")
    heart_rate: Optional[int] = Field(None, description="Beats per minute")
    respiratory_rate: Optional[int] = Field(None, description="Breaths per minute")
    spo2: Optional[int] = Field(None, description="Oxygen saturation percent (0-100)")
    temperature: Optional[float] = Field(None, description="Temperature in Fahrenheit")
    gcs: Optional[int] = Field(None, description="Glasgow Coma Scale (3-15)")

    chief_complaint: Optional[str] = Field(None, description="One-sentence primary reason for the encounter")
    hpi: Optional[str] = Field(None, description="History of present illness")
    assessment: Optional[str] = Field(None, description="Medic's clinical assessment")
    treatment: Optional[str] = Field(None, description="Interventions administered")

    confidence: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="Model's self-reported confidence in the overall extraction quality",
    )


class CreateEncounterRequest(BaseModel):
    ambulance_id: Optional[str] = None
    chief_complaint: Optional[str] = None
    severity: Optional[int] = None


class CreateEncounterResponse(BaseModel):
    encounter_id: int
    pcr_id: int
    started_at: datetime


class JobResponse(BaseModel):
    job_id: int
    encounter_id: int
    status: Literal["queued", "transcribing", "extracting", "completed", "failed"]
    transcript: Optional[str] = None
    extraction: Optional[PCRExtraction] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class PCRListItem(BaseModel):
    pcr_id: int
    encounter_id: int
    status: str
    chief_complaint: Optional[str]
    patient_name: Optional[str]
    extracted_at: Optional[datetime]
    created_at: datetime
