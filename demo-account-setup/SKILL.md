---
name: demo-account-setup
description: >
  Spin up a fresh Tranzact demo account end-to-end: signup, company onboarding, network counter-parties,
  master units, inventory items, and three pre-canned Order-Confirmation flows (OC + Invoice + Challan
  variations). Use this skill whenever the user wants to "create a demo account", "set up a new
  Tranzact demo", "make a fresh test company", "seed an account for a demo", "build a sandbox account
  for a customer demo", or any phrasing that implies provisioning a brand-new Tranzact account with
  pre-populated transactional data. Intended for non-technical teammates — the skill collects inputs
  conversationally, fills `data.md`, runs the bundled scripts in order inside Claude's code-execution
  sandbox, and reports the credentials to share with the demo audience. The skill ONLY uses the
  standalone scripts shipped with it under `scripts/`. Do NOT use this skill to test arbitrary API
  endpoints or to catalog a curl.
---

# Demo Account Setup

Provision a fresh Tranzact demo account with realistic seed data, ready for a customer or internal
demo. Built for non-technical teammates: install the skill once in claude.ai → ask Claude to "set up
a demo account" → answer a handful of questions → done.

Everything runs inside Claude's code-execution sandbox. The sandbox already has Python 3.11+ and
`requests` available, so the teammate installs nothing on their machine.

The skill bundles the standalone Python scripts (`000_`–`013_`) plus a `data.md.template` under
`scripts/`. They are `stdlib + requests` only — no external framework imports.

## What the run produces

After a successful run the demo account will have:

1. A registered company with onboarding completed (billing address, delivery location, bank).
2. Three counter-parties on the network: a Supplier, a Buyer, and a "Both" company. Names come from
   `data.md` (`SUPPLIER_COMPANY_NAME`, `BUYER_COMPANY_NAME`, `BOTH_COMPANY_NAME`). Contact emails are
   derived as `<first_name>@<first_word_of_company>.test`.
3. Master units of measure seeded (derived from `[[BOM]]` rows in `data.md`).
4. Inventory items derived from `[[BOM]]` in `data.md` — every distinct item across all
   BOM recipes is created on the account, deduped by name, at a per-unit price of
   `price / qty`. The BOM(s) are authored so this resolves to **exactly 6 unique items**
   (≥3 sell-capable and ≥3 buy-capable), keeping the initial inventory seed small.
5. Three Order Confirmation flows (sales side):
   - OC for the first buyer.
   - OC for the second buyer, followed by a Challan and Invoice.
   - OC followed by an Invoice and two split Challans.
6. Three Purchase Order flows (buy side):
   - PO for the first supplier → Inward (100%) → Invoice.
   - PO for the second supplier → Inward (60% partial receipt).
   - PO for the first supplier (1 item) → split Inwards (40% + 60%) → two QIRs (full / 90% accept)
     → PRDC for the rejected 10% → Invoice for the full PO quantity.
7. Three Sales Enquiries (lead-tracking side):
   - SE for the first buyer (2 items).
   - SE for the second buyer (1 item), flipped to deal_status = Rejected.
   - SE for the first buyer (1 item) followed by an SQ-from-SE.
8. Three direct Sales Quotations:
   - SQ for the first buyer (2 items).
   - SQ for the second buyer (1 item), flipped to deal_status = Lost.
   - SQ for the first buyer (1 item), flipped to deal_status = Won.
9. One or two Bills of Materials built from the `[[BOM]]` block(s) in `data.md` — each a
   finished good + its raw materials, published against the first non-reject store and the
   first available BOM number series.
10. **(Optional)** The company logo, when `LOGO_PATH` in `data.md` points to an image file.
    If `LOGO_PATH` is empty the logo step is skipped and the account is set up without a logo.

The teammate gets a printable email + password to hand to the demo audience.

## Inputs to collect

All configurable inputs live in `scripts/data.md.template`. **Always ask the teammate for every
value** listed in the table below — do not silently auto-fill from the template. The template's
values are last-resort fallbacks used only when the teammate explicitly declines to supply a value
(e.g. "use default", "skip", "you pick"). Never invent values or pull constants from the `.py`
scripts.

