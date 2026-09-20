# IRS 60-day / 120-day paraphrase diagnosis

Inspected 2026-09-18. This is an offline inspection of saved evaluation passages and a local reconstruction of current chunks; no retrieval tuning, embedding, reranker, or generation calls were made.

## Conclusion

There are two different problems in the preferred working candidate, `evals/results/f11-after.json`: the 120-day bullet is separated from its explanatory list stem, while the 60-day bullet already retains that stem but is not selected for the accounting paraphrase. More general or adjacent passages occupy the four returned slots. In the newer `f11-after-boundaries.json` and `f11-after-review.json`, the entire safe-harbour list and stem are together, yet both paraphrases still miss them. Thus list fragmentation is real in the older candidate but does not fully explain the misses, and has already been eliminated for this list in the current implementation. The saved data do not establish whether the current complete chunk falls outside candidate generation or loses at reranking.

All row identifiers below are **zero-based indices into `generation.rows`**, so row 7 is the eighth question and row 10 the eleventh. All pages refer to `irs-publication-463-travel-gift-car-expenses.pdf` (2025 Publication 463).

## Source evidence and what the questions need

The extracted source `/tmp/irs-publication-463-travel-gift-car-expenses.json`, element 42 (PDF page 43), contains the inline heading **Reasonable period of time.** The paragraph first says the definition depends on facts and circumstances, then says actions within the following times will be treated as within a reasonable period:

- Receive an advance within 30 days of having an expense.
- Adequately account for expenses within **60 days after they were paid or incurred**.
- Return excess reimbursement within **120 days after the expense was paid or incurred**.
- Receive a periodic statement, at least quarterly, asking to return or account for outstanding advances, and comply within **120 days of the statement**.

The stem matters: these are stated safe-harbour periods, not an unconditional assertion that every later action fails the reasonable-period test. The two 120-day provisions have different triggers. The generic rule on page 42 says to account and return excess within a reasonable period, but does not supply these numeric periods. Page 46's **Returning Excess Reimbursements** similarly discusses unused advances and points back to the earlier reasonable-period discussion.

## Row 7: unused advance / 120 days

Question: “How long do I have to hand back money my employer advanced me for a trip that I did not end up spending?”

| Saved artifact | Selected evidence and result |
| --- | --- |
| `f11-before.json` | S1 is the page 46 Phoenix trip example; S3 is page 46 travel-advance prose saying to return excess “within a reasonable period of time.” Other slots are page 9 and a non-IRS page 1. Answer cites S3 and gives only a reasonable period. `answer_retrieved=false`. |
| `f11-after.json` (preferred candidate) | All S1–S4 are page 46 under **Returning Excess Reimbursements**. S1 begins mid-sentence, “1), you must return this unproven amount ... within a reasonable period of time”; S2 begins “ce before you actually have the expense”; S3 is the Phoenix example; S4 defines excess reimbursement and says adequate accounting and reasonable period “were discussed earlier in this chapter.” The answer cites S1 and gives only a reasonable period. No page 43 deadline is returned. `answer_retrieved=false`. |
| `f11-after-boundaries.json` and `f11-after-review.json` | The row's saved passages and answer agree across both artifacts. S1 and S3 are page 46 **Returning Excess Reimbursements > Per diem allowance more than federal rate**; S2 is page 46 **Returning Excess Reimbursements > Travel advance**, containing the complete generic reasonable-period rule; S4 is an unrelated page 8 travel-day prorating example. Answer cites S2 and still gives only a reasonable period. `answer_retrieved=false`. |

Concrete missing context: the page 43 return-of-excess bullet and its reasonable-period stem. The retrieved passages strongly match “advance,” “trip,” and unused money but stop at the cross-reference/general rule. The passage selection is a failure even after the source list becomes complete in one chunk.

## Row 10: accounting / 60 days

Question: “How quickly must I give my employer an accounting of my expenses for the reimbursement plan to count as accountable?”

| Saved artifact | Selected evidence and result |
| --- | --- |
| `f11-before.json` | S1 page 43 **Adequate Accounting** explains records/receipts; S2 page 42 **Accountable Plans** says to adequately account within a reasonable period; S3 page 46 and S4 page 43 concern nonaccountable plans / failure to return excess. Answer is “within a reasonable period of time,” citing S2. `answer_retrieved=false`. |
| `f11-after.json` (preferred candidate) | S1 page 43 **Accountable Plans** starts with the quarterly-statement 120-day bullet, then merges employee-plan outcome prose. S2 page 43 **Adequate Accounting** is generic accounting procedure. S3 page 42 **Accountable Plans** gives the three generic requirements. S4 page 43 **Accountable Plans** is only the excess-return 120-day bullet. Answer combines a reasonable accounting period with returning excess within 120 days; it does not give the requested 60-day accounting period. `answer_retrieved=false`. |
| `f11-after-boundaries.json` and `f11-after-review.json` | Both artifacts return identical passages here: S1 page 43 **Adequate Accounting**; S2 page 42 **Accountable Plans**; S3 page 46 **Returning Excess Reimbursements > Travel advance**; S4 page 43 **Accountable Plans > Reimbursement of nondeductible expenses**. Answer cites S2 and says only a reasonable period. No 60-day bullet is selected. `answer_retrieved=false`. |

