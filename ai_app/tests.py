import unittest

from ai_app.services.remove_bg_guidance import (
    BACKGROUND_REMOVAL_IMPROVEMENT_TIPS,
    get_remove_bg_improvement_tips,
)


class RemoveBgGuidanceTests(unittest.TestCase):
    def test_returns_tips_for_1423_code(self):
        tips = get_remove_bg_improvement_tips("1423", {})
        self.assertEqual(tips, BACKGROUND_REMOVAL_IMPROVEMENT_TIPS)

    def test_returns_tips_for_background_not_removed_failure_type(self):
        tips = get_remove_bg_improvement_tips("1500", {"failure_type": "background_not_removed"})
        self.assertEqual(tips, BACKGROUND_REMOVAL_IMPROVEMENT_TIPS)

    def test_returns_empty_for_unrelated_failure(self):
        tips = get_remove_bg_improvement_tips("1410", {"failure_type": "too_dark"})
        self.assertEqual(tips, [])
