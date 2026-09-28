"""Captcha solvers for the eCourts search form.

eCourts uses a Securimage captcha on case searches. We only need it the first
time a case is looked up; after that we store the court's internal case id and
refresh the case history without a captcha.
"""

from __future__ import annotations

import io
import re
import time
from pathlib import Path
from typing import Protocol


class CaptchaSolver(Protocol):
    def solve(self, image: bytes) -> str: ...


class TesseractSolver:
    """Automatic OCR. Accuracy on Securimage is imperfect, so callers retry."""

    def __init__(self) -> None:
        try:
            import pytesseract  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "Tesseract solver needs `pip install courtwatch[ocr]` and the tesseract binary "
                "(apt install tesseract-ocr). Or set COURTWATCH_CAPTCHA_SOLVER=manual."
            ) from exc

    def solve(self, image: bytes) -> str:
        import pytesseract
        from PIL import Image, ImageFilter, ImageOps

        img = Image.open(io.BytesIO(image)).convert("L")
        img = img.resize((img.width * 3, img.height * 3))
        img = img.filter(ImageFilter.MedianFilter(3))
        img = ImageOps.autocontrast(img)
        img = img.point(lambda p: 255 if p > 140 else 0)
        text = pytesseract.image_to_string(
            img,
            config="--psm 7 -c tessedit_char_whitelist=abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        )
        return re.sub(r"[^A-Za-z0-9]", "", text)


class ManualSolver:
    """Saves the captcha image and asks the operator to type it in."""

    def __init__(self, directory: str = "captchas") -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def solve(self, image: bytes) -> str:
        path = self.directory / f"captcha-{int(time.time() * 1000)}.png"
        path.write_bytes(image)
        return input(f"Open {path} and type the captcha: ").strip()


def make_solver(kind: str, directory: str = "captchas") -> CaptchaSolver:
    if kind == "manual":
        return ManualSolver(directory)
    if kind == "tesseract":
        return TesseractSolver()
    raise ValueError(f"Unknown captcha solver {kind!r} (use 'tesseract' or 'manual')")
