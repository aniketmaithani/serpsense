# Eval: group_narratives (group_narratives/v1)

- Run on 2026-10-03, model `claude-opus-5-5`.
- Items: 25, answered: 25.
- Calls: 1 (0 without a usable answer); mean latency 17,673 ms.
- Cost: $0.0567.

## Agreement with the golden stories

| Measure | Value |
|---|---|
| Pairs placed together by the model | 19 |
| Pairs of one story in the golden set | 17 |
| Pairs in both | 14 |
| Precision | 73.7% |
| Recall | 82.4% |
| F1 | 77.8% |
| Joined the open narrative | 3/3 |
| One-offs left alone | 8/8 (see the notes) |
| Placements set aside (items dropped, answers ignored) | not counted in this run |

## Narratives the model started (AI-generated labels)

- new1: Drivers do not accept bookings or cancel after accepting
- new2: Notices issued over overcharging by ride-hailing apps
- new3: Ride-hailing apps suspended in Chandigarh
- new4: Delhi High Court notice on women's safety in cabs
- new5: Customer support unreachable or not resolving complaints

## Placements

`-` is a one-off. A golden story with one item may start a narrative alone; it scores no pairs.

| Item | Difficulty | Golden story | Model's narrative |
|---|---|---|---|
| n001 | hard | - | - |
| n002 | hard | notices_overcharging | new2 |
| n003 | easy | wrong_drop | new5 |
| n004 | hard | no_ride_accepted | - |
| n005 | easy | - | - |
| n006 | easy | fare_above_app | fare_above_app |
| n007 | easy | - | - |
| n008 | easy | no_ride_accepted | new1 |
| n009 | hard | - | - |
| n010 | hard | - | - |
| n011 | hard | fare_above_app | fare_above_app |
| n012 | hard | wrong_drop | - |
| n013 | hard | billing_unresolved | new5 |
| n014 | easy | fare_above_app | fare_above_app |
| n015 | easy | billing_unresolved | new5 |
| n016 | hard | ahmedabad_boycott | - |
| n017 | easy | chandigarh_suspension | new3 |
| n018 | easy | - | - |
| n019 | easy | - | - |
| n020 | hard | billing_unresolved | new5 |
| n021 | easy | - | - |
| n022 | easy | hc_safety_pil | new4 |
| n023 | easy | no_ride_accepted | new1 |
| n024 | easy | billing_unresolved | new5 |
| n025 | hard | billing_unresolved | new5 |

## Notes

- **Indicative only.** An AI agent drafted the golden stories, and no person has reviewed them yet (evals/golden/README.md).
- **Run with `--split all`.** Every item is in `test`; the set has no dev split yet. The run used the default preset's grouping settings: Opus 5.5, medium effort.
- **Where the model differed:**
  - It folded n003, a wrong drop whose author also says support can't be reached, into its support story.
  - It left n004 (the outstation no-show) and n012 (the unsafe wrong drop) as one-offs.
  - It put all five `billing_unresolved` items in one story, "Customer support unreachable or not resolving complaints". That counts as agreement, but the golden story itself is disputed. It groups five different grievances by a shared topic, which prompt v1's rule 4 tells the model not to do. It also holds 10 of the 17 golden pairs (59%). So most of the recall here rests on one decision a reviewer may reverse, and the model followed the topic rather than the prompt's rule.
- **Hand-edited rows.** This run predates two report rows, which were added by hand from the placements table below:
  - "One-offs left alone": 8/8 counts every golden one-off shown as `-`.
  - "Placements set aside": not counted in this run. The scorer of the time showed an item it couldn't place as `-`, the same as a one-off. So a `-` row could be a set-aside placement, and only a new run can tell. A new run was not made: no prompt changed, and the eval budget is kept for prompt changes.
- **Limits of this baseline:**
  - One run, so there is no run-to-run spread. The latency is a single call's.
  - There is no breakdown by difficulty or source.
  - Every multi-item golden story is made of Play reviews. Grouping across surfaces, which the spread alert relies on, is untested.
  - The set has no injection items.
  - One item moves recall by 6 to 23.5 points, so the 2-point rule can't be applied here (README).
- **Cost.** One run today: $0.0567, of the $1.00 allowed for this baseline.
