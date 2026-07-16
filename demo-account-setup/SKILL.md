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
   BOM nodes (finished goods, sub-assemblies, and bought leaves) is created on the account,
   deduped by name, at a per-unit price of `price / qty`. The item count is **not fixed** — it
   flexes with the good being modelled (a simple product seeds a handful of items; a complex
   multi-level one seeds a few dozen). The top finished good is typed `"Sell"`; sub-assemblies and
   bought leaves are `"Both"`, so there are always ample sell-capable and buy-capable items for the
   downstream document steps.
5. Three Order Confirmation flows (sales side). Each script picks its line items **at random**
   from the full sellable-goods catalog (no fixed offset), so the exact products vary per run
   while the catalog is exercised broadly across repeated runs. A little overlap between
   documents is acceptable for demo data:
   - OC for the first buyer (4 items).
   - OC for the second buyer (1 item), followed by a Challan and Invoice.
   - OC followed by an Invoice and two split Challans (3 items).
6. Three Purchase Order flows (buy side). Each script picks its line items at random from the
   full buyable-goods catalog:
   - PO for the first supplier (3 items) → Inward (100%) → Invoice.
   - PO for the second supplier (3 items) → Inward (60% partial receipt).
   - PO for the first supplier (1 item) → split Inwards (40% + 60%) → two QIRs (full / 90% accept)
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
9. Bills of Materials built from the `[[BOM]]` block(s) in `data.md` — a realistic **multi-level
   tree** by default (the top finished good, its manufactured sub-assemblies each with their own
   BOM, linked via `child_bom`, down to bought leaf materials), published bottom-up against the
   first non-reject store and the first available BOM number series. The BOM step is **non-fatal**:
   if it fails for **any** reason (the `bom` premium feature
   not enabled → HTTP 426, or a backend error → HTTP 500), it is **skipped** and everything else is
   still created — the BOM can be added later by re-running `004` once the cause is resolved.
10. **(Optional)** The company logo, uploaded from the image file at `LOGO_PATH` — either one the
    teammate supplied, or one the agent fetched from the company website and saved during setup.
    If `LOGO_PATH` is empty the logo step is skipped (non-fatal) and the account has no logo.

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
6. **Company logo (optional)** — `LOGO_PATH`. Ask whether the teammate wants a company logo on the
   account. Two ways to get one, both ending in a `LOGO_PATH` file that step 013 uploads:
   - **An image file** — the teammate gives the path to a png/jpg/webp in the sandbox (e.g. one
     they uploaded to the chat). Set `LOGO_PATH` to it.
   - **A company website** — if they have no file but can give the company's website URL, **you
     (the agent) fetch the logo from that site yourself** using your web/browser tools: find the
     brand logo, download it, save it to a file in the sandbox, and set `LOGO_PATH` to that file.
     (Do this during setup, before running 013 — the script only uploads whatever `LOGO_PATH`
     points at; it does not fetch anything itself.)
   If they decline or provide neither a file nor a reachable site, leave `LOGO_PATH` empty — the
   logo step is then skipped (non-fatal). Never generate or substitute a placeholder image.

For each value the teammate provides, replace the template default in memory. Only when the
teammate explicitly skips a field, fall back to the template default for that field.

**Industry-derived counter-parties + BOM(s).** Use the industry answer from prompt #2 to invent:

- Three plausible counter-party names — one Supplier, one Buyer, one "Both" — that would
  realistically transact with a company in that industry.
- A realistic **multi-level BOM tree** for the finished good(s) the company makes. Each `[[BOM]]`
  block is one node — a finished good or a sub-assembly — plus the parts it consumes. The BOMs are
  the **sole source of truth** for the inventory items and UoMs seeded on the account (there is no
  separate `INVENTORY_ITEMS` list). How many blocks, how many items, and how deep the tree goes
  all **flex with the good** — see "Author the BOM tree" below for the method. This is the default;
  a flat single-level seed is available as a quick alternative.

`[[BOM]]` shape (TOML):

