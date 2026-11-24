"""Append offline meeting analysis to the pinned Infinite application.

The original Session.work/emit and original browser script are not rewritten.
Only lifecycle/archive hooks and separate analysis routes are added here.
"""
import argparse
import codecs
import ctypes
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np
from dependency_check import verify_audio8, verify_audio8_sdk
from runtime_logging import entrypoint, private_log, sanitize
from transcript_alignment import associate, display_result

ROOT = Path(__file__).resolve().parents[1]


def release_unused_host_pages():
    """Return free glibc arena pages after an explicitly completed model unload."""
    if not sys.platform.startswith('linux'):
        return False
    try:
        trim=ctypes.CDLL(None).malloc_trim
    except AttributeError:
        return False
    trim.argtypes=[ctypes.c_size_t]
    trim.restype=ctypes.c_int
    return bool(trim(0))


class AnalysisStopped(Exception):
    pass


class Meetings:
    def __init__(self, args):
        self.args = args
        self.root = ROOT / 'demo/state/infinite-meetings'
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.analysis_threads = set()
        self.current = None
        self.active = False
        self.process = None
        self.latest = None
        # Preserve existing completed records, with the new business association.
        candidates = sorted((ROOT / 'demo/state/meetings').glob('*/result.json'), key=lambda p:p.stat().st_mtime)
        for source in reversed(candidates):
            try:
                old = json.loads(source.read_text())
                old.update(source_video='ami_meeting_video.mp4', source_offset_seconds=0)
                old=display_result(old)
                self.latest = old
                break
            except (OSError, ValueError, KeyError, TypeError):
                continue
        completed = sorted(self.root.glob('*/result.json'), key=lambda p:p.stat().st_mtime)
        for source in reversed(completed):
            try:
                latest = json.loads(source.read_text())
                if not isinstance(latest.get('rows'), list):
                    continue
                latest=display_result(latest)
                record = source.parent / 'session.json'
                try:
                    current = json.loads(record.read_text())
                except (OSError, ValueError):
                    current = {'id': source.parent.name, 'video': latest['source_video']}
                current['state'] = 'complete'
                self.latest, self.current = latest, current
                break
            except (OSError, ValueError, KeyError, TypeError):
                continue

        # A stopped recording remains available for an explicit analysis action.
        for record in sorted(self.root.glob('*/session.json'), key=lambda p:p.stat().st_mtime, reverse=True):
            try:
                saved = json.loads(record.read_text())
                if saved.get('state') == 'stopped' and saved.get('captured_samples', 0) > 0:
                    newest_result = max((p.stat().st_mtime for p in completed), default=0)
                    if record.stat().st_mtime > newest_result:
                        self.current = saved
                    break
            except (OSError, ValueError, TypeError):
                continue

    def start_analysis(self, record, original):
        with self.lock:
            if self.stopping.is_set():
                record['state'] = 'stopped'
                self.active = False
                return
            def run():
                # ASR's owner thread was created while acquisition signals were blocked.
                # Do not pass that inherited mask into independently owned worker processes.
                signal.pthread_sigmask(signal.SIG_UNBLOCK,{signal.SIGTERM,signal.SIGINT})
                try:
                    self.analyze(record, original)
                finally:
                    with self.lock:
                        self.analysis_threads.discard(threading.current_thread())
            thread = threading.Thread(target=run, name='meeting-analysis')
            self.analysis_threads.add(thread)
            thread.start()

    def close(self):
        self.stopping.set()
        with self.lock:
            process = self.process
            workers = list(self.analysis_threads)
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for worker in workers:
            worker.join()

    def snapshot(self):
        with self.lock:
            r = self.current
            return {'id': r['id'] if r else None, 'state': r['state'] if r else 'ready',
                        'source_video': r['video'] if r else None, 'error': r.get('error') if r else None,
                        'progress': r.get('progress') if r else None, 'result_ready': self.latest is not None and (not r or r['state']=='complete'),
                        'live_seconds': r.get('captured_samples',0)/16000 if r else 0,
                        'asr_engine_loaded': bool(getattr(self,'session',None) and self.session.asr is not None)}

    def new(self, video, offset, lang, jump):
        sid = uuid.uuid4().hex
        path = self.root / sid
        path.mkdir()
        self.current = {'id': sid,'path': str(path),'video': video,'offset': offset,'language': lang,
                            'jump': jump,'state': 'transcribing','tokens': [],'captured_samples': 0}
        return self.current

    def execute(self, record, mode, models):
        path=Path(record['path'])
        command=[sys.executable,'-u',str(ROOT/'demo/meeting_worker.py'),mode,'--session',str(path),
                 '--models',models]
        with private_log(path/(mode+'.stderr.log')) as err, (path/(mode+'.events.jsonl')).open('w') as log:
            with self.lock:
                if self.stopping.is_set():
                    raise AnalysisStopped()
                self.process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=err,text=True,start_new_session=True)
                process=self.process
            for line in process.stdout:
                log.write(line);log.flush()
                try:event=json.loads(line)
                except ValueError:continue
                if isinstance(event,dict) and event.get('type')=='progress':
                    with self.lock:record['progress']=event
            code=process.wait()
            with self.lock:self.process=None
            if self.stopping.is_set():raise AnalysisStopped()
            if code:raise RuntimeError(mode+' failed; sanitized worker log retained')

    def analyze(self, record, original):
        try:
            path=Path(record['path'])
            seconds=record.get('source_end',record['offset']+record['captured_samples']/16000)-record['offset']
            if not 0<seconds<=1800:raise ValueError('Meeting analysis supports a continuous segment up to 30 minutes')
            source=original.media_path(record['video'])
            if source is None:raise ValueError('Source recording is unavailable')
            # ASR keeps its original browser capture. Offline diarization uses a
            # canonical decode of the same source interval, avoiding capture drift.
            pcm=subprocess.check_output(['ffmpeg','-v','error','-ss',str(record['offset']),'-i',source,
                                         '-af',f'aresample=16000:async=1:first_pts=0,apad=whole_dur={seconds}',
                                         '-t',str(seconds),'-ac','1','-ar','16000','-f','f32le','-'])
            if not pcm:raise ValueError('No source audio in the selected interval')
            (path/'audio.f32').write_bytes(pcm)
            if self.stopping.is_set():raise AnalysisStopped()
            self.execute(record,'diarization',self.args.models)
            diar=json.loads((path/'diarization.json').read_text())
            duration=len(pcm)/64000
            tokens=record['tokens'];text=''.join(t['text'] for t in tokens)
            (path/'transcript.json').write_text(json.dumps({'text':text}))
            relative=[dict(t,time=max(0,t['time']-record['offset'])) for t in tokens]
            attributed=associate(relative,diar['segments'],duration)
            result=dict(duration=duration,transcript=text,diarization=diar,**attributed)
            result.update(tokens=tokens,source_video=record['video'],source_offset_seconds=record['offset'],
                          asr_events_file='asr.events.jsonl',
                          input_provenance={'asr':'Unchanged Infinite browser PCM16 capture before its VAD gate',
                                            'diarization':'Canonical source-video decode of the played continuous interval',
                                            'canonical_pcm_sha256':hashlib.sha256(pcm).hexdigest()})
            result=display_result(result)
            assert text==''.join(row['text'] for row in result['rows'])
            (path/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
            with self.lock:self.latest=result;record['state']='complete'
        except AnalysisStopped:
            with self.lock:record.update(state='stopped',error=None,progress=None)
        except Exception as error:  # noqa: BLE001
            with self.lock:record.update(state='failed',error=sanitize(str(error)))
        finally:
            with self.lock:
                self.active=False
                (Path(record['path'])/'session.json').write_text(json.dumps(record,ensure_ascii=False))


def load_infinite(args):
    # Reuse the independently delivered Audio8 installation; do not copy its SDK or model.
    asr_root=Path(args.asr_root).expanduser().resolve()
    server=asr_root/'demo/demo_server.py'
    for required in [asr_root/'sdk/python/audio8_asr/__init__.py',server,asr_root/'demo/reset_policy.py',asr_root/'demo/vad_gate.py']:
        if not required.is_file():
            raise ValueError('Incomplete Audio8 ASR Infinite installation: '+str(required))
    verify_audio8(asr_root)
    import audio8_asr as sdk
    verify_audio8_sdk(asr_root, sdk)
    # Application helpers remain in the component; the SDK is imported from pip.
    sys.path.insert(0,str(asr_root/'demo'))
    os.environ.update(A8_CLOCK_MS='160',A8_LANG='en')
    spec=importlib.util.spec_from_file_location('external_audio8_application',server)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.MODELS=sdk.resolve_model_dir(args.asr_models)
    media = ROOT / 'demo/media'
    uploads = media / 'uploads'
    uploads.mkdir(parents=True, exist_ok=True)
    module.MEDIA = str(media)
    module.UPLOADS = str(uploads)
    return module, sdk.CpuOptions('default')


def build_application(args, meetings):
    original, asr_cpu = load_infinite(args)

    class Session(original.Session):
        def __init__(self):
            self.archive_decoder=None;self.cached_info=None;self.deferred_analysis=None
            self.start_lock=threading.Lock()
            self.start_load=None
            super().__init__(asr_cpu)
            try:
                self.cached_info=super().info()
            except BaseException:
                self.close()
                raise

        def info(self):
            # Audio8 0.7.2 caches its engine profile and retains current failure/reload flags.
            # Returning the old meeting snapshot would hide those flags after unload/failure.
            with meetings.lock:
                return super().info()

        def start(self,vid,offset_s=0.,jump=False,hotwords=None):
            # Keep reload and teardown on the persistent ASR owner. Requests wait
            # outside the meeting lock so the owner can finish the transition.
            with self.start_lock:
                with meetings.lock:
                    if meetings.stopping.is_set():return None,'Server is closing'
                    if meetings.active or (meetings.current and meetings.current['state']=='finishing_asr'):
                        return None,'Meeting analysis is running; wait for it to finish'
                    if self.state=='reload':return None,'engine is loading'
                    loaded=None
                    if self.asr is None:
                        loaded=threading.Event()
                        self.start_load=loaded
                        with self.lock:
                            self.pending=(self.clock,self.lang)
                            self.state='reload'
                if loaded is not None:
                    while not loaded.wait(.2):
                        if self.closing.is_set():return None,'Server is closing'
                with meetings.lock:
                    if meetings.stopping.is_set():return None,'Server is closing'
                    if self.asr is None:return None,'ASR engine could not be loaded'
                    sid,error=super().start(vid,offset_s,jump,hotwords)
                    if error is None:
                        meetings.new(vid,max(0.,float(offset_s)),self.lang,jump)
                        self.archive_decoder=codecs.getincrementaldecoder('utf-8')(errors='replace')
                    return sid,error

        def close_engine(self):
            had_engine=self.asr is not None
            super().close_engine()
            # The synchronous owner has finished and released all ASR resources.
            # Trim only free allocator pages; never touch live native allocations.
            if had_engine:release_unused_host_pages()

        def configure(self,clock,lang):
            with meetings.lock:
                if meetings.active or (meetings.current and meetings.current['state']=='finishing_asr'):
                    return 'Meeting analysis is running; wait for it to finish'
                return super().configure(clock,lang)

        def stop(self, sid=None):
            with meetings.lock:
                previous_sid = self.sid
                super().stop(sid)
                if self.sid == previous_sid:
                    return
                if meetings.current and meetings.current['state'] in ['transcribing','finishing_asr']:
                    meetings.current['state']='stopped'
                    record=meetings.current
                    (Path(record['path'])/'session.json').write_text(json.dumps(record,ensure_ascii=False))

        def reload(self):
            if self.deferred_analysis is None:
                loaded=self.start_load
                try:
                    return super().reload()
                finally:
                    if loaded is not None:
                        self.start_load=None
                        loaded.set()
            # Release on the original engine-owning worker, after any last feed.
            with meetings.lock:
                record=self.deferred_analysis;self.deferred_analysis=None
                self.cached_info=super().info();self.close_engine()
                with self.lock:self.state='idle'
                record['state']='diarizing'
                meetings.start_analysis(record,original)

        def delete(self,vid):
            with meetings.lock:
                if meetings.active and meetings.current and meetings.current['video']==vid:
                    return 'Meeting analysis is using this recording; wait for it to finish'
                return super().delete(vid)

        def push_audio(self,sid,pcm16):
            with meetings.lock:
                accepted=sid==self.sid and self.state in ('reset','running') and not self.ended
                super().push_audio(sid,pcm16)
                if accepted and meetings.current:
                    raw=np.frombuffer(pcm16,'<i2').astype('<f4')/32768.
                    with (Path(meetings.current['path'])/'asr-capture.f32').open('ab') as f:f.write(raw.tobytes())
                    meetings.current['captured_samples']+=len(raw)

        def emit(self,toks,dec,fed,steps,first_char,final=False,eot=None):
            with meetings.lock:
                r=meetings.current
                if r and self.archive_decoder:
                    for token in toks:
                        text=self.archive_decoder.decode(self.asr.token_bytes(token.id))
                        if text:r['tokens'].append({'text': text,'time': self.off_s+max(0.,token.audio_t-self.clock_ms*self.asr.info['num_delay_tokens']/1000),'audio_clock': token.audio_t,'eot': list(token.eot)})
                    if final:
                        text=self.archive_decoder.decode(b'',final=True)
                        if text:r['tokens'].append({'text': text,'time': self.off_s+fed/self.fs,'audio_clock': None,'eot': []})
                emitted=super().emit(toks,dec,fed,steps,first_char,final,eot)
                if r and r['state']=='stopped':
                    (Path(r['path'])/'session.json').write_text(json.dumps(r,ensure_ascii=False))
                return emitted

        def broadcast(self,event):
            super().broadcast(event)
            with meetings.lock:
                r=meetings.current
                if r and event.get('type') in ['reset','text','state']:
                    with (Path(r['path'])/'asr.events.jsonl').open('a') as f:f.write(json.dumps(event)+'\n')
                if r and event.get('type')=='state' and event.get('error'):
                    r.update(state='failed',error=event['error'])
                if event.get('type')=='state' and event.get('state')=='done' and r:
                    # This callback runs on the original model-owning worker after
                    # its finish/emit. Release it before any analysis worker starts.
                    self.cached_info=super().info();self.close_engine()
                    r['state']='diarizing';meetings.active=True
                    meetings.start_analysis(r,original)

    original.S=Session()
    meetings.session=original.S

    class Handler(original.Handler):
        def end_headers(self):
            if not urlsplit(getattr(self,'path','')).path.startswith('/media/'):
                self.send_header('Cache-Control','no-store')
            return super().end_headers()

        def do_GET(self):
            path=urlsplit(self.path).path
            if path in ['/','/meeting','/meeting/']:
                return self._file(str(ROOT/'demo/static/infinite_meeting.html'),'text/html; charset=utf-8')
            if path=='/infinite':
                data=(Path(args.asr_root)/'demo/static/index.html').read_text()
                data=data.replace('</head>','<link rel="stylesheet" href="/static/infinite-layout.css"></head>')
                raw=data.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
            if path in ['/logo.png','/pandora-mark.svg','/favicon.ico']:
                name='logo.png' if path=='/logo.png' else 'pandora-mark.svg'
                return self._file(str(ROOT/'demo/static'/name),'image/png' if name.endswith('png') else 'image/svg+xml')
            if path in ['/static/infinite_meeting.js','/static/meeting_transport.js','/static/infinite_meeting.css','/static/infinite-layout.css']:
                return self._file(str(ROOT/'demo/static'/Path(path).name),'text/javascript' if path.endswith('.js') else 'text/css')
            if path=='/api/meeting-analysis':return self._json(meetings.snapshot())
            if path=='/api/meeting-analysis/result':
                selected=parse_qs(urlsplit(self.path).query).get('session',[None])[0]
                with meetings.lock:
                    current=meetings.current
                    if not current or current['state']!='complete' or not meetings.latest or (selected is not None and selected!=current['id']):
                        return self._json({'error':'Completed analysis is unavailable or the session changed'},409)
                    payload=dict(meetings.latest,session_id=current['id'])
                return self._json(payload)
            return super().do_GET()

        def do_POST(self):
            if meetings.stopping.is_set():return self._json({'error':'Server is closing'},503)
            if urlsplit(self.path).path=='/api/meeting-analysis/end':
                body=self._body()
                with meetings.lock:
                    r=meetings.current
                    if not r or r['state'] not in ['transcribing','stopped'] or r['captured_samples']<=0:return self._json({'error':'No recorded audio available'},409)
                    stopped=r['state']=='stopped'
                    end=r['offset']+r['captured_samples']/16000 if stopped else body.get('source_end')
                    if not isinstance(end,(int,float)) or not np.isfinite(end) or end<=r['offset']:
                        return self._json({'error':'Invalid source end'},400)
                    r.update(source_end=end,state='finishing_asr')
                    if stopped:
                        meetings.active=True
                        with original.S.lock:
                            original.S.deferred_analysis=r
                            original.S.state='reload'
                return self._json({'accepted':True})
            return super().do_POST()

    return original, Handler


def serve(args):
    meetings=Meetings(args)
    original=server=None
    stop_requested=False
    def stop(_signum, _frame):
        nonlocal stop_requested
        # Signal handlers can re-enter; Event.set() takes a non-reentrant lock.
        stop_requested=True
    previous={sig:signal.signal(sig,stop) for sig in (signal.SIGTERM,signal.SIGINT)}
    try:
        mask=signal.pthread_sigmask(signal.SIG_BLOCK,previous)
        try:
            original,Handler=build_application(args,meetings)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK,mask)
        if stop_requested:return
        class Server(original.ThreadingHTTPServer):
            daemon_threads=False
            block_on_close=True
            def get_request(self):
                request,address=super().get_request()
                request.settimeout(5)
                return request,address
        server=Server(('0.0.0.0',args.port),Handler)
        server.timeout=.2
        print(json.dumps({'url':f'http://0.0.0.0:{args.port}/meeting','base':'External Audio8 SDK/application; separate offline speaker analysis'}),flush=True)
        while not stop_requested:
            server.handle_request()
    finally:
        try:
            meetings.stopping.set()
            session=getattr(meetings,'session',None)
            if session is not None:session.closing.set()
            if server is not None:server.server_close()
            if session is not None:session.close()
            meetings.close()
            print(json.dumps({'meeting_closed':True,'asr_workers_joined':True,'analysis_workers_joined':True}),flush=True)
        finally:
            for sig,handler in previous.items():signal.signal(sig,handler)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',type=int,default=8767)
    p.add_argument('--asr-root', default=str(ROOT / 'components/asr'))
    p.add_argument('--asr-models',required=True)
    p.add_argument('--models', default=None)
    args=p.parse_args()
    args.models = str(Path(args.models or ROOT / 'models').resolve())
    return serve(args)


if __name__=='__main__':raise SystemExit(entrypoint(main))
