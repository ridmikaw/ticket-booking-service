from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from enum import Enum
import httpx, os, logging, uuid, json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Ticket Booking Service",
    description="""
    Handles ticket reservation and passenger bookings for the Train Booking System.

    ## Integration Points
    - **Train Management Service** → Verifies schedule via `GET /api/schedules/{id}`
    - **Seat Availability Service** → Reserves seats via `PUT /api/seats/{scheduleId}/reserve`
    - **Kafka** → Publishes `booking.created` and `booking.cancelled` events
    """,
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

# ── Service URLs ──────────────────────────────────────────────────────────────
TRAIN_SERVICE_URL = os.getenv("TRAIN_MANAGEMENT_URL",   "http://localhost:3001")
SEAT_SERVICE_URL  = os.getenv("SEAT_AVAILABILITY_URL",  "http://localhost:3002")
KAFKA_BROKER      = os.getenv("KAFKA_BROKER",           "localhost:9092")
KAFKA_ENABLED     = os.getenv("KAFKA_ENABLED", "false").lower() == "true"

# ── Enums ─────────────────────────────────────────────────────────────────────
class SeatClass(str, Enum):
    FIRST  = "FIRST"
    SECOND = "SECOND"
    THIRD  = "THIRD"

class BookingStatus(str, Enum):
    PENDING   = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"

class PaymentStatus(str, Enum):
    UNPAID   = "UNPAID"
    PAID     = "PAID"
    REFUNDED = "REFUNDED"

# ── In-memory store ───────────────────────────────────────────────────────────
bookings_db: dict = {}

def generate_booking_ref() -> str:
    return f"BK-{str(uuid.uuid4())[:8].upper()}"

# ── Pydantic Models (matching Node.js schema exactly) ────────────────────────
class Passenger(BaseModel):
    name:       str  = Field(..., min_length=1)
    email:      str  = Field(..., description="Passenger email")
    phone:      str  = Field(..., min_length=1)
    nationalId: Optional[str] = None
    age:        Optional[int] = Field(None, ge=0)
    seatNumber: Optional[str] = None

class BookingCreate(BaseModel):
    scheduleId:   str        = Field(..., description="Schedule ID from Train Management Service")
    trainId:      str        = Field(..., description="Train ID from Train Management Service")
    seatClass:    SeatClass
    passengers:   List[Passenger] = Field(..., min_length=1)
    contactEmail: str
    journeyDate:  str        = Field(..., description="ISO date e.g. 2026-04-01")
    origin:       str
    destination:  str

class BookingResponse(BaseModel):
    id:               str
    bookingReference: str
    scheduleId:       str
    trainId:          str
    seatClass:        str
    passengers:       List[dict]
    totalAmount:      float
    status:           str
    paymentStatus:    str
    contactEmail:     str
    journeyDate:      str
    origin:           str
    destination:      str
    cancelledAt:      Optional[str] = None
    cancelReason:     Optional[str] = None
    createdAt:        str
    updatedAt:        str

class CancelRequest(BaseModel):
    reason: Optional[str] = "User requested cancellation"

# ── Kafka Publisher ───────────────────────────────────────────────────────────
async def publish_event(topic: str, message: dict):
    """Publish event to Kafka — same topics as Node.js service."""
    if not KAFKA_ENABLED:
        logger.info(f"[kafka] Disabled — skipping publish to {topic}: {message}")
        return
    try:
        from aiokafka import AIOKafkaProducer
        producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BROKER)
        await producer.start()
        await producer.send_and_wait(topic, json.dumps(message).encode())
        await producer.stop()
        logger.info(f"[kafka] Published to {topic}")
    except Exception as e:
        logger.error(f"[kafka] Failed to publish to {topic}: {e}")

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
        logger.warning("Train Service unreachable — standalone mode")
    return {"user_id": "standalone-user", "role": "user"}

# ══════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════

@app.get("/health", tags=["Health"])
def health():
    return {
        "status":    "healthy",
        "service":   "ticket-booking",
        "timestamp": datetime.utcnow().isoformat(),
        "uptime":    "running",
    }

# ── GET /api/bookings ─────────────────────────────────────────────────────────
@app.get("/api/bookings", tags=["Bookings"],
         summary="List bookings filtered by email or status")