**Exception:** `PASSWORD` is never asked. It is carried verbatim from `data.md.template` into
`data.md` — the demo password is not security-sensitive and not worth a prompt.

Group prompts into a small number of conversational asks (credentials, company profile, owner
contact, then the industry-driven block for counter-parties + inventory). Do not dump the full
table at once.

`BASE_URL` is **not** in the table below — it defaults to the value in `data.md.template` and is
**never asked about** in conversation. Always use that default as-is. **Only exception:** if the
teammate explicitly tells you to use a specific different base URL, use the one they give instead.
Carry the resulting value through to `data.md`. When — and only when — the teammate overrode it,
surface the base URL used in the final credentials report (Step 7); otherwise never show it.

| Key                     | Meaning                                | Default source            |
| ----------------------- | -------------------------------------- | ------------------------- |
| `EMAIL`                 | Login email for the new account        | ask — must be unique      |
| `PASSWORD`              | Login password (never asked)           | `data.md.template` (fixed)|
| `COMPANY_NAME`          | Demo company name                      | `data.md.template`        |
| `ADDRESS1`              | Office address line 1                  | `data.md.template`        |
| `PIN`                   | PIN code                               | `data.md.template`        |
| `CITY`                  | City                                   | `data.md.template`        |
| `STATE`                 | State                                  | `data.md.template`        |
| `COUNTRY`               | Country                                | `data.md.template`        |
| `FIRST_NAME`            | Owner first name                       | `data.md.template`        |
| `LAST_NAME`             | Owner last name                        | `data.md.template`        |
| `CONTACT_NO`            | Owner mobile (10 digits, no `+91`)     | `data.md.template`        |
| `SUPPLIER_COMPANY_NAME` | Counter-party (Supplier) name          | `data.md.template`        |
| `BUYER_COMPANY_NAME`    | Counter-party (Buyer) name             | `data.md.template`        |
| `BOTH_COMPANY_NAME`     | Counter-party (Both) name              | `data.md.template`        |
| `BOM`                   | Bill(s) of materials — sole source of inventory items + UoMs | `data.md.template` |
| `LOGO_PATH`             | Optional path to a logo image in the sandbox; empty ⇒ logo step skipped | `data.md.template` (empty) |

Always confirm `EMAIL` is unique on the target env — duplicate emails fail the signup.

## Procedure

The teammate's only actions are: (a) upload this skill in claude.ai → Customize → Skills → Add
skill, (b) start a chat and say "set up a demo account". Everything below runs inside the
code-execution sandbox.

### Step 1 — Locate the skill directory

The skill's files are mounted by the sandbox; resolve the directory containing this `SKILL.md` and
treat `scripts/` (sibling) as the working folder for all later commands.

### Step 2 — Load template defaults

Read `scripts/data.md.template`. Parse the TOML block to recover every default value. These defaults
are the **only** values shown to the user as suggestions. **Never** read or quote constants from the
`.py` scripts; the Python files are opaque executables from the user's perspective.

### Step 3 — Collect inputs conversationally

Ask the teammate for **every** key in the input table. Never auto-apply a template value without
first giving the teammate a chance to provide one. Group related fields into single prompts:

1. **Credentials** — `EMAIL` only (must be unique on the target env). Do not ask for `PASSWORD` —
   carry it verbatim from the template.
2. **Industry of the demo company.** Ask up front: *"What does the demo company do? (industry /
   line of business / what they buy and sell)"* — the answer drives the counter-party names,
   inventory items, and ideally the `COMPANY_NAME` too, so everything in the seeded account hangs
   together coherently.
3. **Company profile** — `COMPANY_NAME` (suggest an industry-flavoured name based on the answer
   above, e.g. *"Prime Window Works"* for a window manufacturer), `ADDRESS1`, `PIN`, `CITY`,
   `STATE`, `COUNTRY`.
