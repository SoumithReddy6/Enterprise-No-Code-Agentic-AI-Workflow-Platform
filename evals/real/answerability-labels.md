# Expanded answerability labels

Frozen before new score measurement, 2026-09-18. `questions-expanded.json` contains the original 38 rows with every field unchanged, followed by 15 manually reviewed unanswerable cases: 8 calibration and 7 holdout. There are 33 original answerable and 20 total unanswerable questions. Indices below are zero-based.

## Label meaning and review method

Unanswerable means the requested exact fact cannot be established from the four supplied corpus files. It does not mean the fact cannot exist elsewhere or that the system cannot explain a related general rule. A response giving general advice, a nearby numeric limit, or a source's external URL has not supplied the missing requested fact. Cases with unsupported universal requirements should explain the absence of a universal figure/product rather than fabricate one; the runtime's abstention representation is still an evaluation convention, not the only useful user-facing response.

Manual review used the saved extracted page arrays `/tmp/irs-publication-463-travel-gift-car-expenses.json`, `/tmp/nist-sp-800-63b-4-authentication.json`, `/tmp/49-cfr-part-395-hours-of-service.json`, and `/tmp/29-cfr-1910.178-powered-industrial-trucks.json`, corresponding to the two PDFs and two HTML documents under `evals/real/`. PDF references below are physical PDF pages, not printed NIST page labels. Each HTML extraction has one page; section identifiers supply the useful location. Read the neighboring substantive rules and searched the complete extracted documents for the absent dates/entities/parameters; labels are based on missing requested evidence, not just missing query wording. No web sources, model judgments, embeddings, reranker calls, or answer generation were used.

Whole-corpus case-insensitive checks found zero occurrences of `Okta`, `Northstar Robotics`, `Elena Ruiz`, `FL-2048`, and `bcrypt`. In IRS, the sole `2027` occurrence is a lease inclusion example on page 33; Seattle appears only in a page 10 travel example. The driving text has no Texas or intrastate rule text and no civil penalty schedule. The forklift text contains no voltage or mph value, and its training clause gives modalities rather than a fixed classroom duration. These checks support the specific rationales below; absence from extracted text is bounded by extraction quality.

## Fixed split

New indices 38–45 are calibration; 46–52 are holdout. The original five unanswerables (33–37) are legacy calibration cases for threshold selection; their dataset fields remain unchanged to preserve all 38 original rows exactly. Do not silently put the seven holdout cases into threshold fitting. Do not change these labels, splits, or questions after seeing scores without recording a dataset revision. Once holdout outcomes influence a later choice, they are no longer untouched holdout evidence.

Both sets include IRS, NIST, driving, and forklift subjects. The new questions cover unsupported dates, an external locality table, jurisdiction mismatch, missing employer/tenant/driver/equipment/transaction records, missing penalty data, and unsupported exact or universal requirements.

## New case review

### Index 38 — calibration — unsupported_date

What is the IRS standard mileage rate for business driving in 2027?

**Label:** unanswerable. **Corpus rationale:** IRS p1 states the 2025 rate; the only 2027 occurrence is p33 in a lease inclusion example, not a mileage-rate schedule.

### Index 39 — calibration — unsupported_exact_parameter

What exact bcrypt work-factor number does NIST SP 800-63B-4 require every password verifier to use?

**Label:** unanswerable. **Corpus rationale:** NIST PDF p27 recommends a cost factor as high as practical without negatively impacting performance; it supplies no universal bcrypt work-factor number.

### Index 40 — calibration — jurisdiction_mismatch

What is the maximum daily driving time under Texas rules for a truck driver operating exclusively within Texas?

**Label:** unanswerable. **Corpus rationale:** 49 CFR Part 395 supplies federal driving rules, including section 395.3, but no Texas intrastate rules; Texas and intrastate are absent from that extracted document.

### Index 41 — calibration — unsupported_exact_limit

What is the maximum forklift speed in miles per hour permitted in every warehouse under 29 CFR 1910.178?

**Label:** unanswerable. **Corpus rationale:** Section 1910.178(n)(1) refers to authorized plant speed limits and (n)(8) requires safe stopping; neither provides a universal mph value.

### Index 42 — calibration — missing_external_table

What is the federal lodging per diem rate for Seattle for the night of May 12, 2025?