```toml
[[BOM]]
[BOM.FG]                 # the node's finished good (exactly one per block). "Sell" if it's the
qty = 1                  # top good, "Both" if it's a sub-assembly (an FG that is also consumed).
unit = "Pcs"
name = "Sliding Window"
price = 8500             # value of `qty` units of the FG (per-unit = price / qty)
type = "Sell"

[[BOM.RM]]               # one or more parts this node consumes (bought leaves are "Both")
qty = 12
unit = "Sqft"
name = "Float Glass"
price = 960
type = "Both"
# child_bom = true        # set on a part that is itself MANUFACTURED: links it to its own
                          # BOM so its sub-parts expand inline (this is what nests the tree).
                          # The part must be the [BOM.FG] of an EARLIER [[BOM]] block (published
                          # first). Pass a bom_number/bom_name string to target a specific BOM.

# ...more [[BOM.RM]] rows as needed
```

**Author the BOM tree.** Real goods are made as a *tree*, not a flat list: a finished good is
built from parts, some of which the company **makes** itself (sub-assemblies, each with its own
BOM) and some it **buys** finished (leaf materials).

**Every good's tree is unique — derive it from how *that* good is actually made; never reshape a
good to match an example.** The number of BOMs, the number of items, how deep it nests, and *where*
it nests all fall out of the specific product. A garam-masala pack, a cotton shirt, an LED bulb, a
sofa and a table fan decompose into completely different shapes — different depths, different
breadths, a different branch carrying the depth, and some barely nesting at all. The worked examples
further down (and the fan in `data.md.template`) illustrate the **method**, not a shape to copy —
treat them as "here's how the reasoning goes," then reason the same way from scratch for the good in
front of you. Design the tree like this:

1. **Start from the finished good(s)** the account should sell — derived from the industry, or
   named by the teammate.
2. **Walk every part and ask the make-vs-buy question:** *does the company MAKE this from other
   things, or BUY it finished?*
   - **MAKE → it's a sub-assembly.** Give it its own `[[BOM]]` block (its `[BOM.FG]` is the part;
     its `[[BOM.RM]]` rows are what it's made from), then recurse into *those* parts. Link it into
     its parent as an `[[BOM.RM]]` row carrying `child_bom = true`. In-house steps that make
     something a sub-assembly: **assembling** parts into a module (a motor, a gearbox),
     **fabricating/forming** a shape from raw stock (a guard bent from wire, a pipe cut to size),
     **casting/molding** from raw material (a die-cast arm).
   - **BUY → it's a leaf.** Just an `[[BOM.RM]]` row, no block of its own. Standard commodity parts
     (bearings, capacitors, connectors), **fasteners/hardware** (screws, nuts, washers, pins),
     **raw stock** bought by weight/length (wire, rod, pipe, sheet, granules), and
     **packaging/labels** (cartons, poly bags, stickers, tape) are always leaves — even an
     internally-complex one (a bought bearing is a leaf), because *this* company doesn't make it.
3. **Go deep only where it matters.** A real BOM is deep in the **1–2 branches that are the heart
   of the product** and flat everywhere else — a fan's motor nests 3 levels, but its carton and
   screws hang straight off the top. Don't decompose every branch.
4. **Bound it as a window, not a quota.** Typically **~2–3 levels** deep and a handful of BOMs,
   *scaling with the good*: a simple product (a wooden stool) may be 1–2 levels and 2 BOMs; a
   complex one (a fan, a pump) 3 levels and ~5–8 BOMs. Stop at ~3 levels; when a part is borderline
   make-or-buy near that edge, call it **bought**.

**Order the blocks bottom-up.** A child's `[[BOM]]` block **must appear before** the parent that
consumes it, so it is published first: list the deepest sub-assemblies first, then the ones above
them, then the top finished good last. `004` walks the blocks in order and wires each child into
its parent via `child_bom` (you may also pass a `bom_number`/`bom_name` string instead of `true`
to target a specific BOM, but `true` — "link this item's one published BOM" — is what you want on
a fresh account where each sub-assembly has exactly one BOM).

**Typing — a fixed rule that holds for any tree shape:**
- the **top finished good is `"Sell"`**,
- every **sub-assembly is `"Both"`** (it is manufactured *and* consumed as a line in its parent),
- every **bought leaf is `"Both"`** (a purchased input that is also resellable).

