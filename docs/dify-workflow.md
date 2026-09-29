# Dify workflow notes

The exported workflow at [../dify/ai-logistics-quotation-workflow.yml](../dify/ai-logistics-quotation-workflow.yml) is the source of truth. It was built for Dify Community Edition 1.17.1. Import it into Dify and supply a compatible Tongyi model credential; do not rebuild the graph from this document.

## Actual graph

| Node | Type | Responsibility |
|---|---|---|
| 客户询盘 | Start | inquiry text and controlled simulate option |
| 参数提取器 | Parameter Extractor | LLM extraction of nine freight fields |
| Validate Inquiry验证询价 | Code | required fields and default air mode |
| 条件分支 | IF/ELSE | clarification or quotation path |
| 创建 Email | LLM | missing-information email |
| 输出 | End | clarification result |
| Get Freight Quote | HTTP Request | deterministic FastAPI call |
| Handle HTTP Failure | Template | transport-level fallback |
| HTTP Failure Response | End | failed HTTP terminal |
| Validate Quote Response | Code | HTTP, JSON, required-field, and positive-price validation |
| 判断报价 | IF/ELSE | valid or business-invalid quote |
| Handle Quote Failure（处理报价失败） | Template | safe business fallback |
| 报价失败终止 | End | invalid quote terminal |
| Generate Quotation Email2 | LLM | customer quotation draft |
| 人工介入Review Quotation | Human Input | one-hour approval window |
| Send Quotation | Code | simulated send after approval |
| Quotation Sent | End | approved terminal |
| Quotation Rejected | End | rejected terminal |
| Review Timeout | End | approval timeout terminal |

Important boundaries:

- Parameter extraction may be probabilistic; required-field validation is deterministic.
- A successful HTTP exchange is not business success. The response must parse, contain required fields, and have total_price > 0.
- Pricing is produced only by the FastAPI service.
- Human Input is the approval control; Send Quotation does not contact an email provider.

## Failure demonstrations

Use the Start node's simulate option:

- none: valid quotation reaches Human Input.
- slow: one-second response succeeds.
- timeout: three-second service delay exceeds the HTTP node's two-second timeout; after two retries it reaches HTTP Failure Response.
- error_500: after retries it reaches HTTP Failure Response.
- invalid_json: HTTP succeeds, response validation fails, and the run reaches 报价失败终止.
- invalid_business: HTTP 200 carries total_price 0, response validation fails, and the run reaches 报价失败终止.

For an incomplete inquiry, verify that no HTTP call occurs and the clarification terminal identifies the missing information.

## Execution tracing

In Dify, open the application, then Run History and the relevant workflow run. Inspect each node's inputs, outputs, duration, retry attempts, and final branch. The HTTP node sends sys.workflow_run_id as X-Request-ID.

For the API container:

~~~bash
docker logs ai-quotation-api
~~~

Match the Dify workflow run ID to request_id in the one-line JSON logs. Successful quotes also include quote_id. Inquiry bodies and customer email addresses are not logged.

## Docker networking and SSRF protection

The API joins Dify's existing external Docker network and is reachable inside it as:

~~~text
http://ai-quotation-api:8000/api/v1/rates/quote
~~~

The host-only address http://127.0.0.1:18000 is for local checks; Dify containers must use the service name above.

Dify correctly blocks private destinations by default. Keep that protection enabled and allowlist only the current Dify network CIDR:

1. Read the network name from this project's .env; the default is docker_default.
2. Inspect its current CIDR:

   ~~~bash
   docker network inspect docker_default --format '{{(index .IPAM.Config 0).Subnet}}'
   ~~~

3. Set SSRF_PROXY_ALLOW_PRIVATE_IPS in Dify's docker/.env to exactly that returned CIDR.
4. Recreate only the ssrf_proxy service using Dify's normal Compose project.

Docker may allocate a different subnet after recreation or on another host, so re-inspect before troubleshooting. Do not copy an old CIDR, allowlist all RFC1918 ranges, or disable SSRF checks globally.

These steps change Docker runtime state. Review them before execution; this project does not apply them automatically and does not modify NixOS, systemd, or firewall configuration.

## Quick API checks

Health:

~~~bash
curl -i http://127.0.0.1:18000/health
~~~

Normal quote:

~~~bash
curl -i -X POST http://127.0.0.1:18000/api/v1/rates/quote \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: demo-normal-001' \
  -d '{"origin":"Shenzhen","destination":"Los Angeles","weight_kg":850,"volume_cbm":3.2,"cargo_type":"general cargo","transport_mode":"air"}'
~~~

Invalid request:

~~~bash
curl -i -X POST http://127.0.0.1:18000/api/v1/rates/quote \
  -H 'Content-Type: application/json' \
  -d '{"origin":"Shenzhen","destination":"Los Angeles"}'
~~~

## Troubleshooting order

1. Confirm the API health endpoint from the host.
2. Confirm the API container is attached to the same Docker network as Dify.
3. Resolve ai-quotation-api from a container on that network.
4. Re-inspect the network CIDR and compare it with Dify's narrow SSRF allowlist.
5. Inspect the Dify HTTP node output and API logs using the same request ID.
6. For HTTP 200 failures, inspect the Validate Quote Response output rather than changing networking.
