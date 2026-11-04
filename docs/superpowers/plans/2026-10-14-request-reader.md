# Request reader: one node that reads, extracts and checks a request

> **Status: implemented; measured. Safety gate passes, admission gate fails (9/20).**
> See the [measurement review](../../reviews/request-reader-2026-10-14.md).
> Baseline: `326647d` (request admission, Step 8).
>
> Deviations from this plan, made during implementation:
> 1. Section 6.3 was dropped. An unchecked field could flow downstream, so every field still
>    needs a rule; the editor's suggestions add an `accept_quoted` rule for each new field.
> 2. An optional field the request does not state may pass as `absent`, only beside at least
>    one passed field. A cross-check that cannot compare because a field it actually reads
>    is absent is not applied; fields that `coalesce` does not reach never disable it.
> 3. New rule: when a field passes by `accept_quoted`, every number in the request must be
>    read (`unread_number`). Added after a live run showed a hidden contradiction.
> 4. The reader has a fifth output, `reading`. A review pause keeps the reading, so resuming
>    makes no second model call. An omitted field reads as null; a schema-refused value gets
>    a code reply, not a repair.
> 5. Record reads that supply the reader's reference run before it (the graph allows one
>    flow edge out of the input), so a prepared reply in those templates makes one read.
> 6. `fields_read_downstream` is a frontend function (`fieldsReadDownstream`); the editor is
>    its only user.
> 7. The release rule (section 12) was not applied; it is a decision for review.
> It changes parts of the [request routing and extraction checks plan](2026-10-10-request-routing-and-extraction-checks.md),
> whose Step 8 measurement failed its usability gate: 9/20 valid requests admitted, against 18/20 required.

## 1. Why

Step 8 proved that the safety boundary works. A test forced every invalid message through,
and all 50 stopped before a business branch or a send. But the system blocked most valid
work:

| Step 8 result | Value |
| --- | --- |
| Valid requests admitted automatically | 9/20 (gate: 18/20) |
| Valid requests sent to human review | 8 |
| Router mistakes | 22/60 |
| Model calls for each request | 2 (router, then extraction) |
| Latency p50 / p95 | 4.46 s / 8.10 s |

There are three causes:

1. **The router's decision is final.** A second model reads the same message as the
   extraction model. When it says "unclear" or "out of scope", the request stops, even if
   the extracted facts are complete and checked.
2. **A number found in the message never passes on its own.** In `verify_extraction`, a
   `quoted` rule proves origin only, so a quoted quantity or price stays "unverified" and
   goes to review. Purchases have no product list for numbers, so nearly every valid order
   needs a person.
3. **"Required" cannot express alternatives.** A purchase needs `total`, *or* `quantity`
   and `unit_price`. `required_fields` can only list fields that are all required.

## 2. The idea

Replace the entry router and the separate extraction Agent and check with one node, the
**Request reader**. It has one settings panel, which is the workflow's request contract.

```text
Before:  Input → Greeting cache → Request router (LLM) → Extraction Agent (LLM) → Extraction check → …
After:   Input → Request reader (prepared replies, then one LLM call, then code checks) → …
```

The rule stays the same: **the model suggests, the code decides.**

## 3. Settings: the request contract

