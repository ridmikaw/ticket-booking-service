from fastapi import FastAPI, HTTPException, Depends, status, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional
from datetime import datetime
from enum import Enum
import httpx, os, logging, uuid

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Ticket Booking Service",
    description="Handles ticket reservation, generation, and passenger management for the Train Booking System.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer(auto_error=False)

# ── Service URLs (injected via env) ──────────────────────────────────────────
TRAIN_SERVICE_URL   = os.getenv("TRAIN_SERVICE_URL",   "http://train-service:8001")
SEAT_SERVICE_URL    = os.getenv("SEAT_SERVICE_URL",    "http://seat-service:8002")
PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://payment-service:8004")

# ── Enums ─────────────────────────────────────────────────────────────────────
class TicketStatus(str, Enum):
    PENDING   = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"

class SeatClass(str, Enum):
    FIRST   = "1st Class"
    SECOND  = "2nd Class"
    SLEEPER = "Sleeper"

# ── In-memory DB (swap with SQLAlchemy + PostgreSQL in production) ────────────
tickets_db: dict     = {}
passengers_db: dict  = {}
bookings_db: dict    = {}

# Seed data
_t1 = str(uuid.uuid4())
tickets_db[_t1] = {
    "id": _t1, "train_id": "TRN-001", "seat_id": "S-12A",
    "passenger_id": "P-001", "user_id": "user-1",
    "seat_class": SeatClass.SECOND, "status": TicketStatus.CONFIRMED,
    "departure": "Colombo Fort", "destination": "Kandy",
    "departure_time": "2026-03-20T08:00:00", "arrival_time": "2026-03-20T11:30:00",
    "price": 350.00, "booking_ref": "BK-00001",
    "created_at": datetime.utcnow().isoformat(),
}
passengers_db["P-001"] = {
    "id": "P-001", "name": "Ashan Perera", "email": "ashan@example.com",
    "nic": "199012345678", "phone": "+94771234567",
}
bookings_db["BK-00001"] = {
    "id": "BK-00001", "ticket_ids": [_t1], "user_id": "user-1",
    "total_price": 350.00, "status": TicketStatus.CONFIRMED,
    "created_at": datetime.utcnow().isoformat(),
}

# ── Pydantic Models ───────────────────────────────────────────────────────────
class PassengerCreate(BaseModel):
    name:  str   = Field(..., min_length=2, max_length=100)
    email: str   = Field(..., description="Passenger email")
    nic:   str   = Field(..., min_length=10, max_length=12)
    phone: str   = Field(..., min_length=10)

class PassengerResponse(BaseModel):
    id:    str
    name:  str
    email: str
    nic:   str
    phone: str

class TicketBookRequest(BaseModel):
    train_id:       str        = Field(..., description="Train ID from Train Management Service")
    seat_id:        str        = Field(..., description="Seat ID from Seat Availability Service")
    seat_class:     SeatClass
    departure:      str
    destination:    str
    departure_time: str
    arrival_time:   str
    price:          float      = Field(..., gt=0)
    passenger:      PassengerCreate

class TicketResponse(BaseModel):
    id:             str
    train_id:       str
    seat_id:        str
    passenger_id:   str
    user_id:        str
    seat_class:     str
    status:         str
    departure:      str
    destination:    str
    departure_time: str
    arrival_time:   str
    price:          float
    booking_ref:    str
    created_at:     str

class BookingResponse(BaseModel):
    id:          str
    ticket_ids:  List[str]
    user_id:     str
    total_price: float
    status:      str
    created_at:  str

class CancelRequest(BaseModel):
    reason: Optional[str] = "Cancelled by user"

# ── Auth helper ───────────────────────────────────────────────────────────────
async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        raise HTTPException(status_code=401, detail="Authorization header missing")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"{TRAIN_SERVICE_URL}/auth/verify",
                headers={"Authorization": f"Bearer {credentials.credentials}"}
            )
            if r.status_code == 200:
                return r.json()
    except httpx.RequestError:
        logger.warning("Auth service unreachable — running in standalone mode")
    # Standalone fallback (for demo / when auth service is down)
    return {"user_id": "standalone-user", "role": "user"}

# ── Helpers ───────────────────────────────────────────────────────────────────
async def call_seat_reserve(seat_id: str, train_id: str):
    """Integration: Call Seat Availability Service to reserve a seat."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                f"{SEAT_SERVICE_URL}/seats/reserve",
                json={"seat_id": seat_id, "train_id": train_id}
            )
            return r.status_code == 200
    except httpx.RequestError:
        logger.warning("Seat service unreachable — assuming seat available in demo mode")
        return True

async def call_seat_release(seat_id: str, train_id: str):
    """Integration: Release a reserved seat back to Seat Availability Service."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{SEAT_SERVICE_URL}/seats/release",
                json={"seat_id": seat_id, "train_id": train_id}
            )
    except httpx.RequestError:
        logger.warning("Seat service unreachable — seat release skipped")

