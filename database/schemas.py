from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from database.models import (
    AppointmentStatus,
    AvailabilityStatus,
    CallOutcome,
    ReminderMethod,
    ReminderStatus,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = ""


class DepartmentRead(ORMModel):
    department_id: int
    name: str
    description: str


class DoctorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    department_id: int
    consultation_fee: Decimal = Field(ge=0)
    availability: AvailabilityStatus = AvailabilityStatus.AVAILABLE


class DoctorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    consultation_fee: Decimal | None = Field(default=None, ge=0)
    availability: AvailabilityStatus | None = None


class DoctorRead(ORMModel):
    doctor_id: int
    name: str
    department_id: int
    consultation_fee: Decimal
    availability: AvailabilityStatus
    department: DepartmentRead | None = None


class ScheduleCreate(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    slot_minutes: int = Field(default=30, ge=5, le=120)

    @field_validator("end_time")
    @classmethod
    def end_after_start(cls, v, info):
        if info.data.get("start_time") and v <= info.data["start_time"]:
            raise ValueError("end_time must be after start_time")
        return v


class ScheduleRead(ORMModel):
    schedule_id: int
    doctor_id: int
    doctor_name: str | None = None
    day_of_week: int
    start_time: time
    end_time: time
    slot_minutes: int


class PatientCreate(BaseModel):
    full_name: str | None = Field(default=None, max_length=120)
    phone_number: str


class PatientRead(ORMModel):
    patient_id: int
    full_name: str
    phone_number: str
    registered_at: datetime


class AppointmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_id: int
    doctor_id: int
    appointment_at: datetime
    reason: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=64)


class AppointmentRead(ORMModel):
    appointment_id: int
    patient_id: int
    doctor_id: int
    appointment_at: datetime
    status: AppointmentStatus
    reason: str | None
    idempotency_key: str | None
    created_at: datetime
    patient_name: str | None = None
    doctor_name: str | None = None


class AppointmentUpdate(BaseModel):
    status: AppointmentStatus


class AvailabilityRead(BaseModel):
    doctor_id: int
    date: date
    available_times: list[str]


class CallSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caller_phone_number: str
    patient_id: int | None = None


class CallSessionRead(ORMModel):
    call_id: int
    patient_id: int | None
    caller_phone_number: str
    started_at: datetime
    ended_at: datetime | None
    outcome: CallOutcome | None
    summary: str | None


class CallSessionUpdate(BaseModel):
    ended_at: datetime | None = None
    outcome: CallOutcome | None = None
    summary: str | None = None
    patient_id: int | None = None


class ReminderCreate(BaseModel):
    appointment_id: int
    scheduled_at: datetime
    method: ReminderMethod = ReminderMethod.SMS


class ReminderRead(ORMModel):
    reminder_id: int
    appointment_id: int
    scheduled_at: datetime
    method: ReminderMethod
    status: ReminderStatus
    sent_at: datetime | None


class ReminderUpdate(BaseModel):
    status: ReminderStatus | None = None
    sent_at: datetime | None = None


__all__ = [
    "AppointmentCreate",
    "AppointmentRead",
    "AppointmentStatus",
    "AppointmentUpdate",
    "AvailabilityRead",
    "AvailabilityStatus",
    "CallOutcome",
    "CallSessionCreate",
    "CallSessionRead",
    "CallSessionUpdate",
    "DepartmentCreate",
    "DepartmentRead",
    "DoctorCreate",
    "DoctorRead",
    "DoctorUpdate",
    "PatientCreate",
    "PatientRead",
    "ReminderCreate",
    "ReminderMethod",
    "ReminderRead",
    "ReminderStatus",
    "ReminderUpdate",
    "ScheduleCreate",
    "ScheduleRead",
]
