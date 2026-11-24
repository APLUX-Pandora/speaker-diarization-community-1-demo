"""Business-only text/speaker association; raw model timings remain available."""


def suppress_short_overlaps(segments, minimum_seconds=0.2):
    """Suppress only short standard turns fully covered by another long turn.

    Preserve isolated acknowledgements, longer overlaps and raw input records.
    Exclusive model output is carried through unchanged.
    """
    anchors = [s for s in segments if s['kind'] == 'standard'
               and s['end'] - s['start'] >= minimum_seconds - 1e-9]
    kept, suppressed = [], []
    for segment in segments:
        short = (segment['kind'] == 'standard'
                 and 0 < segment['end'] - segment['start'] < minimum_seconds - 1e-9)
        covered = short and any(other['speaker'] != segment['speaker']
                                and other['start'] <= segment['start']
                                and other['end'] >= segment['end'] for other in anchors)
        (suppressed if covered else kept).append(dict(segment))
    return kept, suppressed


def display_result(result):
    """Apply a reversible meeting-display policy; retain native diarization."""
    if not result.get('diarization', {}).get('segments'):
        return dict(result)
    kept, suppressed = suppress_short_overlaps(result['diarization']['segments'])
    output = {**result, 'display_diarization': {
        'segments': kept, 'suppressed_segments': suppressed,
        'policy': {'minimum_overlap_seconds': 0.2,
                   'rule': 'short standard turn fully covered by another speaker turn of at least 200 ms'}}}
    if result.get('tokens'):
        offset = result.get('source_offset_seconds', 0)
        relative = [dict(t, time=max(0., t['time'] - offset)) for t in result['tokens']]
        associated = associate(relative, kept, result['duration'])
        associated.pop('tokens')  # Preserve original exported ASR token timestamps.
        output.update(associated)
    return output


def associate(tokens, segments, duration, radius=0.08, minimum_coverage=0.6):
    turns = [s for s in segments if s['kind'] == 'standard']
    attributed = []
    for token in tokens:
        t = min(duration, max(0., token['time']))
        left, right = max(0., t - radius), min(duration, t + radius)
        spans = {}
        for turn in turns:
            overlap = max(0., min(right, turn['end']) - max(left, turn['start']))
            if overlap:
                spans[turn['speaker']] = spans.get(turn['speaker'], 0.) + overlap
        candidates = sorted(spans, key=spans.get, reverse=True)
        speaker = None
        coverage = min(1., spans[candidates[0]] / max(1e-9, right - left)) if candidates else 0.
        ambiguous = len(candidates) > 1
        if candidates and coverage >= minimum_coverage and not ambiguous:
            speaker = candidates[0]
        attributed.append({**token, 'time': t, 'speaker': speaker, 'overlap_or_boundary': ambiguous,
                           'coverage': coverage, 'timestamp_method': 'delay-compensated-asr-clock'})
    rows = []
    for token in attributed:
        if (rows and rows[-1]['speaker'] == token['speaker']
                and rows[-1]['overlap_or_boundary'] == token['overlap_or_boundary']
                and token['time'] - rows[-1]['end'] < 1.2):
            rows[-1]['text'] += token['text']
            rows[-1]['end'] = max(rows[-1]['end'], token['time'])
        else:
            rows.append({'start': token['time'], 'end': token['time'], 'text': token['text'],
                             'speaker': token['speaker'], 'overlap_or_boundary': token['overlap_or_boundary']})
    assert ''.join(r['text'] for r in rows) == ''.join(t['text'] for t in tokens)
    return {'rows': rows, 'tokens': attributed,
            'alignment_method': 'ASR clock association, not forced word alignment',
            'unassigned_tokens': sum(t['speaker'] is None for t in attributed)}
