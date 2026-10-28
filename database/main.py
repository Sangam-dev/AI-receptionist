from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import models, schemas
from database.db import get_session, init_db

ADMIN_HTML = Path(__file__).resolve().parent / "admin.html"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Receptionist Database API", version="0.1.0", lifespan=lifespan)

Session = AsyncSession


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/admin", include_in_schema=False)
async def admin_page():
    return FileResponse(ADMIN_HTML)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/admin")


@app.get("/departments", response_model=list[schemas.DepartmentRead])
async def list_departments(db: Session = Depends(get_session)):
    result = await db.scalars(
        select(models.Department).order_by(models.Department.name)
    )
    return list(result)


@app.post("/departments", response_model=schemas.DepartmentRead, status_code=201)
async def create_department(
    payload: schemas.DepartmentCreate, db: Session = Depends(get_session)
):
    department = models.Department(**payload.model_dump())
    db.add(department)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, detail=f"department '{payload.name}' already exists")
    await db.refresh(department)
    return department


@app.get("/departments/{department_id}", response_model=schemas.DepartmentRead)
async def get_department(department_id: int, db: Session = Depends(get_session)):
    department = await db.get(models.Department, department_id)
    if department is None:
        raise HTTPException(404, detail="department not found")
    return department


@app.get("/doctors", response_model=list[schemas.DoctorRead])
async def list_doctors(
    department_id: int | None = Query(default=None),
    db: Session = Depends(get_session),
):
    query = select(models.Doctor).options(selectinload(models.Doctor.department))
    if department_id is not None:
        query = query.where(models.Doctor.department_id == department_id)
    result = await db.scalars(query.order_by(models.Doctor.name))
    return list(result)


@app.post("/doctors", response_model=schemas.DoctorRead, status_code=201)
async def create_doctor(
    payload: schemas.DoctorCreate, db: Session = Depends(get_session)
):
    if await db.get(models.Department, payload.department_id) is None:
        raise HTTPException(404, detail="department not found")
    doctor = models.Doctor(**payload.model_dump())
    db.add(doctor)
    await db.commit()
    doctor = await db.scalar(
        select(models.Doctor)
        .options(selectinload(models.Doctor.department))
        .where(models.Doctor.doctor_id == doctor.doctor_id)
    )
    return doctor


@app.patch("/doctors/{doctor_id}", response_model=schemas.DoctorRead)
async def update_doctor(
    doctor_id: int, payload: schemas.DoctorUpdate, db: Session = Depends(get_session)
):
    doctor = await db.get(models.Doctor, doctor_id)
    if doctor is None:
        raise HTTPException(404, detail="doctor not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(doctor, field, value)
    await db.commit()
    return await db.scalar(
        select(models.Doctor)
        .options(selectinload(models.Doctor.department))
        .where(models.Doctor.doctor_id == doctor_id)
    )


@app.get("/doctors/{doctor_id}", response_model=schemas.DoctorRead)
async def get_doctor(doctor_id: int, db: Session = Depends(get_session)):
    doctor = await db.scalar(
        select(models.Doctor)
        .options(selectinload(models.Doctor.department))
        .where(models.Doctor.doctor_id == doctor_id)
    )
    if doctor is None:
        raise HTTPException(404, detail="doctor not found")
    return doctor


@app.post(
    "/doctors/{doctor_id}/schedules",
    response_model=schemas.ScheduleRead,
    status_code=201,
)
async def create_schedule(
    doctor_id: int, payload: schemas.ScheduleCreate, db: Session = Depends(get_session)
):
    if await db.get(models.Doctor, doctor_id) is None:
        raise HTTPException(404, detail="doctor not found")
    schedule = models.DoctorSchedule(doctor_id=doctor_id, **payload.model_dump())
    db.add(schedule)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            409,
            detail=f"doctor already has a schedule for weekday {payload.day_of_week}",
        )
    await db.commit()
    return await db.scalar(
        select(models.DoctorSchedule)
        .options(selectinload(models.DoctorSchedule.doctor))
        .where(models.DoctorSchedule.schedule_id == schedule.schedule_id)
    )


