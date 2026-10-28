import asyncio
from datetime import time
from decimal import Decimal

from sqlalchemy import delete

from database import models
from database.db import SessionLocal, init_db

DEPARTMENTS = [
    ("General Dentistry", "Checkups, cleanings and fillings."),
    ("Orthodontics", "Braces, aligners and teeth straightening."),
    ("Endodontics", "Root canal treatments."),
    ("Pediatric Dentistry", "Dental care for children."),
]

DOCTORS = [
    ("Dr. Sarah Mitchell", "General Dentistry", "800.00"),
    ("Dr. James Carter", "General Dentistry", "900.00"),
    ("Dr. Emily Chen", "Orthodontics", "1500.00"),
    ("Dr. Raj Patel", "Endodontics", "1200.00"),
]

WEEKDAY_START = time(9, 0)
WEEKDAY_END = time(17, 0)
SLOT_MINUTES = 30


async def seed() -> None:
    await init_db()
    async with SessionLocal() as db:
        await db.execute(delete(models.Reminder))
        await db.execute(delete(models.CallSession))
        await db.execute(delete(models.Appointment))
        await db.execute(delete(models.DoctorSchedule))
        await db.execute(delete(models.Doctor))
        await db.execute(delete(models.Patient))
        await db.execute(delete(models.Department))

        departments = {
            name: models.Department(name=name, description=description)
            for name, description in DEPARTMENTS
        }
        db.add_all(departments.values())

        doctors = []
        for name, department_name, fee in DOCTORS:
            doctors.append(
                models.Doctor(
                    name=name,
                    department=departments[department_name],
                    consultation_fee=Decimal(fee),
                )
            )
        db.add_all(doctors)
        await db.flush()

        for doctor in doctors:
            for day in range(6):
                db.add(
                    models.DoctorSchedule(
                        doctor_id=doctor.doctor_id,
                        day_of_week=day,
                        start_time=WEEKDAY_START,
                        end_time=WEEKDAY_END,
                        slot_minutes=SLOT_MINUTES,
                    )
                )

        db.add_all(
            [
                models.Patient(full_name="Rekha Iyer", phone_number="+91-9810012345"),
                models.Patient(full_name="Arjun Nair", phone_number="+91-9810067890"),
            ]
        )
        await db.commit()

        print(
            f"seeded {len(departments)} departments, {len(doctors)} doctors "
            f"with Mon-Sat 09:00-17:00 schedules (30 min slots)"
        )


if __name__ == "__main__":
    asyncio.run(seed())
