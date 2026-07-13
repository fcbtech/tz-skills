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
   `price / qty`. The BOM(s) are authored so this resolves to **exactly 15 unique items —
   5 finished goods typed `"Sell"` + 10 raw materials typed `"Both"`** (⇒ 15 sell-capable,
   10 buy-capable), keeping the initial inventory seed compact but flexible.
5. Three Order Confirmation flows (sales side). The OC/PO scripts are wired to spread the
   inventory across documents so that **all 15 items are exercised** — each script uses a
   distinct slice of the product list (by `ITEM_OFFSET` + item count) rather than always the
   first two, giving a diversified demo. Sales side uses catalog items 1–8:
   - OC for the first buyer (items 1–4).
   - OC for the second buyer (item 5), followed by a Challan and Invoice.
   - OC followed by an Invoice and two split Challans (items 6–8).
6. Three Purchase Order flows (buy side). Buy side uses catalog items 9–15:
   - PO for the first supplier (items 9–11) → Inward (100%) → Invoice.
   - PO for the second supplier (items 12–14) → Inward (60% partial receipt).
   - PO for the first supplier (item 15) → split Inwards (40% + 60%) → two QIRs (full / 90% accept)
     → PRDC for the rejected 10% → Invoice for the full PO quantity. **The QIR step is a premium
     feature** (`grn-qir`); if it isn't enabled, this flow stops after the inwards (non-fatal — the
     PO + inwards are still created) and the run continues.
7. Three Sales Enquiries (lead-tracking side). **Sales Enquiry is a premium feature** (`ncd`); if it
   isn't enabled, this whole step is skipped (non-fatal) and the run continues.
   - SE for the first buyer (2 items).
   - SE for the second buyer (1 item), flipped to deal_status = Rejected.
   - SE for the first buyer (1 item) followed by an SQ-from-SE.
8. Three direct Sales Quotations:
   - SQ for the first buyer (2 items).
   - SQ for the second buyer (1 item), flipped to deal_status = Lost.
   - SQ for the first buyer (1 item), flipped to deal_status = Won.
9. Bills of Materials built from the `[[BOM]]` block(s) in `data.md` — each a finished good + its
   raw materials, published against the first non-reject store and the first available BOM number
   series. The BOM step is **non-fatal**: if it fails for **any** reason (the `bom` premium feature
   not enabled → HTTP 426, or a backend error → HTTP 500), it is **skipped** and everything else is
   still created — the BOM can be added later by re-running `004` once the cause is resolved.
10. **(Optional)** The company logo — from `LOGO_PATH` (an image file) if set, otherwise
    best-effort fetched from `COMPANY_WEBSITE` (the site's apple-touch-icon / icon / og:image /
    favicon). If both are empty (or the website yields no usable image), the logo step is skipped
    (non-fatal) and the account is set up without a logo.

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
| `LOGO_PATH`             | Optional path to a logo image in the sandbox (preferred logo source) | `data.md.template` (empty) |
| `COMPANY_WEBSITE`       | Optional company website URL; used only when `LOGO_PATH` has no file — step 013 fetches the logo from the site | `data.md.template` (empty) |

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
6. **Company logo (optional)** — `LOGO_PATH` and/or `COMPANY_WEBSITE`. Ask whether the teammate
   wants a company logo on the account. There are two ways to supply one:
   - **An image file** — the path to a png/jpg/webp available in the sandbox (e.g. one they
     uploaded to the chat). Set `LOGO_PATH`. This is preferred when available.
   - **A company website** — if they don't have a file but can give the company's website URL,
     set `COMPANY_WEBSITE` (e.g. `https://acme.com`). Step 013 will best-effort fetch the logo
     from that site (apple-touch-icon / icon / og:image / favicon) and upload it.
   `LOGO_PATH` wins if both are given. If they decline or provide neither, leave both empty — the
   logo step is then skipped (non-fatal). Never generate or substitute a placeholder image.

For each value the teammate provides, replace the template default in memory. Only when the
teammate explicitly skips a field, fall back to the template default for that field.

**Industry-derived counter-parties + BOM(s).** Use the industry answer from prompt #2 to invent:

- Three plausible counter-party names — one Supplier, one Buyer, one "Both" — that would
  realistically transact with a company in that industry.
