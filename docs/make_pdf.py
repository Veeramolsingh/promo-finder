"""Turn docs/solution_doc.html into docs/solution_doc.pdf with headless Edge or Chrome.

    python docs/make_pdf.py
"""

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    shutil.which("google-chrome") or "", shutil.which("chromium") or "", shutil.which("msedge") or "",
]


def main() -> None:
    browser = next((c for c in CANDIDATES if c and Path(c).exists()), None)
    if not browser:
        sys.exit("No Edge/Chrome found - open solution_doc.html in a browser and Print > Save as PDF.")
    src = (HERE / "solution_doc.html").resolve()
    out = (HERE / "solution_doc.pdf").resolve()
    subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={out}", src.as_uri()], check=True, timeout=120)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
