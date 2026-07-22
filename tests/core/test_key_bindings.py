import unittest

from utilities.key_bindings import KeyBindingLease


class FakeCmd:
    def __init__(self):
        self.key_mappings = {"ALT-left": "old"}

    def set_key(self, key, callback):
        self.key_mappings[key] = (callback, (), {})


class KeyBindingLeaseTests(unittest.TestCase):
    def test_release_restores_existing_and_removes_new_keys(self):
        fake = FakeCmd()
        lease = KeyBindingLease(
            fake, {"ALT-left": lambda: None, "ALT-right": lambda: None}, "test"
        )
        lease.acquire()
        lease.release()
        self.assertEqual(fake.key_mappings, {"ALT-left": "old"})

    def test_release_preserves_external_rebinding(self):
        fake = FakeCmd()
        lease = KeyBindingLease(fake, {"ALT-left": lambda: None}, "test")
        lease.acquire()
        fake.key_mappings["ALT-left"] = "newer"
        lease.release()
        self.assertEqual(fake.key_mappings["ALT-left"], "newer")


if __name__ == "__main__":
    unittest.main()