This guarantees the downstream scripts always have enough **sell-capable** items (the `"Sell"` FG
plus every `"Both"` item) and **buy-capable** items (every `"Both"` item): the sales scripts
`011_…`/`012_…` need ≥3 sell-side products and the PO scripts `008_…`/`009_…` need ≥2 buyable —
any realistic tree clears both with room to spare. (A `"Both"` sub-assembly is safe: the old worry
that a purchasable finished-good-style item trips BOM-create was the missing `doc_wip_store`, now
fixed.)

**Consistency + prices.** A sub-assembly must use the **same `unit` and per-unit `price`** wherever
it appears — as its own `[BOM.FG]` and as a `child_bom` line in its parent — because reused names
collapse to a single inventory item and the child-link lookup matches on item **+ unit**. Any item
name reused across blocks must agree on `type` and `unit` (first occurrence wins on price). `qty` is
how much of that item this recipe consumes/produces; `price` is the value of that `qty` in INR
(per-unit = `price / qty`). Choose realistic `unit`s (Nos, Pcs, Kg, Gms, Metres, Sqft, Set, Litres,
Sheets, …). The 003 script creates one inventory item per unique name at the per-unit price and
auto-attaches the company's default GST — **do not** put a tax field in the BOM (003 fails fast if
the account has no GST master).

**Flat is still an option.** If the teammate just wants a quick, non-realistic seed, a **flat set of
single-level BOMs** — each finished good → a few bought raw materials, no `child_bom` — still works.
Offer it, but the **default is the realistic multi-level tree** above.

Every name, item, unit, and price must be **relatable to the stated industry**. The examples below
exist to show that **structure is derived from the good** — notice how differently each one nests.
They are illustrations of the method, **not** shapes to reuse; design the tree for the actual good
you're given, even if it looks nothing like any of these.

- *Table fan (electro-mechanical → deep, nests at the drivetrain).* 3 levels: **Stator** ←
  copper winding wire, stamping core, insulation (bought) → rolls up into **Motor Assembly** ←
  stator + bearing, capacitor, shaft, housing → rolls up into **Table Fan** ← motor + blade,
  guards, base, regulator, carton, fasteners. The depth lives entirely in the motor; the rest of
  the fan is flat bought parts. (This is the shape in `data.md.template`.)
- *Cotton shirt (cut-and-sew → shallow, nests at a stitched component).* 2 levels: **Collar** ←
  collar fabric + interlining + thread (bought/consumed) → rolls up into **Shirt** ← collar + cut
  body panels (from bought Fabric, Metres), buttons, sewing thread, care label, polybag. Fabric is
  a bought raw material consumed by the metre; only the collar is a real sub-assembly.
- *Garam masala pack (process/FMCG → shallow, nests at an intermediate blend).* 2 levels:
  **Spice Blend** ← turmeric, coriander, cumin, chilli (bought by Kg) → rolls up into **Masala
  100g Pack** ← spice blend + stand-up pouch + label. Quantities are fractional weights; packaging
  is leaves. No hardware, no deep tree — the "manufacturing" is the blend.
- *Plumbing repair kit (assortment → genuinely flat, no sub-assembly at all).* 1 level: **Repair
  Kit** ← washers, O-rings, PTFE tape, adapters, screws — all bought and just packed together.
  Some goods legitimately don't nest; don't invent sub-assemblies that aren't real.

(A **flat single-level seed** — every finished good → a few bought raw materials, no `child_bom` —
is also the quick opt-in alternative when a teammate wants something fast rather than realistic;
e.g. a window maker: Sliding Window / Glass Door (`"Sell"`) each ← aluminium section, float glass,
handle set, door lock, gasket, screws (`"Both"`).)

Show the generated finished good(s) + the BOM tree (indented so the nesting is visible) to the
teammate so they can accept, edit individual entries, or override entirely. If they decline to give
an industry, fall back to the template defaults for the counter-party names and the BOM.

