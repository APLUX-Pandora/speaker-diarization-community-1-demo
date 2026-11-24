'use strict';
const $=id=>document.getElementById(id), live=$('live'), replay=$('replay');
const colors=['#70ddc3','#aca7fa','#eec17d','#de9eea','#8ec9f3','#f49e9e'];
const fmt=t=>`${String(Math.floor(Math.max(0,t)/60)).padStart(2,'0')}:${String(Math.floor(Math.max(0,t)%60)).padStart(2,'0')}`;
let connectionUncertain=false, polling=false;
let result=null, current=null, loadedId=undefined, previousState=null, meetingId=null, visibleRows=[], turnButtons=[], selectedTurn=null, activeRow=null, submitting=false;
async function api(path,body){return MeetingTransport.requestJSON(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)},10000);}
function uncertain(error){
 connectionUncertain=true;setAnalysisBusy(false);$('retryStatus').hidden=false;
 $('finish').disabled=true;$('pauseLive').disabled=true;
 $('phase').textContent='Connection interrupted; completion is unconfirmed. '+error.message;
 $('progress').textContent=result?'Showing the last confirmed analysis.':'Reconnect to check the recording and analysis status.';
}
function tab(name){const analysis=name==='analysis';if(analysis&&$('analysisTab').disabled)return;document.body.classList.toggle('analysis-mode',analysis);$('livePanel').hidden=analysis;$('analysisPanel').hidden=!analysis;$('analysisTab').setAttribute('aria-selected',String(analysis));$('liveTab').setAttribute('aria-selected',String(!analysis));if(!analysis){replay.pause();live.contentWindow.dispatchEvent(new Event('resize'));}}
$('liveTab').onclick=()=>tab('live');$('analysisTab').onclick=()=>tab('analysis');$('goLive').onclick=()=>tab('live');
let savedRecording=null;
$('restoreMeeting').onclick=()=>{if(savedRecording){meetingId=savedRecording.id;current=savedRecording;renderStage(current);$('restoreMeeting').hidden=true;}};
function matchingRow(t,turn=null){
 const candidates=result.rows.filter(r=>turn?Math.min(r.end,turn.end)>Math.max(r.start,turn.start):r.start-.02<=t&&t<r.end);
 if(!turn)return candidates[0]||null;
 const distance=r=>Math.max(r.start-t,t-r.end,0);
 return candidates.sort((a,b)=>Number(b.speaker===turn.speaker)-Number(a.speaker===turn.speaker)||distance(a)-distance(b)||a.start-b.start)[0]||null;
}
function syncTranscript(t,explicit=false){
 if(!result)return;
 const turn=selectedTurn&&t>=selectedTurn.start-.02&&t<selectedTurn.end?selectedTurn:null;
 const row=matchingRow(t,turn);
 if(explicit&&row&&!visibleRows.some(v=>v.row===row)){$('filter').value='all';rows();}
 if(explicit||row)$('matchHint').hidden=Boolean(row);
 for(const v of visibleRows){const active=v.row===row;v.button.classList.toggle('active',active);if(active)v.button.setAttribute('aria-current','true');else v.button.removeAttribute('aria-current');}
 const visible=visibleRows.find(v=>v.row===row);
 if(visible&&(activeRow!==row||explicit)){
  const container=$('rows'),rect=visible.button.getBoundingClientRect(),bounds=container.getBoundingClientRect();
  if(rect.top<bounds.top||rect.bottom>bounds.bottom)container.scrollTo({top:container.scrollTop+rect.top-bounds.top-8,behavior:explicit?'instant':'smooth'});
 }
 activeRow=row;
 for(const v of turnButtons){const active=t>=v.segment.start&&t<v.segment.end;v.button.classList.toggle('active',active);v.button.setAttribute('aria-pressed',String(active));}
}
async function seek(t,turn=null){t=Math.max(0,Math.min(t,result.duration-.01));selectedTurn=turn;replay.currentTime=t+(result.source_offset_seconds||0);syncTranscript(t,true);try{await replay.play();}catch(e){if(e.name!=='AbortError')$('progress').textContent=e.message;}}
function rows(){
 const filter=$('filter').value;visibleRows=[];activeRow=null;
 $('rows').replaceChildren(...result.rows.filter(r=>filter==='all'||(filter==='pending'?(r.speaker===null):String(r.speaker)===filter)).map(r=>{
  const b=document.createElement('button');b.className='utterance';b.style.setProperty('--voice',r.speaker===null?'#8b93a5':colors[r.speaker%colors.length]);
  const h=document.createElement('strong');h.textContent=`${r.speaker===null?'Speaker uncertain':`Speaker ${String(r.speaker).padStart(2,'0')}`} · ${fmt(r.start+(result.source_offset_seconds||0))}`;b.append(h);
  const text=document.createElement('span');text.textContent=r.text;b.append(text);
  visibleRows.push({row:r,button:b});b.onclick=()=>seek(r.start);return b;
 }));
}
function showResult(r){
 result=r;selectedTurn=null;turnButtons=[];$('mediaCredit').textContent=r.source_video==='ami_meeting_video.mp4'?'AMI Meeting Corpus / AMI Consortium · CC BY 4.0 · 60–660 s excerpt · Trimmed & transcoded':'Uploaded media retain their own rights.';replay.src='/media/'+encodeURI(r.source_video);const segments=(r.display_diarization||r.diarization).segments.filter(s=>s.end>0&&s.start<r.duration&&s.end>s.start).map(s=>({...s,start:Math.max(0,s.start),end:Math.min(r.duration,s.end)}));const ids=[...new Set(segments.filter(s=>s.kind==='standard').map(s=>s.speaker))].sort((a,b)=>a-b);
 $('filter').replaceChildren(new Option('All speakers','all'),...ids.map(id=>new Option(`Speaker ${String(id).padStart(2,'0')}`,String(id))),new Option('Unassigned text','pending'));$('filter').disabled=false;rows();
 $('summary').textContent=`${fmt(r.duration)} · ${ids.length} speakers · ${r.unassigned_tokens} unassigned text tokens`;
 $('tracks').replaceChildren(...ids.map(id=>{const track=document.createElement('div');track.className='track';const label=document.createElement('label');label.textContent=`Speaker ${String(id).padStart(2,'0')}`;const lane=document.createElement('div');lane.className='lane';for(const s of segments.filter(s=>s.kind==='standard'&&s.speaker===id)){const b=document.createElement('button');b.className='turn';b.style.left=100*s.start/r.duration+'%';b.style.width=100*(s.end-s.start)/r.duration+'%';b.style.background=colors[id%colors.length];b.title=`Speaker ${id} · ${fmt(s.start+(r.source_offset_seconds||0))}`;b.onclick=()=>seek(s.start,s);turnButtons.push({segment:s,button:b});lane.append(b);}track.append(label,lane);return track;}));
 $('export').hidden=false;$('export').href='#';$('export').download='meeting.json';$('analysisHint').textContent=`Analyzed: ${fmt(r.source_offset_seconds||0)}–${fmt((r.source_offset_seconds||0)+r.duration)} · Click text to replay.`;
}
$('filter').onchange=()=>{rows();syncTranscript(replay.currentTime-(result.source_offset_seconds||0));};
function setAnalysisBusy(on,label='Preparing analysis…'){
 const overlay=$('analysisLoading'),opening=on&&overlay.hidden;
 overlay.hidden=!on;$('loadingLabel').textContent=label;
 for(const element of [document.querySelector('nav.tabs'),$('livePanel'),$('analysisPanel')])element.inert=on;
 document.body.setAttribute('aria-busy',String(on));
 if(opening)overlay.focus();
}
function canAnalyze(s){
 const video=live.contentDocument?.getElementById('video');
 return Boolean(!connectionUncertain&&!submitting&&s&&meetingId&&s.id===meetingId&&['transcribing','stopped'].includes(s.state)&&s.live_seconds>0&&video?.paused&&!video.seeking);
}
function watchLivePlayer(){
 const video=live.contentDocument?.getElementById('video');
 if(!video)return;
 for(const event of ['play','pause','seeking','seeked'])video.addEventListener(event,()=>{
  if(current)renderStage(current);
  if(event==='play'&&!$('analysisPanel').hidden&&current?.state==='transcribing')tab('live');
 });
 if(current)renderStage(current);
}
live.addEventListener('load',watchLivePlayer);
watchLivePlayer();
$('pauseLive').onclick=async()=>{
 const video=live.contentDocument?.getElementById('video');
 if(!video||submitting||current?.state!=='transcribing')return;
 try{if(video.paused)await video.play();else video.pause();}catch(e){$('phase').textContent=e.message;}
};
$('finish').onclick=async()=>{
 if(!canAnalyze(current))return;
 submitting=true;setAnalysisBusy(true);
 try{const v=live.contentDocument.getElementById('video');v.pause();$('finish').disabled=true;await api('/api/meeting-analysis/end',{source_end:v.currentTime});if(current.state==='transcribing')live.contentWindow.sendEnd();renderStage({id:meetingId,state:'finishing_asr'});}
 catch(e){uncertain(e);}
 finally{submitting=false;}
};