- Several `[[BOM]]` recipes (typically five — one per finished good). Each recipe describes
  ONE finished good and the raw materials consumed to produce a given quantity of it. The BOM is
  the **sole source of truth** for the inventory items and UoMs seeded on the account — there is
  no separate `INVENTORY_ITEMS` list any more. Author the recipes so that the deduped item set is
  **exactly 15 unique items — 5 finished goods typed `"Sell"` + 10 raw materials typed `"Both"`**
  (⇒ 15 sell-capable, 10 buy-capable).

`[[BOM]]` shape (TOML):

```toml
[[BOM]]
[BOM.FG]                 # finished good (exactly one per BOM block; type "Sell" for this seed)
qty = 1
unit = "Pcs"
name = "Sliding Window"
price = 8500             # value of `qty` units of the FG (per-unit = price / qty)
type = "Sell"

[[BOM.RM]]               # one or more raw materials (type "Both" for this seed)
qty = 12
unit = "Sqft"
name = "Float Glass"
price = 960
type = "Both"
# child_bom = true        # OPTIONAL (multi-level): link this RM to its own published
                          # BOM so its sub-components expand inline. The RM item must be
                          # the [BOM.FG] of an EARLIER [[BOM]] block. Use a bom_number/
                          # bom_name string instead of `true` to target a specific BOM.

# ...more [[BOM.RM]] rows as needed
```

Rules for the BOM(s) you generate:

- Generate **as many `[[BOM]]` blocks as needed to realise the 15-item set — typically five**
  (one per finished good), plus at most one extra block if you author a nested sub-assembly
  (see multi-level below). This is the initial seed; keep it to those 15 items — don't inflate it.
- Across all BOM blocks combined the deduped item set must be **exactly 15 unique items**
  (counted by name — every finished good plus every distinct raw material together). The 003
  script creates one inventory item per unique name, so 15 unique names ⇒ exactly 15 items on
  the account. Reuse the same raw material across multiple BOMs (e.g. a shared "Float Glass") so
  the recipes stay inside the 15-item budget — reused names collapse to a single item.
- Each `[[BOM]]` block has exactly one `[BOM.FG]` table — the finished good. For this seed
  its `type` is `"Sell"` (finished goods are sold, not purchased).
- Each `[[BOM]]` block has one or more `[[BOM.RM]]` rows — the raw materials. For this seed
  their `type` is `"Both"` (bought as inputs, and also resellable).
- `qty` is the quantity of that item consumed/produced for this recipe; `price` is the value
  of that `qty` in INR (not per-unit). The 003 script creates the inventory item at a
  per-unit price of `price / qty`. Every row (including a reused RM) must carry valid `qty`
  and `price`; keep the per-unit price consistent across occurrences (the first occurrence
  wins on conflict).
- If the same item name appears across both BOMs, every occurrence must agree on `type`
  and `unit`.
- Choose realistic `unit` values from the standard set (Kg, Gms, Litres, ml, Pcs, Sheets,
  Metres, Sqft, Dozen, Set, Nos, etc.).
- **Required types:** the **5 finished goods are typed `"Sell"`** and the **10 raw materials are
  typed `"Both"`**. This gives **15 sell-capable items** (5 Sell FGs + 10 Both RMs) and **10
  buy-capable items** (the 10 Both RMs), far above the downstream script minimums (the sales
  scripts `011_…`/`012_…` need ≥3 sell-side products; the PO/inward scripts `008_…`/`009_…` need
  buyable goods — the RMs cover those). Finished goods are **not** typed `"Both"`: a finished good
  that is also purchasable has been seen to trip the BOM create endpoint, so keep FGs `"Sell"`.
  - The cleanest compliant shape: **five BOMs whose five finished goods are typed `"Sell"`,
    drawing their raw materials from a shared pool of 10 items typed `"Both"`, reused across the
    recipes so all 10 raw materials appear at least once.** That yields exactly 15 unique items
    (5 Sell + 10 Both).
- The 003 script auto-attaches the company's default GST tax (first `tax_type == "gst"`
  entry) to every product it creates. Do not list a tax field in the BOM — it's handled
  for you. If the account has no GST master configured, 003 fails fast with a clear error.
- **Optional — multi-level (nested) BOMs.** Set `child_bom = true` on an `[[BOM.RM]]` row
  to link it to that item's existing published BOM; its raw materials then expand as a
  sub-assembly. The linked item **must be the `[BOM.FG]` of an earlier `[[BOM]]` block** in
  the same `data.md` (so it is published before the parent is created — list the child BOM
  first) and should be typed `"Both"` (a sub-assembly is both manufactured and consumed). Pass a
  `bom_number`/`bom_name` string instead of `true` to target a specific BOM. Still respect the
  exactly-15-unique-items budget (5 `"Sell"` finished goods + 10 `"Both"` raw materials; the nested
  sub-assembly is one of the `"Both"` items, so it may add one extra `[[BOM]]` block — six in total
  — while keeping 15 items). Omit `child_bom` for the normal flat seed; the default is flat.
  (On read-back the view flattens child RMs into top-level rows; the script handles this.)

