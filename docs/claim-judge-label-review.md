# Claim-judge annotation review

These labels were assigned by the Codex assistant before running the judge. Independent human review is pending. They are diagnostic annotations, not human-labelled ground truth. Full passages are preserved in [the dataset](../evals/claim-calibration-labelled.json).

Review the question, answer, and cited passages together. The current judge does not receive the question, which limits evaluation of bare-number answers. “Supported” means textual support, not necessarily a correct answer to the original question.

## real-0

Question: What is the standard mileage rate for business use of a car in 2025?

Answer: 70 cents ($0.70) per mile [S1][S2][S3]

Assistant label: **supported**

The cited passages explicitly state the 2025 business mileage rate is 70 cents ($0.70) per mile.

## real-1

Question: Under an accountable plan, within how many days must you return any excess reimbursement?

Answer: 120 days [S3]

Assistant label: **supported**

The cited bullet explicitly requires excess reimbursement returned within 120 days.

## real-2

Question: What is the high-low per diem rate for high-cost localities effective October 1, 2025?

Answer: $319 (which includes $86 for M&IE) [S1]

Assistant label: **supported**

The cited passage states the October 2025 high-cost rate is $319 including $86 M&IE.

## real-3

Question: How much of the $319 high-cost per diem rate is for M&IE?

Answer: $86 [S1]

Assistant label: **supported**

The cited passage explicitly assigns $86 to M&IE.

## real-4

Question: What is the per diem amount for all other (non-high-cost) localities under the high-low method?

Answer: $225 (which includes $74 for M&IE) [S1]

Assistant label: **supported**

The cited passage gives the other-locality per diem as $225 including $74 M&IE.

## real-5

Question: What percentage limit generally applies to meal expenses?

Answer: 50% [S1]

Assistant label: **supported**

The cited passage explicitly applies a 50% limit to meals.

## real-11

Question: What is the minimum password length when a password is used as a single-factor authentication mechanism?

Answer: 15 characters [S1]

Assistant label: **supported**

The cited numbered rule requires at least 15 characters for single-factor passwords.

## real-12

Question: What is the minimum length for passwords used only as part of multi-factor authentication?

Answer: 8 characters [S1]

Assistant label: **supported**

The cited numbered rule permits eight characters for multi-factor passwords.

## real-14

Question: May verifiers require subscribers to change passwords periodically?

Answer: No, verifiers SHALL NOT require subscribers to change passwords periodically. [S1]

Assistant label: **supported**

The cited rule explicitly prohibits periodic password changes except compromise; the answer states the general rule.

## real-18

Question: When measuring password length, how is a single Unicode code point counted?

Answer: Each Unicode code point SHALL be counted as a single character when evaluating password length [S2].

Assistant label: **supported**

The cited rule explicitly counts each Unicode code point as one character.

## real-21

Question: How many consecutive hours off duty must a property-carrying driver take before driving at the start of a work shift?

Answer: 10 consecutive hours [S1][S2]

Assistant label: **supported**

Both cited passages state 10 consecutive off-duty hours; this supports the fragment.

## real-26

Question: How often must an evaluation of each powered industrial truck operator's performance be conducted?

Answer: at least once every three years [S1]

Assistant label: **supported**

The cited rule explicitly says at least once every three years.

## failure-23

Question: What is the on-duty limit over 8 consecutive days for a trucker whose company runs vehicles every day of the week?

Answer: 80 hours [S1]

Assistant label: **supported**

The isolated cited excerpt explicitly says 80 hours in eight days. This is a known wrong answer for the question's intended general rule, but the missing section scope makes its literal citation support indistinguishable from a correct quotation. A citation-only judge cannot recover lost source context.

## failure-24

Question: How long must a driver stay off duty to restart the 7- or 8-day on-duty clock?

Answer: at least 8 consecutive hours [S4]

Assistant label: **insufficient_evidence**

The cited text states eight hours after a passenger-carrying driver's run, not the weekly 7/8-day clock restart requested by the question. It does not support that answer in the question's scope. The current judge does not receive the question, a deliberate calibration limitation.

## failure-33

Question: What is the maximum load weight a forklift is allowed to lift?

Answer: 30,000 pounds [S3]

Assistant label: **contradicted**

The cited text explicitly discusses trucks with capacity greater than 30,000 pounds, contradicting a universal maximum of 30,000 pounds; it says the data plate determines the actual limit.

## failure-35

Question: How many days of paid vacation do federal employees receive each year?

Answer: 21/2 days [S2]

Assistant label: **insufficient_evidence**

The 2.5 days in the cited text are meal-allowance days for a trip, not federal employees' annual paid vacation. The passage does not support the answer in the question's scope.

## counter-mileage

Question: What is the standard mileage rate for business use of a car in 2025?

Answer: The 2025 business mileage rate is 50 cents per mile [S2].

Assistant label: **contradicted**

S2 explicitly states 70 cents, not 50.

## counter-return

Question: Under an accountable plan, within how many days must you return any excess reimbursement?

Answer: Excess reimbursement must be returned within 30 days [S3].

Assistant label: **contradicted**

S3 assigns 30 days to receiving advances and 120 days to returning excess reimbursement.

## counter-password

Question: What is the minimum password length when a password is used as a single-factor authentication mechanism?

Answer: Single-factor passwords need only 6 characters [S1].

Assistant label: **contradicted**

S1 requires a minimum of 15 characters.

## counter-evaluation

Question: How often must an evaluation of each powered industrial truck operator's performance be conducted?

Answer: Operator performance must be evaluated at least every five years [S1].

Assistant label: **contradicted**

S1 requires evaluation at least once every three years.

## unsupported-bank

Question: Under an accountable plan, within how many days must you return any excess reimbursement?

Answer: The company payroll bank is Northstar Bank [S3].

Assistant label: **insufficient_evidence**

No bank is identified in the expense timing passage.

## unsupported-vacation

Question: What is the high-low per diem rate for high-cost localities effective October 1, 2025?

Answer: Federal employees receive 25 paid vacation days per year [S1].

Assistant label: **insufficient_evidence**

The passage discusses travel per diem rates, not leave entitlements.

## unsupported-password-storage

Question: What is the minimum password length when a password is used as a single-factor authentication mechanism?

Answer: Passwords are stored using AES-256 encryption [S1].

Assistant label: **insufficient_evidence**

The cited passage states password rules but gives no storage encryption algorithm.

## unsupported-salary

Question: How often must an evaluation of each powered industrial truck operator's performance be conducted?

Answer: Forklift operators earn 30 dollars per hour [S1].

Assistant label: **insufficient_evidence**

The cited sentence specifies evaluation frequency, not pay.
