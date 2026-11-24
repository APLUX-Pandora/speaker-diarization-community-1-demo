"""Transcribe a finite recording, then attribute preserved text without an HTTP server."""
import argparse
import json
import math
import os
import signal
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

DEMO_DIR = Path(__file__).resolve().parent
ROOT = DEMO_DIR.parent

# Isolated Python omits the script directory from its import path.
if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))

from dependency_check import verify_audio8  # noqa: E402
from runtime_logging import entrypoint  # noqa: E402
from transcript_alignment import associate  # noqa: E402

STOP_REQUESTED = False
CHILD = None


class MeetingStopped(Exception):
    pass


def stop(_signum, _frame):
    global STOP_REQUESTED
    STOP_REQUESTED = True
    if CHILD is not None and CHILD.poll() is None:
        try:
            os.killpg(CHILD.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def run_worker(command, log):
    global CHILD
    if STOP_REQUESTED:
        raise MeetingStopped()
    try:
        CHILD = subprocess.Popen(command, stdout=log, start_new_session=True)
        if STOP_REQUESTED:
            stop(signal.SIGTERM, None)
        code = CHILD.wait()
        if STOP_REQUESTED:
            raise MeetingStopped()
        if code:
            raise subprocess.CalledProcessError(code, command)
    finally:
        if CHILD is not None and CHILD.poll() is None:
            stop(signal.SIGTERM, None)
            CHILD.wait()
        CHILD = None


def run():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('audio', type=Path)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--asr-root', default=str(ROOT / 'components/asr'))
    p.add_argument('--asr-models', required=True)
    p.add_argument('--models', required=True)
    p.add_argument('--lang', choices=['en', 'zh'], default='en')
    args = p.parse_args()
    verify_audio8(args.asr_root)
    args.out.mkdir(parents=True, exist_ok=False)
    pcm, rate = sf.read(args.audio, dtype='float32', always_2d=True)
    pcm = pcm.mean(axis=1, dtype=np.float32)
    if not np.isfinite(pcm).all():
        raise ValueError('Audio must contain finite samples')
    if rate != 16000:
        divisor = math.gcd(rate, 16000)
        pcm = resample_poly(pcm, 16000 // divisor, rate // divisor).astype(np.float32)
    pcm = np.ascontiguousarray(pcm, dtype='<f4')
    if not len(pcm) or not np.isfinite(pcm).all():
        raise ValueError('Use nonempty, finite PCM')
    np.clip(pcm, -1.0, 1.0, out=pcm)
    pcm.tofile(args.out / 'audio.f32')
    (args.out / 'input.done').touch()
    common = [sys.executable, '-u', str(DEMO_DIR / 'meeting_worker.py')]
    options = ['--session', str(args.out)]
    tokens = []
    # Shared business workers call the same public Python SDKs as the Web application.
    for mode in ['asr', 'diarization']:
        extra = (['--models', args.asr_models, '--asr-root', args.asr_root, '--lang', args.lang]
                 if mode == 'asr' else ['--models', args.models])
        with (args.out / (mode + '.events.jsonl')).open('w') as log:
            run_worker(common + [mode] + options + extra, log)
        if mode == 'asr':
            for line in (args.out / 'asr.events.jsonl').read_text().splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get('type') == 'tokens':
                    tokens.extend(event['tokens'])
    diar = json.loads((args.out / 'diarization.json').read_text())
    alignment = associate(tokens, diar['segments'], len(pcm) / 16000)
    result = dict(alignment)
    result.update(duration=len(pcm) / 16000, transcript=''.join(t['text'] for t in tokens),
                  tokens=tokens, diarization=diar)
    (args.out / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'result': str(args.out / 'result.json'), 'rows': len(result['rows']),
                      'unassigned': result.get('unassigned_tokens')}, ensure_ascii=False))


def main():
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        return run()
    except MeetingStopped:
        print(json.dumps({'meeting_stopped': True}))
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    raise SystemExit(entrypoint(main))
