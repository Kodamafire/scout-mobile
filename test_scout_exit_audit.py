import unittest
from scout_exit_audit import audit_giveback


class ExitAuditTests(unittest.TestCase):
    def test_first_crossing_uses_logged_observation(self):
        rows = [dict(cycle="1", symbol="ABC", pnl_pct="6", high_water="6"),
                dict(cycle="2", symbol="ABC", pnl_pct="2", high_water="6"),
                dict(cycle="3", symbol="ABC", pnl_pct="-2", high_water="6")]
        result = audit_giveback(rows, "ABC", 5, 3)
        self.assertEqual(result["first_cycle"], "2")
        self.assertEqual(result["observed_pct"], 2)
        self.assertEqual(result["latest_pct"], -2)
        self.assertFalse(audit_giveback(rows, "ABC", 10, 3)["triggered"])


if __name__ == "__main__":
    unittest.main()
