"""The test toolchain itself, so a dependency drift fails here rather than in every test at once."""

import subprocess
import sys
import unittest


class ToolchainTests(unittest.TestCase):
    def test_test_client_runs_on_httpx2(self):
        """#19: without httpx2, Starlette's TestClient falls back to httpx with a deprecation
        warning, and a later release drops the fallback.

        Imported in a fresh interpreter: by now this one has imported the test client already,
        so its warning has come and gone. The filter is set in code, not with -W: -W is parsed
        before site-packages can be imported, and silently drops a third-party warning class.
        """
        result = subprocess.run([sys.executable, "-c", (
            "import warnings\n"
            "from starlette.exceptions import StarletteDeprecationWarning\n"
            "warnings.simplefilter('error', StarletteDeprecationWarning)\n"
            "import fastapi.testclient\n")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
