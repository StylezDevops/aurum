"""Generate an OBVIOUSLY-FAKE 24KR release for live pipeline testing.

Produces, into RELEASES_DIR/<cat>/: loud white-noise WAV track(s), a 1400x1400 PNG, and a
release.yaml clearly marked as a test. Cat numbers 24KJ900-24KJ999 are the test range; ISRCs are
auto-assigned by the pipeline from isrc_state, so run `scripts/isrc_state_tool.py set --seq 899`
first (→ UKPZD2600900+) and `restore` after. DELETE the staged folder + the LE draft after testing.

    python scripts/make_test_release.py --cat 24KJ900 --artist "Scott Devotion" --tracks 2
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import argparse
import wave
from pathlib import Path

DEFAULT_OUT = r"C:\Users\24kar\OneDrive\24kr-release-pipeline\24KR-Releases"


def make_noise_wav(path: Path, *, seconds: int = 5, rate: int = 44100, amp: float = 0.9) -> None:
    """Loud white-noise WAV (44.1kHz, 16-bit stereo). amp near 1.0 = loud."""
    import numpy as np
    n = int(seconds * rate)
    data = (np.random.uniform(-1.0, 1.0, (n, 2)) * amp * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())


def make_art(path: Path, *, size: int = 1400) -> None:
    """A 1400x1400 PNG (LE minimum) clearly labelled as a test."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (size, size), (18, 18, 26))
    d = ImageDraw.Draw(img)
    for i, txt in enumerate(["AURUM", "AUTOMATED TEST", "DO NOT RELEASE", "DELETE ME"]):
        d.text((60, 60 + i * 120), txt, fill=(255, 90, 200))
    img.save(str(path), "PNG")


def main() -> int:
    ap = argparse.ArgumentParser(description="Stage an obviously-fake 24KR test release.")
    ap.add_argument("--out", default=DEFAULT_OUT, help="RELEASES_DIR")
    ap.add_argument("--cat", default="24KJ900", help="test cat (24KJ900-24KJ999)")
    ap.add_argument("--artist", default="Scott Devotion", help="an EXISTING Label Engine artist")
    ap.add_argument("--title", default="AURUM AUTOMATED TEST - DO NOT RELEASE")
    ap.add_argument("--tracks", type=int, default=1)
    ap.add_argument("--seconds", type=int, default=5)
    a = ap.parse_args()

    folder = Path(a.out) / a.cat
    folder.mkdir(parents=True, exist_ok=True)
    track_lines = []
    for i in range(a.tracks):
        fn = f"AURUM_TEST_{i + 1}.wav"
        make_noise_wav(folder / fn, seconds=a.seconds)
        track_lines.append(f"  - title: AURUM Test Track {i + 1}\n    mix: Original Mix\n    file: {fn}")
    make_art(folder / "artwork.png")

    release_yaml = (
        "# AURUM AUTOMATED TEST RELEASE - DO NOT RELEASE - DELETE AFTER TEST\n"
        f"artist: {a.artist}\n"
        f"title: {a.title}\n"
        f"release_type: {'EP' if a.tracks > 1 else 'Single'}\n"
        f"cat: {a.cat}\n\n"
        "tracks:\n" + "\n".join(track_lines) + "\n\n"
        "release_date: 2026-12-31\n"
        "description: >\n  AURUM automated pipeline test. Noise audio + placeholder art. DELETE AFTER TEST.\n"
    )
    (folder / "release.yaml").write_text(release_yaml, encoding="utf-8")

    from PIL import Image
    w, h = Image.open(folder / "artwork.png").size
    print(f"staged fake release: {folder}")
    print(f"  files: {sorted(p.name for p in folder.iterdir())}")
    print(f"  artwork: {w}x{h} | cat={a.cat} | artist={a.artist!r} | tracks={a.tracks}")
    print("NOTE: set isrc_state last_sequence=899 BEFORE /submit (-> UKPZD2600900+), restore after.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
