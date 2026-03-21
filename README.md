# 🚂 Ticket Booking Service — SE4010 Cloud Computing Assignment

> **Service 3 of 4** in the Train Booking System microservice group project.

## Group Architecture

| # | Service | Port | Your Role |
|---|---------|------|-----------|
| 1 | Train Management Service | :8001 | Auth tokens, train data |
| 2 | Seat Availability Service | :8002 | Seat reservation |
| **3** | **Ticket Booking Service** | **:8003** | **YOU** |
| 4 | Payment Service | :8004 | Payments & refunds |

---

## Quick Start (Local)

```bash
# 1. Backend
cd ticket-booking-service
python -m venv venv && source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8003

# Visit: http://localhost:8003/docs

# 2. Frontend (separate terminal)
cd ticket-booking-frontend
npm install
cp .env.example .env.local
npm run dev

# Visit: http://localhost:3000
```

## Run with Docker

```bash
cd ticket-booking-service
docker build -t ticket-booking-service .
docker run -p 8003:8000 ticket-booking-service

# Or with PostgreSQL:
docker-compose up
```

## Run Tests

```bash
cd ticket-booking-service
pip install pytest pytest-asyncio httpx
pytest tests/ -v
```

---

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | /health | No | Health check |
| POST | /tickets/book | ✅ JWT | Book a new ticket |
| GET | /tickets/{id} | No | Get ticket by ID |
| DELETE | /tickets/{id} | ✅ JWT | Cancel a ticket |
| GET | /tickets/user/{userId} | No | List user's tickets |
| GET | /bookings/{ref} | No | Get booking by reference |
| GET | /passengers/{id} | ✅ JWT | Get passenger info |

## Integration Points

1. **Train Management Service** — validates JWT tokens on write endpoints
2. **Seat Availability Service** — reserves seat on booking, releases on cancel
3. **Payment Service** — triggers refund on cancellation

---

## Deployment to AWS ECS

See the full step-by-step guide in `CTSE_Assignment_Guide.docx`.

```bash
# Build & push to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <ACCOUNT>.dkr.ecr.us-east-1.amazonaws.com
docker build -t ticket-booking-service .
docker tag ticket-booking-service:latest <ACCOUNT>.dkr.ecr.us-east-1.amazonaws.com/ticket-booking-service:latest
docker push <ACCOUNT>.dkr.ecr.us-east-1.amazonaws.com/ticket-booking-service:latest
```

## GitHub Secrets Required

| Secret | Value |
|--------|-------|
| AWS_ACCESS_KEY_ID | From IAM user |
| AWS_SECRET_ACCESS_KEY | From IAM user |
| SNYK_TOKEN | From snyk.io (free) |
| SONAR_TOKEN | From sonarcloud.io (free) |

## Security Features

- Non-root Docker user (principle of least privilege)
- JWT token validation via inter-service call
- Pydantic input validation on all endpoints
- SAST scanning via Snyk + SonarCloud in CI/CD
- AWS IAM roles with least-privilege permissions
- Security groups restricting port exposure