function renderStage(s){
 const recording=s.state==='transcribing', finishing=s.state==='finishing_asr', busy=['diarizing'].includes(s.state);
 setAnalysisBusy(submitting||finishing||busy,s.state==='diarizing'?'Analyzing speakers…':'Preparing analysis…');
 const eligible=Boolean(meetingId&&s.id===meetingId);
 const hasResult=Boolean(eligible&&result&&s.result_ready), paused=canAnalyze(s);
 $('analysisTab').disabled=!(eligible&&(paused||busy||hasResult||s.state==='failed'));
 $('analysisTab').title=$('analysisTab').disabled?(recording?'Pause live transcription first':'Start live transcription first'):'View speaker analysis';
 $('pauseLive').hidden=!recording;
 $('pauseLive').disabled=submitting||connectionUncertain;
 $('pauseLive').textContent=paused?'Resume':'Pause';
 $('finish').hidden=hasResult;
 $('finish').disabled=!paused;
 $('finish').textContent=finishing?'Finishing transcription…':busy?'Analyzing…':'Analyze speakers';
 $('finish').title=paused?'Analyze the recording so far':recording?'Pause live transcription first':'Start live transcription first';
 const phases={ready:'Click Start below to begin',transcribing:paused?'Paused · Ready to analyze':'Live · Pause to analyze',finishing_asr:'Finishing transcription…',stopped:paused?'Stopped · Ready to analyze':'Stopped',diarizing:'Analyzing speakers…',complete:'Analysis ready',failed:'Analysis failed'};
 $('phase').textContent=phases[s.state]||s.state;
 $('emptyState').hidden=hasResult;
 for(const id of ['resultGrid','timeline','mediaCredit','filter'])$(id).hidden=!hasResult;
 $('export').hidden=!hasResult;
 $('analysisHint').hidden=!hasResult;
 $('emptyTitle').textContent=paused?'Ready to analyze':recording?'Transcription in progress':finishing?'Finishing transcription…':busy?'Analyzing speakers…':s.state==='failed'?'Analysis failed':'Transcribe first';
 $('emptyHint').textContent=paused?'Analyze the recorded audio.':recording?'Pause live transcription to analyze speakers.':finishing?'Collecting the final words.':busy?'Speaker labels will appear here.':s.state==='failed'?'Return to Step 1 to try again.':'Start live transcription in Step 1.';
 $('goLive').hidden=finishing||busy;
}

