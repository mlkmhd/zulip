# ruff: noqa: INP001
import random
import unittest
from importlib import import_module

from zerver.lib.stream_color import STREAM_ASSIGNMENT_COLORS, _pick_colors_python, pick_colors


class StreamColorParityTest(unittest.TestCase):
    def assert_matching_colors(
        self, used: set[str], explicit: dict[int, str], recipient_ids: list[int]
    ) -> None:
        expected = _pick_colors_python(used, explicit, recipient_ids)
        original = (used.copy(), explicit.copy(), recipient_ids.copy())
        actual = pick_colors(used, explicit, recipient_ids)
        self.assertEqual(list(actual.items()), list(expected.items()))
        self.assertEqual((used, explicit, recipient_ids), original)

    def test_uses_rust_extension(self) -> None:
        self.assertIs(pick_colors, import_module("_zulip_backend").pick_colors)

    def test_all_palette_slots(self) -> None:
        # Also detects changes to either copy of the palette during migration.
        self.assert_matching_colors(set(), {}, list(reversed(range(50))))

    def test_signed_id_bounds_with_exhausted_palette(self) -> None:
        self.assert_matching_colors(set(STREAM_ASSIGNMENT_COLORS), {}, [2**63 - 1, -(2**63), 0])

    def test_generated_cases(self) -> None:
        rng = random.Random(0)
        palette = STREAM_ASSIGNMENT_COLORS
        custom_palette = [*palette, "#000000", "#ffffff"]
        for case in range(500):
            used = set(rng.sample(palette, rng.randrange(len(palette) + 1)))
            explicit = {
                recipient_id: rng.choice(custom_palette)
                for recipient_id in rng.sample(range(1, 121), rng.randrange(61))
            }
            recipient_ids = rng.choices(range(1, 101), k=rng.randrange(81))
            with self.subTest(case=case):
                self.assert_matching_colors(used, explicit, recipient_ids)