4. **Owner contact** — `FIRST_NAME`, `LAST_NAME`, `CONTACT_NO` (10 digits, no `+91`).
5. **Counter-parties + inventory** (industry-derived, see below).
6. **Company logo (optional)** — `LOGO_PATH`. Ask whether the teammate wants a company logo on
   the account. If yes, they must provide the path to an image file (png/jpg/webp) available in
   the sandbox (e.g. one they uploaded to the chat). If they decline or have no file, leave
   `LOGO_PATH` empty — the logo step is then skipped. Never generate or substitute a placeholder
   image.

For each value the teammate provides, replace the template default in memory. Only when the
teammate explicitly skips a field, fall back to the template default for that field.

**Industry-derived counter-parties + BOM(s).** Use the industry answer from prompt #2 to invent:

- Three plausible counter-party names — one Supplier, one Buyer, one "Both" — that would
  realistically transact with a company in that industry.
- One or at most two `[[BOM]]` recipes. Each recipe describes ONE finished good and the raw
  materials consumed to produce a given quantity of it. The BOM is the **sole source of truth**
  for the inventory items and UoMs seeded on the account — there is no separate `INVENTORY_ITEMS`
  list any more. Author the recipe(s) so that the deduped item set is **exactly 6 unique items**
  (≥3 sell-capable and ≥3 buy-capable).

`[[BOM]]` shape (TOML):

```toml
[[BOM]]
[BOM.FG]                 # finished good (exactly one per BOM block; type "Sell" or "Both")
qty = 1
unit = "Pcs"
name = "Sliding Window"
price = 8500             # value of `qty` units of the FG (per-unit = price / qty)
type = "Sell"

[[BOM.RM]]               # one or more raw materials (type "Buy" or "Both")
qty = 12
unit = "Sqft"
name = "Float Glass"
price = 960
type = "Buy"
# child_bom = true        # OPTIONAL (multi-level): link this RM to its own published
                          # BOM so its sub-components expand inline. The RM item must be
                          # the [BOM.FG] of an EARLIER [[BOM]] block. Use a bom_number/
                          # bom_name string instead of `true` to target a specific BOM.

# ...more [[BOM.RM]] rows as needed
```

Rules for the BOM(s) you generate:

- Generate **one `[[BOM]]` block, or at most two** — never more. This is the initial seed;
  keep it deliberately small.
- Across all BOM blocks combined the deduped item set must be **exactly 6 unique items**
  (counted by name — the finished good(s) plus every distinct raw material together). The 003
  script creates one inventory item per unique name, so 6 unique names ⇒ exactly 6 items on
  the account. Reuse the same raw material across both BOMs (e.g. a shared "Float Glass") to
  keep two recipes inside the 6-item budget — reused names collapse to a single item.
- Each `[[BOM]]` block has exactly one `[BOM.FG]` table — the finished good. Its `type` is
  `"Sell"` (or `"Both"` if the company also resells it as-is).
- Each `[[BOM]]` block has one or more `[[BOM.RM]]` rows — the raw materials. Their `type`
  is `"Buy"` (or `"Both"` when the same item is both bought as a raw material and resold
  finished).
- `qty` is the quantity of that item consumed/produced for this recipe; `price` is the value
  of that `qty` in INR (not per-unit). The 003 script creates the inventory item at a
  per-unit price of `price / qty`. Every row (including a reused RM) must carry valid `qty`
  and `price`; keep the per-unit price consistent across occurrences (the first occurrence
  wins on conflict).
- If the same item name appears across both BOMs, every occurrence must agree on `type`
  and `unit`.
- Choose realistic `unit` values from the standard set (Kg, Gms, Litres, ml, Pcs, Sheets,
  Metres, Sqft, Dozen, Set, Nos, etc.).
- **Downstream minimums you MUST satisfy within those 6 items** (a `"Both"` item counts
  toward both tallies):
  - **≥3 sell-capable items** (type `"Sell"` or `"Both"`) — the sales-document scripts
    (`011_…`, `012_…`) need 3 sell-side products on the account.
  - **≥3 buy-capable items** (type `"Buy"` or `"Both"`) — the PO/inward scripts (`008_…`,
    `009_…`) need buyable goods; author 3 to leave comfortable margin.
  - The cleanest compliant shape: **two BOMs whose two finished goods are `"Sell"`, with one
    shared raw material typed `"Both"` and three other raw materials typed `"Buy"`.** That
    yields 3 sell-capable items (2 FGs + the Both) and ≥3 buy-capable items (the Both + 3 Buy),
    all inside exactly 6 unique items.
