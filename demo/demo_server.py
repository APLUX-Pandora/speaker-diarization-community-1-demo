"""Offline recording analysis and playback UI over the native SDK."""
import argparse
import hashlib
import itertools
import json
import math
import mimetypes
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np
import soundfile as sf
from pandora_speaker_diarization_community_1 import DiarizationModel
from runtime_logging import entrypoint

ROOT = Path(__file__).resolve().parents[1]

def summarize(segments, duration):
    result = {}
    for kind in ['standard', 'exclusive']:
        speakers, events = {}, {}
        for segment in segments:
            if segment['kind'] != kind:
                continue
            start, end = max(0., segment['start']), min(duration, segment['end'])
            if end <= start:
                continue
            row = speakers.setdefault(segment['speaker'], {'speaker': segment['speaker'],
                                                          'seconds': 0., 'segments': 0})
            row['seconds'] += end - start
            row['segments'] += 1
            events[start] = events.get(start, 0) + 1
            events[end] = events.get(end, 0) - 1
        active, previous, speech, overlap = 0, 0., 0., 0.
        for position in sorted(events):
            if active:
                speech += position - previous
            if active > 1:
                overlap += position - previous
            active += events[position]
            previous = position
        result[kind] = {'speakers': [speakers[k] for k in sorted(speakers)],
                        'speech_seconds': speech, 'overlap_seconds': overlap}
    return result


