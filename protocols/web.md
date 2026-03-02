# Web/SaaS Development Protocol

## Stack
- Backend: Python (FastAPI/Flask) or Node.js (Express/NestJS)
- Frontend: TypeScript (React/Vue/Svelte) or Vanilla JS
- Database: PostgreSQL / SQLite / MongoDB
- API: RESTful with JSON responses

## Architecture Rules
- Strict separation: backend/ frontend/ database/ directories
- API-first: all endpoints defined in api_contract.json before coding
- Backend handles ALL business logic — frontend is presentation only
- Database migrations versioned and reversible

## API Patterns
- RESTful naming: `/api/{resource}` (plural nouns)
- Standard HTTP methods: GET (read), POST (create), PUT (update), DELETE (remove)
- Consistent response format: `{"success": bool, "data": {}, "error": null}`
- Pagination: `?page=1&limit=20` with total count in response
- Auth: JWT tokens in Authorization header (Bearer scheme)
- Rate limiting on all public endpoints
- CORS configured per environment

## Frontend Rules
- Component-based architecture (reusable, isolated)
- State management: centralized store (Redux/Pinia/Context)
- API calls through a single HTTP client wrapper
- Form validation: client-side + server-side (never trust client alone)
- Responsive design: mobile-first, breakpoints at 640/768/1024/1280px
- Accessibility: WCAG AA minimum (aria labels, keyboard nav, contrast)

## Database Rules
- All tables have: id (PK), created_at, updated_at
- Foreign keys enforced
- Indexes on frequently queried columns
- No raw SQL in application code — use ORM or query builder
- Migrations: up + down for every change
- Seeds: development data for all tables

## Security (OWASP Top 10)
- No hardcoded secrets — use environment variables
- Input sanitization on all user inputs
- Parameterized queries (prevent SQL injection)
- XSS prevention: escape all rendered content
- CSRF tokens on state-changing requests
- Rate limiting + request size limits
- Helmet/security headers on all responses
- Dependency audit: zero known vulnerabilities

## Testing
- Unit tests: all business logic functions
- Integration tests: API endpoint coverage
- E2E tests: Playwright for critical user flows
- Minimum 80% code coverage

## Filesystem Access
Workers, Kimi, and the Orchestrator have FULL filesystem access to the project folder.
This includes: creating/editing/deleting files, running Docker, executing shell scripts,
installing dependencies, running builds, and any operational task.
NO human permission required for operational actions.
Human involvement ONLY for: architectural decisions, bug fix strategy, and escalations.
