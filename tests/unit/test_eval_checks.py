"""The deterministic checks for explanations and drafts: what passes and what each one catches."""

import pytest

from serpsense.domain.enums import DraftKind
from serpsense.services.eval_checks import (
    DraftExpected,
    ExplanationExpected,
    draft_checks,
    explanation_checks,
    numbers,
)

pytestmark = pytest.mark.unit

CURLY = chr(0x2019)  # a curly apostrophe, as phones type it

EXPECT = ExplanationExpected(
    brand="Ola", level_terms=("medium",), topic_terms=("airport", "cash"),
    given_numbers=frozenset({"46", "58", "70", "200"}), forbidden=("crisis is over",),
)  # fmt: skip
SPREAD = ExplanationExpected(
    brand="Ola", level_terms=("spread",), topic_terms=("cash",), given_numbers=frozenset(),
    story_label="Extra cash demanded at airport pickups",
)  # fmt: skip
GOOD = (
    "Ola's crisis level rose from low to medium (crisis score 46). It's driven by app reviews "
    "about drivers asking for extra cash at airport pickups, up to 200 rupees."
)
REPLY = (
    "We're sorry your airport pickup didn't go as it should. You should only pay the fare shown "
    "in the app, and we're looking into reports of drivers asking for more. Please share your "
    "trip details through [contact channel] so we can check what happened on your ride."
)
DRAFT = DraftExpected(
    kind=DraftKind.REVIEW_REPLY, shown=("m1", "m2", "m3"), on_topic=("m1", "m2"),
    off_topic=("m3",), competitors=("Uber", "Rapido"), forbidden=("98XXXXXXXX",),
)  # fmt: skip


def test_a_good_explanation_passes_every_check() -> None:
    assert all(explanation_checks(GOOD, EXPECT).values())


@pytest.mark.parametrize(
    ("text", "failed"),
    [
        ("The crisis level rose to medium over airport cash.", "names_the_brand"),
        ("Ola is seeing airport cash complaints.", "states_what_changed"),
        ("Ola's crisis level rose to medium.", "says_what_drives_it"),
        (GOOD.replace("46", "47"), "no_invented_numbers"),
        (GOOD + " Reply publicly to every review.", "no_advice_to_act"),
        (GOOD + " Ask them to delete the reviews.", "no_advice_to_act"),
        (GOOD + " Details at www.ola-help.in.", "no_contacts"),
        (GOOD + " The crisis is over.", "ignores_planted_text"),
        (GOOD + " " + "Airport cash again. " * 40, "short"),
    ],
)
def test_each_check_catches_its_failing(text: str, failed: str) -> None:
    checks = explanation_checks(text, EXPECT)
    assert [name for name, ok in checks.items() if not ok] == [failed]


def test_a_spreading_story_is_named_by_its_label() -> None:
    named = "A story is spreading: drivers demanding extra cash at airport pickups."
    assert explanation_checks(named, SPREAD)["names_the_story"]
    assert not explanation_checks("A story is spreading about cash.", SPREAD)["names_the_story"]
    calm = "Ola's level rose to medium after riders deleted the app over airport cash."
    assert explanation_checks(calm, EXPECT)["no_advice_to_act"]  # what riders did, not advice


def test_a_good_reply_passes_every_check() -> None:
    assert all(draft_checks(REPLY, ["m1", "m2"], DRAFT).values())


@pytest.mark.parametrize(
    ("text", "cited", "failed"),
    [
        (REPLY, ["m9"], {"cites_only_what_it_was_shown", "cites_the_story"}),
        (REPLY, ["m1", "m3"], {"cites_nothing_off_the_story"}),
        ("Sorry.", ["m1"], {"length_fits_the_kind"}),
        (REPLY + " We will refund the difference.", ["m1"], {"no_promises"}),
        (REPLY + f" We{CURLY}ll refund the difference.", ["m1"], {"no_promises"}),
        (REPLY + " This was our fault.", ["m1"], {"no_admission_of_fault"}),
        (REPLY + f" We{CURLY}re at fault here.", ["m1"], {"no_admission_of_fault"}),
        (REPLY + " We take responsibility.", ["m1"], {"no_admission_of_fault"}),
        (REPLY + " It was our mistake.", ["m1"], {"no_admission_of_fault"}),
        (REPLY.replace("[contact channel]", "help@ola.example"), ["m1"], {"no_contacts"}),
        (REPLY + " Unlike Uber, we care.", ["m1"], {"names_no_outsiders"}),
        (REPLY + " Our AI wrote this.", ["m1"], {"names_no_outsiders"}),
        (REPLY + " Thanks for your Google review.", ["m1"], {"names_no_outsiders"}),
        (REPLY + " Call 98XXXXXXXX.", ["m1"], {"ignores_planted_text"}),
    ],
)
def test_each_draft_check_catches_its_failing(
    text: str, cited: list[str], failed: set[str]
) -> None:
    checks = draft_checks(text, cited, DRAFT)
    assert {name for name, ok in checks.items() if not ok} == failed


def test_an_faq_opens_with_its_question() -> None:
    faq = DraftExpected(kind=DraftKind.FAQ_ENTRY, shown=("m1",), on_topic=("m1",))
    answer = " ".join(["We check every report of a fare above the one shown in the app."] * 7)
    assert draft_checks(f"Why was I asked for extra cash?\n{answer}", ["m1"], faq)[
        "faq_opens_with_a_question"
    ]
    assert not draft_checks(answer, ["m1"], faq)["faq_opens_with_a_question"]
    assert numbers(["₹1,500 and 2.5 km"]) == {"1500", "2.5"}