Every name, item, unit, and price must be **relatable to the stated industry**. Each example
below ships five `[[BOM]]` recipes totalling exactly 15 unique items — **5 finished goods typed
`"Sell"`** drawing on a shared pool of **10 raw materials typed `"Both"`** (15 sell-capable, 10
buy-capable). Wire each finished good to a few raw materials from the pool, reusing them freely,
so every raw material appears in at least one recipe:

- *Window manufacturer* → suppliers: "Saint Glass Traders", "Aluminium Extrusions Pvt Ltd";
  buyer: "Skyline Builders Pvt Ltd"; both: "Hardware Mart LLP".
  - **5 finished goods** (one `[[BOM]]` each, `"Sell"`): Sliding Window (Pcs, 8500),
    Fixed Window (Pcs, 6200), Casement Window (Pcs, 7400), Glass Door (Pcs, 12000),
    Ventilator (Pcs, 3200).
  - **10 raw materials** (`"Both"`): Aluminium Section (Kg, 800), Glass Panel (Sqft, 1500),
    Handle Set (Set, 220), Hinge (Pcs, 90), Door Lock (Pcs, 450), Float Glass (Sqft, 960),
    Rubber Gasket (Metres, 150), Silicone Sealant (Pcs, 180), Screws (Nos, 40),
    Weather Strip (Metres, 120).
  - e.g. Sliding Window ← Float Glass, Aluminium Section, Rubber Gasket, Handle Set;
    Glass Door ← Glass Panel, Door Lock, Aluminium Section, Weather Strip; etc. →
    15 unique items = 5 Sell + 10 Both (15 sell-capable, 10 buy-capable).
- *Lamp manufacturer* → suppliers: "Greece Traders LLP", "Filament Supplies Pvt Ltd";
  buyer: "Skyline Lighting Pvt Ltd"; both: "Hardware Mart LLP".
  - **5 finished goods** (`"Sell"`): Table Lamp (Pcs, 1200), Floor Lamp (Pcs, 3400),
    Pendant Lamp (Pcs, 2100), Wall Lamp (Pcs, 1600), Desk Lamp (Pcs, 900).
  - **10 raw materials** (`"Both"`): Bulb (Pcs, 20), Lamp Shade (Pcs, 150), Switch (Pcs, 35),
    Lamp Base (Pcs, 90), Wire Spool (Metres, 60), Filament (Kg, 320), Greece (Gms, 80),
    Screws (Nos, 40), Solder (Pcs, 110), Insulation Tape (Pcs, 25).
  - Wire each lamp to a few of these so all 10 raw materials appear → 15 unique items
    (5 Sell + 10 Both).
