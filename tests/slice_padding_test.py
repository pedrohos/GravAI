"""Guards the lead-in that keeps the first words of a sentence in the cut.

Meet's level indicator animates after the audio it reflects, and the observer
waits for a second class mutation before calling it speech. Measured on a live
call, the words began at 187.75s and the timeline marked 190.99s: cutting on the
timeline alone dropped "Pedro, tá se falando" from the transcript and left the
participant's track starting mid-sentence.
"""

from gravai.slicing.slice import _SEGMENT_LEAD_S, _SEGMENT_TAIL_S, _pad_segments


def test_a_segment_gains_lead_and_tail():
    assert _pad_segments([(10.0, 12.0)], lead=2.0, tail=0.8) == [(8.0, 12.8)]


def test_the_lead_never_runs_before_the_recording():
    assert _pad_segments([(0.5, 2.0)], lead=2.0, tail=0.8) == [(0.0, 2.8)]


def test_segments_that_overlap_after_padding_are_merged():
    """Concat replays whatever it is given twice, so a sentence detected as three
    bursts would come back stuttering."""
    assert _pad_segments([(10.0, 11.0), (12.0, 13.0)], lead=2.0, tail=0.8) == [(8.0, 13.8)]


def test_distant_segments_stay_separate():
    assert _pad_segments([(10.0, 11.0), (40.0, 41.0)], lead=2.0, tail=0.8) == [
        (8.0, 11.8),
        (38.0, 41.8),
    ]


def test_unordered_segments_are_handled():
    assert _pad_segments([(40.0, 41.0), (10.0, 11.0)], lead=2.0, tail=0.8) == [
        (8.0, 11.8),
        (38.0, 41.8),
    ]


def test_no_segments_pads_to_nothing():
    assert _pad_segments([]) == []


def test_the_defaults_cover_the_measured_lag():
    """1.7s of it was measured; the default has to stay above that."""
    assert _SEGMENT_LEAD_S >= 1.7
    assert _SEGMENT_TAIL_S >= 0.6
