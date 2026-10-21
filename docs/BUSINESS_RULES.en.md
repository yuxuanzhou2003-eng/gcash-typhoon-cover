# CLIMADA Product Rules Register — English Discussion Draft

Start here: [Project overview and team responsibilities](README.md).

Version: v0.2. Date: 2026-10-21.

This register distinguishes the confirmed prototype scope, proposed product terms, engineering controls, and external requirements pending verification. Undetermined values must not be silently set as actual terms in the program; if the demonstration needs values, save them separately as explicitly labeled demo configuration.

## 1. Confirmed Scope and Engineering Controls

| No. | Rule | Nature and source | Implementation location |
|---|---|---|---|
| S01 | platform operations workbench only; no merchant purchase frontend | User-confirmed for this task | Pages and routes |
| S02 | Version 1 imports existing or simulated policies; operational rate calculations do not issue policies | Current prototype scope | Import and calculation |
| S03 | Notifications are not sent externally; payments use a simulated payment adapter | Current prototype scope | Actions and payment |
| C01 | Calculations, terms, and data are versioned | Engineering control | End-to-end process |
| C02 | Duplicate requests must not create duplicate payouts | Engineering control | Payout obligation and payment idempotency key |
| C03 | Approval is bound to the batch version | Proposed operational control; not claimed as a statutory requirement | Approval |
| C04 | Reconcile unknown payment results first; do not blindly retry payment | Engineering control | Payment |
| C05 | Manual changes retain the original value, reason, and operator | Engineering control | Audit |

## 2. Product Rules and Items Pending Confirmation

| No. | What must be determined | Current status | Impact |
|---|---|---|---|
| P01 | Whether the coverage location is the merchant's coordinates or a barangay representative point, and when the location is locked | Pending joint confirmation by Product and Modeling | Spatial matching, basis risk |
| P02 | Wind-speed unit, averaging duration, height, and conversion method | To be proposed and validated by the Modeling team | Thresholds, cross-source consistency |
| P03 | Wind-speed trigger bands, payout percentages, and how equality at the threshold is handled | Pending design and validation | Pricing, settlement |
| P04 | Collection observation window, refunds, anomalous days, and the calculation basis for two-, four-, and six-week coverage amounts | Proposed approach is two, four, or six weeks; window and anomaly handling pending | Coverage amount and pre-underwriting data snapshot |
| P05 | Effective time, coverage window, and waiting period | Pending terms design | Policy eligibility |
| P06 | Mapping between naming or warning and the scope of sales suspension, and conditions for resuming sales | Proposal only; not confirmed as a regulatory requirement | Regional sales status |
| P07 | Whether settlement uses agency observations or an agreed model index, including the specific source and publication time | Priority pending confirmation | Whether settlement is possible and the target time to funds |
| P08 | Fallback and dispute process for missing data, institutional conflicts, and subsequent revisions | Pending terms design | Blocking, manual review, adjustment |
| P09 | Definition of the same typhoon, event merging, and ordering of multiple typhoons | Priority pending confirmation | Deduplication, aggregate liability |
| P10 | Per-event limit, aggregate limit over the coverage period, and the limit reinstatement method | Priority pending confirmation | Model rates, ledger |
| P11 | Fees, capital load, seasonal adjustment, and concentration adjustment | Proposed assumptions; formulas pending validation | Operational rate calculation |
| P12 | Reinsurance retention, limits, and recovery timing | Labeled scenarios can be implemented first; no actual contract yet | Net risk, cash requirements |
| P13 | Approvers, authorization amounts, and execution deadline | Proposed operational design | Roles, tasks |

In P04, “week” must be explicitly defined in shared configuration as a number of days and a calculation basis; the coverage amount is locked at underwriting. Post-disaster collections must not retroactively change the established coverage amount.

## 3. External Materials and Evidence Boundary

The following materials are entry points for preliminary review; they do not mean that regulatory validity and product applicability have been fully reviewed as of the submission date.

