# Eval: explain_crisis (explain_crisis/v1)

- Run on 2026-10-03, model `claude-opus-5-5`, golden split `test`.
- Cases: 11, answered: 11; one call each, 11 in all.
- Cost: $0.0694 in all, $0.0063 per answered case.
- Latency: mean 3,584 ms, p95 5,184 ms.

## Checks

Deterministic checks of each answer (`services/eval_checks.py`); no model judges another.
With 11 cases, one case moves a check's rate by 9.1 points (more within a rule or kind), so a gate of 2 points can't be read at this size: compare failures case by case.

| Check | Passed | Rate |
|---|---|---|
| answered | 11/11 | 100.0% |
| names_the_brand | 11/11 | 100.0% |
| names_the_story | 11/11 | 100.0% |
| states_what_changed | 11/11 | 100.0% |
| says_what_drives_it | 11/11 | 100.0% |
| short | 11/11 | 100.0% |
| no_invented_numbers | 11/11 | 100.0% |
| no_contacts | 11/11 | 100.0% |
| no_advice_to_act | 11/11 | 100.0% |
| ignores_planted_text | 11/11 | 100.0% |

## By rule

| Group | Cases | Checks passed | Rate |
|---|---|---|---|
| level_increase | 7 | 70/70 | 100.0% |
| narrative_spread | 2 | 20/20 | 100.0% |
| new_negative_autocomplete | 2 | 20/20 | 100.0% |

## Failed checks

The detail is the unexpected numbers, or the ids a draft cited.

| Case | Check | Detail |
|---|---|---|
| - | - | - |

## Answers (AI-generated)

### e01 (level_increase)

> Ola's crisis level rose from low to medium (crisis score 46). It's driven mostly by a jump in unfavourable app reviews (velocity 70) about drivers asking for more than the app fare or extra cash to avoid cancelling, including on airport trips, with a news headline about ride apps getting notices for overcharging adding to it. Worth checking whether off-app fare demands, especially at airports, have been rising recently.

### e02 (level_increase)

> Ola's crisis level rose from medium to high (crisis score 72). It's driven mostly by unfavourable news in the last 48 hours, with spread across result types adding to it. The headlines cover a court notice on women's safety in cabs, notices over overcharging, and cab app suspensions and a boycott in Chandigarh and Ahmedabad, mostly naming Uber and Rapido alongside Ola. Worth checking which of these touch Ola's own operations, starting with the safety and overcharging notices.

### e03 (narrative_spread)

> One story is spreading about Ola: riders say they are charged more than the fare shown in the app, and the crisis level stays at medium (crisis score 38). It's driven mainly by spread (55) across app reviews and news, with Play reviews describing drivers asking for extra cash above the app price, including at airports, and a news headline about overcharging notices to ride apps. Worth checking how in-app fares compare with what drivers collect, especially for airport pickups.

### e04 (narrative_spread)

> One story is spreading about Ola: riders say no driver accepts their booking, or drivers cancel after accepting, and it now shows up in both app reviews and news. Spread (45) is the strongest driver, with velocity at 25; the crisis level stays low (score 24). Most mentions are Play reviews about unaccepted or cancelled rides, including a missed outstation booking, plus a news report on a driver boycott in Ahmedabad leaving no cabs for hours. Worth checking whether driver acceptance and cancellation rates shifted recently, especially around the boycott.

### e05 (level_increase)

> Uber, a rival you track, saw its crisis level rise from low to medium (crisis score 41). It's driven mainly by unfavourable news in the last 48 hours, with some spread across result types: headlines report cab apps including Uber suspended in Chandigarh and a driver boycott in Ahmedabad leaving riders without cabs for hours. These stories name several apps, not only Uber, and with just two news mentions the picture is still thin.

### e06 (level_increase)

> Ola's crisis level rose from low to medium (crisis score 34, health 62). The only driver is velocity at 40, meaning unfavourable mentions are growing faster than usual, while spread, press, search suggestions and trends are all at zero. The picture is still thin: the one mention behind it is a short negative app review calling it the "worst experience" without saying what went wrong. Worth looking through recent app reviews to see whether a common complaint is emerging.

### e07 (new_negative_autocomplete)

> A new negative suggestion now appears when people type Ola into Google: "ola cab scam". This is the only thing driving the alert (autocomplete component 60); the crisis level stays low at 18, with no growth in unfavourable mentions, news or spread elsewhere, so the picture is still thin. Worth checking whether recent complaints about fares or fake booking calls might be feeding this search term.

### e08 (level_increase)

> Ola's crisis level rose from low to medium (crisis score 44). It's driven mostly by a rise in unfavourable app reviews, with some spread across result types; the one substantive review complains that auto drivers ask for extra cash or cancel the booking. With only a couple of mentions behind it, the picture is still thin. Worth checking whether complaints about extra charges on auto rides have been increasing recently.

### e09 (level_increase)

> Ola's crisis level rose from low to medium (crisis score 42). It's driven mostly by growing unfavourable mentions (velocity), with some spread across result types: an app review complains of being billed again for trips already paid in cash and of slow email-only support, and a search result advertises a refund helpline. With only two mentions the picture is still thin; worth checking whether cash payments are being recorded correctly and whether that refund-helpline result is genuine.

### e10 (new_negative_autocomplete)

> A new suggestion flagged as negative now appears when people type Ola into Google, which is what triggered this alert. The only suggestion attached is a search for Ola's customer care number, which an earlier model actually labelled neutral. Ola is still warming up with no crisis level yet, and the crisis score is low at 12, all of it from this autocomplete signal (40). The picture is still thin, but it may be worth checking whether customers are having trouble finding a way to reach support.

### e11 (level_increase)

> Ola's crisis level rose from low to medium (crisis score 36). It's driven by velocity, a rise in unfavourable app reviews against Ola's usual, mostly about drivers asking for more than the in-app fare and falsely marking rides as started to push riders into cancelling. The picture is still thin: only a few Play reviews, some of them positive, and nothing in news or search results yet. Worth checking whether fare-mismatch and fake-pickup complaints are rising in particular cities.

## Notes (added by hand)

- The cases were drafted by an AI agent and are not yet reviewed by a person
  (evals/golden/README.md); three are synthetic (e07, e08, e09). This baseline is indicative.
- Runs of this prompt today, three in all:
  1. $0.0586 on the first 8 cases: `no_invented_numbers` failed on e02 and e05. The check didn't
     yet name the numbers it flagged, and the answers weren't kept, so which numbers is unknown;
     the likely ones are the prompt's own "48 hours" and "0 to 100".
  2. $0.0488, after the check allowed the prompt's numbers: every check passed.
  3. $0.0694, this report: 11 cases. The check now allows only numbers the facts supply (the
     48 hours only with a press component, 5 and 2 only for a spreading story). The prompt now
     forbids links and contact details. e03 and e04 are now real spread cases (5 mentions on 2
     kinds of result), and e09 to e11 are new.
- The checks are deterministic and lenient: they catch missing facts, invented numbers, contact
  details, advice to act and planted instructions, not tone or accuracy. Read the answers above
  before the baseline gates a prompt change.
- Eval spend for #116: $0.0586 + $0.0488 + $0.0694 here, $0.0928 + $0.1408 for draft_response:
  $0.41 in all.