**Default to the realistic multi-level tree; offer flat as the quick alternative.** When you present
the BOM(s) for confirmation — before any script runs — lead with the multi-level tree you designed
(showing which parts are made-in-house sub-assemblies vs. bought leaves, and how deep it nests), and
mention that a flat single-level seed is available if they'd prefer something quick. Only drop to
the flat shape if the teammate asks for it.

### Step 4 — Confirm the final seed values with the user

Before running anything, print a clean summary of every value that will be written to `data.md`,
covering: credentials, company profile, owner contact, counter-party company names, the
**BOM tree** — show it indented so the nesting is visible (the top finished good, each
sub-assembly under its parent, and the bought leaves), with qty, unit, name, price-for-qty and
type per row, and mark which parts are sub-assemblies (`child_bom`) — and the company logo
(`LOGO_PATH`, or "none — logo step skipped" when empty). Mark which values came from the user vs. which fell back to defaults. **Do not include any
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

`013_upload_company_logo.py` is a no-op when `LOGO_PATH` is empty in `data.md` — it logs that the
upload was skipped and exits `0`, so it is safe to always include in the run order.

**Throttle-safe pacing — YOU (the agent) manage this, not the scripts.** The scripts make raw API
calls and do **no** rate-limit handling of their own; pacing and recovery are your job. Leave a
**few seconds between scripts** (≈3s is a fine default) to keep the burst under the backend's
per-minute limit. Use your judgment: if you start seeing throttling, **space the scripts out more**;
if everything's flowing, a short spacer is enough.

**Handling throttle / rate-limit errors — react intelligently.** The Tranzact backend rate-limits
bursts of calls, usually surfacing as **HTTP 429** (or a body mentioning "throttled" / "rate limit"
/ "too many requests"), most often around the 5th–6th script. When a script fails this way, do
**not** treat it as fatal — reason about it and recover:

1. **Wait the right amount.** Look for a hint in the response — a `Retry-After` header, a
   `retry_after` / `wait_seconds` field, or a message like `"available in 42 seconds"` — and wait
   that long (round up, +2s). No hint → wait ~30s. If it's already thrown twice, wait longer.
2. **Re-run the same script** (don't skip ahead), then continue. Note that scripts aren't
   idempotent, so a re-run redoes that script's work — prefer to *avoid* throttling by pacing over
   leaning on re-runs, and only re-run the one script that failed.
3. **Adapt.** If throttling keeps happening, increase the gap between scripts for the rest of the
   run. If the *same* script throttles to failure twice in a row even after waiting, stop and report
   it — the backend may be degraded.

(Rationale: this is a skill — keep the scripts as lean, deterministic automations and let the agent
handle situational things like rate-limiting with judgment, rather than hard-coding retry logic.)

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

So the only thing that halts the run is a failure in a **prerequisite** (`000`–`003`). A
**throttling** failure isn't fatal either — you wait and re-run that script (see throttle handling
above). Any `004`–`013` failure is skipped, logged, and reported — you get as much of the demo as
the backend allows, never an empty account.

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

Mention the OCs, units, and inventory were seeded. For the logo: if one was uploaded (from a
supplied file, or one you fetched from the company website), note it; if `LOGO_PATH` was empty,
note the account has no logo. **List every document step
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
   names, a realistic BOM tree, units, and prices. Do not mix items from unrelated industries. The
   `[[BOM]]` blocks are the sole source of truth for which inventory items and UoMs end up on the
   account — there is no separate `INVENTORY_ITEMS` config. **Size the BOM to the good, not to a
   fixed count** (see "Author the BOM tree"): decompose it into a realistic multi-level tree —
   made-in-house parts become sub-assemblies with their own BOMs, bought parts are leaves — deep in
   the 1–2 branches that matter, ~2–3 levels, scaling with the product. Type the **top finished
   good `"Sell"`** and **every sub-assembly and bought leaf `"Both"`**; that always clears the
   downstream minimums (≥3 sell-side, ≥2 buyable).
5. **Always confirm the full seed-value summary before execution.** Print every value about to be
   written to `data.md`, mark user-supplied vs. default, and wait for explicit go-ahead.
