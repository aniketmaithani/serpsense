"""SoundNest in the demo story: VoltBox's competitor, untouched by its trouble."""

from datetime import timedelta

from serpsense.adapters.replay.story.content import (
    OFF,
    Article,
    Content,
    Question,
    Result,
    StoryBrand,
    good,
    plain,
)
from serpsense.domain.enums import Topic

S = "https://soundnest.example.com"
SOUNDNEST = Content(
    results=(
        Result(
            0,
            10,
            "SoundNest: earbuds and speakers",
            "Official site. SoundNest Pods, Pods Lite and portable speakers.",
            f"{S}/",
            plain("The brand's own site."),
        ),
        Result(
            0,
            20,
            "SoundNest Pods review: the comfiest budget earbuds",
            "Light, snug and clear, if a little short on battery.",
            "https://reviews.example.com/soundnest-pods",
            good("A warm review."),
        ),
        Result(
            0,
            30,
            "VoltBox vs SoundNest: the best budget earbuds of 2026",
            "We tested both for a month. VoltBox lasts longer, SoundNest fits better.",
            "https://gadgets.example.com/voltbox-vs-soundnest",
            plain("A fair comparison."),
        ),
    ),
    questions=(
        Question(0, 1, "Is SoundNest a good brand?", plain("A general question about the brand.")),
    ),
    articles=(
        Article(
            2,
            "SoundNest launches Pods Lite at Rs 1,999",
            "Tech Ledger",
            "https://techledger.example.com/soundnest-pods-lite",
            good("A launch story."),
        ),
        Article(
            12,
            "SoundNest teams up with a music app for three months free",
            "City Wire",
            "https://citywire.example.com/soundnest-music",
            good("A partnership.", Topic.CORPORATE),
        ),
        Article(
            20,
            "Budget earbuds sales rise this festive season",
            "Metro Business",
            "https://metrobusiness.example.com/earbuds-sales",
            plain("A market story.", Topic.CORPORATE),
        ),
    ),
    rating=((0, 4.2),),
)

RIVAL = StoryBrand(
    "SoundNest",
    "soundnest",
    "com.example.soundnest",
    {
        "languages": ["en"],
        "search_page": {"templates": ["{brand}"], "pages": 1, "ai_overview": False},
        "autocomplete": OFF,
        "news": {"extra_terms": []},
        "trends": OFF,  # VoltBox's scan compares the two
        "play": {"review_pages": 0},  # the rating only
        "maps": OFF,
        "youtube": OFF,
    },
    2,
    timedelta(minutes=30),
    SOUNDNEST,
)
