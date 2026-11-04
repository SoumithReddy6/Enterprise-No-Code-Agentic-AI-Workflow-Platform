# Request-admission draft workflows

These examples use contract 2 and the Request reader, Agent, Calculate, Condition
and Response nodes. No example configures an external write. They demonstrate the
enforcement path; live model accuracy is measured separately.
Older examples, saved contract-1 workflows and saved workflows with the earlier
cache → router → check chain remain loadable.

The Request reader replies to prepared messages, reads the request with one model
call, then checks every value in code. Code, not the model, chooses the branch.
Select an enabled model for the reader (and for the Agent in `qa.json`). The editor
does this for a newly created draft using the first enabled local model, or an enabled
cloud model. Imported files preserve their settings; select models after importing.

| Example | Request and meaning |
|---|---|
| `qa.json` | `How do AI workflows work?` — broad narrative Q&A; no business verification claimed. |
| `purchase.json` | `3 laptops at $450 each.` or `laptops, $1,350 in total` — an item plus either a total or a quantity and unit price. Numbers must be written in the request (`accept_quoted`); a stated total must equal quantity × unit price. The item must match an independent product record. Code calculates the total. |
| `support.json` | `{"summary":"Invoice download is broken","category":"billing"}` — exact original fields plus allowed category, then a ticket-draft queue. No ticket is created. |
| `refund.json` | `{"order_id":"ORDER-1","amount":24}` — independent order membership and the exact requested amount, then an illustrative $100 threshold. This neither establishes refund entitlement nor sends money. |
| `yesno.json` | `Is record MEMBER-1 eligible?` — record identity plus a yes/no interpretation. The generic example requires human confirmation of eligibility; membership of a boolean in some other record proves nothing about this member. |

### `purchase-demo.json`: run it with no connections

`purchase.json` needs a Jira connection with product records. `purchase-demo.json` is the
same workflow without the record read: it needs only an enabled `llama3.1:latest` (or
select another model after import). Every other check is the same. Because no product
record is checked, any item that is written in the request is accepted.

These messages gave the same outcome in 5 of 5 runs with local `llama3.1:latest`:

| Message | Outcome |
|---|---|
| `Hello` | Prepared reply; no model call |
| `I need 3 laptops at $450 each` | Code calculates $1,350 → "exceeds $1,000" branch |
| `Order a desk for $800.` | $800 → "$1,000 or less" branch |
| `laptops for a total of $1,350` | The stated total is used → "exceeds" branch |
| `Buy some laptops.` | Code asks for a total, or a quantity and unit price |
| `I need 3 laptops at $450 each, total $1,000` | The cross-check fails → waits for a person |
| `What is the weather tomorrow?` | The model's reply; no business branch |

A small model is not always consistent. For example, `3 laptops at $450 each.` was often
read without the quantity, so the reader asked for details. That is safe, but not a
clean demo.

Purchase, refund and yes/no include a **read-only Jira search** for independent
reference records. It runs before the reader, which checks values against it, so a
prepared reply in these examples still makes one record read (but no model call).
Select its real connection and adapt its JQL/field pointers to
your record schema before running. The supplied project names are illustrative.
Missing connections or failed reads stop execution. Empty/mismatched records
reach verification review; they never become invented products or approvals.

`reference-fixtures.json` contains explicit test-only remote response shapes used
by the worker tests. It is not imported as workflow data and cannot issue a grant.

Prepared replies are configured whole-message matches. `Hello!` uses no model;
`Hello, buy 3 laptops` does not match and reaches the model reading. Yes/no are not
greeting aliases. The model's reading type never approves anything: a reading of
"unrelated" with confirmed facts goes to a person, and unconfirmed values go to a
person whatever the type.

Interpretation reviews accept/reject the exact saved payload. Decision reviews
choose true/false explicitly. Outgoing write approvals remain a separate kind.
Rejected or expired reviews authorize nothing. See [request contracts](../../docs/request-contract-interfaces.md).
