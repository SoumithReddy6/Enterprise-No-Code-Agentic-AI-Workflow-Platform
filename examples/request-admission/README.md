# Request-admission draft workflows

These examples use contract 2 and the generic cache, router, Agent, verification,
Calculate, Condition and Response nodes. No example configures an external write.
They demonstrate the enforcement path; live model accuracy is measured in Step 8.
Older examples and saved contract-1 workflows remain loadable.

Select enabled models for both the router and Agent. The editor does this for a
newly created draft using the first enabled local model, or an enabled cloud model.
Imported files preserve their settings; select models after importing.

| Example | Request and meaning |
|---|---|
| `qa.json` | `How do AI workflows work?` — broad narrative Q&A; no business verification claimed. |
| `purchase.json` | `3 laptops at $450 each.` — extraction, record membership and role checks, then exact code calculation. Natural-language role ambiguity requires review. For an automatic fixture test, use `{"item":"laptops","quantity":3,"unit_price":450}` with matching independent product records. |
| `support.json` | `{"summary":"Invoice download is broken","category":"billing"}` — exact original fields plus allowed category, then a ticket-draft queue. No ticket is created. |
| `refund.json` | `{"order_id":"ORDER-1","amount":24}` — independent order membership and the exact requested amount, then an illustrative $100 threshold. This neither establishes refund entitlement nor sends money. |
| `yesno.json` | `Is record MEMBER-1 eligible?` — record identity plus a yes/no interpretation. The generic example requires human confirmation of eligibility; membership of a boolean in some other record proves nothing about this member. |

Purchase, refund and yes/no include a **read-only Jira search** for independent
reference records. Select its real connection and adapt its JQL/field pointers to
your record schema before running. The supplied project names are illustrative.
Missing connections or failed reads stop execution. Empty/mismatched records
reach verification review; they never become invented products or approvals.

`reference-fixtures.json` contains explicit test-only remote response shapes used
by the worker tests. It is not imported as workflow data and cannot issue a grant.

Prepared greetings are configured whole-message matches. `Hello!` uses no model;
`Hello, buy 3 laptops` does not match that greeting and reaches routing. Yes/no are
not greeting aliases. Router admission is separate from business approval.

Interpretation reviews accept/reject the exact saved payload. Decision reviews
choose true/false explicitly. Outgoing write approvals remain a separate kind.
Rejected or expired reviews authorize nothing. See [request contracts](../../docs/request-contract-interfaces.md).