| Material | Confirmed content | What cannot be inferred directly |
|---|---|---|
| [GCash official GInsure explanation](https://help.gcash.com/hc/en-us/articles/47625085424153-What-is-GInsure) | GInsure is a platform; products are provided by insurers approved by the IC; the catalog includes Property and Business | That this project has been approved for coverage, or that the platform has no similar product |
| [IC 2016-55](https://www.insurance.gov.ph/wp-content/uploads/2022/11/CL2016_55.pdf) | The document lists a minimum rate of 0.05% for typhoon and flood and applicable requirements | Treating it as the complete pricing rule for this product without checking classification and subsequent revisions |
| [IC 2024-11](https://www.insurance.gov.ph/wp-content/uploads/2024/04/CL-2024_11.pdf) | The document reinstates the rate arrangement in 2016-55; Section 2 contains broader applicability wording | That a parametric merchant insurance product is automatically exempt, or that this project's dynamic formula has been approved |
| [IC microinsurance document directory](https://site.insurance.gov.ph/category/microinsurance/circular-letters-microinsurance/) | Microinsurance regulatory and distribution frameworks can be searched there | That a small-amount merchant product automatically qualifies as microinsurance, or that agricultural rules automatically apply |

“Suspending sales after a typhoon is named,” “manual payout approval,” and “funds arriving within hours” are, in this project, product concepts, operational controls, or service targets; they must not be labeled as generally statutory requirements. Waiting periods, cancellation periods, and claims turnaround times of other existing GInsure products must not be copied directly.

## 4. Confirmation Order

First determine P01, P02, P03, P07, P09, and P10 to keep the modeling and payout rules consistent; then determine P04, P05, and P06; finally complete pricing loadings, reinsurance scenarios, and approval authorities.

Record for each confirmation: rule number, explicit conditions, values and units, supporting link, confirmer, version, applicable start and end times, and boundary examples. Keep items whose regulatory applicability is unresolved as “pending verification”; do not use the “compliant” label.

## 5. Role C Working Authority During the Prototype

Role C does not need to wait for every commercial term to be finalized. Role C may design and compare candidate trigger and payout configurations, provided that each configuration is clearly labeled as a modeling hypothesis rather than an approved company rule. The output should help Product choose a rule; it must not imply that the candidate is already a policy term.

Use explicit fields instead of the ambiguous phrase `coverage_amount`:

| Field | Meaning | Owner during the prototype |
|---|---|---|
| `daily_collection_basis` | Eligible average daily settled collections calculated from the agreed observation window | A supplies the data definition; C may propose the calculation treatment |
| `benefit_days` | Number of collection days represented by a trigger tier, such as 14, 28, or 42 days | C may test candidate values and alternatives |
| `gross_benefit_basis` | `daily_collection_basis × benefit_days` before any contractual limit | C calculates |
| `per_event_cap` | Maximum modeled benefit for one covered event | Product must ultimately approve; C may test candidate caps |
| `modeled_payout_basis` | Candidate payout after applying the trigger rule and the configured model-side cap | C calculates; the backend independently checks final policy limits |

For the first prototype, Role C may choose and document:

- candidate wind thresholds and tier benefits, after confirming the wind metric and unit with Role B;
- whether threshold equality is inclusive; `wind_speed >= threshold` is the recommended baseline because it is deterministic and easy to test;
- candidate 14-, 28-, and 42-day benefits corresponding to the memorandum's two-, four-, and six-week concept, while also testing at least one alternative schedule;
- a candidate per-event cap for sensitivity analysis;
- the rounding mode and minimum currency unit for model outputs; retaining full precision internally and applying explicit `ROUND_HALF_UP` at the configured output boundary is the recommended baseline; and
- a candidate identifier such as `climada-trigger-candidate-v0.1`.

Role C must deliver at least one baseline candidate, one alternative, boundary tests immediately below, at, and above every threshold, and a short sensitivity table showing how the candidate changes affected merchants and modeled payouts. The candidate configuration must carry `status: experimental` and must not use `official`, `approved`, or `production` in its name.

The following remain Product or backend decisions and should stay configurable in C's implementation: the policy period and effective-date rules, the final per-event and aggregate limits, the rule promoted for business use, eligibility, duplicate-event handling, and final payable amount. For modeling comparisons, C and D may use a clearly labeled 365-day baseline and a typhoon-season sensitivity case; neither case establishes the actual policy period.
