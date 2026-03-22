from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

# Auth header for protected routes
AUTH = {"Authorization": "Bearer test-token"}

# ── Health ─────────────────────────────────────────────────────────────────────
def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"
    assert r.json()["service"] == "ticket-booking"

# ── Book ticket ────────────────────────────────────────────────────────────────
BOOK_PAYLOAD = {
    "train_id":       "TRN-002",
    "seat_id":        "S-05B",
    "seat_class":     "2nd Class",
    "departure":      "Colombo Fort",
    "destination":    "Galle",
    "departure_time": "2026-04-01T09:00:00",
    "arrival_time":   "2026-04-01T12:00:00",
    "price":          280.00,
    "passenger": {
        "name":  "Nimal Silva",
        "email": "nimal@example.com",
        "nic":   "200112345678",
        "phone": "+94771111111"
    }
}

def test_book_ticket():
    r = client.post("/tickets/book", json=BOOK_PAYLOAD, headers=AUTH)
    assert r.status_code == 201
    data = r.json()
    assert data["train_id"] == "TRN-002"
    assert data["status"] == "CONFIRMED"
    assert data["booking_ref"].startswith("BK-")

def test_book_same_passenger_twice():
    r1 = client.post("/tickets/book", json=BOOK_PAYLOAD, headers=AUTH)
    r2 = client.post("/tickets/book", json={**BOOK_PAYLOAD, "seat_id": "S-06C"}, headers=AUTH)
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["passenger_id"] == r2.json()["passenger_id"]

def test_get_existing_ticket():
    from app.main import tickets_db
    ticket_id = list(tickets_db.keys())[0]
    r = client.get(f"/tickets/{ticket_id}")
    assert r.status_code == 200
    assert r.json()["id"] == ticket_id

def test_get_nonexistent_ticket():
    r = client.get("/tickets/does-not-exist")
    assert r.status_code == 404

def test_cancel_ticket():
    book = client.post("/tickets/book", json=BOOK_PAYLOAD, headers=AUTH)
    assert book.status_code == 201
    ticket_id = book.json()["id"]
    headers = AUTH.copy() if 'AUTH' in locals() or 'AUTH' in globals() else {}
    cancel = client.request(
        "DELETE",
        f"/tickets/{ticket_id}",
        json={"reason": "Test"},
        headers=headers
    )
    assert cancel.status_code == 200
    assert "cancelled" in cancel.json()["message"].lower()

def test_cancel_already_cancelled():
    book = client.post("/tickets/book", json=BOOK_PAYLOAD, headers=AUTH)
    assert book.status_code == 201
    ticket_id = book.json()["id"]
    client.delete(f"/tickets/{ticket_id}", headers=AUTH)
    r = client.delete(f"/tickets/{ticket_id}", headers=AUTH)
    assert r.status_code == 400

def test_get_user_tickets():
    r = client.get("/tickets/user/user-1")
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_get_user_tickets_filtered_by_status():
    r = client.get("/tickets/user/user-1?status=CONFIRMED")
    assert r.status_code == 200
    for t in r.json():
        assert t["status"] == "CONFIRMED"

def test_get_booking():
    r = client.get("/bookings/BK-00001")
    assert r.status_code == 200
    assert r.json()["id"] == "BK-00001"

def test_get_booking_not_found():
    r = client.get("/bookings/BK-99999")
    assert r.status_code == 404

def test_get_passenger():
    r = client.get("/passengers/P-001", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["id"] == "P-001"