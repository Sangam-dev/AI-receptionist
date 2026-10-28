import enum
from datetime import datetime, time
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AvailabilityStatus(str, enum.Enum):
    AVAILABLE = "available"
    ON_LEAVE = "on_leave"
    UNAVAILABLE = "unavailable"


class AppointmentStatus(str, enum.Enum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class ReminderMethod(str, enum.Enum):
    SMS = "sms"
    VOICE = "voice"
    EMAIL = "email"


class ReminderStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class CallOutcome(str, enum.Enum):
    BOOKED = "booked"
    CANCELLED = "cancelled"
    RESCHEDULED = "rescheduled"
    INQUIRY = "inquiry"
    TRANSFERRED = "transferred"
    NO_ACTION = "no_action"


def _enum(cls):
    return Enum(
        cls,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


class Department(Base):
    __tablename__ = "departments"

    department_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")

    doctors: Mapped[list["Doctor"]] = relationship(back_populates="department")


class Doctor(Base):
    __tablename__ = "doctors"

    doctor_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.department_id"))
    consultation_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    availability: Mapped[AvailabilityStatus] = mapped_column(
        _enum(AvailabilityStatus), default=AvailabilityStatus.AVAILABLE
    )

    department: Mapped["Department"] = relationship(back_populates="doctors")
    schedules: Mapped[list["DoctorSchedule"]] = relationship(back_populates="doctor")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="doctor")


class DoctorSchedule(Base):
    __tablename__ = "doctor_schedules"
    __table_args__ = (
        UniqueConstraint("doctor_id", "day_of_week", name="uq_schedule_doctor_day"),
    )

    schedule_id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.doctor_id"))
    day_of_week: Mapped[int] = mapped_column(default=0)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    slot_minutes: Mapped[int] = mapped_column(default=30)

    doctor: Mapped["Doctor"] = relationship(back_populates="schedules")

    @property
    def doctor_name(self) -> str | None:
        return self.doctor.name if self.doctor is not None else None


class Patient(Base):
    __tablename__ = "patients"

    patient_id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    phone_number: Mapped[str] = mapped_column(String(20), unique=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        UniqueConstraint(
            "doctor_id", "appointment_at", name="uq_appointment_doctor_slot"
        ),
        Index("ix_appointments_doctor_time", "doctor_id", "appointment_at"),
    )

    appointment_id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.patient_id"))
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.doctor_id"))
    appointment_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[AppointmentStatus] = mapped_column(
        _enum(AppointmentStatus), default=AppointmentStatus.CONFIRMED
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    patient: Mapped["Patient"] = relationship()
    doctor: Mapped["Doctor"] = relationship(back_populates="appointments")
    reminders: Mapped[list["Reminder"]] = relationship(back_populates="appointment")

    @property
    def patient_name(self) -> str | None:
        return self.patient.full_name if self.patient is not None else None

    @property
    def doctor_name(self) -> str | None:
        return self.doctor.name if self.doctor is not None else None


class CallSession(Base):
    __tablename__ = "call_sessions"

    call_id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int | None] = mapped_column(
        ForeignKey("patients.patient_id"), nullable=True
    )
    caller_phone_number: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    outcome: Mapped[CallOutcome | None] = mapped_column(
        _enum(CallOutcome), nullable=True
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    patient: Mapped["Patient | None"] = relationship()


class Reminder(Base):
    __tablename__ = "reminders"

    reminder_id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(
        ForeignKey("appointments.appointment_id")
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime)
    method: Mapped[ReminderMethod] = mapped_column(
        _enum(ReminderMethod), default=ReminderMethod.SMS
    )
    status: Mapped[ReminderStatus] = mapped_column(
        _enum(ReminderStatus), default=ReminderStatus.PENDING
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    appointment: Mapped["Appointment"] = relationship(back_populates="reminders")
