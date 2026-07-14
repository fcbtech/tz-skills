# Default investigation contract

Applied when the ticket does **not** carry its own `Investigation Contract` block. If the ticket includes one, follow that one silently — don't restate its rules in the output.

## Output shape

```
**Diagnosis**: <≤3 sentences>

Evidence:
- NR:    <NRQL one-liner + key result>
- DB:    <SQL + interpretation; cite tz-core model>
- Code:  <repo path + file:line if behaviour traced>
- Docs:  <tz-documentation/content/... reference if relevant>

Next support action: <one sentence>

Notes (only if non-obvious): ...
```

## Length cap

- **≤400 words total.** Hard cap. Cut Evidence rows that don't change the diagnosis.
- **≤3 sentences** for the Diagnosis line.

## Uncertainty markers

Inline, when a claim isn't directly supported by the evidence:

- `[unverified]` — claim wasn't checked but is consistent with what was checked.
- `[inferred from X]` — claim follows from X (cite the row/line/log).

## Citation discipline

- **NR**: include the NRQL one-liner. Show the key row(s), not the full result set.
- **DB**: include the SQL or a tight summary, the interpreted result, and the model file. `tz-core/<...>/models.py:<Class>` is the preferred citation form.
- **Code**: `<repo>/<path>/<file>.py:<line>` form. Only if you actually traced the behaviour.
- **Docs**: `~/Work/tranzact/tz-documentation/content/<path>` — check before concluding "this is a bug."

## What this output does NOT include

- **Code fixes** — never. Unless the user explicitly asked, stop after diagnosis + one next-action.
- **Patch-style "you should change X to Y."**
- **Speculative theories.** If you don't have evidence, say "[unverified]" or omit.
- **Restated rules from this contract.** The user knows the format.
- **A wrap-up paragraph.** End with `Next support action`.

## When to break the cap

- The ticket's own Investigation Contract overrides this one.
- The user explicitly asked for a longer write-up.
- The diagnosis genuinely requires showing a multi-row table (e.g. "5 documents had the wrong status; here they are"). Even then, keep prose tight.

## Stop condition

Once you've produced the Diagnosis + Evidence + Next support action, **stop calling tools.** Do not run additional queries "for completeness." If something is missing, the user will ask.
