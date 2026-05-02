from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

# ── Health ─────────────────────────────────────────────────────────────────────
def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"
    assert r.json()["service"] == "ticket-booking"

# ── Booking payload ───────────────────────────────────────────────────────────
BOOK_PAYLOAD = {
    "scheduleId":   "65f1a2b3c4d5e6f7a8b9c0d1",
    "trainId":      "69f47db02ccda6e75e7ead60",
    "seatClass":    "SECOND",
    "contactEmail": "ridmi@gmail.com",
    "journeyDate":  "2026-04-01",
    "origin":       "Colombo Fort",
    "destination":  "Kandy",
    "passengers": [
        {
            "name":       "Ridmi Ranasinghe",
            "email":      "ridmi@gmail.com",
            "phone":      "+94770791180",
            "nationalId": "200257900923",
            "age":        23
        }
    ]
}

# ── Create Booking ─────────────────────────────────────────────────────────────
def test_create_booking():
    r = client.post("/api/bookings", json=BOOK_PAYLOAD)
    assert r.status_code == 201
    data = r.json()

    assert data["success"] is True
    assert data["data"]["bookingReference"].startswith("BK-")
    assert data["data"]["status"] == "CONFIRMED"
    assert data["data"]["seatClass"] == "SECOND"
    assert data["data"]["origin"] == "Colombo Fort"
    assert data["data"]["destination"] == "Kandy"
    assert len(data["data"]["passengers"]) == 1

    # Instead of return → assert
    assert data["data"]["id"] is not None

def test_create_booking_invalid_seat_class():
    bad = {**BOOK_PAYLOAD, "seatClass": "BUSINESS"}
    r = client.post("/api/bookings", json=bad)
    assert r.status_code == 422

def test_create_booking_missing_passengers():
    bad = {**BOOK_PAYLOAD, "passengers": []}
    r = client.post("/api/bookings", json=bad)
    assert r.status_code == 422

def test_create_booking_multiple_passengers():
    payload = {
        **BOOK_PAYLOAD,
        "passengers": [
            {"name": "Passenger One", "email": "one@test.com", "phone": "+94771111111"},
            {"name": "Passenger Two", "email": "two@test.com", "phone": "+94772222222"},
        ]
    }
    r = client.post("/api/bookings", json=payload)
    assert r.status_code == 201
    assert len(r.json()["data"]["passengers"]) == 2

# ── Get All Bookings ───────────────────────────────────────────────────────────
def test_get_all_bookings():
    client.post("/api/bookings", json=BOOK_PAYLOAD)
    r = client.get("/api/bookings")
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert isinstance(data["data"], list)
    assert "total" in data
    assert "page" in data

def test_get_bookings_filter_by_email():
    client.post("/api/bookings", json=BOOK_PAYLOAD)
    r = client.get("/api/bookings?email=ridmi@gmail.com")
    assert r.status_code == 200
    for b in r.json()["data"]:
        assert b["contactEmail"] == "ridmi@gmail.com"

def test_get_bookings_filter_by_status():
    client.post("/api/bookings", json=BOOK_PAYLOAD)
    r = client.get("/api/bookings?status=CONFIRMED")
    assert r.status_code == 200
    for b in r.json()["data"]:
        assert b["status"] == "CONFIRMED"

def test_get_bookings_pagination():
    r = client.get("/api/bookings?page=1&limit=5")
    assert r.status_code == 200
    assert len(r.json()["data"]) <= 5

# ── Get Booking by ID ──────────────────────────────────────────────────────────
def test_get_booking_by_id():
    created = client.post("/api/bookings", json=BOOK_PAYLOAD)
    booking_id = created.json()["data"]["id"]

    r = client.get(f"/api/bookings/{booking_id}")
    assert r.status_code == 200
    assert r.json()["data"]["id"] == booking_id

def test_get_booking_by_id_not_found():
    r = client.get("/api/bookings/nonexistent-id-12345")
    assert r.status_code == 404

# ── Get Booking by Reference ───────────────────────────────────────────────────
def test_get_booking_by_reference():
    created = client.post("/api/bookings", json=BOOK_PAYLOAD)
    ref = created.json()["data"]["bookingReference"]

    r = client.get(f"/api/bookings/ref/{ref}")
    assert r.status_code == 200
    assert r.json()["data"]["bookingReference"] == ref

def test_get_booking_by_reference_not_found():
    r = client.get("/api/bookings/ref/BK-INVALID")
    assert r.status_code == 404

def test_get_booking_ref_case_insensitive():
    created = client.post("/api/bookings", json=BOOK_PAYLOAD)
    ref = created.json()["data"]["bookingReference"].lower()

    r = client.get(f"/api/bookings/ref/{ref}")
    assert r.status_code == 200

# ── Cancel Booking ─────────────────────────────────────────────────────────────
def test_cancel_booking():
    created = client.post("/api/bookings", json=BOOK_PAYLOAD)
    booking_id = created.json()["data"]["id"]

    # ✅ FIXED HERE
    r = client.request(
        "DELETE",
        f"/api/bookings/{booking_id}",
        json={"reason": "Change of plans"}
    )

    assert r.status_code == 200
    assert r.json()["success"] is True
    assert r.json()["data"]["status"] == "CANCELLED"
    assert r.json()["data"]["cancelReason"] == "Change of plans"

def test_cancel_already_cancelled():
    created = client.post("/api/bookings", json=BOOK_PAYLOAD)
    booking_id = created.json()["data"]["id"]

    client.request("DELETE", f"/api/bookings/{booking_id}")
    r = client.request("DELETE", f"/api/bookings/{booking_id}")

    assert r.status_code == 400
    assert "already" in r.json()["detail"].lower()

def test_cancel_not_found():
    r = client.request("DELETE", "/api/bookings/nonexistent-id")
    assert r.status_code == 404

def test_cancel_default_reason():
    created = client.post("/api/bookings", json=BOOK_PAYLOAD)
    booking_id = created.json()["data"]["id"]

    r = client.request("DELETE", f"/api/bookings/{booking_id}")

    assert r.status_code == 200
    assert r.json()["data"]["cancelReason"] is not None