class Application:
    def __init__(self, models, media_dir, state_dir):
        self.models = models
        self.media_dir, self.state_dir = Path(media_dir).resolve(), Path(state_dir).resolve()
        self.upload_dir = self.state_dir / 'uploads'
        self.result_dir = self.state_dir / 'results'
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self.max_upload = 200 * 1024 * 1024
        self.max_duration = 1800.
        self.lock = threading.RLock()
        self.media, self.jobs = {}, {}
        self.active = None
        self.worker_thread = None
        sample = ROOT / 'examples/audio/meeting.wav'
        if self.media_dir == ROOT / 'demo/media' and sample.is_file():
            self.register('ami_meeting', sample, 'AMI meeting excerpts', True, {
                'title': 'AMI Meeting Corpus / AMI Consortium',
                'url': 'https://groups.inf.ed.ac.uk/ami/',
                'license': 'CC BY 4.0',
                'license_url': 'https://creativecommons.org/licenses/by/4.0/',
                'changes': 'Selected mono speech excerpts from EN2002a joined with short pauses.',
            })
        catalog = self.media_dir / 'catalog.json'
        if catalog.exists():
            for entry in json.loads(catalog.read_text()):
                path = (self.media_dir / entry['filename']).resolve()
                if path.parent != self.media_dir.resolve():
                    raise ValueError('Catalog paths must stay in media directory')
                if path.is_file():
                    playback = self.media_file(entry.get('playback_filename'))
                    if path.suffix.lower() == '.mp4':
                        playback = path
                        path = self.prepare_builtin_video(playback)
                    poster = self.media_file(entry.get('poster_filename'))
                    self.register(entry['id'], path, entry['title'], True, entry.get('credit'),
                                  playback, poster, entry.get('visible', True))
        for metadata in self.upload_dir.glob('*.json'):
            try:
                entry = json.loads(metadata.read_text())
                path = self.upload_dir / entry['filename']
                if path.parent == self.upload_dir and path.is_file():
                    playback = self.upload_dir / entry.get('playback_filename', entry['filename'])
                    if playback.resolve().parent != self.upload_dir:
                        raise ValueError('Invalid playback path')
                    self.register(entry['id'], path, entry['title'], False, None, playback)
            except (ValueError, KeyError, RuntimeError):
                print('Ignoring invalid upload metadata:', metadata.name, file=sys.stderr)
        # Recover recent completed analyses; an interrupted analysis is never resumed.
        for saved in sorted(self.result_dir.glob('*.json'), key=lambda p: p.stat().st_mtime)[-20:]:
            try:
                result = json.loads(saved.read_text())
                media_id = result['media_id']
                if media_id not in self.media or not re.fullmatch(r'[a-f0-9]{32}', saved.stem):
                    continue
                elapsed = result['analysis_seconds']
                self.jobs[saved.stem] = {'id': saved.stem, 'media_id': media_id,
                                         'status': 'complete', 'stage': 'complete',
                                         'completed': 1, 'total': 1, 'percent': 100,
                                         'elapsed_seconds': elapsed, 'result': result}
                self.media[media_id]['last_job'] = saved.stem
            except (ValueError, KeyError, TypeError):
                print('Ignoring invalid analysis file:', saved.name, file=sys.stderr)

    def validate(self, path):
        info = sf.info(path)
        if info.format not in {'WAV', 'WAVEX', 'FLAC', 'OGG'}:
            raise ValueError('Use a WAV, FLAC or OGG recording.')
        if not 8000 <= info.samplerate <= 192000 or not 1 <= info.channels <= 2:
            raise ValueError('Unsupported audio sample rate or channel count.')
        if not 0 < info.duration <= self.max_duration:
            raise ValueError('Choose a recording between 0 and 30 minutes long.')
        if info.frames * info.channels * 4 > 512 * 1024 * 1024:
            raise ValueError('Decoded audio is too large. Export a mono recording first.')
        return info

    def media_file(self, name):
        if name is None:
            return None
        path = (self.media_dir / name).resolve()
        if path.parent != self.media_dir or not path.is_file():
            raise ValueError('Catalog media file is missing or outside media directory')
        return path

    def probe_video(self, path):
        try:
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                                    '-of', 'json', str(path)], capture_output=True, timeout=20, check=True)
            data = json.loads(probe.stdout)
            audio = next(s for s in data['streams'] if s['codec_type'] == 'audio')
            video = next(s for s in data['streams'] if s['codec_type'] == 'video')
            duration = float(data['format']['duration'])
            if 'mp4' not in data['format']['format_name'] or video['codec_name'] != 'h264' or audio['codec_name'] != 'aac':
                raise ValueError('Use an MP4 with H.264 video and AAC audio.')
            if not 0 < duration <= self.max_duration or not 1 <= int(audio['channels']) <= 8:
                raise ValueError('Choose a video up to 30 minutes with 1–8 audio channels.')
            if not 8000 <= int(audio['sample_rate']) <= 192000:
                raise ValueError('Unsupported video audio sample rate.')
            return duration
        except FileNotFoundError:
            raise ValueError('Video support requires FFmpeg and ffprobe on the server.')
        except (subprocess.SubprocessError, StopIteration, KeyError, json.JSONDecodeError):
            raise ValueError('Use WAV, FLAC, OGG or an MP4 with H.264 video and AAC audio.')

    def decode_video(self, path, output, duration):
        try:
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '2', '-i', str(path),
                            '-map', '0:a:0', '-vn', '-af', f'aresample=16000:async=1:first_pts=0,apad=whole_dur={duration}',
                            '-ac', '1', '-ar', '16000', '-t', str(duration), '-c:a', 'pcm_f32le',
                            '-y', str(output)], capture_output=True, timeout=120, check=True)
        except (OSError, subprocess.SubprocessError):
            output.unlink(missing_ok=True)
            raise ValueError('The video audio could not be decoded.')

    def prepare_builtin_video(self, playback):
        # Cache only decoded media IO; no model output or speaker labels are cached here.
        digest = hashlib.sha256()
        with playback.open('rb') as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(block)
        prepared = self.state_dir / 'prepared'
        prepared.mkdir(exist_ok=True)
        output = prepared / (digest.hexdigest() + '.wav')
        duration = self.probe_video(playback)
        if not output.exists():
            temporary = output.with_suffix('.tmp.wav')
            self.decode_video(playback, temporary, duration)
            self.validate(temporary)
            temporary.replace(output)
        self.validate(output)
        return output

    def register(self, media_id, path, title, builtin, credit, playback=None, poster=None, visible=True):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', media_id):
            raise ValueError('Invalid media ID')
        info = self.validate(path)
        playback = playback or path
        video = playback.suffix.lower() == '.mp4'
        if video and abs(self.probe_video(playback) - info.duration) > 0.1:
            raise ValueError('Video and analysis audio durations differ.')
        with self.lock:
            self.media[media_id] = {'id': media_id, 'path': path, 'title': str(title)[:180],
                                    'builtin': builtin, 'duration': info.duration,
                                    'sample_rate': info.samplerate, 'channels': info.channels,
                                    'credit': credit, 'last_job': None, 'visible': bool(visible),
                                    'media_type': 'video' if video else 'audio',
                                    'playback_path': playback, 'poster_path': poster,
                                    'playback_url': f'/api/media/{media_id}/playback',
                                    'poster_url': f'/api/media/{media_id}/poster' if poster else None}

    def media_list(self):
        with self.lock:
            return {'items': [{k: v for k, v in row.items() if k != 'path' and not k.endswith('_path')}
                              for row in self.media.values()],
                    'active_job': self.active, 'max_upload_mb': self.max_upload // (1024 * 1024),
                    'max_duration_seconds': self.max_duration}

    def start(self, media_id):
        with self.lock:
            if self.active:
                return None
            if media_id not in self.media:
                raise KeyError('Recording not found')
            job_id = uuid.uuid4().hex
            self.jobs[job_id] = {'id': job_id, 'media_id': media_id, 'status': 'queued',
                                 'stage': 'preparing', 'completed': 0, 'total': 1,
                                 'percent': 0, 'elapsed_seconds': 0}
            self.active = job_id
            self.worker_thread = threading.Thread(target=self.analyze, args=(job_id,))
            self.worker_thread.start()
            return job_id

    def wait_for_analysis(self):
        with self.lock:
            worker = self.worker_thread
        if worker is not None:
            worker.join()

    def analyze(self, job_id):
        started = time.monotonic()
        try:
            with self.lock:
                job = self.jobs[job_id]
                media = self.media[job['media_id']].copy()
                job.update(status='analyzing', stage='preparing', _started=started)
            pcm, rate = sf.read(media['path'], dtype='float32', always_2d=True)
            if not np.isfinite(pcm).all():
                raise ValueError('Audio must contain finite samples')
            np.clip(pcm, -1.0, 1.0, out=pcm)
            waveform_pcm = pcm.mean(axis=1, dtype=np.float64)
            duration = len(pcm) / rate
            edges = np.linspace(0, len(pcm), min(1200, len(pcm)) + 1, dtype=np.int64)
            peaks = [float(np.abs(waveform_pcm[left:right]).max())
                     for left, right in itertools.pairwise(edges)]
            with self.lock:
                job.update(stage='loading', percent=3)
            with DiarizationModel.from_pretrained(self.models) as model:
                with self.lock:
                    job.update(stage='analyzing', percent=5)
                output = model.diarize(pcm, sample_rate=rate)
            segments = [
                {'start': turn.start, 'end': turn.end,
                 'speaker': int(turn.speaker.removeprefix('SPEAKER_')), 'kind': kind}
                for kind, turns in [('standard', output.segments),
                                    ('exclusive', output.exclusive_speaker_diarization)]
                for turn in turns
            ]
            result = {'schema_version': 1, 'media_id': media['id'], 'title': media['title'],
                      'duration': duration, 'segments': segments, 'waveform': peaks,
                      'summary': summarize(segments, duration), 'credit': media['credit'],
                      'pcm_sha256': hashlib.sha256(pcm.tobytes()).hexdigest(),
                      'analysis_seconds': time.monotonic() - started}
            result_path = self.result_dir / (job_id + '.json')
            result_path.write_text(json.dumps(result, allow_nan=False))
            with self.lock:
                job.update(status='complete', stage='complete', percent=100,
                           elapsed_seconds=time.monotonic() - started, result=result)
                self.media[media['id']]['last_job'] = job_id
        except Exception as error:  # noqa: BLE001
            print('Analysis failed:', type(error).__name__, str(error), file=sys.stderr, flush=True)
            with self.lock:
                self.jobs[job_id].update(status='failed', stage='failed',
                                         error=str(error), elapsed_seconds=time.monotonic() - started)
        finally:
            with self.lock:
                self.active = None
                # Retain recent results in memory; files remain in state/results.
                for key in list(self.jobs)[:-20]:
                    expired = self.jobs.pop(key)
                    recording = self.media.get(expired['media_id'])
                    if recording is not None and recording['last_job'] == key:
                        recording['last_job'] = None