@app.get("/schedules", response_model=list[schemas.ScheduleRead])
async def list_all_schedules(db: Session = Depends(get_session)):
    result = await db.scalars(
        select(models.DoctorSchedule).options(
            selectinload(models.DoctorSchedule.doctor)
        )
    )
    return list(result)


@app.get("/doctors/{doctor_id}/schedules", response_model=list[schemas.ScheduleRead])
async def list_schedules(doctor_id: int, db: Session = Depends(get_session)):
    result = await db.scalars(
        select(models.DoctorSchedule)
        .options(selectinload(models.DoctorSchedule.doctor))
        .where(models.DoctorSchedule.doctor_id == doctor_id)
        .order_by(models.DoctorSchedule.day_of_week)
    )
    return list(result)


@app.get("/doctors/{doctor_id}/availability", response_model=schemas.AvailabilityRead)
async def doctor_availability(
    doctor_id: int,
    day: date = Query(...),
    db: Session = Depends(get_session),
):
    if await db.get(models.Doctor, doctor_id) is None:
        raise HTTPException(404, detail="doctor not found")
    schedule = await db.scalar(
        select(models.DoctorSchedule).where(
            models.DoctorSchedule.doctor_id == doctor_id,
            models.DoctorSchedule.day_of_week == day.weekday(),
        )
    )
    booked = list(
        await db.scalars(
            select(models.Appointment.appointment_at).where(
                models.Appointment.doctor_id == doctor_id,
                models.Appointment.status != models.AppointmentStatus.CANCELLED,
                models.Appointment.appointment_at >= datetime.combine(day, time.min),
                models.Appointment.appointment_at < datetime.combine(day, time.max),
            )
        )
    )
    slots: list[str] = []
    if schedule is not None:
        step = timedelta(minutes=schedule.slot_minutes)
        cursor = datetime.combine(day, schedule.start_time)
        end = datetime.combine(day, schedule.end_time)
        now = datetime.now()
        while cursor + step <= end:
            if not (day == now.date() and cursor < now) and not any(
                cursor <= at < cursor + step for at in booked
            ):
                slots.append(cursor.strftime("%H:%M"))
            cursor += step
    return schemas.AvailabilityRead(
        doctor_id=doctor_id, date=day, available_times=slots
    )


@app.post("/patients", response_model=schemas.PatientRead, status_code=201)
async def register_patient(
    payload: schemas.PatientCreate,
    response: Response,
    db: Session = Depends(get_session),
):
    existing = await db.scalar(
        select(models.Patient).where(
            models.Patient.phone_number == payload.phone_number
        )
    )
    if existing is not None:
        response.status_code = 200
        return existing
    patient = models.Patient(
        full_name=payload.full_name or payload.phone_number,
        phone_number=payload.phone_number,
    )
    db.add(patient)
    await db.commit()
    await db.refresh(patient)
    return patient


@app.get("/patients/{patient_id}", response_model=schemas.PatientRead)
async def get_patient(patient_id: int, db: Session = Depends(get_session)):
    patient = await db.get(models.Patient, patient_id)
    if patient is None:
        raise HTTPException(404, detail="patient not found")
    return patient


@app.get("/patients", response_model=list[schemas.PatientRead])
async def list_patients(db: Session = Depends(get_session)):
    result = await db.scalars(
        select(models.Patient).order_by(models.Patient.registered_at.desc())
    )
    return list(result)


@app.get("/patients/search/by-phone", response_model=schemas.PatientRead)
async def search_patient(
    phone_number: str = Query(...), db: Session = Depends(get_session)
):
    patient = await db.scalar(
        select(models.Patient).where(models.Patient.phone_number == phone_number)
    )
    if patient is None:
        raise HTTPException(404, detail="patient not found")
    return patient