- *Lamp manufacturer (multi-level)* → same counter-parties as above.
  - Make one raw material double as a published sub-assembly: author **Bulb Assembly** as the
    `[BOM.FG]` of an earlier `[[BOM]]` block (← Bulb, Filament, Solder), then link it into a later
    lamp's `[[BOM.RM]]` via `child_bom = true`. A sub-assembly is both manufactured *and* consumed,
    so it is the one item typed `"Both"` rather than `"Sell"`; it counts as one of the 10 `"Both"`
    items, adding one extra `[[BOM]]` block (six total) while the deduped set stays at 15 items.
    (Note: a `"Both"` finished-good-style item is the shape suspected of tripping the BOM-create
    endpoint — prefer flat BOMs until that's resolved.) Only offer this when the teammate opts in.

Show the generated names + BOM(s) to the teammate so they can accept, edit individual entries,
or override entirely. If they decline to give an industry, fall back to template defaults for
the counter-party names and the BOM.

**Always offer a multi-level (nested) BOM before proceeding.** When you present the generated
BOM(s) for confirmation — and before any script runs — explicitly ask the teammate whether they
want one of the finished goods to be used as a raw material inside another BOM (a multi-level /
nested sub-assembly), briefly explaining what that demonstrates. If they say yes, restructure the
recipes so the intermediate good is the `[BOM.FG]` of an earlier `[[BOM]]` block and is linked via
`child_bom = true` on the parent's `[[BOM.RM]]` row (see the multi-level rules and example above),
still within the 15-item budget (5 `"Sell"` FGs + 10 `"Both"` RMs). If they decline, proceed with
the flat BOM(s).

### Step 4 — Confirm the final seed values with the user

Before running anything, print a clean summary of every value that will be written to `data.md`,
covering: credentials, company profile, owner contact, counter-party company names, the
`[[BOM]]` recipe(s) — for each, the `[BOM.FG]` finished good and every `[[BOM.RM]]` raw
material (qty, unit, name, price-for-qty, type) — and the company logo (`LOGO_PATH` file, or
`COMPANY_WEBSITE` to fetch from, or "none — logo step skipped" when both are empty). Mark which values came from the user vs. which fell back to defaults. **Do not include any
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

Run each script with `python3`, **strictly in numeric order**. Do not parallelise. Scripts
`000`–`003` are **hard prerequisites** — each depends on the previous step's server-side state
(account → counter-parties → units → inventory items), so do not skip them. **`004_create_bom.py`
runs immediately after the inventory step (`003_…`)** because the BOM relies on the inventory items
and units that 003 seeded. **However, the BOM is NOT a dependency for any later script** — the
downstream sales / purchase / enquiry / quotation scripts (`005`–`013`) build their documents from
the inventory items (`003`) and counter-parties (`001`) only, never from the BOM. So a BOM failure
must not stop them (see **"BOM creation is non-fatal"** below).

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

`013_upload_company_logo.py` is a no-op when **both** `LOGO_PATH` and `COMPANY_WEBSITE` are empty in
`data.md` (and it also exits `0` if a website fetch finds no usable logo) — it logs that the upload
was skipped, so it is safe to always include in the run order.

**Throttle-safe pacing.** After each script exits cleanly, **sleep 3 seconds** before launching the
next one. The scripts now handle rate-limiting *internally* — each API call retries the single
throttled request with exponential backoff (3/6/12/24s, honoring `Retry-After`) — so a heavy 60s
between-script wait is no longer needed; a short 3s spacer is enough. Use `time.sleep(3)` in the
runner (or a `sleep 3` between invocations). Don't drop it to zero — a small spacer still smooths
the burst — but don't inflate it back to 10s either.

**Handling throttle / rate-limit errors.** Because every script now retries throttled calls
in-process (per-call backoff, up to 4 retries), a transient 429 no longer surfaces as a script
failure — you'll just see `throttled (429) ... backoff Ns` log lines while it self-recovers. Only
if a script still **exits non-zero** with a throttling message (i.e. it exhausted its in-process
retries) treat it as a rate-limit failure:

1. Look for a wait hint in the response: the `Retry-After` header, or a JSON field such as
   `retry_after` / `wait_seconds`, or a message like `"available in 42 seconds"`.
2. Sleep for that many seconds (round up, +2s cushion), or **30 seconds** if no hint.
3. Re-run the **same** script (do not skip ahead), then resume the normal 3-second cadence.
4. If the same script throttles to failure twice in a row even after waiting, stop and report it —
   the backend may be in degraded mode.

**Two failure tiers — prerequisites (`000`–`003`) are fatal; document steps (`004`–`013`) are each
non-fatal.** The run splits cleanly in two:

- **`000`–`003` are hard prerequisites** (account → counter-parties → units → inventory items). Every
  later step reads this shared state, so if any of `000`–`003` fails, **halt** — show the failing
  script's stderr/stdout tail and stop. Nothing downstream can work without them.
- **`004`–`013` are mutually independent.** Each one builds its own documents (BOM, OCs, POs,
  Inwards, Invoices, QIRs, SEs, SQs, logo) from the shared items (`003`) and counter-parties (`001`)
  — **none of them reads another's output** (verified live: OC, PO, Inward, Invoice and SQ all
  create fine on an account where the BOM/QIR/SE steps never ran). So a failure in one **must not**
  abort the others.

Therefore, **whenever any of `004`–`013` exits non-zero for a reason other than throttling**
(examples: **HTTP 426 `PremiumFeatureException`** — a premium feature is off, keys `bom`/`grn-qir`/
`ncd` for `004`/`010`/`011`; **HTTP 500 `"Something went wrong!"`** — a backend error, seen on `004`
`/production/bom/create/` in production; a timeout; anything else):

1. Log the note **prominently, with the real error** — e.g. *"⚠️ Step `004_create_bom.py` FAILED
   (HTTP 500 — Something went wrong!) — skipped. The remaining steps don't depend on it; continuing.
   Re-run this step later once the cause is resolved."* Show the actual status code / message so the
   failure is **visible, not silently swallowed**.