- The 003 script auto-attaches the company's default GST tax (first `tax_type == "gst"`
  entry) to every product it creates. Do not list a tax field in the BOM — it's handled
  for you. If the account has no GST master configured, 003 fails fast with a clear error.
- **Optional — multi-level (nested) BOMs.** Set `child_bom = true` on an `[[BOM.RM]]` row
  to link it to that item's existing published BOM; its raw materials then expand as a
  sub-assembly. The linked item **must be the `[BOM.FG]` of an earlier `[[BOM]]` block** in
  the same `data.md` (so it is published before the parent is created — list the child BOM
  first) and should be typed `"Both"`. Pass a `bom_number`/`bom_name` string instead of
  `true` to target a specific BOM. Still respect the exactly-6-unique-items budget and the
  ≥3-sell / ≥3-buy minimums. Omit `child_bom` for the normal flat seed; the default is flat.
  (On read-back the view flattens child RMs into top-level rows; the script handles this.)

Every name, item, unit, and price must be **relatable to the stated industry**. Each example
below ships two `[[BOM]]` recipes totalling exactly 6 unique items, with one shared `"Both"`
raw material so the ≥3-sell / ≥3-buy minimums are met:

- *Window manufacturer* → suppliers: "Saint Glass Traders", "Aluminium Extrusions Pvt Ltd";
  buyer: "Skyline Builders Pvt Ltd"; both: "Hardware Mart LLP".
  - BOM #1: 1 Pcs Sliding Window (Sell, 8500) ← 12 Sqft Float Glass (Buy, 960),
    2.5 Kg Aluminium Section (Both, 800).
  - BOM #2: 1 Pcs Fixed Window (Sell, 6200) ← 10 Sqft Float Glass (Buy, 800, reused),
    6 Metres Rubber Gasket (Buy, 150), 2 Set Handle Set (Buy, 220).
  - 6 unique items: Sliding Window (Sell), Fixed Window (Sell), Float Glass (Buy),
    Aluminium Section (Both), Rubber Gasket (Buy), Handle Set (Buy) → 3 sell-capable,
    4 buy-capable.
- *Lamp manufacturer* → suppliers: "Greece Traders LLP", "Filament Supplies Pvt Ltd";
  buyer: "Skyline Lighting Pvt Ltd"; both: "Hardware Mart LLP".
  - BOM #1: 1 Dozen Lamp (Sell, 8000) ← 100 Gms Greece (Buy, 80), 12 Pcs Bulb (Both, 240),
    1 Pcs Lamp Shade (Buy, 150).
  - BOM #2: 1 Pcs Table Lamp (Sell, 1200) ← 1 Pcs Bulb (Both, 20, reused),
    0.3 Kg Filament (Buy, 320).
  - 6 unique items: Lamp (Sell), Table Lamp (Sell), Greece (Buy), Bulb (Both),
    Lamp Shade (Buy), Filament (Buy) → 3 sell-capable, 4 buy-capable.
- *Lamp manufacturer (multi-level)* → suppliers: "Filament Supplies Pvt Ltd", "Lamp
  Components Co"; buyer: "Skyline Lighting Pvt Ltd"; both: "Hardware Mart LLP".
  - BOM #1 (child, listed first): 1 Pcs **Bulb Assembly** (Both, 300) ← 1 Pcs Bulb
    (Both, 20), 0.3 Kg Filament (Buy, 320).
  - BOM #2 (parent): 1 Pcs Table Lamp (Sell, 1200) ← 1 Pcs **Bulb Assembly**
    (`child_bom = true`, Both, 300), 1 Pcs Lamp Base (Buy, 90), 1 Pcs Lamp Shade (Buy, 150).
  - 6 unique items: Bulb Assembly (Both), Bulb (Both), Filament (Buy), Table Lamp (Sell),
    Lamp Base (Buy), Lamp Shade (Buy) → 3 sell-capable, 5 buy-capable. The Table Lamp's
    Bulb Assembly RM links to BOM #1 as a child BOM.

