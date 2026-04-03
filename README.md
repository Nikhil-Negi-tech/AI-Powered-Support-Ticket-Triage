# AI-Powered Support Ticket Triage

An intelligent support ticket management system that uses AI to automatically analyze and categorize support tickets, making ticket triage faster and more efficient.

## 📋 Project Structure

```
AI-Powered-Support-Ticket-Triage/
├── backend/                    # Node.js Express API
│   ├── server.js              # Main server file
│   ├── config.js              # Configuration
│   ├── package.json           # Dependencies
│   ├── Dockerfile             # Docker configuration
│   ├── routes/                # API routes
│   │   └── tickets.js         # Ticket endpoints
│   ├── services/              # Business logic
│   │   ├── analyzer.js        # AI analysis service
│   │   └── ticket.model.js    # Ticket data model
│   └── tests/                 # Unit tests
│       └── analyzer.test.js   # Analyzer tests
├── frontend/                   # React frontend
│   ├── Dockerfile             # Docker configuration
│   └── public/
│       └── index.html         # Main HTML
├── docker-compose.yml         # Docker Compose configuration
└── README.md                  # This file
```

## 🚀 Quick Start

### Option 1: Docker Compose (Recommended)

```bash
cd "AI-Powered-Support-Ticket-Triage"
docker-compose up --build
```

- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:5000
- **MongoDB**: mongodb://localhost:27017

### Option 2: Local Development

#### Backend
```bash
cd backend
npm install
npm start
```
Runs on `http://localhost:5000`

#### Frontend
```bash
cd frontend
npm install
npm start
```
Runs on `http://localhost:3000`

**Requirements:**
- Node.js (v18+)
- MongoDB running locally

## 🛠️ Technologies

- **Frontend**: React
- **Backend**: Node.js, Express.js
- **Database**: MongoDB
- **Containerization**: Docker, Docker Compose

## 📦 Backend Dependencies

- `express` - Web framework
- `mongoose` - MongoDB ODM
- `cors` - Cross-Origin Resource Sharing

## 🔧 Configuration

Backend environment variables (configured in `docker-compose.yml`):
- `MONGO_URI` - MongoDB connection string

## 🧪 Testing

Run backend tests:
```bash
cd backend
npm test
```

## 🌐 API Endpoints

Base URL: `http://localhost:5000`

- `POST /api/tickets` - Create a ticket
- `GET /api/tickets` - Get all tickets
- `GET /api/tickets/:id` - Get ticket by ID
- `PUT /api/tickets/:id` - Update ticket
- `DELETE /api/tickets/:id` - Delete ticket

## 🚢 Deployment

This project is ready for deployment on:
- **Railway** - Drag-and-drop deployment
- **Render** - Free tier available
- **DigitalOcean** - App Platform or VPS
- **AWS** - ECR + ECS or other services

### Steps to Deploy:
1. Ensure MongoDB is hosted (MongoDB Atlas recommended)
2. Update environment variables for production
3. Push to GitHub
4. Connect to preferred deployment platform
5. Deploy!

## 📝 License

MIT License

## 👤 Author

Created by Nikhil Negi Tech
│   └── tests/
│       └── analyzer.test.js
├── frontend/
│   └── public/
│       └── index.html      # Single-page UI
└── docker-compose.yml
```

**Separation of concerns:**
- `routes/tickets.js` — controller layer (HTTP only)
- `services/analyzer.js` — business logic (classification)
- `config.js` — all keyword rules isolated in one place
- `ticket.model.js` — database schema

---

## Classification Logic

All logic is local and keyword-based — no external APIs.

1. **Category Classification** — The ticket message is lowercased and checked against keyword lists for each category (Billing, Technical, Account, Feature Request). The category with the most keyword matches wins.

2. **Urgency Detection** — If the message contains words like "urgent", "asap", "down", "outage", etc., it is flagged as urgent.

3. **Priority Assignment** — Priority (P0–P3) is assigned based on severity signals. P0 words like "outage" or "data loss" rank highest, down to P3 for low-priority suggestions.

4. **Confidence Score** — Calculated as the ratio of matched keywords to total keywords in the matched category, capped at 1.0. Falls back to 0.3 for "Other".

5. **Keyword Extraction** — All known keywords found in the message are returned as a list.

---

## Custom Rule: Refund Escalation

**Rule:** Any ticket mentioning "refund", "money back", "charge back", or "dispute" is automatically assigned **P1 (High)** priority, regardless of other signals.

**Rationale:** Refund requests are time-sensitive and financially sensitive. Customers requesting refunds are often already frustrated. Delaying a refund response increases the risk of chargebacks, negative reviews, and customer churn. By automatically escalating these to P1, support teams can ensure they are handled quickly. This rule overrides lower severity scores like P2 or P3 that might otherwise apply to a standard billing inquiry.

---

## Running Tests

```bash
cd backend
npm install
node tests/analyzer.test.js
```

Tests cover category classification, urgency detection, priority logic, the custom refund rule, and confidence scoring.

---

## Data Model

Each ticket stored in MongoDB has:

| Field | Type | Description |
|-------|------|-------------|
| message | String | Original ticket text |
| category | String | Classified category |
| priority | String | P0 / P1 / P2 / P3 |
| isUrgent | Boolean | Urgency flag |
| keywords | [String] | Matched keywords |
| confidence | Number | Score 0.0 – 1.0 |
| createdAt | Date | Auto-timestamp |

MongoDB was chosen for its flexible document model, which is well-suited for tickets that may evolve over time (adding new fields without migrations). Mongoose provides simple schema validation.

---

## Reflection

### Design Decisions
- Kept the frontend as plain HTML/JS to reduce complexity. No React or build tools needed.
- Config-driven keyword rules mean classification behavior can be changed without touching logic code.
- Separated the controller (routes) from the analyzer (service) so each does one job.

### Trade-offs
- Keyword matching is simple and fast but not accurate for complex sentences. For example, "I don't have a billing issue" would still match the Billing category.
- Confidence score is a rough estimate based on keyword hit rate, not a true probabilistic score.
- The "Other" category is a fallback — it catches anything not matched, which may be too broad.

### Limitations
- No authentication or rate limiting on the API.
- No pagination on the tickets list (limited to 50 most recent).
- NLP is entirely surface-level — no stemming, synonyms, or sentence structure awareness.

### What I Would Improve With More Time
- Add stemming (e.g., "billing" matches "billed", "bills") for better recall.
- Add negation handling to avoid false positives like "not a billing issue".
- Add pagination and filtering to the ticket list view.
- Add rate limiting and basic auth to protect the API.
- Use a proper scoring model (e.g., TF-IDF) instead of raw keyword counts for confidence.
