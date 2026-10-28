import logging
from datetime import datetime

import httpx
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:8000"

UNREACHABLE = "Sorry, I could not reach the appointment system right now."


class NoArgs(BaseModel):
    pass


class FindDoctorsArgs(BaseModel):
    department_name: str = Field(description="Department name, e.g. Cardiology")


class CheckAvailabilityArgs(BaseModel):
    doctor_name: str = Field(description="Doctor name, e.g. Dr. Aditi Verma")
    day: str = Field(description="Date to check, in YYYY-MM-DD format")


class FindPatientArgs(BaseModel):
    phone_number: str = Field(description="Patient phone number")


class RegisterPatientArgs(BaseModel):
    full_name: str = Field(description="Patient full name")
    phone_number: str = Field(description="Patient phone number")


class BookAppointmentArgs(BaseModel):
    patient_phone: str = Field(
        description="Phone number the patient is registered with"
    )
    doctor_name: str = Field(description="Doctor name, e.g. Dr. Aditi Verma")
    day: str = Field(description="Appointment date, in YYYY-MM-DD format")
    time: str = Field(description="Appointment start time, in HH:MM 24 hour format")
    reason: str = Field(default="", description="Reason for the visit")
    idempotency_key: str | None = Field(
        default=None,
        description="Unique key for this booking attempt so a retry never double-books",
    )


class ListAppointmentsArgs(BaseModel):
    patient_phone: str = Field(
        description="Phone number the patient is registered with"
    )


class CancelAppointmentArgs(BaseModel):
    appointment_id: int = Field(description="Id of the appointment to cancel")


