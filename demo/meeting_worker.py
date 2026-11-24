"""Isolated inference workers using the delivered public Python SDK interfaces."""
import argparse
import codecs
import hashlib
import json
import signal
import sys
import threading
import time
from contextlib import ExitStack
from pathlib import Path

import numpy as np
from runtime_logging import entrypoint

ROOT = Path(__file__).resolve().parents[1]


def emit(kind, **data):
    print(json.dumps({'type': kind, **data}), flush=True)


STOP_REQUESTED=False


def _stop(_signum, _frame):
    global STOP_REQUESTED
    STOP_REQUESTED=True


def run():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['asr', 'diarization'])
    p.add_argument('--session', type=Path, required=True)
    p.add_argument('--models', required=True)
    p.add_argument('--asr-root', type=Path)
    p.add_argument('--lang', default='en', choices=['zh', 'en'])
    args = p.parse_args()
    if args.mode == 'asr':
        from dependency_check import verify_audio8, verify_audio8_sdk
        verify_audio8(args.asr_root)
        sys.path.insert(0, str(args.asr_root / 'demo'))
        import audio8_asr
        verify_audio8_sdk(args.asr_root, audio8_asr)
        from audio8_asr import ASRModel
        from reset_policy import ResetPolicy
        from vad_gate import SileroGate
        with ExitStack() as stack:
            model = stack.enter_context(ASRModel.from_pretrained(args.models, clock_ms=160))
            engine = stack.enter_context(model.create_stream(sample_rate=16000, language=args.lang))
            if STOP_REQUESTED:
                emit('worker_stopped',mode=args.mode)
                return
            decoder = codecs.getincrementaldecoder('utf-8')()
            gate = SileroGate(str(args.asr_root / 'demo/assets/silero_vad.onnx'))
            policy = ResetPolicy(engine)
            delay = engine.info['num_delay_tokens'] * engine.clock_s
            pending = np.zeros(0, np.float32)
            last_clock = 0.
            def output(tokens, final=False):
                nonlocal last_clock
                text = []
                for token in tokens:
                    raw = engine.token_bytes(token.id)
                    value = decoder.decode(raw)
                    last_clock = max(0., token.audio_t - delay)
                    if value:
                        text.append({'text': value, 'time': last_clock, 'audio_clock': token.audio_t,
                                         'token_id': token.id, 'eot': list(token.eot)})
                if final:
                    value = decoder.decode(b'', final=True)
                    if value:
                        text.append({'text': value, 'time': last_clock, 'audio_clock': None, 'token_id': None, 'eot': []})
                if text:
                    emit('tokens', tokens=text)
            def feed(pcm):
                nonlocal pending
                pending = np.concatenate([pending, pcm])
                while len(pending) >= engine.samples_per_token:
                    block, pending = pending[:engine.samples_per_token], pending[engine.samples_per_token:]
                    replay = policy.check(*gate.segment())
                    if replay:
                        emit('recovery', event=policy.resets[-1])
                        output(replay)
                    engine.feed(block)
                    tokens = list(engine.last_tokens)
                    policy.observe(tokens)
                    output(tokens)
                    emit('clock', fed_seconds=engine.stats()['audio_fed_s'])
            emit('ready', sdk_version=model.sdk_version, backend='public-sdk',
                 delay_seconds=delay, info=engine.info)
            with (args.session / 'audio.f32').open('rb') as f:
                while not STOP_REQUESTED:
                    raw = f.read(6400)
                    if raw:
                        if len(raw) % 4:
                            raise ValueError('Incomplete float32 stream')
                        feed(gate.process(np.frombuffer(raw, dtype='<f4')))
                    elif (args.session / 'input.done').exists():
                        break
                    else:
                        time.sleep(.02)
            if STOP_REQUESTED:
                emit('worker_stopped',mode=args.mode)
                return
            feed(gate.flush())
            if len(pending):
                engine.feed(pending)
                tokens = list(engine.last_tokens)
                policy.observe(tokens)
                output(tokens)
            engine.finish()
            tokens = list(engine.last_tokens)
            policy.observe(tokens)
            output(tokens, final=True)
            emit('asr_complete', stats=engine.stats(), resets=policy.resets)
    else:
        from pandora_speaker_diarization_community_1 import DiarizationModel
        pcm = np.fromfile(args.session / 'audio.f32', dtype='<f4')
        if not np.isfinite(pcm).all():
            raise ValueError('Audio must contain finite samples')
        np.clip(pcm, -1.0, 1.0, out=pcm)
        with DiarizationModel.from_pretrained(args.models) as model:
            if STOP_REQUESTED:
                emit('worker_stopped',mode=args.mode)
                return
            output = model.diarize(pcm, sample_rate=16000)
            segments = [{'start': turn.start, 'end': turn.end, 'speaker': int(turn.speaker.removeprefix('SPEAKER_')), 'kind': kind}
                        for kind, turns in [('standard', output.segments),
                                            ('exclusive', output.exclusive_speaker_diarization)]
                        for turn in turns]
            result = {'segments': segments,
                          'pcm_sha256': hashlib.sha256(pcm.tobytes()).hexdigest()}
        if STOP_REQUESTED:
            emit('worker_stopped',mode=args.mode)
            return
        (args.session / 'diarization.json').write_text(json.dumps(result))
        emit('diarization_complete', segments=len(segments))


def main():
    signals={signal.SIGTERM,signal.SIGINT}
    # Bootstrap blocks these before importing NumPy or vendor libraries, so
    # native threads inherit the mask. Consume requests without interrupting
    # synchronous driver waits or Python progress callbacks.
    signal.pthread_sigmask(signal.SIG_BLOCK,signals)
    done=threading.Event()
    def receive():
        while not done.wait(.05):
            # Only consume a genuinely pending termination signal. Older
            # interpreters can return invalid siginfo after an interrupted
            # timed wait; a nonempty result alone is not evidence of shutdown.
            if signal.sigpending() & signals:
                info = signal.sigwaitinfo(signals)
                if info.si_signo in signals:
                    _stop(None,None)
    receiver=threading.Thread(target=receive,name='worker-stop-requests')
    receiver.start()
    try:
        return run()
    finally:
        done.set()
        receiver.join()


if __name__ == '__main__':
    raise SystemExit(entrypoint(main,block_termination=True))
