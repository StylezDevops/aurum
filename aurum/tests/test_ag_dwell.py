"""AG promotion dwell (AURUM_ERR_010 anti-flap) is enforced on the caller's clock — previously
dead because the kernel passed now=None. The dwell gates rapid BAND re-crossing only; the
authority SCALAR is never dwell-gated. The kernel now threads its domain clock into apply_outcome.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

from aurum.novel.authority_governor import AuthorityGovernor


def test_promotion_dwell_holds_band_within_window_scalar_ungated():
    ag = AuthorityGovernor(dwell_seconds=60.0)
    cc = "x"
    ag.set_authority(cc, 0.62, now=0.0)                 # readonly band, two crossings below full
    assert ag.band(cc) == "readonly"

    # Many grounded-good outcomes at the SAME instant. The authority SCALAR climbs (dwell never
    # gates it), but the BAND can cross at most once within one dwell window.
    for _ in range(30):
        ag.apply_outcome(cc, good=True, grounded=True, now=100.0)
    assert ag.authority(cc) > 0.62                      # scalar moved despite the dwell
    assert ag.band(cc) != "full"                        # dwell held the band short of full

    # Space the outcomes past dwell_seconds → each subsequent band crossing now clears.
    t = 100.0
    for _ in range(10):
        t += 61.0
        ag.apply_outcome(cc, good=True, grounded=True, now=t)
    assert ag.band(cc) == "full"                        # dwell satisfied → band climbs to full


def test_dwell_disabled_when_now_is_none_first_promote_always_allowed():
    # now=None (no clock) keeps the legacy behaviour: dwell cannot be evaluated, so a promote is
    # allowed — this is why the cage (fresh kernel per message, last_promote empty) never stalls.
    ag = AuthorityGovernor(dwell_seconds=60.0)
    ag.set_authority("y", 0.62)
    for _ in range(30):
        ag.apply_outcome("y", good=True, grounded=True)   # now=None
    assert ag.band("y") == "full"                         # unbounded climb without a clock
