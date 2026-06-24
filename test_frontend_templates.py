import subprocess
import tempfile
import unittest
from pathlib import Path


class FrontendTemplateTests(unittest.TestCase):
    def test_analysis_inline_script_is_valid_javascript(self):
        html = Path("templates/analysis.html").read_text()
        scripts = []
        cursor = 0
        while True:
            start = html.find("<script>", cursor)
            if start < 0:
                break
            start += len("<script>")
            end = html.find("</script>", start)
            if end < 0:
                break
            scripts.append(html[start:end])
            cursor = end + len("</script>")
        self.assertTrue(scripts)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tmp:
            tmp.write("\n".join(scripts))
            tmp_path = tmp.name
        try:
            result = subprocess.run(["node", "--check", tmp_path], text=True, capture_output=True)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