def get_all_bookings(
    email:  Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    page:   int = Query(1, ge=1),
    limit:  int = Query(20, ge=1, le=100),
):
    """List bookings, optionally filtered by email or status. Paginated."""
    results = list(bookings_db.values())

    if email:
        results = [b for b in results if b["contactEmail"] == email.lower()]
    if status:
        results = [b for b in results if b["status"] == status.upper()]

    # Sort by createdAt descending
    results.sort(key=lambda x: x["createdAt"], reverse=True)

    total = len(results)
    start = (page - 1) * limit
    results = results[start: start + limit]

    return {"success": True, "total": total, "page": page, "data": results}

# ── GET /api/bookings/ref/:reference ─────────────────────────────────────────
@app.get("/api/bookings/ref/{reference}", tags=["Bookings"],
         summary="Get booking by booking reference number")
def get_booking_by_reference(reference: str):
    """Get booking by reference e.g. BK-A1B2C3D4"""
    booking = next(
        (b for b in bookings_db.values()
         if b["bookingReference"] == reference.upper()), None
    )
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    return {"success": True, "data": booking}

# ── GET /api/bookings/:id ─────────────────────────────────────────────────────
@app.get("/api/bookings/{booking_id}", tags=["Bookings"],
         summary="Get booking by ID")
def get_booking_by_id(booking_id: str):
    if booking_id not in bookings_db:
        raise HTTPException(status_code=404, detail="Booking not found")
    return {"success": True, "data": bookings_db[booking_id]}

# ── POST /api/bookings ────────────────────────────────────────────────────────
@app.post("/api/bookings", status_code=201, tags=["Bookings"],
          summary="Create a new ticket booking")
async def create_booking(req: BookingCreate):
    """
    Orchestrates the full booking flow:
    1. Validates the schedule via Train Management Service
    2. Reserves seats via Seat Availability Service
    3. Calculates fare and saves booking
    4. Publishes `booking.created` Kafka event
    """
    # Step 1: Verify schedule via Train Management Service
    schedule_info = None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{TRAIN_SERVICE_URL}/api/schedules/{req.scheduleId}")
            if r.status_code == 200:
                schedule_info = r.json().get("data")
            if schedule_info and schedule_info.get("status") == "CANCELLED":
                raise HTTPException(status_code=400, detail="This schedule has been cancelled")
    except HTTPException:
        raise
    except httpx.RequestError:
        logger.warning("Train Service unreachable — continuing in demo mode")

    # Step 2: Reserve seats via Seat Availability Service
    seat_count     = len(req.passengers)
    reserved_seats = []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.put(
                f"{SEAT_SERVICE_URL}/api/seats/{req.scheduleId}/reserve",
                json={
                    "bookingId":   "TEMP",
                    "seatClass":   req.seatClass,
                    "seatCount":   seat_count,
                    "passengerId": req.contactEmail,
                }
            )
            if r.status_code == 200:
                reserved_seats = r.json().get("data", {}).get("reservedSeats", [])
            elif r.status_code == 409:
                raise HTTPException(status_code=409,
                    detail=r.json().get("message", "No seats available"))
    except HTTPException:
        raise
    except httpx.RequestError:
        logger.warning("Seat Service unreachable — continuing in demo mode")

    # Step 3: Calculate fare
    total_amount = 0.0
    if schedule_info:
        classes    = schedule_info.get("trainId", {}).get("classes", [])
        class_info = next((c for c in classes if c["className"] == req.seatClass), None)
        price_per_km = class_info["pricePerKm"] if class_info else 2.5
        distance_km  = schedule_info.get("distanceKm", 0)
        total_amount = round(distance_km * price_per_km * seat_count, 2)
    else:
        # Fallback pricing when Train Service is down
        fallback = {"FIRST": 5.0, "SECOND": 2.5, "THIRD": 1.5}
        total_amount = round(100 * fallback.get(req.seatClass, 2.5) * seat_count, 2)

    # Step 4: Assign seat numbers to passengers
    passengers_with_seats = []
    for i, p in enumerate(req.passengers):
        passenger_dict = p.dict()
        if i < len(reserved_seats):
            passenger_dict["seatNumber"] = reserved_seats[i].get("seatNumber")
        passengers_with_seats.append(passenger_dict)

    # Step 5: Create booking record
    booking_id  = str(uuid.uuid4())
    booking_ref = generate_booking_ref()
    now         = datetime.utcnow().isoformat()

    booking = {
        "id":               booking_id,
        "bookingReference": booking_ref,
        "scheduleId":       req.scheduleId,
        "trainId":          req.trainId,
        "seatClass":        req.seatClass,
        "passengers":       passengers_with_seats,
        "totalAmount":      total_amount,
        "status":           BookingStatus.CONFIRMED,
        "paymentStatus":    PaymentStatus.UNPAID,
        "contactEmail":     req.contactEmail.lower(),
        "journeyDate":      req.journeyDate,
        "origin":           req.origin,
        "destination":      req.destination,
        "cancelledAt":      None,
        "cancelReason":     None,
        "createdAt":        now,
        "updatedAt":        now,
    }
    bookings_db[booking_id] = booking

    # Step 6: Update seat reservation with real booking ID
    if reserved_seats:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.put(
                    f"{SEAT_SERVICE_URL}/api/seats/{req.scheduleId}/reserve",
                    json={"bookingId": booking_id, "seatClass": req.seatClass, "seatCount": 0}
                )
        except Exception:
            pass  # non-critical

    # Step 7: Publish booking.created Kafka event
    await publish_event("booking.created", {
        "bookingId":        booking_id,
        "bookingReference": booking_ref,
        "contactEmail":     booking["contactEmail"],
        "passengers":       [{"name": p["name"], "email": p["email"],
                               "seatNumber": p.get("seatNumber")}
                              for p in passengers_with_seats],
        "origin":           req.origin,
        "destination":      req.destination,
        "journeyDate":      req.journeyDate,
        "seatClass":        req.seatClass,
        "totalAmount":      total_amount,
        "scheduleId":       req.scheduleId,
    })

    logger.info(f"Booking created: {booking_id} | Ref: {booking_ref}")
    return {"success": True, "data": booking}