class Handler(BaseHTTPRequestHandler):
    server_version = 'RecordingAnalysis/0.1'

    @property
    def app(self):
        return self.server.app

    def send_json(self, value, status=200, filename=None):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_bytes(data, 'application/json; charset=utf-8', status, filename)

    def send_bytes(self, data, content_type, status=200, filename=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if filename:
            self.send_header('Content-Disposition', 'attachment; filename="' + filename + '"')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def send_file(self, path):
        size = path.stat().st_size
        start, end = 0, size - 1
        requested = self.headers.get('Range')
        if requested:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
            if match and any(match.groups()):
                left, right = match.groups()
                if left:
                    start, end = int(left), min(int(right), size - 1) if right else size - 1
                else:
                    start = max(0, size - int(right))
            else:
                start = size
            if start > end or start >= size:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
        self.send_response(206 if requested else 200)
        self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if requested:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if self.command == 'HEAD':
            return
        with path.open('rb') as file:
            file.seek(start)
            remaining = end - start + 1
            while remaining:
                block = file.read(min(65536, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == '/api/media':
                self.send_json(self.app.media_list())
            elif path.startswith('/api/media/'):
                parts = path.strip('/').split('/')
                if len(parts) not in {3, 4} or (len(parts) == 4 and parts[3] not in {'playback', 'poster'}):
                    raise KeyError('Endpoint not found')
                with self.app.lock:
                    media = self.app.media[parts[2]]
                file = media['path'] if len(parts) == 3 else media[parts[3] + '_path']
                if file is None:
                    raise KeyError('Media file not found')
                self.send_file(file)
            elif path.startswith('/api/jobs/'):
                parts = path.strip('/').split('/')
                if len(parts) not in {3, 4} or (len(parts) == 4 and parts[3] != 'export'):
                    raise KeyError('Endpoint not found')
                with self.app.lock:
                    job = self.app.jobs[parts[2]].copy()
                started = job.pop('_started', None)
                if started is not None and job['status'] == 'analyzing':
                    job['elapsed_seconds'] = time.monotonic() - started
                if len(parts) == 3:
                    self.send_json(job)
                elif job['status'] != 'complete':
                    self.send_json({'error': 'Analysis is not complete.'}, 409)
                else:
                    query = parse_qs(parsed.query)
                    format_ = query.get('format', ['json'])[0]
                    mode = query.get('mode', ['standard'])[0]
                    if format_ == 'json':
                        self.send_json(job['result'], filename='recording-analysis.json')
                    elif format_ == 'rttm' and mode in {'standard', 'exclusive'}:
                        lines = [f"SPEAKER {job['media_id']} 1 {s['start']:.3f} {s['end'] - s['start']:.3f} <NA> <NA> SPEAKER_{s['speaker']:02d} <NA> <NA>\n"
                                 for s in job['result']['segments'] if s['kind'] == mode]
                        self.send_bytes(''.join(lines).encode(), 'text/plain; charset=utf-8', filename=mode + '.rttm')
                    else:
                        self.send_json({'error': 'Unknown export format or mode.'}, 400)
            else:
                static = {'/': 'index.html', '/static/app.js': 'app.js', '/static/style.css': 'style.css',
                          '/static/logo.png': 'logo.png', '/static/pandora-mark.svg': 'pandora-mark.svg'}
                self.send_file(ROOT / 'demo/static' / static[path])
        except KeyError:
            self.send_json({'error': 'Recording, analysis or page not found.'}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def read_body(self, maximum):
        try:
            size = int(self.headers.get('Content-Length', '-1'))
        except ValueError:
            raise ValueError('A valid Content-Length is required.')
        if not 0 < size <= maximum:
            raise ValueError('Request is empty or exceeds the upload size limit.')
        self.connection.settimeout(60)
        return size

    def do_POST(self):
        temporary = None
        try:
            parsed = urlsplit(self.path)
            if parsed.path == '/api/analyze':
                size = self.read_body(4096)
                data = self.rfile.read(size)
                if len(data) != size:
                    raise ValueError('Incomplete request.')
                body = json.loads(data)
                job = self.app.start(body.get('media_id'))
                if job:
                    self.send_json({'job_id': job}, 202)
                else:
                    self.send_json({'error': 'Another recording is being analyzed. Please wait.',
                                    'active_job': self.app.active}, 409)
            elif parsed.path == '/api/uploads':
                size = self.read_body(self.app.max_upload)
                with self.app.lock:
                    used = sum(p.stat().st_size for p in self.app.upload_dir.iterdir() if p.is_file())
                    if used + size > 1024 * 1024 * 1024:
                        raise ValueError('Upload storage is full. Contact the demo operator.')
                media_id = uuid.uuid4().hex
                temporary = self.app.upload_dir / (media_id + '.part')
                title = parse_qs(parsed.query).get('name', ['Uploaded recording'])[0]
                title = title.replace('\\', '/').rsplit('/', 1)[-1][:180]
                with temporary.open('wb') as output:
                    remaining = size
                    while remaining:
                        block = self.rfile.read(min(65536, remaining))
                        if not block:
                            raise ValueError('Upload was interrupted.')
                        output.write(block)
                        remaining -= len(block)
                try:
                    info = self.app.validate(temporary)
                except sf.LibsndfileError:
                    info = None
                if info is not None:
                    extension = {'WAV': '.wav', 'WAVEX': '.wav', 'FLAC': '.flac', 'OGG': '.ogg'}[info.format]
                    target = self.app.upload_dir / (media_id + extension)
                    playback = target
                else:
                    duration = self.app.probe_video(temporary)
                    with self.app.lock:
                        used = sum(p.stat().st_size for p in self.app.upload_dir.iterdir() if p.is_file())
                        if used + math.ceil(duration * 16000 * 4) + 4096 > 1024 * 1024 * 1024:
                            raise ValueError('Upload storage is full. Contact the demo operator.')
                    target = self.app.upload_dir / (media_id + '.wav')
                    playback = self.app.upload_dir / (media_id + '.mp4')
                    self.app.decode_video(temporary, target, duration)
                    self.app.validate(target)
                temporary.replace(playback)
                temporary = None
                self.app.register(media_id, target, title, False, None, playback)
                (self.app.upload_dir / (media_id + '.json')).write_text(json.dumps(
                    {'id': media_id, 'title': title, 'filename': target.name,
                     'playback_filename': playback.name}))
                self.send_json({'media_id': media_id}, 201)
            else:
                self.send_json({'error': 'Endpoint not found.'}, 404)
        except KeyError:
            self.send_json({'error': 'Recording not found.'}, 404)
        except (ValueError, RuntimeError, TypeError, AttributeError, sf.LibsndfileError) as error:
            self.send_json({'error': str(error)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--models', default=os.environ.get('SDIAR_MODELS', str(ROOT / 'models')))
    parser.add_argument('--media-dir', default=str(ROOT / 'demo/media'))
    parser.add_argument('--state-dir', default=os.environ.get('SDIAR_STATE_DIR', str(ROOT / 'demo/state')))
    args = parser.parse_args()
    app = Application(args.models, args.media_dir, args.state_dir)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = False
    server.app = app
    print(f'Recording analysis demo: http://{args.host}:{args.port}', flush=True)
    def stop(_signal, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.wait_for_analysis()
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    raise SystemExit(entrypoint(main))