**Label:** unanswerable. **Corpus rationale:** IRS p8 points to GSA locality tables rather than including Seattle lodging rates; its Seattle mentions on p10 are a travel example, not rate data.

### Index 43 — calibration — missing_entity_configuration

After exactly how many failed password attempts is our company's Okta tenant currently configured to lock an account?

**Label:** unanswerable. **Corpus rationale:** NIST p41 states a generic ceiling on consecutive attempts; it does not contain this company tenant configuration, and Okta is absent from the corpus.

### Index 44 — calibration — missing_penalty_schedule

What is the exact federal civil fine in dollars for a first violation of the 11-hour property-carrying driving limit?

**Label:** unanswerable. **Corpus rationale:** Part 395 section 395.3 supplies the driving limit, but the corpus has no civil penalty dollar schedule for this violation; civil and penalty are absent from the extracted Part 395 text.

### Index 45 — calibration — unsupported_exact_duration

How many classroom hours does OSHA require for every initial forklift operator training course?

**Label:** unanswerable. **Corpus rationale:** Section 1910.178(l)(2)(ii) requires formal instruction, practical training, and evaluation; the supplied section does not specify a universal classroom-hour count.

### Index 46 — holdout — missing_employer_policy

What nightly hotel reimbursement cap does Northstar Robotics apply to its employees on business trips?

**Label:** unanswerable. **Corpus rationale:** Publication 463 discusses tax treatment and reimbursement plans, not this employer policy; Northstar Robotics is absent from all four source texts.

### Index 47 — holdout — missing_enumerated_data

What is the complete list of password strings that NIST SP 800-63B-4 requires every service to block?

**Label:** unanswerable. **Corpus rationale:** NIST pp25-26 defines blocklist requirements and categories, including service-specific words, without enumerating a complete universal list of strings.

### Index 48 — holdout — missing_individual_records

How many driving hours does driver Elena Ruiz have left today after her last eight days of work?

**Label:** unanswerable. **Corpus rationale:** Part 395 supplies duty limits and section 395.8 record requirements, but no duty log for Elena Ruiz; her name is absent from the corpus.

### Index 49 — holdout — missing_equipment_specification

What battery charging voltage should be selected for the forklift with serial number FL-2048?

**Label:** unanswerable. **Corpus rationale:** Section 1910.178(g) describes charging-area safety; the corpus has no manufacturer specification or serial record for FL-2048 and no voltage in the extracted forklift section.

### Index 50 — holdout — missing_transaction_record

On what date did my employer deposit the reimbursement for my March 2025 business trip?

**Label:** unanswerable. **Corpus rationale:** Publication 463 includes reimbursement rules and illustrative trips, but no personal payroll or bank transaction record establishing this deposit date.

### Index 51 — holdout — unsupported_product_requirement

Which specific commercial password manager product does NIST SP 800-63B-4 require all verifiers to use?

**Label:** unanswerable. **Corpus rationale:** NIST p26 requires allowing password managers and autofill, and recommends paste support; it does not designate one mandatory commercial product.

### Index 52 — holdout — missing_equipment_capacity

What is the rated lifting capacity in pounds of the forklift with serial number FL-2048?

**Label:** unanswerable. **Corpus rationale:** Section 1910.178(o)(2) limits loads to truck rated capacity, but supplies no capacity plate or equipment specification for FL-2048.

## Limitations

This is a small, manually authored challenge set, not a representative random sample of production questions. The seven holdout negatives give coarse estimates; one outcome changes the holdout rate by roughly 14 percentage points. All new cases are negative, so this expansion improves negative coverage but does not add independent positive validation. The original 33 positives retain known substring-based evaluation limitations and ambiguous historical wording, which this task deliberately does not change.

Some negatives can support a helpful partial explanation (for example, observe plant speed limits, consult a capacity plate, or distinguish a generic NIST ceiling from a tenant setting). Treating all such responses as required abstentions measures the chosen corpus-grounding contract, not overall conversational usefulness. Missing personal records are a distinct failure mode from difficult semantic near-misses and should be reported separately when possible. The two FL-2048 equipment cases share an entity, so they are not fully independent; one tests charging specifications and one rated load. Generalized absence checks cannot rule out PDF extraction omissions. No runtime pass/fail or threshold claim is made here.
