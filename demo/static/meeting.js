'use strict';
const $ = id => document.getElementById(id), video=$('video');
let media=[], sid=null, cursor=0, tokens=[], result=null, offset=0, sourceSamples=0, queuedTarget=0;
let capturing=false, queue=Promise.resolve(), ending=false, failedUpload=null, restoring=false, pumpTimer=null;
const colors=['#70ddc3','#aca7fa','#eec17d','#de9eea','#8ec9f3','#f49e9e'];
const fmt=t=>`${String(Math.floor(t/60)).padStart(2,'0')}:${String(Math.floor(t%60)).padStart(2,'0')}`;
async function request(url, body, headers={}) {
  const r=await fetch(url, body===undefined?{}:{method:'POST',body,headers});
  const d=await r.json(); if(!r.ok) throw Error(d.error||`HTTP ${r.status}`); return d;
}
function error(e){$('error').textContent=e.message||String(e);$('error').hidden=false;}
async function playVideo(){try{await video.play();}catch(e){if(e.name!=='AbortError')throw e;}}
function selectMedia(){const m=media.find(m=>m.id===$('media').value);video.src=m.playback_url;video.poster=m.poster_url||'';$('sourceCredit').textContent=m.credit?`${m.credit.title} · ${m.credit.license}`:'User-supplied recording';$('mediaCredit').textContent=m.credit?`${m.credit.title}. ${m.credit.license}. ${m.credit.changes} Source: ${m.credit.url} License: ${m.credit.license_url}`:'User-supplied recordings retain their own rights and do not acquire the built-in media license.';}
async function loadMedia(){const d=await request('/api/media');media=d.items.filter(m=>m.visible);$('media').replaceChildren(...media.map(m=>new Option(m.title,m.id)));selectMedia();}
function enqueueAdvance(target){
  target=Math.min(sourceSamples,target);if(target<=queuedTarget)return;queuedTarget=target;
  queue=queue.then(async()=>{
    while(offset<target){const next=Math.min(target,offset+64000);const d=await request(`/api/meeting/advance?id=${sid}`,JSON.stringify({samples:next}),{'Content-Type':'application/json'});offset=d.samples;}
  });
  queue.catch(e=>{failedUpload=e;capturing=false;clearInterval(pumpTimer);video.pause();error(e);});
}
async function start(){
  $('error').hidden=true;$('start').disabled=true;$('media').disabled=true;$('language').disabled=true;$('upload').disabled=true;
  sid=null;cursor=0;tokens=[];result=null;offset=0;queuedTarget=0;sourceSamples=0;ending=false;clearInterval(pumpTimer);queue=Promise.resolve();failedUpload=null;
  $('transcript').textContent='Loading transcription model…';$('status').textContent='Loading ASR';
  $('badge').textContent='PREPARING MEETING';$('notesTitle').textContent='Live transcript';$('summary').textContent='';$('hint').textContent='Speaker IDs will be added after the meeting ends.';
  $('speaker').replaceChildren(new Option('All speakers','all'));$('speaker').disabled=true;$('download').hidden=true;$('tracks').textContent='Speaker timeline will appear after analysis.';
  video.pause();video.currentTime=0;video.controls=false;
  const d=await request('/api/meeting/start',JSON.stringify({media_id:$('media').value,language:$('language').value,input_mode:'file_clock'}),{'Content-Type':'application/json'});sid=d.id;$('cancel').disabled=false;localStorage.setItem('meetingSession',sid);
  poll();
}
async function end(){
  if(ending||!sid)return;if(video.currentTime<=0)throw Error('Wait for audio before ending the meeting.');
  ending=true;capturing=false;clearInterval(pumpTimer);video.pause();$('end').disabled=true;
  enqueueAdvance(Math.floor(video.currentTime*16000));await queue;if(failedUpload)throw failedUpload;
  await request(`/api/meeting/end?id=${sid}`,JSON.stringify({capture_samples:offset,playback_end_seconds:video.currentTime,source_rate:16000,input_mode:'file_clock'}),{'Content-Type':'application/json'});$('status').textContent='Finishing transcription…';
}
function renderLive(){const p=document.createElement('p');p.className='live';p.textContent=tokens.map(t=>t.text).join('');$('transcript').replaceChildren(p);$('transcript').scrollTop=$('transcript').scrollHeight;}
function renderRows(){
  const filter=$('speaker').value;
  $('transcript').replaceChildren(...result.rows.filter(r=>filter==='all'||String(r.speaker)===filter).map(r=>{
    const b=document.createElement('button');b.className='utterance';b.style.setProperty('--speaker-color',r.speaker===null?'#9baabe':colors[r.speaker%colors.length]);b.dataset.start=r.start;b.dataset.end=r.end;
    const title=document.createElement('strong');title.textContent=`${r.speaker===null?'Unassigned':`Speaker ${String(r.speaker).padStart(2,'0')}`} · ${fmt(r.start)}${r.overlap_or_boundary?' · overlap / boundary':''}`;
    const text=document.createElement('span');text.textContent=r.text;b.append(title,text);b.onclick=()=>{video.currentTime=r.start;playVideo().catch(error);};return b;
  }));
}
function finishResult(r){
  result=r;$('cancel').disabled=true;video.controls=true;$('notesTitle').textContent='Who said what';$('hint').textContent='Precomputed speaker labels with approximate ASR clocks. Click a passage to replay.';
  const ids=[...new Set(r.diarization.segments.map(s=>s.speaker))].sort((a,b)=>a-b);
  $('speaker').replaceChildren(new Option('All speakers','all'),...ids.map(id=>new Option(`Speaker ${String(id).padStart(2,'0')}`,String(id))),new Option('Unassigned','null'));$('speaker').disabled=false;renderRows();
  $('tracks').replaceChildren(...ids.map(id=>{const track=document.createElement('div');track.className='track';const label=document.createElement('label');label.textContent=`Speaker ${String(id).padStart(2,'0')}`;const lane=document.createElement('div');lane.className='lane';for(const s of r.diarization.segments.filter(s=>s.kind==='standard'&&s.speaker===id)){const b=document.createElement('button');b.className='turn';b.style.left=`${100*s.start/r.duration}%`;b.style.width=`${100*(s.end-s.start)/r.duration}%`;b.style.background=colors[id%colors.length];b.title=`Speaker ${id} · ${fmt(s.start)}–${fmt(s.end)}`;b.onclick=()=>{video.currentTime=s.start;playVideo().catch(error);};lane.append(b);}track.append(label,lane);return track;}));
  $('download').href=`/api/meeting/result?id=${sid}`;$('download').download='meeting.json';$('download').hidden=false;
  $('summary').textContent=`${fmt(r.duration)} recorded · ${ids.length} speakers · ${r.unassigned_tokens} unassigned text tokens`;
  $('start').disabled=false;$('media').disabled=false;$('language').disabled=false;$('upload').disabled=false;
}
async function poll(){
  try{
    const d=await request(`/api/meeting?id=${sid}&cursor=${cursor}`);cursor=d.cursor;
    if(d.tokens.length){tokens.push(...d.tokens);renderLive();}
    sourceSamples=d.source_samples||0;const phase=d.state;
    if(restoring){
      restoring=false;
      if(phase==='recording'){
        offset=d.samples;ending=true;$('start').disabled=true;$('end').disabled=false;
        $('status').textContent='Playback interrupted · finish captured audio';
        $('end').onclick=async()=>{try{await request(`/api/meeting/end?id=${sid}`,'');$('end').disabled=true;poll();}catch(e){error(e);}};
        $('hint').textContent='The page was reloaded. Finish the captured prefix; missing audio will not be invented.';
        return;
      }
    }
    $('step1').classList.toggle('active',['loading','recording','finishing_asr'].includes(phase));$('step2').classList.toggle('active',['diarizing'].includes(phase));$('step3').classList.toggle('active',phase==='complete');
    if(phase==='recording'&&!capturing&&!ending&&!failedUpload){capturing=true;$('end').disabled=false;$('hint').textContent='Live transcript · speaker IDs will be added after the meeting ends.';$('notesTitle').textContent='Live transcript';$('badge').textContent='MEETING IN PROGRESS';await playVideo();pumpTimer=setInterval(()=>{if(capturing&&!video.paused)enqueueAdvance(Math.floor(video.currentTime*16000));},100);}
    $('status').textContent=({loading:'Loading ASR',recording:'Live transcription',finishing_asr:'Finishing transcription',diarizing:'Analyzing complete recording',complete:'Meeting record ready',failed:'Analysis failed'})[phase]||phase;
    if(phase==='diarizing'){$('badge').textContent='MEETING ENDED · IDENTIFYING SPEAKERS';if(d.progress)$('status').textContent+=` · ${d.progress.completed}/${d.progress.total}`;}
    if(phase==='complete'){$('badge').textContent='REPLAY · PRECOMPUTED LABELS';finishResult(d.result);return;}
    if(phase==='cancelled'){capturing=false;video.pause();$('cancel').disabled=true;$('end').disabled=true;$('start').disabled=false;$('media').disabled=false;$('upload').disabled=false;$('language').disabled=false;$('badge').textContent='MEETING CANCELLED';return;}
    if(phase==='failed'){$('start').disabled=false;$('end').disabled=true;$('cancel').disabled=true;throw Error(d.error);}
    setTimeout(poll,350);
  }catch(e){capturing=false;video.pause();error(e);}
}
$('cancel').onclick=async()=>{try{capturing=false;clearInterval(pumpTimer);video.pause();await request(`/api/meeting/cancel?id=${sid}`,'');$('dialog').close();poll();}catch(e){error(e);}};
$('start').onclick=()=>start().catch(error);$('end').onclick=()=>end().catch(error);video.onended=()=>{if(capturing)end().catch(error);};
video.ontimeupdate=()=>{$('time').textContent=fmt(video.currentTime);if(result)for(const b of document.querySelectorAll('.utterance'))b.classList.toggle('selected',video.currentTime>=Number(b.dataset.start)&&video.currentTime<=Number(b.dataset.end)+.3);};
$('speaker').onchange=renderRows;$('media').onchange=selectMedia;$('details').onclick=()=>$('dialog').showModal();$('close').onclick=()=>$('dialog').close();
$('upload').onclick=()=>$('file').click();$('file').onchange=async()=>{try{const f=$('file').files[0];if(!f)return;const d=await request('/api/uploads?name='+encodeURIComponent(f.name),f);await loadMedia();$('media').value=d.media_id;selectMedia();}catch(e){error(e);}};
loadMedia().then(async()=>{
  const saved=localStorage.getItem('meetingSession');
  if(saved){
    try{const state=await request(`/api/meeting?id=${saved}`);sid=saved;restoring=true;$('media').value=state.media_id;selectMedia();poll();}
    catch(_e){localStorage.removeItem('meetingSession');}
  }
}).catch(error);