| Setting | Meaning | Purchase example |
| --- | --- | --- |
| Purpose | What the workflow is for. 1–2,000 characters. Required. | "Handle purchase requests for office equipment." |
| Accepted / excluded requests | Short descriptions; at most 20 each | "Orders with an item and a price" / "Questions about past orders" |
| Prepared replies | Exact messages answered without a model (today's greeting list) | "hello" → "Hi! What would you like to order?" |
| Fields | JSON Schema of the values to extract | `item` text, `quantity`, `unit_price`, `total` numbers, all nullable |
| Required | Fields that must be present | `item` |
| Required, one of | Groups where one complete group is enough | `[total]` or `[quantity, unit_price]` |
| Field rules | How code checks each field (section 6) | `item`: reference list; numbers: `accept_quoted` |
| Cross-checks | Calculate check expressions | `total == quantity * unit_price` when all three are present |
| Model | Provider, model, credential, token limit | The same settings as an Agent |

The editor suggests **Fields** and **Required** from the nodes that read the reader's
output (section 8). The author confirms them.

## 4. What happens with each message

1. **Prepared reply.** If the message matches a prepared message exactly, after lookup
   normalisation, the node replies with no model call. This is today's greeting rule.
2. **One model call.** The model gets the purpose, the accepted/excluded descriptions and
   the field list as instructions, and the message as separate untrusted data. It returns
   one JSON object:

   ```json
   {"type": "request", "values": {"item": "laptops", "quantity": 3, "unit_price": 450, "total": null}, "reply": ""}
   ```

   `type` is `request`, `unclear` or `unrelated`. An invalid answer gets one repair, as today;
   a second invalid answer fails the node with no branch.
3. **Code checks the values** with the existing `verify_extraction`, extended in section 6.
4. **Code chooses the branch:**

| Check result | Model `type` | Branch |
| --- | --- | --- |
| Passed | `request` or `unclear` | **continue**, with a verification receipt |
| Passed | `unrelated` | **person decides** (the facts check out, but the model reads it as not a request) |
| Required facts missing, some values present | any | **reply**: ask for the missing facts, named by code |
| No values at all | `unrelated` | **reply**: the model's polite reply, labelled as a model reply |
| No values at all | `request` or `unclear` | **reply**: ask for the required facts |
| Values present but not confirmed, or a check failed | any | **person decides** (today's review pause) |

The model's `type` can never make a request continue automatically, and it can never stop
a request whose facts the code confirms as complete. The only exception is `unrelated`,
which sends that request to a person, not to rejection. This separates the facts, which code
checks, from the intent, which only the model can judge.

## 5. Outputs and handles

- Ports: `text` (the extracted values, as exact JSON), `branch`, `reply`, `report`. The
  same names as today's check node, so Calculate and Conditions bind as before.
- Handles: `continue` and `reply`. The review pause uses the existing typed
  `verification` review.
- Reserved metadata: the existing `_verification` receipt, and a `_reading` record for the
  validated model answer with its policy and input digests, so that replay makes no model call.

## 6. Changes to the checks

1. **`required_any`** (new): a list of field groups, at most 8 groups of 8 fields. The check
   passes completeness if every field in at least one group is present. `required_fields`
   keeps its meaning.
2. **`accept_quoted`** (new rule kind, explicit and per field): the field passes if its exact
   value is written in the trusted original message. It proves the value is written there,
   not its role. Use it for numbers together with a Calculate cross-check where possible.
   For text, a `reference_member` rule against real data is preferred. The editor shows
   this risk next to the rule.
3. **Fields read by nobody.** An extracted field that no rule and no later node uses is
   reported, but it does not block. Today such a field becomes `rule_missing` and forces
   review.

The existing `quoted`, `source_field`, `enum`, `reference_member` and cross-check rules keep
their behaviour. A receipt is still bound to run, node, input, value and policy digests.

## 7. Contract 2 and the old nodes

- The contract-2 entry rule becomes **Input → Request reader**. The reader must read the
  trigger message directly.
- `greeting_cache`, `request_router` and `verify_extraction` stay loadable and runnable for
  saved workflows, but leave the palette, as `prompt` and `llm` did. A contract-2 workflow
  that uses the old chain still validates.
- The contract-2 starter templates (purchase, support, refund, yes/no) use the reader.
- The rules that a write or a business decision needs a valid receipt, and that error edges
  and attachments cannot bypass the check, are unchanged.

## 8. Field suggestions in the editor

The compiler already knows which fields later nodes read: Condition `field` paths and
Calculate expression paths. A new function, `fields_read_downstream(workflow, node_id)`,
lists every field read from the reader's `text` output, and by which node. The editor shows:

> Suggested from this workflow: `quantity`, `unit_price`, `total` (read by *Order total*).

The author accepts, edits or ignores the suggestions. Nothing is added without the author.

## 9. Remove Redis from prepared replies

The reply always comes from the saved configuration; Redis only holds a copy that the code
compares with that configuration. Remove the Redis client, the Compose service, the CI
service, the readiness check and `COMMON_REPLY_REDIS_URL`. Prepared replies use the
configuration lookup that already exists as the fallback. Redis returns later in roadmap
step B2, for shared budgets and rate limits.

## 10. Evidence files

Step 8 added about 337,000 lines of report JSON to git, and `.git` is 26 MB. From now on,
commit the summary, the hashes and the gate result. Keep full reports as CI artifacts or in
an ignored folder, with their SHA256 in the summary. Do not rewrite history.

## 11. Tasks

Each task is one reviewed commit, with hosted CI green before the next.

**Task 1: check extensions.** Add `required_any` and `accept_quoted`, and change the
handling of unused fields, in `request_contracts.py` and `extraction_checks.py`. *Tests:*
alternative groups (total only, quantity with price, neither); `accept_quoted` for exact
numbers, number words and text; an invented value not found; a cross-check that disagrees;
an unused field that does not block; old configurations unchanged. Show that the tests fail
without the change.

**Task 2: the Request reader node.** Add `request_reader.py` and register the node.
- Reuse the prompt building from `request_router.py`, the repair from `validate_with_repair`,
  `verify_extraction`, receipts, `ReviewPause`, token accounting and model authorisation.
- *Tests:* every row of the section 4 table; the prepared reply with no model call; one repair
  and then failure; replay with no model call; cancellation and lease loss; a forced
  `request` type with invented values, which must pause, never continue.

**Task 3: contract 2 and the old nodes.** Change the entry rule and update the starter
templates. Hide the old nodes from the palette and keep them loadable. *Tests:* a contract-2
workflow validates with either chain; compiler bypass tests on both; registry fixture parity.

**Task 4: editor settings and field suggestions.** Add `fields_read_downstream`, the settings
form, and the risk note on `accept_quoted`. *Tests:* unit tests for suggestions, and a browser
test that builds a purchase workflow and accepts the suggestions.

**Task 5: remove Redis.** Remove the dependency, Compose, CI and readiness entries. *Tests:*
prepared replies give the same results without Redis; readiness no longer lists it.

**Task 6: measure.** Run the frozen 60-case suite unchanged, with the same held-out cases and
the same gates. Report against Step 8: admission, reviews, mistakes, unsafe cases, model
calls, tokens and latency.
- Run the deterministic fault profile: force `type: request` on all non-greetings and inject
  invented values. Zero sends and zero business branches remain required.
- Store evidence as section 10 describes.

## 12. Release rule

Contract 2 stays the default for new workflows only if Task 6 passes the frozen gates:

- 0 unsafe automatic actions;
- at least 18/20 valid requests admitted automatically;
- every prepared-reply case answered with no model call;
- all existing suites green.

If the gate fails, the new workflow default returns to contract 1 until a later
measurement passes. The gates and the expected labels do not change after a run.

## 13. Limits and risks

| Risk | Handling |
| --- | --- |
| The model extracts a wrong value that is written in the message (`item: "order"`) | `reference_member` for text; review when no reference exists |
| `accept_quoted` passes a number in the wrong role | Opt-in per field; Calculate cross-checks; measured. Not observed for numbers in earlier runs, but possible |
| The model types a real request as `unrelated` | Goes to a person, not to rejection; measured as review, not admission |
| One model now does two jobs | Measured on the same frozen suite; a combined prompt can be worse or better than two calls |
| Suggested fields are wrong | Suggestions only; the author confirms |

## 14. Decisions for review

1. Approve the section 4 table, especially "Passed + `unrelated` → person decides".
2. Approve `accept_quoted` as an explicit per-field opt-in.
3. Approve removing Redis from prepared replies.
4. Approve the evidence-file rule in section 10.
