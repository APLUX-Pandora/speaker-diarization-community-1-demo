"""Meeting business service: streamed ASR first, complete-recording diarization second."""
import argparse
import atexit
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np
from demo_server import ROOT, Application, Handler, ThreadingHTTPServer
from transcript_alignment import associate


class MeetingApplication(Application):
    def __init__(self, args):
        super().__init__(args.models, args.media_dir, args.state_dir)
        self.config = args
        self.sessions = {}
        self.meeting_active = None
        saved_root = self.state_dir / 'meetings'
        for record in sorted(saved_root.glob('*/session.json'), key=lambda f: f.stat().st_mtime)[-20:]:
            try:
                saved = json.loads(record.read_text())
                sid = record.parent.name
                if (len(sid) != 32 or any(c not in '0123456789abcdef' for c in sid)
                        or saved['id'] != sid or saved['state'] != 'complete'):
                    continue
                saved['path'] = str(record.parent)
                saved.pop('cursor', None)
                self.sessions[sid] = saved
            except (ValueError, KeyError):
                print('Ignoring invalid saved meeting:', record.name, file=sys.stderr)

    def start(self, media_id):
        with self.lock:
            if self.meeting_active:
                return None
            return super().start(media_id)

    def snapshot(self, sid, cursor=0):
        with self.lock:
            s = self.sessions[sid]
            return {**{k: v for k, v in s.items() if k not in ['process', 'tokens', 'path', 'source_path', '_recording_started', '_phase_started']},
                    'tokens': s['tokens'][cursor:], 'cursor': len(s['tokens']),
                    'stage_seconds': int(time.monotonic() - s.get('_phase_started', time.monotonic()))}

    def start_meeting(self, media_id, lang, input_mode='pcm'):
        if input_mode not in ['pcm', 'file_clock']:
            raise ValueError('Unknown meeting input mode')
        if lang not in ['en', 'zh']:
            raise ValueError('Choose English or Chinese')
        with self.lock:
            if self.meeting_active or self.active:
                raise ValueError('Another meeting is active')
            if media_id not in self.media:
                raise ValueError('Unknown recording')
            sid = uuid.uuid4().hex
            path = self.state_dir / 'meetings' / sid
            s = {'id': sid, 'media_id': media_id, 'language': lang, 'state': 'loading', 'samples': 0,
                     'tokens': [], 'path': str(path), 'progress': None, 'result': None, 'error': None}
            s['input_mode'] = input_mode
            if input_mode == 'file_clock':
                import math

                import soundfile as sf
                from scipy.signal import resample_poly
                try:
                    pcm, rate = sf.read(self.media[media_id]['path'], dtype='float32', always_2d=True)
                except sf.LibsndfileError as error:
                    raise ValueError('The meeting audio could not be decoded') from error
                pcm = pcm[:, 0].copy() if pcm.shape[1] == 1 else pcm.mean(axis=1, dtype=np.float32)
                if rate != 16000:
                    divisor = math.gcd(rate, 16000)
                    pcm = resample_poly(pcm, 16000 // divisor, rate // divisor).astype(np.float32)
                if not np.isfinite(pcm).all():
                    raise ValueError('Audio must contain finite samples')
                np.clip(pcm, -1.0, 1.0, out=pcm)
                raw = np.ascontiguousarray(pcm, dtype='<f4').tobytes()
                digest = hashlib.sha256(raw).hexdigest()
                prepared = self.state_dir / 'prepared'
                prepared.mkdir(parents=True, exist_ok=True)
                source = prepared / ('stream-' + digest + '.f32')
                if not source.exists():
                    pending = source.with_suffix(f'.{sid}.tmp')
                    try:
                        pending.write_bytes(raw)
                        pending.replace(source)
                    finally:
                        pending.unlink(missing_ok=True)
                s.update(source_path=str(source), source_samples=len(pcm), source_pcm_sha256=digest)
            path.mkdir(parents=True)
            try:
                (path / 'audio.f32').touch()
            except OSError:
                shutil.rmtree(path, ignore_errors=True)
                raise
            self.sessions[sid] = s
            self.meeting_active = sid
        threading.Thread(target=self.run_meeting, args=(sid,), daemon=True).start()
        return sid

    def append(self, sid, offset, raw, internal_file=False):
        if not raw or len(raw) % 4 or len(raw) > 256000:
            raise ValueError('Send at most 64000 little-endian float32 mono samples')
        pcm = np.frombuffer(raw, dtype='<f4')
        if not np.isfinite(pcm).all() or np.max(np.abs(pcm)) > 1.0:
            raise ValueError('PCM must be finite and in [-1, 1]')
        with self.lock:
            s = self.sessions[sid]
            if s.get('input_mode') == 'file_clock' and not internal_file:
                raise ValueError('This session uses the file playback clock')
            if s['state'] != 'recording':
                raise ValueError('Meeting is not receiving audio')
            if offset != s['samples']:
                raise ValueError('PCM offset mismatch: no skipped or duplicate chunks are accepted')
            if (offset + len(pcm)) / 16000 > self.max_duration:
                raise ValueError('Meeting demo duration limit reached; finish the meeting')
            with (Path(s['path']) / 'audio.f32').open('ab') as f:
                f.write(raw)
            s['samples'] += len(pcm)
            return s['samples']

    def advance(self, sid, target):
        with self.lock:
            s = self.sessions[sid]
            if s.get('input_mode') != 'file_clock' or s['state'] != 'recording':
                raise ValueError('File-clock meeting is not recording')
            if not s['samples'] <= target <= s['source_samples']:
                raise ValueError('Invalid playback sample position')
            if target > (time.monotonic() - s['_recording_started'] + .5) * 16000:
                raise ValueError('Future audio cannot be fed ahead of realtime playback')
            if target - s['samples'] > 64000:
                raise ValueError('Advance in blocks of at most four seconds')
            if target == s['samples']:
                return target
            with open(s['source_path'], 'rb') as f:
                f.seek(s['samples'] * 4)
                raw = f.read((target - s['samples']) * 4)
            return self.append(sid, s['samples'], raw, internal_file=True)

    def end_meeting(self, sid, capture=None):
        with self.lock:
            s = self.sessions[sid]
            if s['state'] != 'recording' or not s['samples']:
                raise ValueError('Record audio before ending the meeting')
            if capture is not None:
                if not isinstance(capture, dict):
                    raise ValueError('Capture metadata must be an object')
                for key in ['playback_end_seconds', 'source_rate']:
                    if key in capture and (not isinstance(capture[key], (int, float)) or not np.isfinite(capture[key]) or capture[key] < 0):
                        raise ValueError('Invalid capture metadata')
                if capture.get('capture_samples', s['samples']) != s['samples']:
                    raise ValueError('Final capture sample count mismatch')
                s['capture'] = capture
            s['state'] = 'finishing_asr'
            (Path(s['path']) / 'input.done').touch()

    def cancel_meeting(self, sid):
        with self.lock:
            s = self.sessions[sid]
            if s['state'] in ['complete', 'failed', 'cancelled']:
                raise ValueError('Meeting is already finished')
            s.update(state='cancelled', error='Meeting cancelled; captured audio remains saved')
            process = s.get('process')
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)

    def worker(self, sid, mode):
        s = self.sessions[sid]
        path = Path(s['path'])
        if s['state'] == 'cancelled':
            raise RuntimeError('Meeting cancelled')
        args = [sys.executable, '-u', str(ROOT / 'demo/meeting_worker.py'), mode,
                '--session', str(path)]
        if mode == 'asr':
            args += ['--models', self.config.asr_models, '--asr-root', self.config.asr_root,
                     '--lang', s['language']]
        else:
            args += ['--models', str(self.models)]
        with (path / (mode + '.stderr.log')).open('w') as stderr:
            with self.lock:
                if s['state'] == 'cancelled':
                    raise RuntimeError('Meeting cancelled')
                process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=stderr, text=True,
                                           bufsize=1, start_new_session=True)
                s['process'] = process
            with (path / (mode + '.events.jsonl')).open('w') as log:
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue  # External runtime diagnostics are retained in the private event log.
                    if not isinstance(event, dict):
                        continue
                    with self.lock:
                        if s['state'] == 'cancelled':
                            continue
                        kind = event.get('type')
                        if kind == 'ready':
                            s['state'] = 'recording'
                            s['_recording_started'] = time.monotonic()
                            s['asr_runtime'] = event
                        elif kind == 'tokens':
                            s['tokens'].extend(event['tokens'])
                        elif kind == 'clock':
                            s['asr_processed_seconds'] = event['fed_seconds']
                        elif kind == 'progress':
                            s['progress'] = event
                        elif kind == 'asr_complete':
                            s['asr_stats'] = event
            code = process.wait()
            with self.lock:
                s.pop('process', None)
            if code:
                raise RuntimeError(mode + ' worker failed; see ' + mode + '.stderr.log')

    def run_meeting(self, sid):
        s = self.sessions[sid]
        path = Path(s['path'])
        try:
            self.worker(sid, 'asr')
            if s['state'] == 'cancelled':
                return
            # Worker exit releases all ASR/vendor contexts before diarization is created.
            with self.lock:
                if s['state'] == 'cancelled':
                    return
                s['state'] = 'diarizing'
                s['_phase_started'] = time.monotonic()
            self.worker(sid, 'diarization')
            if s['state'] == 'cancelled':
                return
            diar = json.loads((path / 'diarization.json').read_text())
            duration = s['samples'] / 16000
            aligned = associate(s['tokens'], diar['segments'], duration)
            (path / 'transcript.json').write_text(json.dumps({'text': ''.join(t['text'] for t in s['tokens'])}))
            aligned['tokens'] = s['tokens']
            result = dict(duration=duration, capture=s.get('capture'), transcript=''.join(t['text'] for t in s['tokens']),
                          **aligned, diarization=diar,
                          timing_note='Word times are estimates; unclear words and overlapping voices may remain unassigned.')
            (path / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
            with self.lock:
                if s['state'] != 'cancelled':
                    s.update(state='complete', result=result)
        except Exception as error:  # noqa: BLE001
            with self.lock:
                if s['state'] != 'cancelled':
                    s.update(state='failed', error=str(error))
        finally:
            with self.lock:
                self.meeting_active = None
                (path / 'session.json').write_text(json.dumps(self.snapshot(sid), ensure_ascii=False))

    def close(self):
        with self.lock:
            procs = [s.get('process') for s in self.sessions.values()]
            for s in self.sessions.values():
                if s['state'] not in ['complete', 'failed', 'cancelled']:
                    s.update(state='cancelled', error='Server stopped; captured audio remains saved')
        for process in procs:
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)