Show the generated names + BOM(s) to the teammate so they can accept, edit individual entries,
or override entirely. If they decline to give an industry, fall back to template defaults for
the counter-party names and the BOM.

**Always offer a multi-level (nested) BOM before proceeding.** When you present the generated
BOM(s) for confirmation — and before any script runs — explicitly ask the teammate whether they
want one of the finished goods to be used as a raw material inside another BOM (a multi-level /
nested sub-assembly), briefly explaining what that demonstrates. If they say yes, restructure the
recipes so the intermediate good is the `[BOM.FG]` of an earlier `[[BOM]]` block and is linked via
`child_bom = true` on the parent's `[[BOM.RM]]` row (see the multi-level rules and example above),
still within the 6-item / ≥3-sell / ≥3-buy budget. If they decline, proceed with the flat BOM(s).

### Step 4 — Confirm the final seed values with the user

Before running anything, print a clean summary of every value that will be written to `data.md`,
covering: credentials, company profile, owner contact, counter-party company names, the
`[[BOM]]` recipe(s) — for each, the `[BOM.FG]` finished good and every `[[BOM.RM]]` raw
material (qty, unit, name, price-for-qty, type) — and the company logo (`LOGO_PATH`, or
"none — logo step skipped" when empty). Mark which values came from the user vs. which fell back to defaults. **Do not include any
constants from the Python scripts** (contact first/last names, phones, addresses inside the
counter-parties, etc.) — they are not user-facing. **Do not include `BASE_URL`** in the summary
unless the teammate explicitly overrode it with a specific URL — in that case show the overridden
base URL so they can confirm the target env. Otherwise it is fixed infrastructure, not a tunable.

Wait for the teammate to confirm ("yes / go / proceed") before continuing. If they want to change
anything, edit and reprint.

### Step 5 — Write `scripts/data.md`

Write the confirmed values into a single fenced ```toml block at `scripts/data.md`, sibling of the
`.py` files. Preserve the section comments from the template. The loader regex rejects anything
other than a single fenced toml block.

### Step 6 — Run scripts in order

Run each script with `python3`, **strictly in numeric order**. Each script depends on the previous
step's server-side state, so do not parallelise and do not skip. **`004_create_bom.py` now runs
immediately after the inventory step (`003_…`)** — the BOM relies on the inventory items and units
that 003 seeded, and every downstream sales/purchase script assumes the BOM already exists.

```
python3 scripts/000_account_setup.py
python3 scripts/001_add_network_companies.py
python3 scripts/002_add_master_units.py
python3 scripts/003_add_inventory_items.py
python3 scripts/004_create_bom.py
python3 scripts/005_create_oc_first_buyer.py
python3 scripts/006_create_oc_second_buyer_with_challan_invoice.py
python3 scripts/007_create_oc_invoice_split_challans.py
python3 scripts/008_create_po_inward_invoice.py
python3 scripts/009_create_po_inward_60pct_second_seller.py
python3 scripts/010_create_po_split_inwards_qirs_prdc_invoice.py
python3 scripts/011_create_three_sales_enquiries_with_sq.py
python3 scripts/012_create_three_sales_quotations_with_deal_status.py
python3 scripts/013_upload_company_logo.py
```

`013_upload_company_logo.py` is a no-op when `LOGO_PATH` is empty in `data.md` — it logs that the
upload was skipped and exits `0`, so it is safe to always include in the run order.

**Throttle-safe pacing.** After each script exits cleanly, **sleep 10 seconds** before launching
the next one. This keeps the burst of API calls well under the Tranzact backend's per-minute rate
limit and avoids the 429-style throttling errors that otherwise hit around the 5th or 6th script.
Use `time.sleep(10)` in the runner, or shell out a `sleep 10` between invocations — either is
fine. Do not skip the wait, even when running on a fast box.

**Handling throttle / rate-limit errors.** If a script fails with a throttling response (HTTP
status `429`, or the body contains phrases like "throttled", "rate limit", "too many requests"),
do **not** treat it as a fatal failure. Instead:

1. Look for a wait hint in the response: the `Retry-After` header (seconds or HTTP-date), or a
   field in the JSON body such as `retry_after`, `wait_seconds`, or a message like `"available in
   42 seconds"`. Parse out the number of seconds.
