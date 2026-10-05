import tempfile
import unittest
from pathlib import Path
from scout_options.dashboard import dashboard_html, ensure_dashboard


class OptionsDashboardTests(unittest.TestCase):
    def test_offline_and_demo_are_explicit_and_status_path_is_local(self):
        html=dashboard_html('status.json')
        self.assertIn('const STATUS_FILE="status.json";',html)
        self.assertIn('No brokerage orders',html)
        self.assertIn('Made-up prices',html)
        self.assertIn('Local background research is available; cloud hosting is not configured',html)
        self.assertIn('Saved alert history',html)
        self.assertIn('Fresh-session test',html)
        self.assertIn('Results update after market close',html)
        self.assertIn('Option quote recorder',html)
        self.assertIn('Service offline / stale',html)
        self.assertNotIn('innerHTML',html)

    def test_filename_cannot_inject_script_or_escape_folder(self):
        html=dashboard_html("x'<script>.json")
        self.assertIn('\\u003cscript\\u003e',html)
        for name in ('../status.json','https://example.com/status.json','status.txt'):
            with self.assertRaises(ValueError):dashboard_html(name)

    def test_dashboard_is_generated_beside_status_without_overwriting_existing_page(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'status.json'
            output=ensure_dashboard(path)
            self.assertEqual(output.name,'options.html')
            self.assertIn('const STATUS_FILE="status.json";',output.read_text())
            output.write_text('existing dashboard')
            self.assertEqual(ensure_dashboard(path).read_text(),'existing dashboard')

if __name__=='__main__':unittest.main()