class MeetingHandler(Handler):
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path in ['/static/meeting.js', '/static/meeting.css']:
            self.send_file(ROOT / 'demo/static' / Path(parsed.path).name)
        elif parsed.path == '/meeting' or parsed.path == '/meeting/':
            self.send_bytes((ROOT / 'demo/static/meeting.html').read_bytes(), 'text/html')
        elif parsed.path == '/api/meeting':
            query = parse_qs(parsed.query)
            try:
                self.send_json(self.app.snapshot(query['id'][0], max(0, int(query.get('cursor', ['0'])[0]))))
            except (ValueError, KeyError):
                self.send_json({'error': 'Unknown session or invalid cursor'}, 400)
        elif parsed.path == '/api/meeting/result':
            try:
                s = self.app.snapshot(parse_qs(parsed.query)['id'][0])
                if s['state'] != 'complete':
                    raise ValueError('Result not ready')
                self.send_json(s['result'])
            except (ValueError, KeyError) as e:
                self.send_json({'error': str(e)}, 400)
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlsplit(self.path)
        if not parsed.path.startswith('/api/meeting/'):
            return super().do_POST()
        try:
            query = parse_qs(parsed.query)
            if parsed.path == '/api/meeting/pcm':
                length = self.read_body(256000)
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError('Incomplete PCM upload')
                samples = self.app.append(query['id'][0], int(query['offset'][0]), raw)
                self.send_json({'samples': samples})
            elif parsed.path == '/api/meeting/start':
                length = self.read_body(4096)
                data = json.loads(self.rfile.read(length))
                sid = self.app.start_meeting(data['media_id'], data.get('language', 'en'), data.get('input_mode', 'pcm'))
                self.send_json({'id': sid}, 202)
            elif parsed.path == '/api/meeting/advance':
                length = self.read_body(4096)
                data = json.loads(self.rfile.read(length))
                samples = self.app.advance(query['id'][0], int(data['samples']))
                self.send_json({'samples': samples})
            elif parsed.path == '/api/meeting/cancel':
                self.app.cancel_meeting(query['id'][0])
                self.send_json({'cancelled': True})
            elif parsed.path == '/api/meeting/end':
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 4096:
                    raise ValueError('Invalid end metadata length')
                capture = json.loads(self.rfile.read(length)) if length else None
                self.app.end_meeting(query['id'][0], capture)
                self.send_json({'accepted': True}, 202)
            else:
                self.send_json({'error': 'Unknown meeting endpoint'}, 404)
        except (ValueError, KeyError, TypeError, OverflowError) as error:
            self.send_json({'error': str(error)}, 400)
        except OSError as error:
            print('Meeting I/O failed:', error, file=sys.stderr, flush=True)
            self.send_json({'error': 'Could not access meeting storage'}, 500)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host', default='0.0.0.0')
    p.add_argument('--port', type=int, default=8767)
    p.add_argument('--models', required=True)
    p.add_argument('--asr-root', required=True)
    p.add_argument('--asr-models', required=True)
    p.add_argument('--media-dir', default=str(ROOT / 'demo/media'))
    p.add_argument('--state-dir', default=str(ROOT / 'demo/state'))
    args = p.parse_args()
    app = MeetingApplication(args)
    atexit.register(app.close)
    def terminate(_sig, _frame):
        app.close()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    server = ThreadingHTTPServer((args.host, args.port), MeetingHandler)
    server.app = app
    print(json.dumps({'url': f'http://{args.host}:{args.port}/meeting', 'sdk': 'public Python interfaces'}), flush=True)
    try:
        server.serve_forever()
    finally:
        app.close()
        server.server_close()


if __name__ == '__main__':
    main()