async function poll(){
 if(polling)return;polling=true;
 try{
  let s=await api('/api/meeting-analysis');
  connectionUncertain=false;$('retryStatus').hidden=true;
  if(['transcribing','finishing_asr','diarizing'].includes(s.state))meetingId=s.id;
  savedRecording=['stopped','complete'].includes(s.state)&&s.live_seconds>0?s:null;
  $('restoreMeeting').textContent=s.state==='complete'?'View last result':'Use recorded audio';
  $('restoreMeeting').hidden=!(savedRecording&&meetingId!==s.id);
  if(!meetingId||s.id!==meetingId)s={state:'ready',result_ready:false};
  if(s.state==='transcribing'&&(!current||s.id!==current.id)){
   result=null;loadedId=undefined;selectedTurn=null;activeRow=null;visibleRows=[];turnButtons=[];$('matchHint').hidden=true;$('rows').textContent='Live transcription is running. Speaker labels will appear after the meeting ends.';$('tracks').replaceChildren();$('summary').textContent='';$('filter').disabled=true;$('export').hidden=true;replay.pause();replay.removeAttribute('src');
  }
  current=s;
  renderStage(s);
  const messages={diarizing:'Analyzing speakers…',failed:'Analysis failed: '+s.error};
  if(['diarizing'].includes(s.state)){
   $('progress').textContent=messages[s.state]+(s.progress?` · ${s.progress.completed}/${s.progress.total}`:'');
   if(['transcribing','finishing_asr'].includes(previousState))tab('analysis');
  }else $('progress').textContent=s.state==='failed'?messages.failed:'';
  if(s.result_ready&&loadedId!==s.id){const completed=await api('/api/meeting-analysis/result?session='+encodeURIComponent(s.id));if(completed.session_id!==s.id)throw Error('Analysis session changed; checking status again.');showResult(completed);loadedId=s.id;renderStage(s);tab('analysis');}
  previousState=s.state;
 }catch(e){uncertain(e);}
 finally{polling=false;setTimeout(poll,600);}
}
$('retryStatus').onclick=()=>poll();
$('export').onclick=event=>{
 event.preventDefault();
 if(!result||loadedId!==meetingId)return;
 const href=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));
 const link=document.createElement('a');link.href=href;link.download='meeting-'+loadedId+'.json';
 document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(href),1000);
};
poll();

replay.addEventListener('timeupdate',()=>{if(!result)return;const t=replay.currentTime-(result.source_offset_seconds||0);syncTranscript(t);if(!replay.paused&&t>=result.duration)replay.pause();});
replay.addEventListener('seeked',()=>{if(result)syncTranscript(replay.currentTime-(result.source_offset_seconds||0),true);});
replay.addEventListener('play',()=>{if(result&&(replay.currentTime<(result.source_offset_seconds||0)||replay.currentTime>=(result.source_offset_seconds||0)+result.duration))replay.currentTime=result.source_offset_seconds||0;});
