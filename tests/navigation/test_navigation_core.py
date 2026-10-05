import unittest

from pymolplugins.navigation.model import (
    NavigationEntry,
    NavigationError,
    NavigationSession,
)


def entries(count):
    return [NavigationEntry(id=f"e{i}", kind="object", display_name=f"object_{i}") for i in range(count)]


class NavigationSessionTests(unittest.TestCase):
    def test_empty_entries_are_rejected(self):
        with self.assertRaisesRegex(NavigationError, "at least one"):
            NavigationSession([])

    def test_window_and_batch_forward_backward_are_symmetric(self):
        session = NavigationSession(entries(12), batch_size=5, window_size=2, stride=1)
        self.assertEqual(session.current_indices(), (0, 1))
        for expected in (1, 2, 3, 5):
            self.assertEqual(session.next(), expected)
        self.assertEqual(session.current_indices(), (5, 6))
        self.assertEqual(session.previous(), 3)
        self.assertEqual(session.current_indices(), (3, 4))

    def test_stride_grid_is_symmetric_across_batch_boundaries(self):
        session = NavigationSession(entries(12), batch_size=5, window_size=2, stride=2)
        self.assertEqual(session.next(), 2)
        self.assertEqual(session.next(), 3)
        self.assertEqual(session.next(), 5)
        self.assertEqual(session.previous(), 3)
        self.assertEqual(session.previous(), 2)
        self.assertEqual(session.previous(), 0)

    def test_off_grid_tail_window_is_not_skipped(self):
        session = NavigationSession(
            entries(5), batch_size=5, window_size=2, stride=2, wrap=False
        )
        covered = set(session.current_indices())

        for expected in (2, 3):
            self.assertEqual(session.next(), expected)
            covered.update(session.current_indices())

        self.assertEqual(covered, set(range(5)))
        self.assertEqual(session.next(), 3)

    def test_last_short_batch_has_a_complete_window_when_possible(self):
        session = NavigationSession(entries(13), batch_size=5, window_size=3, stride=1)
        session.next_batch()
        session.next_batch()
        self.assertEqual(session.cursor, 10)
        self.assertEqual(session.current_indices(), (10, 11, 12))

    def test_goto_keeps_the_exact_requested_entry_even_at_a_window_tail(self):
        session = NavigationSession(entries(13), batch_size=5, window_size=3)

        session.goto(12)

        self.assertEqual(session.cursor, 12)
        self.assertEqual(session.current_indices(), (12,))
        self.assertEqual(session.pin(), "e12")

    def test_non_wrapping_boundaries(self):
        session = NavigationSession(entries(4), batch_size=4, window_size=2, wrap=False)
        session.goto(3)
        self.assertEqual(session.cursor, 3)
        self.assertEqual(session.next(), 3)
        session.goto(0)
        self.assertEqual(session.previous(), 0)

    def test_nonwrapping_batch_boundaries_do_not_jump_within_the_same_batch(self):
        session = NavigationSession(
            entries(10), batch_size=5, window_size=2, wrap=False
        )
        session.goto(7)
        self.assertEqual(session.next_batch(), 7)
        session.goto(2)
        self.assertEqual(session.previous_batch(), 2)

    def test_pins_are_visible_without_changing_cursor(self):
        session = NavigationSession(entries(8), batch_size=4, window_size=1)
        session.pin()
        session.next_batch()
        self.assertEqual([entry.id for entry in session.visible_entries()], ["e4", "e0"])
        self.assertEqual(session.cursor, 4)

    def test_duplicate_ids_are_rejected(self):
        duplicate = NavigationEntry(id="same", kind="object", display_name="x")
        with self.assertRaisesRegex(NavigationError, "unique"):
            NavigationSession([duplicate, duplicate])


if __name__ == "__main__":
    unittest.main()