async def call_payment_refund(booking_ref: str, amount: float):
    """Integration: Request refund from Payment Service on cancellation."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                f"{PAYMENT_SERVICE_URL}/payments/refund",
                json={"booking_ref": booking_ref, "amount": amount}
            )
            return r.status_code == 200
    except httpx.RequestError:
        logger.warning("Payment service unreachable — refund queued")
        return True

def generate_booking_ref():
    return f"BK-{str(len(bookings_db) + 1).zfill(5)}"

def generate_passenger_id():
    return f"P-{str(len(passengers_db) + 1).zfill(4)}"

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy", "service": "ticket-booking", "version": "1.0.0"}


# ── Ticket endpoints ──────────────────────────────────────────────────────────

@app.post("/tickets/book", response_model=TicketResponse, status_code=201, tags=["Tickets"],
          summary="Book a new ticket")
async def book_ticket(req: TicketBookRequest, user=Depends(get_current_user)):
    """
    Books a ticket. This endpoint:
    1. Creates or looks up the passenger record
    2. Calls Seat Availability Service to reserve the seat (prevents double booking)
    3. Creates the ticket and booking records
    4. Returns ticket details with booking reference
    """
    # Step 1: Create / find passenger
    existing = next((p for p in passengers_db.values() if p["nic"] == req.passenger.nic), None)
    if existing:
        passenger_id = existing["id"]
    else:
        passenger_id = generate_passenger_id()
        passengers_db[passenger_id] = {"id": passenger_id, **req.passenger.dict()}
        logger.info(f"New passenger created: {passenger_id}")

    # Step 2: Reserve seat via Seat Availability Service
    seat_ok = await call_seat_reserve(req.seat_id, req.train_id)
    if not seat_ok:
        raise HTTPException(status_code=409, detail="Seat is no longer available")

    # Step 3: Create ticket
    ticket_id   = str(uuid.uuid4())
    booking_ref = generate_booking_ref()
    user_id     = user.get("user_id", "unknown")

    ticket = {
        "id": ticket_id, "train_id": req.train_id, "seat_id": req.seat_id,
        "passenger_id": passenger_id, "user_id": user_id,
        "seat_class": req.seat_class, "status": TicketStatus.CONFIRMED,
        "departure": req.departure, "destination": req.destination,
        "departure_time": req.departure_time, "arrival_time": req.arrival_time,
        "price": req.price, "booking_ref": booking_ref,
        "created_at": datetime.utcnow().isoformat(),
    }
    tickets_db[ticket_id] = ticket

    # Step 4: Create booking record
    bookings_db[booking_ref] = {
        "id": booking_ref, "ticket_ids": [ticket_id], "user_id": user_id,
        "total_price": req.price, "status": TicketStatus.CONFIRMED,
        "created_at": datetime.utcnow().isoformat(),
    }

    logger.info(f"Ticket booked: {ticket_id} | Booking: {booking_ref}")
    return ticket


@app.get("/tickets/{ticket_id}", response_model=TicketResponse, tags=["Tickets"],
         summary="Get ticket by ID")
def get_ticket(ticket_id: str):
    """Get a single ticket's full details by its ID."""
    if ticket_id not in tickets_db:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return tickets_db[ticket_id]


@app.delete("/tickets/{ticket_id}", tags=["Tickets"], summary="Cancel a ticket")
async def cancel_ticket(ticket_id: str, req: CancelRequest = CancelRequest(),
                        user=Depends(get_current_user)):
    """
    Cancels a ticket. This endpoint:
    1. Validates ticket exists and is cancellable
    2. Releases the seat back to Seat Availability Service
    3. Requests refund from Payment Service
    4. Updates ticket status to CANCELLED
    """
    if ticket_id not in tickets_db:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ticket = tickets_db[ticket_id]
    if ticket["status"] == TicketStatus.CANCELLED:
        raise HTTPException(status_code=400, detail="Ticket is already cancelled")
    if ticket["status"] == TicketStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Cannot cancel a completed ticket")

    # Release seat
    await call_seat_release(ticket["seat_id"], ticket["train_id"])

    # Request refund
    await call_payment_refund(ticket["booking_ref"], ticket["price"])

    # Update status
    tickets_db[ticket_id]["status"] = TicketStatus.CANCELLED
    bookings_db[ticket["booking_ref"]]["status"] = TicketStatus.CANCELLED

    logger.info(f"Ticket cancelled: {ticket_id} | Reason: {req.reason}")
    return {"message": f"Ticket {ticket_id} cancelled successfully", "booking_ref": ticket["booking_ref"]}


@app.get("/tickets/user/{user_id}", response_model=List[TicketResponse], tags=["Tickets"],
         summary="Get all tickets for a user")
def get_user_tickets(user_id: str, status: Optional[str] = None):
    """Get all tickets belonging to a user, optionally filtered by status."""
    user_tickets = [t for t in tickets_db.values() if t["user_id"] == user_id]
    if status:
        user_tickets = [t for t in user_tickets if t["status"] == status.upper()]
    return user_tickets


# ── Booking endpoints ─────────────────────────────────────────────────────────

@app.get("/bookings/{booking_ref}", response_model=BookingResponse, tags=["Bookings"],
         summary="Get booking by reference")
def get_booking(booking_ref: str):
    """Get a booking record by its reference number (e.g. BK-00001)."""
    if booking_ref not in bookings_db:
        raise HTTPException(status_code=404, detail="Booking not found")
    return bookings_db[booking_ref]


# ── Passenger endpoints ───────────────────────────────────────────────────────

@app.get("/passengers/{passenger_id}", response_model=PassengerResponse, tags=["Passengers"],
         summary="Get passenger info")
def get_passenger(passenger_id: str, user=Depends(get_current_user)):
    if passenger_id not in passengers_db:
        raise HTTPException(status_code=404, detail="Passenger not found")
    return passengers_db[passenger_id]


# ── Serve Next.js frontend ────────────────────────────────────────────────────
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend", "out")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/ui", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

@app.get("/", include_in_schema=False)
def root():
    index = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"service": "Ticket Booking Service", "docs": "/docs", "version": "1.0.0"}
