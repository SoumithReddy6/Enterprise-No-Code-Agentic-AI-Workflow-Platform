# Typed conditions (A5.1)

A condition node reads one value and takes its `true` or `false` branch. Before A5.1 the only test was "the text contains a phrase". A5.1 adds typed comparisons, so a workflow can route a purchase by its amount or a ticket by its priority.

Implementation: [`backend/app/conditions.py`](../backend/app/conditions.py). Tests: [`backend/tests/test_conditions.py`](../backend/tests/test_conditions.py), [`frontend/tests/conditions.test.ts`](../frontend/tests/conditions.test.ts) and [`frontend/tests/browser/condition-inspector.spec.ts`](../frontend/tests/browser/condition-inspector.spec.ts). Production scenarios: S17–S19 and live 7b/7c in [production scenario testing](production-scenario-testing.md).

## Operators

| Operator | Operand | True when |
| --- | --- | --- |
| `contains` (default) | `contains` | The text includes the phrase |
| `eq` / `ne` | `compare_to` | The value equals, or does not equal, the operand |
| `gt` `gte` `lt` `lte` | `compare_to` (a number) | Numeric comparison against the operand |
| `in` | `options` (a list of 1–100) | The value equals one of the options |
| `empty` | none | The value is null, blank text, an empty list or an empty object |

- `field` (optional) reads a key, or a dotted path of keys such as `order.amount`, from a JSON object value, for example an extraction agent's output. Paths read object keys only; a list on the path is an error, not an implicit index.
- `compare_as` (`text` or `number`) applies to `eq`, `ne` and `in`. Ordering operators always compare numbers.
- `case_sensitive` applies to text comparisons. Text comparisons ignore leading and trailing whitespace, which model output often carries.

## Semantics

- **Numbers are exact decimals.** `0.30000000000000004 > 0.3` is true, and integers beyond 2^53 compare correctly. A number is a JSON number or text such as `1200`, `-5`, `.5` or `2.5e3`. `1,200`, `$1200`, `NaN`, `Infinity`, booleans and blank text are not numbers.
- **Text is the default for equality.** `"02134" eq "2134"` is false unless `compare_as` is `number`, so identifiers such as zip codes, order numbers and version strings are never silently compared as numbers.
- **JSON values become text predictably.** `true` becomes `true`, `15` becomes `15`, and `15.0` becomes `15.0`. Use `compare_as: number` to compare numbers by value.
- **`empty` never treats `0` or `false` as empty.**

## Errors, not silent branches

An invalid comparison never quietly takes the `false` branch.

- **At validation**, before a run is created, the reason is shown as written. Examples: `gt compares numbers, but compare_to 'a thousand' is not a number.`, `The in operator needs options.` and `contains is not used by the gt operator; remove it.` Other node types keep their generic configuration message, because their settings can hold secrets.
- **At run time**, a value that cannot be compared fails the run with the reason, and neither branch runs. Examples: text where a number is needed, a missing field, a value that is not JSON when a field is set, or a list compared as text. A condition cannot use `on_error: continue` or `route`, as before: with no branch chosen, there is no next node.

## Compatibility

A saved condition, `{contains, case_sensitive}`, is valid unchanged; no migration rewrites it. A reference copy of the original implementation is checked against the new one on a fixed Unicode corpus and 20,000 seeded random cases, covering casefolding cases such as `ß`/`SS`, `İ`, ligatures and combining marks. The only behaviour change is for non-text values, such as a list, which the old node crashed on: they now either compare as documented here or fail with a named error, instead of an unexpected-error message.

The inspector shows only the settings the chosen operator uses. Switching operators removes the previous operator's operand, so a stale value cannot invalidate the node.

## Deliberately not in A5.1

- **`matches` (regular expressions).** Python's `re` has no timeout, so a pattern in a saved workflow could block a worker (ReDoS). It needs a linear-time engine or a bounded executor, designed on its own.
- **Lists as condition input.** The input port is still declared as text. Binding a list, such as Jira's `items`, works with `empty` but shows the existing advisory type warning.
- **Multi-way routing** is A5.2. **Parallel branches and joins** are A5.3.
