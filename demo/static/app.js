'use strict';
const $ = id => document.getElementById(id);
const colors = ['#79dfc9', '#9aafff', '#e9b983', '#d6a1ef', '#ef94a6', '#89c9ec', '#c5d78b', '#aebed2'];
const player = $('player');
let media = [], selected = null, result = null, jobId = null, mode = 'standard';
let busy = false, uploading = false, pollTimer = null, animation = null;
let activeKey = null, rows = new Map(), lanes = new Map();
const color = id => colors[id % colors.length];
const label = id => `Speaker ${String(id).padStart(2, '0')}`;
function clock(time) {
  time = Math.max(0, time || 0);
  return `${String(Math.floor(time / 60)).padStart(2, '0')}:${String(Math.floor(time % 60)).padStart(2, '0')}`;
}
function node(tag, className, text) {
  const value = document.createElement(tag);
  if (className) value.className = className;
  if (text !== undefined) value.textContent = text;
  return value;
}
function notice(message) { $('notice').textContent = message || ''; $('notice').hidden = !message; }
function status(text, className = '') { $('status').textContent = text; $('status').className = className; }
function workflow(state, note) {
  $('analysisStep').className = `workflow-step ${state === 'complete' ? 'done' : 'current'}`;
  $('analysisMark').textContent = state === 'complete' ? '✓' : '1';
  $('analysisStepNote').textContent = note;
  $('replayStep').className = `workflow-step ${state === 'complete' ? 'current' : ''}`;
}
function controls() {
  $('recording').disabled = busy || uploading;
  $('uploadButton').disabled = busy || uploading;
  $('analyze').disabled = busy || uploading || !selected;
  $('analyze').textContent = result ? 'Reanalyze recording' : 'Analyze full recording';
  $('mode').disabled = !result;
}
async function api(url, options) {
  const response = await fetch(url, options), data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
function link(text, url) {
  const value = node('a', '', text);
  if (typeof url === 'string' && /^https:\/\//.test(url)) { value.href = url; value.target = '_blank'; value.rel = 'noopener noreferrer'; }
  return value;
}
function sourceInfo() {
  $('sourceCredit').replaceChildren(); $('fullCredit').replaceChildren();
  if (!selected) return;
  $('sourceMeta').textContent = `${selected.title} · ${clock(selected.duration)} · ${selected.media_type === 'video' ? 'Video with sound' : 'Audio recording'}`;
  if (selected.credit) {
    const c = selected.credit;
    $('sourceCredit').append(link(c.title.split(' / ')[0], c.url), ' · ', link(c.license, c.license_url));
    $('fullCredit').append(link(c.title, c.url), ' · ', link(c.license, c.license_url), ` · ${c.changes}`);
  } else $('sourceCredit').textContent = 'Uploaded recording · stored on this device';
}
function overlay(title, text, step = '1', progress = false) {
  $('screenOverlay').hidden = false; $('overlayTitle').textContent = title;
  $('overlayText').textContent = text; $('overlayStep').textContent = step;
  $('progress').hidden = !progress; $('stageCount').textContent = '';
}
function clearResult() {
  if (pollTimer) clearTimeout(pollTimer);
  player.pause(); player.controls = false;
  result = null; jobId = null; activeKey = null; rows.clear(); lanes.clear();
  workflow('ready', 'Playback waits for completion');
  $('activeLabel').textContent = 'Waiting for analysis';
  $('activeHint').textContent = 'Speaker labels appear after the whole recording is analyzed.';
  $('currentSpeaker').className = 'current-speaker'; $('videoBadge').hidden = true;
  $('speakers').replaceChildren(node('p', 'muted', 'Analyze the meeting to reveal its speakers.'));
  $('speakerCount').textContent = '—'; $('position').textContent = '00:00';
  $('duration').textContent = selected ? clock(selected.duration) : '';
  $('timeline').hidden = true; $('timelineEmpty').hidden = false;
  $('timelineEmpty').textContent = 'Your speaker timeline will appear here.';
  $('timelineHint').textContent = 'Analyze first, then click a colored segment to replay.';
  $('analysisInfo').textContent = 'Complete recording → analysis → replay';
  for (const id of ['jsonExport', 'rttmExport']) { $(id).removeAttribute('href'); $(id).setAttribute('aria-disabled', 'true'); }
  overlay('Analyze the full recording first', 'Playback unlocks after all speaker labels have been generated.'); controls();
}
function preview() {
  player.removeAttribute('poster'); player.removeAttribute('src');
  $('audioBackdrop').hidden = selected?.media_type === 'video';
  if (selected) {
    player.src = selected.playback_url || `/api/media/${selected.id}`;
    if (selected.poster_url) player.poster = selected.poster_url;
  }
  player.load();
}
async function loadMedia(preferId) {
  const data = await api('/api/media'); media = data.items.filter(m => m.visible !== false);
  // An ongoing job remains reconnectable even if its built-in item is hidden.
  if (data.active_job) {
    const job = await api(`/api/jobs/${data.active_job}`);
    if (!media.some(m => m.id === job.media_id)) media.push(data.items.find(m => m.id === job.media_id));
    preferId = job.media_id;
  }
  $('recording').replaceChildren();
  for (const item of media.filter(Boolean)) { const option = node('option', '', item.title); option.value = item.id; $('recording').append(option); }
  selected = media.find(m => m.id === preferId) || media[0] || null;
  if (selected) $('recording').value = selected.id;
  else $('recording').append(node('option', '', 'Upload a recording to begin'));
  sourceInfo(); controls(); return data;
}
async function choose() {
  selected = media.find(m => m.id === $('recording').value) || null;
  clearResult(); preview(); sourceInfo(); notice(null); status('Ready to analyze');
  if (selected?.last_job) {
    try { const job = await api(`/api/jobs/${selected.last_job}`); if (job.status === 'complete') showResult(job, true); }
    catch { /* Expired job: allow a new analysis. */ }
  }
}
function stageText(stage) {
  return ({preparing:'Preparing the recording', loading:'Loading the models', segmentation:'Finding speech', embedding:'Comparing voices', clustering:'Grouping speakers', reconstruction:'Building the timeline', finalizing:'Preparing playback'})[stage] || 'Analyzing the meeting';
}
function beginProgress() {
  busy = true; controls(); status('Analyzing full recording…', 'busy');
  workflow('analyzing', 'Processing · playback locked');
  overlay('Analyzing the full recording', 'Playback is locked until the entire recording has been analyzed.', '1', true);
  $('progress').value = 0;
}
async function poll() {
  try {
    const job = await api(`/api/jobs/${jobId}`);
    if (job.status === 'complete') { showResult(job); return; }
    if (job.status === 'failed') throw new Error(job.error || 'Analysis failed.');
    $('overlayTitle').textContent = stageText(job.stage);
    if (job.stage === 'analyzing') $('progress').removeAttribute('value');
    else $('progress').value = job.percent;
    $('stageCount').textContent = `${Math.round(job.elapsed_seconds)} s elapsed${['segmentation','embedding'].includes(job.stage) ? ` · ${job.completed} / ${job.total}` : ''}`;
    pollTimer = setTimeout(poll, 500);
  } catch (error) {
    busy = false; workflow('ready', 'Analysis incomplete · replay locked'); status('Analysis unavailable', 'failed'); notice(error.message + ' Refresh to reconnect.');
    overlay('Analysis unavailable', 'Refresh the page to reconnect, or try again.'); controls();
  }
}
async function analyze() {
  if (!selected || busy) return;
  clearResult(); notice(null); beginProgress();
  try { jobId = (await api('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({media_id:selected.id})})).job_id; await poll(); }
  catch (error) { busy = false; workflow('ready', 'Playback waits for completion'); status('Ready to analyze'); notice(error.message); overlay('Analysis could not start', error.message); controls(); }
}
function segments() { return result ? result.segments.filter(s => s.kind === mode) : []; }
function speakerIds() { return result ? result.summary.standard.speakers.map(s => s.speaker) : []; }
function showResult(job, saved = false) {
  busy = false; result = job.result; jobId = job.id; activeKey = null;
  if (selected) selected.last_job = job.id;
  $('screenOverlay').hidden = true; player.controls = true;
  status(saved ? 'Saved analysis loaded' : 'Full analysis complete'); notice(null); controls();
  workflow('complete', `${clock(result.duration)} analyzed · ${result.analysis_seconds.toFixed(1)} s processing`);
  $('speakerCount').textContent = speakerIds().length;
  $('duration').textContent = clock(result.duration);
  $('analysisInfo').textContent = `Analyzed in ${result.analysis_seconds.toFixed(1)} s · ${speakerIds().length} speakers`;
  for (const id of ['jsonExport','rttmExport']) $(id).setAttribute('aria-disabled','false');
  $('jsonExport').href = `/api/jobs/${jobId}/export?format=json`;
  render(); updatePlayback();
}
function render() {
  if (!result) return;
  const ids = speakerIds(), current = segments();
  $('timelineHint').textContent = mode === 'standard' ? 'Click a colored segment to replay that turn.' : 'One speaker at a time · click a segment to replay.';
  $('rttmExport').href = `/api/jobs/${jobId}/export?format=rttm&mode=${mode}`;
  $('speakers').replaceChildren(); rows.clear();
  for (const id of ids) {
    const row = node('div','speaker-row'); row.style.setProperty('--speaker-color',color(id));
    row.append(node('span','dot'),node('span','',label(id)),node('span','row-state','—'));
    $('speakers').append(row); rows.set(id,row);
  }
  if (!ids.length) $('speakers').append(node('p','muted','No speech detected.'));
  $('timeline').hidden = !ids.length; $('timelineEmpty').hidden = !!ids.length;
  if (!ids.length) $('timelineEmpty').textContent = 'No speech detected. You can still play the recording.';
  const width = $('timeline').clientWidth - parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--label'));
  const target = result.duration * 70 / Math.max(width,200);
  const step = [1,2,5,10,15,30,60,120,300,600].find(x=>x>=target) || 600;
  $('ruler').replaceChildren(node('div','lane-label','Time'));
  const ruler = node('div','plot'); ruler.style.setProperty('--grid',`${step/result.duration*100}%`);
  for (let t=0;t<result.duration;t+=step) { const tick=node('span','tick',clock(t)); tick.style.left=`${t/result.duration*100}%`; ruler.append(tick); }
  $('ruler').append(ruler); $('tracks').replaceChildren(); lanes.clear();
  for (const id of ids) {
    const lane=node('div','lane'); lane.style.setProperty('--speaker-color',color(id));
    const name=node('div','lane-label'); name.append(node('span','dot'),label(id));
    const plot=node('div','plot'); plot.style.setProperty('--grid',`${step/result.duration*100}%`);
    for (const s of current.filter(s=>s.speaker===id)) {
      const start=Math.max(0,s.start),end=Math.min(result.duration,s.end); if(end<=start)continue;
      const block=node('button','segment'); block.style.left=`${start/result.duration*100}%`; block.style.width=`${(end-start)/result.duration*100}%`;
      block.title=`${label(id)} · ${clock(s.start)} – ${clock(s.end)}`; block.setAttribute('aria-label',block.title);
      block.onclick=()=>{player.currentTime=start;player.play().catch(()=>notice('Select Play in the video controls to begin playback.'));updatePlayback();}; plot.append(block);
    }
    lane.append(name,plot); $('tracks').append(lane); lanes.set(id,lane);
  }
  activeKey=null; updatePlayback();
}
function updatePlayback() {
  if(!result)return;
  const time=player.currentTime||0; $('position').textContent=clock(time);
  const active=new Set(segments().filter(s=>s.start<=time&&time<s.end).map(s=>s.speaker));
  const key=[...active].sort((a,b)=>a-b).join(',');
  if(key!==activeKey) {
    activeKey=key;
    $('activeLabel').textContent=active.size ? [...active].map(label).join(' + ') : 'No speech';
    $('currentSpeaker').classList.toggle('has-speaker',!!active.size);
    if(active.size) $('currentSpeaker').style.setProperty('--speaker-color',color([...active][0]));
    $('videoBadge').hidden=!active.size; $('videoBadge').textContent=[...active].map(label).join(' + ');
    for(const [id,row] of rows) {row.classList.toggle('active',active.has(id));row.querySelector('.row-state').textContent=active.has(id)?'●':'—';}
    for(const [id,lane] of lanes)lane.classList.toggle('active',active.has(id));
  }
  $('activeHint').textContent=active.size?(player.paused?'Saved label at playback position':'Replaying precomputed speaker labels'):'No speech at this playback position.';
  $('currentSpeaker').classList.toggle('is-playing',active.size>0&&!player.paused);
  const labelWidth=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--label'));
  $('playhead').style.left=`${labelWidth+Math.min(time/result.duration,1)*Math.max(0,$('timeline').clientWidth-labelWidth)}px`;
}
function animate(){updatePlayback();if(!player.paused&&!player.ended)animation=requestAnimationFrame(animate);else animation=null;}
async function upload(file) {
  if(!file||busy||uploading)return;
  if(file.size>200*1024*1024){notice('Choose a file smaller than 200 MB.');return;}
  uploading=true;clearResult();controls();notice(null);status('Uploading…','busy');
  overlay('Uploading the recording','Video audio is prepared locally before analysis.','↑',true);$('progress').value=0;
  try {
    const id=await new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();xhr.open('POST',`/api/uploads?name=${encodeURIComponent(file.name)}`);
      xhr.upload.onprogress=e=>{if(e.lengthComputable)$('progress').value=e.loaded/e.total*100;};
      xhr.onerror=()=>reject(new Error('Upload connection failed.'));
      xhr.onload=()=>{try{const data=JSON.parse(xhr.responseText);if(xhr.status===201)resolve(data.media_id);else reject(new Error(data.error||'Upload failed.'));}catch{reject(new Error('Upload failed.'));}};
      xhr.send(file);
    });
    await loadMedia(id);await choose();
  }catch(error){status('Upload failed','failed');notice(error.message);overlay('Upload could not finish',error.message);}
  finally{uploading=false;$('upload').value='';controls();}
}
$('recording').onchange=choose;$('analyze').onclick=analyze;
$('uploadButton').onclick=()=>$('upload').click();$('upload').onchange=()=>upload($('upload').files[0]);
$('detailsButton').onclick=()=>$('details').showModal();$('closeDetails').onclick=()=>$('details').close();
$('mode').onchange=()=>{mode=$('mode').value;render();};
for(const event of ['timeupdate','seeked','pause','ended'])player.addEventListener(event,updatePlayback);
player.addEventListener('play',()=>{if(!animation)animation=requestAnimationFrame(animate);});
player.addEventListener('error',()=>{if(selected&&player.getAttribute('src'))notice('Playback is unavailable. Try reloading or use an MP4 with H.264 video and AAC audio.');});
new ResizeObserver(()=>{if(result)render();}).observe($('timeline'));
async function initialize() {
  try {
    const data=await loadMedia();clearResult();preview();
    if(data.active_job){beginProgress();jobId=data.active_job;await poll();}
    else if(selected?.last_job){const job=await api(`/api/jobs/${selected.last_job}`);if(job.status==='complete')showResult(job, true);}
  }catch(error){notice(error.message);status('Connection unavailable','failed');}
}
initialize();
