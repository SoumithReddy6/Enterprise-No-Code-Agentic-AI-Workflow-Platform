# A5.3 revision-3 design recheck — 2026-11-06

Reviewed commit: `89129ca564d201ebf4511dac7bf63522927641a5`.
Design: `docs/superpowers/specs/2026-10-24-parallel-branches-design.md`.
Previous review: `docs/reviews/a53-design-r2-review-2026-11-06.md`.
Evidence: `evals/results/a53-design-r3-review-89129ca/`.

## Verdict

Approved for tests-first Part 1 implementation. No blocking design finding remains from the four revision-2 findings. This is design approval, not implementation or release approval. The design is already committed as `89129ca`, despite the handoff saying it is uncommitted.

The small frontend binding change is appropriate within Part 1. Keep Join hidden in the palette and fork construction through the API until Part 2; there is no need to choose the more restrictive API-only editing alternative.

## Finding closure

| Finding | Revision-3 correction | Recheck |
| --- | --- | --- |
| A53-R2-01, snapshot freshness | Every newly produced full snapshot gets a `publication_sequence`; `spend_generation` retains its existing acknowledgement role. Frozen publications are not re-stamped later. | Correct. Action-only snapshots no longer depend on the model-only clock. All persistence paths and the compiler's synchronous sink feed the publisher. Only the committed publication's spend generation is acknowledged. |
| A53-R2-02, impossible test schedule | A01/A02 let B publish and wait while A owns the serializer; release A before expecting B's commit. A04 explicitly queues an older B publication behind A while a newer C publication exists. | Correct. A04 can distinguish the caller-snapshot implementation from the newest-publication rule. |
| A53-R2-03, side entry | Each branch node has exactly one incoming edge in total, from its expected predecessor, and exits only through the branch chain to its Join. V01 includes the outside Condition route. | Correct. The previously ambiguous internal-edge rule is replaced by a closed-region contract. |
| A53-R2-04, editor binding mismatch | Frontend `bindableOutputs` gets the Join union, checked through the existing shared fixture; the registry fixture regeneration is explicit. | Correct. The actual editor uses this helper. The change closes binding parity without adding Join construction controls or a new fixture contract. |

The allocation table adds up to 28 collected backend cases, including PostgreSQL variants. M1–M17 are present and each has a named oracle. Grouping validation shapes inside a case is acceptable as written, provided every shape retains its own assertion. The added frontend fixture case is also checked by the existing frontend test generator.

## Independent schedule check

`probe_publications.py` models the revision-3 publication/serializer protocol with the real `AccountingDurability` class. It is not a Worker implementation or database test.

With A holding the mutex, B publishes sequence 2, C publishes sequence 3, and C's waiter is cancelled. B and C have not entered the commit section before A is released. The shielded C writer remains alive.

Observed commits under the corrected policy: A commits publication 1; B commits publication 3; C commits publication 3. The second model-usage generation arriving during A's commit remains pending after A's acknowledgement.

Two negative controls establish useful oracles:

- Saving the caller's snapshot makes B commit publication 2. Its final state can still become current when C later commits, so A04 must inspect B's commit, not just the final total.
- Acknowledging the current generation instead of the committed publication's generation makes the pending check false immediately after A's commit. This is the independent M14 oracle.

Both distinctions are in the revised design; no further design edit is required. Carry these assertions into the real Worker/Store tests.

The lock and cancellation rules remain consistent with the published primitives: a task waits for the mutex, while shielding the inner task does not prevent the caller from receiving cancellation. The protected writer must therefore own the mutex through completion. [Python lock documentation](https://docs.python.org/3.12/library/asyncio-sync.html#asyncio.Lock), [Python cancellation shielding](https://docs.python.org/3.12/library/asyncio-task.html#shielding-from-cancellation).

Temporal's documented lock pattern supports shared-state serialization, while publication freshness and TA02 transaction/fencing remain Relay-specific obligations. Revision 3 now makes that distinction. [Temporal workflow message passing](https://docs.temporal.io/develop/python/workflows/message-passing#use-asynciolock-to-prevent-concurrent-handler-execution).

## Verification and next action

- Publication schedule probe: exit 0, corrected policy and both negative controls behave as expected.
- Current baseline tests: `test_binding_contract`, `test_request_examples`, `test_accounting_durability`: **59 passed**, with one Starlette/AnyIO deprecation warning.
- Static checks: exactly 28 backend cases and 17 named mutants; application/frontend source unchanged between revisions 2 and 3.
- No application or design edits were made during this recheck. Prior evidence was preserved.
- Full implementation suites, PostgreSQL crash tests, deployed scenarios and hosted CI were not rerun for this design-only review. They remain the delivery gates already specified in the design.

Proceed directly to the failing Part 1 tests, then the single feature-and-safety commit. No additional design or implementation-plan review round is needed before starting.
