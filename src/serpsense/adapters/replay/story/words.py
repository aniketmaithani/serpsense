"""What the model says in the demo story: its alert explanations and its drafts.

Explanations are found by rule, level and story, so each is written for every level it could be
read at; drafts by story and kind. No link, address or phone number: the drafter flags those.
"""

from typing import Literal

from serpsense.adapters.replay.story.content import REFUNDS, SWELLING
from serpsense.domain.enums import AlertRule, DraftKind

Level = Literal["low", "medium", "high"]
LEVELS: tuple[Level, ...] = ("low", "medium", "high")
ROSE: dict[Level, str] = {
    "low": "VoltBox's crisis now reads low: a few new complaints, nothing out of the ordinary yet.",
    "medium": "VoltBox's crisis rose to medium. New complaints that the Air 2's charging case "
    "overheats appeared in Play reviews, on the search page and in the news, well above its "
    "usual, and 'voltbox overheating' started rising on Google Trends. SoundNest shows nothing "
    "like it, so this is VoltBox's own problem.",
    "high": "VoltBox's crisis is now high. Reports of swelling charging cases jumped on every "
    "surface at once: three new press stories, new reviews and new results on the first page, "
    "and three new rising searches such as 'voltbox case swelling' and 'is voltbox safe'. "
    "Anyone looking the brand up today sees the complaint first.",
}
SPREADING = (
    "The 'Battery swelling' story has spread beyond the reviews: the same complaint, a charging "
    "case that overheats and swells until the lid won't close, now shows on the search page "
    "and in the news as well."
)
EXPLANATIONS: tuple[tuple[AlertRule, Level, str, str], ...] = (
    *((AlertRule.LEVEL_INCREASE, level, "", ROSE[level]) for level in LEVELS),
    *((AlertRule.NARRATIVE_SPREAD, level, SWELLING, SPREADING) for level in LEVELS),
)

DRAFTS: tuple[tuple[str, DraftKind, str], ...] = (
    (
        SWELLING,
        DraftKind.HOLDING_STATEMENT,
        "We're aware of reports of VoltBox Air 2 charging cases overheating while charging. Your "
        "safety comes first: please stop using the charging case and reply to us for a free "
        "replacement while we investigate. We'll share what we find.",
    ),
    (
        SWELLING,
        DraftKind.REVIEW_REPLY,
        "We're sorry your charging case got hot. Please stop using it, and reply to this review "
        "with your order number so we can send you a replacement case free of charge. We're "
        "looking into every report like yours.",
    ),
    (
        SWELLING,
        DraftKind.FAQ_ENTRY,
        "My VoltBox charging case gets hot or has swollen. What should I do?\n\nStop using the "
        "case and unplug it. Every Air 2 owner can get a free replacement case: ask our support "
        "team through the VoltBox Connect app with your order number, and we'll ship one within "
        "two days. Your earbuds are fine to keep using with the new case.",
    ),
    (
        REFUNDS,
        DraftKind.REVIEW_REPLY,
        "We're sorry your refund is taking this long. Please reply with your order number and "
        "we'll chase it ourselves and tell you the date it reaches you.",
    ),
    (
        REFUNDS,
        DraftKind.HOLDING_STATEMENT,
        "Some refunds for returned earbuds have taken longer than they should. We're clearing "
        "the backlog now and will update everyone waiting.",
    ),
    (
        REFUNDS,
        DraftKind.FAQ_ENTRY,
        "How long does a VoltBox refund take?\n\nUsually five to seven working days after we "
        "receive the return. If yours is taking longer, ask our support team in the VoltBox "
        "Connect app and we'll follow it up.",
    ),
)