6. **Never run the scripts out of order or in parallel.** Each step assumes the prior step's
   server-side state.
7. **Never reuse an email.** Signup fails on duplicates. Always confirm with the teammate.
8. **Never ask the teammate to run code or install anything locally.** All execution happens inside
   the sandbox. Their job is to chat.
9. **`scripts/data.md` is sandbox-local and ephemeral.** It holds the demo's credentials but is
   discarded with the sandbox; no commit/storage concerns.
10. **Default to a realistic multi-level BOM tree; flat is the opt-in alternative.** Decompose the
    good into sub-assemblies (made-in-house) and leaves (bought), nesting via `child_bom` and
    ordering child blocks before their parents. Present that tree for confirmation and mention a
    quick flat seed is available; only build flat if the teammate asks for it.

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
  the BOM didn't include enough sell-capable items. Re-run from `000_` with a fresh email and a BOM
  that follows the typing rule — the top finished good `"Sell"` and **every sub-assembly and bought
  leaf `"Both"`** — which makes every item sell-capable, far above the ≥3 the script needs. 003
  auto-attaches GST, so as long as the account has a GST master under Settings → Tax Options, every
  item created will carry one.
- **PO steps (008–009) fail with "Need 2 buyable goods for supplier"** → the BOM had fewer than
  2 buy-capable items. Ensure sub-assemblies and leaves are typed `"Both"` (every `"Both"` item is
  buy-capable) — any realistic tree clears this — then re-run from `000_` with a fresh email.
- **`002` (units) aborts on HTTP 409 "already exists" for a base unit (e.g. `Nos`, `Kg`)** →
  environment-specific (seen on `mstag`), **not** universal — don't treat it as fatal and don't
  edit the bundled script (hard rule 1). On some envs a freshly-onboarded company's master-UoM list
  comes back empty while base units are **globally reserved**, so `002`'s create hits a 409 and,
  being a prerequisite, halts. Recover by hand and continue:
  1. **Seed the required units yourself** via the same endpoints `002` uses (read with
     `POST /api/v3/settings/master-uom/list`, then the master-uom create), **tolerating the 409** —
     a 409 "already exists" means that unit is satisfied, so move on. The create may return a
     **benign HTTP 500 from response serialization *after* the row is created**; confirm by
     re-posting (a 409 back = it exists) rather than treating the 500 as a failure.
  2. **Check the company's own `master_units`** (from `003`'s `get_details_for_add_items/`). If a
     reserved base unit like `Nos`/`Kg` still isn't attached to this company and no attach path is
     exposed, **remap that unit label in `data.md`** to an equally-standard one that creates and
     attaches cleanly (`Nos`→`Pcs`, `Kg`→`Kgs`) — leave item names, quantities, prices and BOM
     structure unchanged; only the label differs.
  3. **Re-run `003`** and carry on with the rest of the run. **Flag the remap in the Step 7 report**
     as an environment adaptation (not a data issue), and note it's worth reporting to the TranZact
     backend team (fresh-company UoM list empty + base units reserved-but-unattached). Do **not**
     bake any of this into `002` — it doesn't happen on every env.
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
- **BOM (`004`) 500 — how to isolate whether it's Tranzact's bug or our data** → the known payload
  cause (a missing `doc_wip_store`) is already fixed in `004`, and `"Both"` sub-assemblies create
  fine, so a 500 now is most likely backend-side. To confirm, on an env where the create is
  reachable test the **shipped `data.md.template` tree** (a known-good multi-level BOM): if that
  500s too, it's the endpoint — capture the full response body / server trace and hand it to
  Tranzact engineering. If only the generated BOM 500s, diff it against the template for a malformed
  row (bad unit, child block listed after its parent, inconsistent sub-assembly unit).
- **013 logo step fails with "LOGO_PATH file not found"** → the path in `data.md` doesn't resolve
  inside the sandbox. Confirm the image (a supplied file, or one you fetched from the company
  website and saved) is at that path, or clear `LOGO_PATH` to skip the logo. The rest of the
  account is already seeded — just re-run `013_` after fixing the path; do not re-run from `000_`.
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