2. If a hint is present, sleep for that many seconds (round up, add a 2-second cushion).
3. If no hint is present, sleep for **60 seconds**.
4. Re-run the **same** script (do not skip ahead). Resume the normal 10-second cadence after it
   succeeds.
5. If the same script throttles twice in a row even after waiting, stop and report it to the
   teammate — the backend may be in degraded mode.

Halt on the first non-zero exit code **that isn't a throttling error**. Show the failing script's
stderr/stdout tail to the teammate and stop — do not patch, retry, or skip ahead. Surface the
failing step number so they can report it.

If `requests` is somehow missing from the sandbox (shouldn't happen, but possible on certain
locked-down deployments), `pip install requests` inside the sandbox and retry. Do **not** ask the
teammate to install anything on their own machine.

### Step 7 — Report credentials

On success, print a clean summary to the teammate:

```
✅ Demo account ready
  Email:    <EMAIL>
  Password: <PASSWORD>
  Company:  <COMPANY_NAME>
```

If — and only if — the teammate overrode the base URL with a specific value, add a
`Base URL: <BASE_URL>` line to the summary so they know which env the scripts ran against. When the
default template base URL was used, omit it entirely.

Mention the three OCs, units, and inventory were seeded. If a logo was provided, note it was
uploaded; if `LOGO_PATH` was empty, note the account has no logo.

## Hard rules

1. **Never modify the bundled Python scripts.** Files under `scripts/*.py` are executed-only inside
   the sandbox — read access is fine, write access is forbidden. If a script fails, surface the
   error. Do not patch, monkey-patch, copy-and-edit, or rewrite. The scripts are signed-off
   automations shipped as-is with the skill.
2. **Never expose constants from the `.py` scripts to the teammate.** Only values that originate in
   `data.md.template` or the teammate's own answers are user-facing. Contact names, phone numbers,
   counter-party addresses, derived email formulas, and similar internal details stay hidden.
3. **Always ask the teammate for every value in the input table.** No silent default usage.
   Accept "use default" / "skip" as a valid answer, in which case fall back to the template value
   for that field. **Exceptions:** `BASE_URL` is never asked about and defaults verbatim from
   `data.md.template`, but the teammate MAY override it by explicitly naming a different base URL —
   in which case use theirs and show it in the final report (Step 7); it is otherwise never shown.
   `PASSWORD` is never asked — carried verbatim from `data.md.template` into `data.md`.
4. **Counter-party names and BOM contents must be relatable to the demo company's industry.**
   Always ask the teammate what the demo company does, then derive coherent, industry-appropriate
   names, BOM recipes (finished good + raw materials), units, and prices. Do not mix items from
   unrelated industries. The `[[BOM]]` blocks are the sole source of truth for which inventory
   items and UoMs end up on the account — there is no separate `INVENTORY_ITEMS` config.
   **Cap the seed at one or at most two `[[BOM]]` blocks resolving to exactly 6 unique items**,
   while still meeting the downstream minimums (≥3 sell-capable, ≥3 buy-capable — a `"Both"`
   item counts for both).
5. **Always confirm the full seed-value summary before execution.** Print every value about to be
   written to `data.md`, mark user-supplied vs. default, and wait for explicit go-ahead.
6. **Never run the scripts out of order or in parallel.** Each step assumes the prior step's
   server-side state.
7. **Never reuse an email.** Signup fails on duplicates. Always confirm with the teammate.
8. **Never ask the teammate to run code or install anything locally.** All execution happens inside
   the sandbox. Their job is to chat.
9. **`scripts/data.md` is sandbox-local and ephemeral.** It holds the demo's credentials but is
   discarded with the sandbox; no commit/storage concerns.