@app.post("/appointments", response_model=schemas.AppointmentRead, status_code=201)
async def book_appointment(
    payload: schemas.AppointmentCreate,
    response: Response,
    idempotency_key: str | None = Header(default=None),
    db: Session = Depends(get_session),
):
    key = payload.idempotency_key or idempotency_key
    if key is not None:
        existing = await db.scalar(
            select(models.Appointment)
            .options(
                selectinload(models.Appointment.patient),
                selectinload(models.Appointment.doctor),
            )
            .where(models.Appointment.idempotency_key == key)
        )
        if existing is not None:
            response.status_code = 200
            return existing
    doctor = await db.get(models.Doctor, payload.doctor_id)
    if doctor is None:
        raise HTTPException(404, detail="doctor not found")
    if doctor.availability != models.AvailabilityStatus.AVAILABLE:
        raise HTTPException(409, detail="doctor is not accepting appointments")
    if await db.get(models.Patient, payload.patient_id) is None:
        raise HTTPException(404, detail="patient not found")
    when = payload.appointment_at.replace(tzinfo=None, second=0, microsecond=0)
    schedule = await db.scalar(
        select(models.DoctorSchedule).where(
            models.DoctorSchedule.doctor_id == doctor.doctor_id,
            models.DoctorSchedule.day_of_week == when.weekday(),
        )
    )
    if schedule is None:
        raise HTTPException(409, detail="doctor has no working hours that day")
    slot = timedelta(minutes=schedule.slot_minutes)
    start = datetime.combine(when.date(), schedule.start_time)
    last_start = datetime.combine(when.date(), schedule.end_time) - slot
    minutes = when.hour * 60 + when.minute
    if minutes % schedule.slot_minutes != 0 or not (start <= when <= last_start):
        raise HTTPException(
            409,
            detail=(
                f"requested time is outside working hours "
                f"({schedule.start_time.strftime('%H:%M')}-"
                f"{schedule.end_time.strftime('%H:%M')}, {schedule.slot_minutes} min slots)"
            ),
        )
    neighbors = list(
        await db.scalars(
            select(models.Appointment.appointment_at).where(
                models.Appointment.doctor_id == doctor.doctor_id,
                models.Appointment.status != models.AppointmentStatus.CANCELLED,
                models.Appointment.appointment_at >= when - slot,
                models.Appointment.appointment_at < when + slot,
            )
        )
    )
    if any(abs(at - when) < slot for at in neighbors):
        raise HTTPException(409, detail="slot is already booked")
    appointment = models.Appointment(
        patient_id=payload.patient_id,
        doctor_id=doctor.doctor_id,
        appointment_at=when,
        reason=payload.reason,
        idempotency_key=key,
    )
    db.add(appointment)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, detail="slot was just taken by another caller")
    appointment = await db.scalar(
        select(models.Appointment)
        .options(
            selectinload(models.Appointment.patient),
            selectinload(models.Appointment.doctor),
        )
        .where(models.Appointment.appointment_id == appointment.appointment_id)
    )
    reminder_at = when - timedelta(hours=24)
    if reminder_at > datetime.now():
        db.add(
            models.Reminder(
                appointment_id=appointment.appointment_id, scheduled_at=reminder_at
            )
        )
        await db.commit()
    return appointment


@app.get("/appointments", response_model=list[schemas.AppointmentRead])
async def list_appointments(
    patient_id: int | None = Query(default=None),
    doctor_id: int | None = Query(default=None),
    status: schemas.AppointmentStatus | None = Query(default=None),
    day: date | None = Query(default=None),
    db: Session = Depends(get_session),
):
    query = (
        select(models.Appointment)
        .options(
            selectinload(models.Appointment.patient),
            selectinload(models.Appointment.doctor),
        )
        .order_by(models.Appointment.appointment_at)
    )
    if patient_id is not None:
        query = query.where(models.Appointment.patient_id == patient_id)
    if doctor_id is not None:
        query = query.where(models.Appointment.doctor_id == doctor_id)
    if status is not None:
        query = query.where(models.Appointment.status == status)
    if day is not None:
        query = query.where(
            func.date(models.Appointment.appointment_at) == day.isoformat()
        )
    return list(await db.scalars(query))