# ── DELETE /api/bookings/:id ──────────────────────────────────────────────────
@app.delete("/api/bookings/{booking_id}", tags=["Bookings"],
            summary="Cancel a booking")
async def cancel_booking(booking_id: str, req: CancelRequest = CancelRequest()):
    """
    Cancels the booking, releases seats via Seat Availability Service,
    and publishes `booking.cancelled` Kafka event.
    """
    if booking_id not in bookings_db:
        raise HTTPException(status_code=404, detail="Booking not found")

    booking = bookings_db[booking_id]

    if booking["status"] in ["CANCELLED", "COMPLETED"]:
        raise HTTPException(
            status_code=400,
            detail=f"Booking is already {booking['status'].lower()}"
        )

    # Step 1: Release seats
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.put(
                f"{SEAT_SERVICE_URL}/api/seats/{booking['scheduleId']}/release",
                json={
                    "bookingId": booking_id,
                    "seatClass": booking["seatClass"],
                    "seatCount": len(booking["passengers"]),
                }
            )
    except httpx.RequestError:
        logger.warning("Seat Service unreachable — seat release skipped (non-fatal)")

    # Step 2: Update booking
    now = datetime.utcnow().isoformat()
    bookings_db[booking_id]["status"]       = BookingStatus.CANCELLED
    bookings_db[booking_id]["cancelledAt"]  = now
    bookings_db[booking_id]["cancelReason"] = req.reason
    bookings_db[booking_id]["updatedAt"]    = now

    # Step 3: Publish booking.cancelled Kafka event
    await publish_event("booking.cancelled", {
        "bookingId":        booking_id,
        "bookingReference": booking["bookingReference"],
        "scheduleId":       booking["scheduleId"],
        "seatClass":        booking["seatClass"],
        "seatCount":        len(booking["passengers"]),
        "contactEmail":     booking["contactEmail"],
        "totalAmount":      booking["totalAmount"],
    })

    logger.info(f"Booking cancelled: {booking_id}")
    return {"success": True, "message": "Booking cancelled",
            "data": bookings_db[booking_id]}