10. **Always offer a multi-level (nested) BOM before execution.** When presenting the generated
    BOM(s) for confirmation, proactively ask whether the teammate wants a finished good used as a
    raw material in another BOM (`child_bom = true`). Suggest it every run; proceed flat only if
    they decline.

## Troubleshooting

- **Signup 400 "email already exists"** → ask for a different `EMAIL`, rewrite `data.md`, restart at
  step 6.
- **Onboarding step fails after signup succeeds** → the account exists but is half-baked. Ask the
  teammate to pick a new email; do not try to resume.
- **OC steps fail with "counter-party not found" or "item not found"** → step 1/2/3 didn't complete
  cleanly. Re-run from `000_` with a fresh email.
- **PO/Inward/QIR/PRDC steps (007–009) fail with "supplier not found" or "no buyable products"** →
  step 1/3 didn't seed a supplier counter-party or buy-side items. Re-run from `000_` with a fresh
  email. The 60%-partial-inward and the QIR/PRDC chain assume the supplier has at least one
  buyable product with a delivery location configured.
- **SE/SQ steps (011–012) fail with "Need at least 3 sell-side products with a GST tax mapping"** →
  the BOM didn't include enough sell-capable items. Re-run from `000_` with a fresh email and a
  BOM that has **≥3 items of type `Sell` or `Both`** across the recipes — within the 6-item cap,
  the easiest way is two `[[BOM]]` blocks (two `Sell` FGs) plus one shared raw material typed
  `Both`. 003 auto-attaches GST, so as long as the account has a GST master under
  Settings → Tax Options, every item created will carry one.
- **PO steps (008–009) fail with "Need 2 buyable goods for supplier"** → the BOM had fewer than
  2 buy-capable items. Ensure **≥3 items of type `Buy` or `Both`** across the recipes (a `Both`
  item counts) so there is comfortable margin, then re-run from `000_` with a fresh email.
- **003 fails with "No GST tax master found on the company"** → the account doesn't have GST
  enabled in Settings → Tax Options. Toggle on GST 18% (or any GST rate) and re-run from `003_`.
- **013 logo step fails with "LOGO_PATH file not found"** → the path in `data.md` doesn't resolve
  inside the sandbox. Confirm the image was uploaded to the chat and use its actual sandbox path,
  or clear `LOGO_PATH` to skip the logo. The rest of the account is already seeded — just re-run
  `013_` after fixing the path; do not re-run from `000_`.
- **Connection refused / DNS error hitting `BASE_URL`** → confirm the env URL is correct and the
  Tranzact backend is up; sandbox cannot reach private/VPN-only hosts.
- **`ModuleNotFoundError: requests`** → run `pip install requests` inside the sandbox and retry.

## Maintenance — script source of truth & sync

The step scripts under `scripts/` are a **verbatim mirror**. The **source of truth** is the
automation project in the QA repo:

```
qa/generated/automations/demo_account_setup/   <-- edit here (source of truth)
.claude/skills/demo-account-setup/scripts/      <-- mirror shipped with this skill
```

Every numbered step script (`NNN_*.py`) must be byte-identical between the two. Only the
auxiliary files differ on purpose: the automation dir keeps a filled `data.md` (real creds) and
`__init__.py`; the skill ships a blank `data.md.template` instead — never copy a filled `data.md`
into the skill.

**When you change a script**, make the edit in the automation dir, then re-sync the mirror and
repackage:

```bash
# 1. re-sync the mirror from the source of truth
cp qa/generated/automations/demo_account_setup/*_*.py \
   .claude/skills/demo-account-setup/scripts/

# 2. verify no drift (exit 0 = in sync)
python qa/check_demo_skill_sync.py

# 3. repackage the uploadable skill zip (excludes caches)
cd .claude/skills && rm -f /tmp/demo-account-setup.zip && \
  zip -r /tmp/demo-account-setup.zip demo-account-setup \
  -x '*/__pycache__/*' -x '*.pyc'
```

`python qa/check_demo_skill_sync.py` is the guardrail — run it after any change (or in CI) to
catch a skill bundle that has drifted behind the automation project.