@app.get("/appointments/{appointment_id}", response_model=schemas.AppointmentRead)
async def get_appointment(appointment_id: int, db: Session = Depends(get_session)):
    appointment = await db.scalar(
        select(models.Appointment)
        .options(
            selectinload(models.Appointment.patient),
            selectinload(models.Appointment.doctor),
        )
        .where(models.Appointment.appointment_id == appointment_id)
    )
    if appointment is None:
        raise HTTPException(404, detail="appointment not found")
    return appointment


@app.patch("/appointments/{appointment_id}", response_model=schemas.AppointmentRead)
async def update_appointment(
    appointment_id: int,
    payload: schemas.AppointmentUpdate,
    db: Session = Depends(get_session),
):
    appointment = await db.get(models.Appointment, appointment_id)
    if appointment is None:
        raise HTTPException(404, detail="appointment not found")
    appointment.status = payload.status
    await db.commit()
    return await db.scalar(
        select(models.Appointment)
        .options(
            selectinload(models.Appointment.patient),
            selectinload(models.Appointment.doctor),
        )
        .where(models.Appointment.appointment_id == appointment_id)
    )


@app.post("/calls", response_model=schemas.CallSessionRead, status_code=201)
async def start_call(
    payload: schemas.CallSessionCreate, db: Session = Depends(get_session)
):
    if (
        payload.patient_id is not None
        and await db.get(models.Patient, payload.patient_id) is None
    ):
        raise HTTPException(404, detail="patient not found")
    call = models.CallSession(**payload.model_dump())
    db.add(call)
    await db.commit()
    await db.refresh(call)
    return call


@app.patch("/calls/{call_id}", response_model=schemas.CallSessionRead)
async def update_call(
    call_id: int, payload: schemas.CallSessionUpdate, db: Session = Depends(get_session)
):
    call = await db.get(models.CallSession, call_id)
    if call is None:
        raise HTTPException(404, detail="call session not found")
    data = payload.model_dump(exclude_unset=True)
    if "patient_id" in data and data["patient_id"] is not None:
        if await db.get(models.Patient, data["patient_id"]) is None:
            raise HTTPException(404, detail="patient not found")
    for field, value in data.items():
        setattr(call, field, value)
    await db.commit()
    await db.refresh(call)
    return call


@app.get("/calls", response_model=list[schemas.CallSessionRead])
async def list_calls(
    phone_number: str | None = Query(default=None),
    patient_id: int | None = Query(default=None),
    db: Session = Depends(get_session),
):
    query = select(models.CallSession).order_by(models.CallSession.started_at.desc())
    if phone_number is not None:
        query = query.where(
            models.CallSession.caller_phone_number.contains(phone_number)
        )
    if patient_id is not None:
        query = query.where(models.CallSession.patient_id == patient_id)
    return list(await db.scalars(query))


@app.post("/reminders", response_model=schemas.ReminderRead, status_code=201)
async def create_reminder(
    payload: schemas.ReminderCreate, db: Session = Depends(get_session)
):
    if await db.get(models.Appointment, payload.appointment_id) is None:
        raise HTTPException(404, detail="appointment not found")
    reminder = models.Reminder(**payload.model_dump())
    db.add(reminder)
    await db.commit()
    await db.refresh(reminder)
    return reminder


@app.get("/reminders", response_model=list[schemas.ReminderRead])
async def list_reminders(
    appointment_id: int | None = Query(default=None),
    status: schemas.ReminderStatus | None = Query(default=None),
    db: Session = Depends(get_session),
):
    query = select(models.Reminder).order_by(models.Reminder.scheduled_at)
    if appointment_id is not None:
        query = query.where(models.Reminder.appointment_id == appointment_id)
    if status is not None:
        query = query.where(models.Reminder.status == status)
    return list(await db.scalars(query))


@app.patch("/reminders/{reminder_id}", response_model=schemas.ReminderRead)
async def update_reminder(
    reminder_id: int,
    payload: schemas.ReminderUpdate,
    db: Session = Depends(get_session),
):
    reminder = await db.get(models.Reminder, reminder_id)
    if reminder is None:
        raise HTTPException(404, detail="reminder not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(reminder, field, value)
    await db.commit()
    await db.refresh(reminder)
    return reminder
