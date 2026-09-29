# AI Logistics Quotation Workflow

A portfolio-grade Dify workflow that turns free-form freight inquiries into deterministic quotations with validation, controlled failure paths, and human approval.

## Problem

Freight inquiries arrive as incomplete natural language. A production-minded solution should use an LLM where language understanding helps, while keeping validation, pricing, exception handling, and approval explicit and testable.

## Architecture

~~~mermaid
flowchart TD
    A[Customer inquiry] --> B[LLM parameter extraction]
    B --> C[Code: validate inquiry]
    C -->|Missing fields| D[LLM: clarification email]
    D --> E[Clarification terminal]
    C -->|Complete| F[HTTP: FastAPI quote API]
    F -->|Transport failure| G[HTTP failure terminal]
    F -->|HTTP response| H[Code: validate quote response]
    H -->|Invalid JSON or business data| I[Quote failure terminal]
    H -->|Valid quote| J[LLM: quotation email]
    J --> K[Human Input: review]
    K -->|Approve| L[Code: simulated send]
    L --> M[Sent terminal]
    K -->|Reject| N[Rejected terminal]
    K -->|Timeout| O[Review timeout terminal]
~~~

The LLM extracts facts and drafts customer-facing text. Python code owns required-field validation. The FastAPI service owns all pricing, and a second code node validates the API response before it reaches an LLM.

## Key design decisions

- **Deterministic pricing:** the LLM never calculates or changes a price.
- **Two validation boundaries:** inquiry data is checked before the API call; HTTP, JSON, and business validity are checked after it.
- **Explicit failure paths:** transport errors, malformed JSON, invalid business data, rejection, and approval timeout have separate terminal outcomes.
- **Human in the loop:** no quotation is considered sent without an approval action.
- **Traceability:** Dify sends sys.workflow_run_id as X-Request-ID; the API returns it and writes structured logs.
- **Small service surface:** one FastAPI module, no database, queue, authentication layer, or real email provider.

## Workflow

The exported Dify DSL is [dify/ai-logistics-quotation-workflow.yml](dify/ai-logistics-quotation-workflow.yml). It targets Dify Community Edition 1.17.1 and contains the complete graph:

1. user input and parameter extraction;
2. deterministic inquiry validation and clarification branch;
3. an HTTP call with two retries and a two-second timeout;
4. quote-response validation, including total_price > 0;
5. quotation drafting and Human Input review;
6. approved, rejected, timeout, HTTP-failure, and business-failure terminals.

The export references a Tongyi model dependency. Importing it on another Dify instance requires a compatible model provider and credentials; the deterministic API remains provider-independent.

## Failure handling

Set the start variable simulate to exercise controlled failures:

| Value | API behavior | Expected workflow result |
|---|---|---|
| none | normal quote | human review |
| slow | waits 1 second | succeeds within the 2-second timeout |
| timeout | waits 3 seconds | retries, then HTTP failure branch |
| error_500 | HTTP 500 | retries, then HTTP failure branch |
| invalid_json | HTTP 200 with malformed JSON | quote validation failure |
| invalid_business | HTTP 200 with total_price: 0 | business validation failure |

All simulated delays are bounded.

## Example output

For Shenzhen → Los Angeles, 850 kg / 3.2 CBM general cargo by air:

~~~json
{
  "currency": "USD",
  "base_rate": 1832.50,
  "fuel_surcharge": 219.90,
  "handling_fee": 85.00,
  "total_price": 2137.40,
  "estimated_transit_days": 5,
  "provider": "Demo Logistics"
}
~~~

The quote ID is derived from the normalized request and current date; validity is seven days.

## Business rules

- Missing transport_mode defaults to air, and the generated email discloses that assumption.
- Air chargeable weight is max(weight_kg, volume_cbm × 167).
- Sea chargeable volume is max(volume_cbm, weight_kg ÷ 1000).
- Known routes use a fixed table; unknown routes use one deterministic fallback rate.
- Fuel surcharge is 12% for air and 8% for sea.
- Handling is USD 85 for air and USD 120 for sea.
- Dangerous cargo adds USD 350 to handling.
- Minimum total charge is USD 1,000; any adjustment is represented in handling_fee.

## Project structure

~~~text
app/main.py                              FastAPI service and pricing rules
dify/ai-logistics-quotation-workflow.yml Exported Dify workflow
docs/dify-workflow.md                    Import, debugging, and networking notes
tests/fixtures/inquiries.json            Inquiry scenarios
tests/test_quote.py                      API and failure-mode tests
compose.yaml                             Local service and Dify network attachment
Dockerfile                               API image
~~~

## Quick start

Local development:

~~~bash
cd ~/workspace/ai-quotation-demo
uv sync --dev
make run
~~~

The API listens at http://127.0.0.1:18000; OpenAPI is at http://127.0.0.1:18000/docs.

Tests:

~~~bash
make test
~~~

Docker, after Dify's docker_default network exists:

~~~bash
cp .env.example .env
docker compose up -d --build
curl -i http://127.0.0.1:18000/health
~~~

From Dify's Docker network, the quote endpoint is:

~~~text
http://ai-quotation-api:8000/api/v1/rates/quote
~~~

See [docs/dify-workflow.md](docs/dify-workflow.md) for import checks, execution tracing, and the narrow SSRF allowlist procedure.

## Security and production notes

- The host port binds to 127.0.0.1, not all interfaces.
- The API logs request IDs and quote IDs, not inquiry bodies or customer email addresses.
- .env, virtual environments, caches, and build artifacts are ignored.
- The demo has no authentication or rate limiting and performs a simulated send only. Add service authentication, secret management, persistent audit records, rate limits, and a real approval-aware mail integration before production use.
- Keep Dify's SSRF protection enabled. Allowlist only the current Dify Docker CIDR required for this internal service; never disable private-network blocking globally.
