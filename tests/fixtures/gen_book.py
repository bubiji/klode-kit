"""fx-book-aurora (WI-00a): a 3-chapter synthetic book with a desk and a frozen holdout set.

The text is invented for tests (an imaginary "aurora kettle"); it names no people.
Chapter heads use the English form ``Chapter N: <title>`` that the vendored splitter knows.
``src/desk.json`` is the tuning set; ``src/holdout.jsonl`` holds 5 frozen questions (r2.1 1.3).
"""

from __future__ import annotations

import gen_common as C

CHAPTERS = [
    ("ch01", "The Copper Shell", [
        "The aurora kettle is built around a copper shell that is hammered thin and then annealed twice.",
        "Annealing relaxes the metal so that the shell can expand without cracking when it meets an open flame.",
        "A brass rim is folded over the upper edge of the shell; the fold hides the seam and stiffens the mouth.",
        "The base is slightly domed. A domed base flexes outward when heated and settles back when it cools, "
        "which keeps the kettle from rocking on a flat stove.",
        "Makers tin the inside of the shell with a thin food-safe layer so that water never touches bare copper.",
        "Retinning is needed roughly once every ten years of daily use, earlier if the water is hard.",
    ]),
    ("ch02", "The Whistle Valve", [
        "The whistle valve sits in the spout and opens only when the steam pressure passes a small threshold.",
        "Inside the valve a reed of spring steel vibrates against two slots, producing a rising two-tone note.",
        "If the note drops to a single tone, the reed is usually coated with scale and needs a vinegar soak.",
        "A lever on the handle lifts the valve cap so that the kettle can be poured without the whistle sounding.",
        "The valve cap is threaded counter-clockwise, a deliberate choice that keeps it from loosening while pouring.",
        "Spare reeds are cut from the same strip as the original so that the pitch of the note stays the same.",
    ]),
    ("ch03", "Night Market Care", [
        "At night markets the kettle is kept on a low ember bed and topped up from a covered jug every hour.",
        "Vendors wipe the shell with a dry wool cloth after each refill, because wet copper darkens quickly in smoke.",
        "A darkened shell is restored with a paste of salt and lemon juice, rinsed, and dried at once.",
        "The handle is wrapped in waxed cord; the wax keeps the cord from charring when the flame flares.",
        "Kettles that will be stored for the winter are emptied, dried upside down, and packed with crumpled paper.",
        "Before the first use in spring the kettle is boiled empty of tea twice to drive off the storage smell.",
    ]),
]

# The original klode-build desk.json shape (desk_build.py docstring): per dimension ``dimension / title / question /
# cards / cues / tests`` (``tests`` = the tuning set). ``construction`` and ``whistle`` carry no hyphen on purpose
# (03 B3). Cues are the chapter's own words, chosen without looking at the holdout (iron law 2): with klode's
# substring routing the last holdout question ("darkened copper shell") is expected to go to ``construction``.
DESK = {
    "kb": "demo-klode",
    "synthetic": True,
    "threshold": 0.8,
    "dimensions": [
        {"dimension": "construction", "title": "Construction: the copper shell",
         "question": "How is the kettle body built, and why does it survive an open flame?",
         "cards": ["ch01"], "cues": ["copper shell", "annealing", "annealed", "domed base", "tinning", "brass rim"],
         "tests": ["Why is the copper shell annealed twice?"]},
        {"dimension": "whistle", "title": "The whistle valve",
         "question": "How does the two-tone whistle work, and how is it kept in tune?",
         "cards": ["ch02"], "cues": ["whistle", "reed", "valve cap", "pitch", "two tones"],
         "tests": ["What makes the whistle produce two tones?"]},
        {"dimension": "night-market-care", "title": "Night market care",
         "question": "How is the kettle kept, cleaned and stored when it works every night?",
         "cards": ["ch03"], "cues": ["night market", "ember bed", "salt and lemon", "winter", "wool cloth",
                                     "waxed cord"],
         "tests": ["How do vendors keep the kettle warm at night markets?"]},
    ],
}

# The frozen holdout set (02 r2.1 row 1.3): one ``{"q", "gold": <dimension>}`` per line, the original ``--gold``
# shape. Never used to tune cues.
HOLDOUT = [
    {"q": "How often does the inside of the kettle need retinning?", "gold": "construction"},
    {"q": "Why does the domed base keep the kettle from rocking?", "gold": "construction"},
    {"q": "What should be done when the whistle drops to a single tone?", "gold": "whistle"},
    {"q": "Which way is the valve cap threaded?", "gold": "whistle"},
    {"q": "How is a darkened copper shell restored?", "gold": "night-market-care"},
]


def book_text() -> str:
    parts = ["The Aurora Kettle: A Small Synthetic Handbook\n\n"]
    for n, (_, title, paras) in enumerate(CHAPTERS, start=1):
        parts.append(f"Chapter {n}: {title}\n\n")
        parts.append("\n\n".join(paras) + "\n\n")
    return "".join(parts).rstrip("\n") + "\n"


def build() -> dict[str, bytes]:
    return {
        "fx-book-aurora/aurora-kettle.txt": C.text(book_text()),
        "fx-book-aurora/src/desk.json": C.jdump(DESK),
        "fx-book-aurora/src/holdout.jsonl": C.jlines(HOLDOUT),
        "fx-book-aurora/scenario.json": C.jdump({
            "name": "fx-book-aurora", "synthetic": True, "kind": "book", "source": "aurora-kettle.txt",
            "description": "3 章合成英文小书（虚构的 aurora kettle），desk.json 3 维度各 1 题调优题（原 klode-build desk.json 结构），holdout 5 题（{q, gold}）。",
            "chapters": [{"id": cid, "head": f"Chapter {n}: {title}"} for n, (cid, title, _) in enumerate(CHAPTERS, start=1)],
            "expect": {"chapters": 3, "holdout": 5},
        }),
    }