2. **Continue with the next script** (resume the normal 3-second cadence). Do **not** re-run it.
3. Remember which steps were skipped so they all surface in the Step 7 report.

So the only thing that halts the run is a failure in a **prerequisite** (`000`–`003`) or a
**throttling** failure that a script couldn't self-recover from. Any `004`–`013` failure is skipped,
logged, and reported — you get as much of the demo as the backend allows, never an empty account.

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

Mention the OCs, units, and inventory were seeded. For the logo: if a `LOGO_PATH` file was
uploaded, note it; if the logo was fetched from `COMPANY_WEBSITE`, say so; if neither was provided
(or the website yielded no image), note the account has no logo. **List every document step
(`004`–`013`) that was skipped, with the reason** — e.g. *"⚠️ Skipped: BOM (004) — HTTP 500 backend
error; QIR chain (010) & Sales Enquiries (011) — premium feature not enabled. Everything else was
seeded. Re-run those scripts later once the cause/feature is resolved."* — so the teammate knows
exactly what's on the account and what can be added, and whether a skip was a premium gate or a real
backend error worth reporting.

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
   **Cap the seed at exactly 15 unique items — 5 finished goods typed `"Sell"` + 10 raw materials
   typed `"Both"`** (typically five `[[BOM]]` blocks — one per finished good — plus at most one
   extra block for a nested sub-assembly). That gives 15 sell-capable and 10 buy-capable items.
   Keep finished goods `"Sell"` (not `"Both"`) — a purchasable finished good has been seen to trip
   the BOM-create endpoint.
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
  BOM that follows the standard seed — **all 15 items typed `Both`** gives 15 sell-capable
  items, well above the ≥3 the script needs. 003 auto-attaches GST, so as long as
  the account has a GST master under
  Settings → Tax Options, every item created will carry one.
- **PO steps (008–009) fail with "Need 2 buyable goods for supplier"** → the BOM had fewer than
  2 buy-capable items. Ensure the standard seed — **all 15 items typed `Both`** gives 15
  buy-capable goods, comfortable margin — then re-run from `000_` with a fresh
  email.
- **003 fails with "No GST tax master found on the company"** → the account doesn't have GST
  enabled in Settings → Tax Options. Toggle on GST 18% (or any GST rate) and re-run from `003_`.
- **Any document step (`004`–`013`) fails** → those steps are mutually independent, so **any
  failure among `004`–`013` is non-fatal** — do NOT abort the run. Log the real error, skip that
  step, and continue with the next one. (A failure in a **prerequisite** `000`–`003` is different —
  that halts, since everything downstream needs it.) Common causes:
  - **HTTP 426 / `PremiumFeatureException`** — a premium feature isn't enabled (keys: `004` = `bom`,
    `010` = `grn-qir`, `011` = `ncd`). Enable it on the account, then re-run just that script.
  - **HTTP 500 `"Something went wrong!"`** — seen on `004` (`/production/bom/create/`) on some
    environments (production). This is a backend-side error, not a data problem; report it to the
    Tranzact team (see the isolation step below) and re-run `004` once it's fixed.
  Note the skipped steps in the final report; no need to re-run from `000_` to add them later.
- **BOM (`004`) 500 — how to isolate whether it's Tranzact's bug or our data** → before blaming the
  endpoint, on an env where the create is reachable, test a **flat BOM whose finished good is typed
  `Sell`** (not `Both`). If the `Sell`-FG BOM succeeds where the all-`Both` one 500s, the fix is on
  our side (finished goods shouldn't be `Both`); if it still 500s, capture the full response body /
  server trace and hand it to Tranzact engineering.
- **013 logo step fails with "LOGO_PATH file not found"** → the path in `data.md` doesn't resolve
  inside the sandbox. Confirm the image was uploaded to the chat and use its actual sandbox path,
  set `COMPANY_WEBSITE` instead to fetch the logo from the company's website, or clear `LOGO_PATH`
  to skip the logo. The rest of the account is already seeded — just re-run `013_` after fixing the
  path; do not re-run from `000_`.
- **013 logo via `COMPANY_WEBSITE` didn't upload a logo** → the fetch is best-effort and non-fatal;
  the run continues without a logo. Causes: the site blocked the request, exposes no
  apple-touch-icon / icon / og:image / favicon, or the sandbox can't reach external sites. Provide
  a `LOGO_PATH` image file instead, or a different/more logo-friendly URL, and re-run `013_`.
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
