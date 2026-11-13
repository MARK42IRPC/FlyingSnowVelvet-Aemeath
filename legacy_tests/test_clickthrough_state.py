import unittest
from unittest.mock import patch

from lib.core import clickthrough_state


class ClickthroughStateTests(unittest.TestCase):
    def test_default_is_used_before_state_is_initialized(self):
        with patch.object(clickthrough_state, "_clickthrough_enabled", None):
            self.assertFalse(clickthrough_state.is_clickthrough_enabled())
            self.assertTrue(clickthrough_state.is_clickthrough_enabled(default=True))

    def test_explicit_state_is_shared_without_qapplication(self):
        with patch.object(clickthrough_state, "_clickthrough_enabled", None):
            clickthrough_state.set_clickthrough_enabled(True)
            self.assertTrue(clickthrough_state.is_clickthrough_enabled())
            clickthrough_state.set_clickthrough_enabled(False)
            self.assertFalse(clickthrough_state.is_clickthrough_enabled(default=True))


if __name__ == "__main__":
    unittest.main()