class DBTools:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=10.0, transport=transport
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> tuple[int, object]:
        try:
            response = await self._client.request(method, path, **kwargs)
            if response.status_code == 204:
                return response.status_code, None
            return response.status_code, response.json()
        except httpx.HTTPError as exc:
            logger.warning("db api %s %s failed: %s", method, path, exc)
            return 0, None

    async def _department_id(self, name: str) -> tuple[int | None, list[dict]]:
        status, data = await self._request("GET", "/departments")
        if status != 200 or not isinstance(data, list):
            return None, []
        wanted = name.strip().lower()
        for department in data:
            if wanted in department["name"].lower():
                return department["department_id"], data
        return None, data

    async def _doctor_by_name(self, name: str) -> tuple[dict | None, list[dict]]:
        status, data = await self._request("GET", "/doctors")
        if status != 200 or not isinstance(data, list):
            return None, []
        wanted = name.strip().lower()
        matches = [d for d in data if wanted in d["name"].lower()]
        if len(matches) == 1:
            return matches[0], data
        return None, matches

    async def find_departments(self) -> str:
        status, data = await self._request("GET", "/departments")
        if status != 200 or not isinstance(data, list):
            return UNREACHABLE
        if not data:
            return "There are no departments listed yet."
        lines = [f"{d['name']}: {d['description']}" for d in data]
        return "Our departments are: " + "; ".join(lines)

    async def find_doctors(self, department_name: str) -> str:
        department_id, departments = await self._department_id(department_name)
        if department_id is None:
            names = ", ".join(d["name"] for d in departments) or "none right now"
            return (
                f"I could not find a department called {department_name}. "
                f"We have: {names}."
            )
        status, doctors = await self._request(
            "GET", "/doctors", params={"department_id": department_id}
        )
        if status != 200 or not isinstance(doctors, list):
            return UNREACHABLE
        if not doctors:
            return f"We have no doctors in {department_name} right now."
        lines = [
            f"{d['name']} (consultation fee {d['consultation_fee']}, {d['availability']})"
            for d in doctors
        ]
        return f"Doctors in {department_name}: " + "; ".join(lines)

    async def check_availability(self, doctor_name: str, day: str) -> str:
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            return "I need the date in year-month-day format to check availability."
        doctor, _ = await self._doctor_by_name(doctor_name)
        if doctor is None:
            return f"I could not find a doctor named {doctor_name}."
        status, data = await self._request(
            "GET", f"/doctors/{doctor['doctor_id']}/availability", params={"day": day}
        )
        if status != 200 or not isinstance(data, dict):
            return UNREACHABLE
        slots = data.get("available_times", [])
        if not slots:
            return f"{doctor['name']} has no free slots on {day}."
        return f"{doctor['name']} is free on {day} at: {', '.join(slots)}."

    async def find_patient(self, phone_number: str) -> str:
        status, data = await self._request(
            "GET", "/patients/search/by-phone", params={"phone_number": phone_number}
        )
        if status == 404:
            return f"I do not have a patient registered with phone {phone_number}."
        if status != 200 or not isinstance(data, dict):
            return UNREACHABLE
        return f"Found patient: {data['full_name']} (patient id {data['patient_id']})."

    async def register_patient(self, full_name: str, phone_number: str) -> str:
        status, data = await self._request(
            "POST",
            "/patients",
            json={"full_name": full_name, "phone_number": phone_number},
        )
        if status not in (200, 201) or not isinstance(data, dict):
            return UNREACHABLE
        return (
            f"Patient on file: {data['full_name']} (patient id {data['patient_id']})."
        )

    async def book_appointment(
        self,
        patient_phone: str,
        doctor_name: str,
        day: str,
        time: str,
        reason: str = "",
        idempotency_key: str | None = None,
    ) -> str:
        try:
            when = datetime.fromisoformat(f"{day}T{time}")
        except ValueError:
            return "I need the date as YYYY-MM-DD and the time as HH:MM to book."
        status, patient = await self._request(
            "GET", "/patients/search/by-phone", params={"phone_number": patient_phone}
        )
        if status == 404:
            return (
                f"I do not have a patient with phone {patient_phone} on file. "
                "Let me register them first."
            )
        if status != 200 or not isinstance(patient, dict):
            return UNREACHABLE
        doctor, _ = await self._doctor_by_name(doctor_name)
        if doctor is None:
            return f"I could not find a doctor named {doctor_name}."
        payload: dict = {
            "patient_id": patient["patient_id"],
            "doctor_id": doctor["doctor_id"],
            "appointment_at": when.isoformat(),
        }
        if reason:
            payload["reason"] = reason
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        status, data = await self._request("POST", "/appointments", json=payload)
        if status == 201 and isinstance(data, dict):
            return (
                f"Booked {doctor['name']} on {day} at {time} for "
                f"{patient['full_name']} (appointment id {data['appointment_id']})."
            )
        if status in (404, 409) and isinstance(data, dict):
            return f"Booking failed: {data.get('detail', 'unknown error')}."
        return UNREACHABLE

    async def list_appointments(self, patient_phone: str) -> str:
        status, patient = await self._request(
            "GET", "/patients/search/by-phone", params={"phone_number": patient_phone}
        )
        if status == 404:
            return f"I do not have a patient registered with phone {patient_phone}."
        if status != 200 or not isinstance(patient, dict):
            return UNREACHABLE
        status, appointments = await self._request(
            "GET", "/appointments", params={"patient_id": patient["patient_id"]}
        )
        if status != 200 or not isinstance(appointments, list):
            return UNREACHABLE
        if not appointments:
            return f"{patient['full_name']} has no appointments."
        status, doctors = await self._request("GET", "/doctors")
        doctor_names = {
            d["doctor_id"]: d["name"] for d in doctors if isinstance(doctors, list)
        }
        lines = [
            f"appointment {a['appointment_id']}: {a['appointment_at'][:16]} with "
            f"{doctor_names.get(a['doctor_id'], 'unknown doctor')}, {a['status']}"
            for a in appointments
        ]
        return f"Appointments for {patient['full_name']}: " + "; ".join(lines)

    async def cancel_appointment(self, appointment_id: int) -> str:
        status, data = await self._request(
            "PATCH", f"/appointments/{appointment_id}", json={"status": "cancelled"}
        )
        if status == 404:
            return f"I could not find appointment {appointment_id}."
        if status != 200:
            return UNREACHABLE
        return f"Appointment {appointment_id} is cancelled."

    def as_langchain_tools(self) -> list[StructuredTool]:
        return [
            StructuredTool.from_function(
                coroutine=self.find_departments,
                name="find_departments",
                description="List the hospital departments with a short description.",
                args_schema=NoArgs,
            ),
            StructuredTool.from_function(
                coroutine=self.find_doctors,
                name="find_doctors",
                description="List the doctors working in a department, by department name.",
                args_schema=FindDoctorsArgs,
            ),
            StructuredTool.from_function(
                coroutine=self.check_availability,
                name="check_availability",
                description="Check which time slots a doctor has free on a given day.",
                args_schema=CheckAvailabilityArgs,
            ),
            StructuredTool.from_function(
                coroutine=self.find_patient,
                name="find_patient",
                description="Look up whether a phone number is already registered as a patient.",
                args_schema=FindPatientArgs,
            ),
            StructuredTool.from_function(
                coroutine=self.register_patient,
                name="register_patient",
                description="Register a new patient with their full name and phone number.",
                args_schema=RegisterPatientArgs,
            ),
            StructuredTool.from_function(
                coroutine=self.book_appointment,
                name="book_appointment",
                description="Book an appointment for a patient with a doctor at a given date and time.",
                args_schema=BookAppointmentArgs,
            ),
            StructuredTool.from_function(
                coroutine=self.list_appointments,
                name="list_appointments",
                description="List a patient's appointments by their phone number.",
                args_schema=ListAppointmentsArgs,
            ),
            StructuredTool.from_function(
                coroutine=self.cancel_appointment,
                name="cancel_appointment",
                description="Cancel an appointment by its appointment id.",
                args_schema=CancelAppointmentArgs,
            ),
        ]