Concrete missing context: the 60-day accounting bullet and the clock starting when expenses were paid or incurred. In the preferred candidate, the alternative statement rule and excess-return rule distract from the requested accounting deadline. The saved answer does not explicitly claim 120 days is the accounting deadline, but omits 60 days and adds a different obligation.

## Where the correct evidence actually appears

Saved **row 1** asks the direct question, “Under an accountable plan, within how many days must you return any excess reimbursement?” This provides evidence the relevant text exists in the indexed source:

- `f11-before.json`, row 1 S3, page 43: a fixed-window passage begins with a partial stem (“ithin the times specified ...”) and contains all four bullets, then employee-plan prose.
- `f11-after.json`, row 1 S1, page 43: **Accountable Plans** followed solely by “• Y ou return any excess reimbursement within 120 days after the expense was paid or incurred.” The qualifying stem is absent from that passage.
- `f11-after.json`, row 1 S4, page 43: **Accountable Plans**, the preceding excess-reimbursement definition, the full reasonable-period stem, the 30-day bullet, and the **60-day bullet**. Therefore the 60-day evidence was not separated from the stem; it was packed with additional introductory material. The 120-day return bullet is in a separate chunk, and the quarterly-statement bullet in another chunk with following prose.
- `f11-after-boundaries.json` and `f11-after-review.json`, row 1 S1, page 43: **Accountable Plans > Reasonable period of time**, the full stem, and **all four bullets together**.

The latter passage demonstrates that the newer representation fixes this particular list's context boundary. It does not demonstrate successful retrieval for the paraphrases, which still return different passages.

## Current implementation check

Read `backend/app/kb/section_chunking.py`; SHA-256 at inspection: `82527dc829fa88fe1ff258f02eb243e2c0469950e00e310eb2fa1345ad77ada7`.

Ran only `section_chunks(pages, 800, 120)` locally against the extracted 62-page JSON. The resulting zero-based chunk **403**, page **43**, has heading path `['Accountable Plans', 'Reasonable period of time']` and a **760-character body** containing the complete reasonable-period paragraph and all four bullets. This matches the safe-harbour text saved in newer row 1 S1. Chunk indices are local reconstruction identifiers, not durable retrieval IDs.

The current inline-heading recognition flushes preceding prose at “Reasonable period of time.” Each bullet starts a new paragraph unit, but `append` packs those units into the same buffer while the size permits. It does **not** flush for every bullet. Here the 760-character body fits within 800, so no stem/list separation occurs. This is a specific observed fit, not a general guarantee that arbitrarily long lists retain their stems. Headings are prefixed separately by `contextual_text`.

No current ranking was computed. Identical saved source text and a reconstructed current chunk do not prove that every artifact used precisely the same code revision.

## Historical ranking probe: supporting evidence only

`/tmp/f11-diagnostics.json` contains older chunk text matching the preferred candidate's fragmented representation, not the current complete-list representation. Treat these as **historical probe results**, not current ranks or a replay of every saved hybrid top-four order:

- For the unused-advance question, the isolated 120-day target has rank **13**, score **−4.9059**; the leading generic page-46-text match scores **1.3433**.
- For the accounting question, the chunk containing the full stem plus 30-/60-day bullets has rank **6**, score **2.9831**. The quarterly-statement chunk scores **5.1880**, generic **Adequate Accounting** **3.8899**, generic page-42 **Accountable Plans** **3.8689**, and the isolated excess-return 120-day bullet **3.5551**.

The probe supports ranking competition in the older representation and directly refutes the claim that the 60-day miss must be caused by losing its list stem. Its scores cannot establish current candidate membership or rank after the chunk changes.

## Boundary for subsequent work

No further tuning is justified solely by assuming the list stem is still missing. Before selecting a retrieval change, inspect stage-by-stage candidate membership and rank of the current complete page 43 chunk for these two exact paraphrases. The saved top-four passages establish omission at final selection; they do not isolate which retrieval stage caused it. This document records that diagnostic need without changing retrieval or launching a new evaluation.
