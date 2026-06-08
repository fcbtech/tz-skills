# Keka Workflows And Safety

## Recommended Workflow

1. Confirm whether credentials/environment are available.
2. Set environment variables or run `scripts/keka_helper.py setup` without revealing values in chat.
3. Run `scripts/keka_helper.py token-test`.
4. Run a tiny tenant read test, usually one employee page with page size 1.
5. For the requested module, fetch one page first and inspect response shape locally.
6. Only then paginate/export.
7. Redact or avoid sensitive fields in chat summaries.
8. For writes, require explicit confirmation and prefer sandbox first.

## Module Rules

### Core HR

- Start read-only: employees list/get and org metadata.
- Export selected fields only unless the user asks for full JSON.
- Avoid printing sensitive fields in chat.

### Attendance

- Always ask or derive an explicit date range for attendance exports.
- Chunk date ranges greater than 90 days.
- Treat punch push/write operations as high risk; require explicit confirmation and sandbox test.

### Payroll

- Payroll data is highly sensitive.
- Prefer local file output over chat output.
- Never paste salary/payregister rows directly into chat unless user explicitly asks and scope is tiny.
- For exports, write to a local CSV/JSON file and provide the path/media only if appropriate.

### Hire

- Verify each Hire endpoint live before building workflows on it.
- Use read-only job/candidate listing first.
- Treat candidate writes/updates as side-effecting and require confirmation.

### Expense

- Start with categories, policies, and claims list.
- Claims may include receipts, approver info, reimbursement amounts, and sensitive context.
- For reports, export to local files rather than chat.

### Leave

- Default to read-only leave requests/balances.
- Create leave requests only with explicit user confirmation and exact employee/date/type details.

## Safety Rules

- Never expose `client_secret`, `api_key`, or access tokens in chat.
- Do not print full employee/payroll/expense records to chat by default.
- Summarize counts and field names; write detailed data to local files.
- Ask for explicit confirmation before POST/PATCH/PUT/DELETE operations.
- Prefer sandbox for write workflows.
- For payroll and HR exports, store files in a clear location and mention sensitivity.

## Common Pitfalls

1. Assuming docs paths are perfect. Some Keka docs examples are incomplete or typoed. Verify with one live request.
2. Auth POST returning 403 from Azure Application Gateway. Add browser-like headers, especially `User-Agent: Mozilla/5.0` and `Accept: application/json`, before assuming credentials are wrong.
3. Hardcoding production. Keep `KEKA_ENV` configurable because sandbox uses `kekademo`.
4. Ignoring 90-day date windows. Attendance and leave date ranges may need chunking.
5. Over-building abstractions. Use small scripts and helpers rather than creating a full CLI unless requirements expand.
6. Leaking PII. Keka data is sensitive; avoid dumping raw data in chat.
7. Not handling 429. Use retries and avoid concurrent bulk calls.

## Verification Checklist

- [ ] Credentials are available via environment variables or secure user-provided method.
- [ ] Token request succeeds without printing secrets.
- [ ] Base URL test succeeds against the expected tenant/environment.
- [ ] One-page sample request succeeds for the target module.
- [ ] Response shape is inspected before pagination/export.
- [ ] Pagination handles `totalPages` and page size safely.
- [ ] 429 handling is present for bulk requests.
- [ ] Sensitive data is written locally, not pasted in chat by default.
- [ ] Any write operation has explicit user confirmation and preferably sandbox validation.

