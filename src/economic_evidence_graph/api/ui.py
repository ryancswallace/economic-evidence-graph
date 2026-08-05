# ruff: noqa: E501
"""Dependency-free local research workbench."""

WORKBENCH_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Economic Evidence Graph</title>
  <style>
    :root { color-scheme: light; --ink:#17252f; --muted:#5d6a72; --paper:#f6f4ed; --card:#fff;
      --accent:#145c52; --line:#d8d5ca; --warn:#8c4a16; }
    * { box-sizing:border-box; } body { margin:0; background:var(--paper); color:var(--ink);
      font:16px/1.5 ui-serif, Georgia, serif; }
    header, main { width:min(1080px, calc(100% - 2rem)); margin:auto; }
    header { padding:2.5rem 0 1.2rem; } h1 { margin:0; font-size:clamp(2rem,5vw,3.8rem); letter-spacing:-.04em; }
    header p { color:var(--muted); max-width:760px; } form { display:grid; gap:1rem; background:var(--card);
      border:1px solid var(--line); padding:1.25rem; border-radius:12px; box-shadow:0 10px 35px #26332b10; }
    label { font-weight:700; } input[type=search], input[type=datetime-local], input[type=date] {
      width:100%; padding:.75rem; border:1px solid #aaa69b; border-radius:6px; font:inherit; background:#fff; }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:1rem; }
    .checks { display:flex; flex-wrap:wrap; gap:.5rem 1rem; } .checks label { font-weight:400; }
    button { width:max-content; border:0; border-radius:6px; padding:.7rem 1.2rem; color:#fff;
      background:var(--accent); font:700 1rem ui-sans-serif,system-ui; cursor:pointer; }
    button:disabled { opacity:.55; } #status { min-height:2rem; margin:1rem 0; color:var(--muted); }
    article { background:var(--card); border:1px solid var(--line); padding:1.2rem; margin:1rem 0; border-radius:10px; }
    article h2 { margin:.1rem 0 .4rem; font-size:1.3rem; } .meta,.explain { color:var(--muted); font-size:.92rem; }
    .excerpt { border-left:3px solid var(--accent); padding-left:1rem; } .warning { color:var(--warn); }
    a { color:var(--accent); } details { margin-top:.8rem; } code { overflow-wrap:anywhere; }
    @media (prefers-reduced-motion:no-preference) { article { animation:rise .2s ease-out; } @keyframes rise {
      from { transform:translateY(4px); opacity:.5; } } }
  </style>
</head>
<body>
<header><h1>Economic Evidence Graph</h1><p>Search economic literature with source-level provenance,
strict historical cutoffs, and visible retrieval explanations.</p><p><a href="/projects">Open projects and evidence tables →</a></p></header>
<main>
  <form id="search-form">
    <div><label for="query">Research question or literature query</label>
      <input id="query" name="query" type="search" maxlength="500" required
        placeholder="How do balance sheet policies affect inflation expectations?"></div>
    <div class="grid">
      <div><label for="cutoff">Strict as-of cutoff (optional)</label><input id="cutoff" type="datetime-local"></div>
      <div><label for="from">Published from</label><input id="from" type="date"></div>
      <div><label for="to">Published through</label><input id="to" type="date"></div>
    </div>
    <div><label>Channels</label><div class="checks">
      <label><input name="mode" type="checkbox" value="lexical" checked> Lexical</label>
      <label><input name="mode" type="checkbox" value="semantic" checked> Deterministic semantic</label>
      <label><input name="mode" type="checkbox" value="structured"> Structured filters</label></div></div>
    <div><label>Sources</label><div class="checks" id="sources">
      <label><input type="checkbox" value="repec"> RePEc</label><label><input type="checkbox" value="crossref"> Crossref</label>
      <label><input type="checkbox" value="core"> CORE</label><label><input type="checkbox" value="boston_fed"> Boston Fed</label>
      <label><input type="checkbox" value="board_fomc"> Board/FOMC</label></div></div>
    <label><input id="save" type="checkbox"> Save a reproducible search run</label>
    <button id="submit" type="submit">Search evidence</button>
  </form>
  <div id="status" role="status" aria-live="polite"></div><section id="results" aria-label="Search results"></section>
</main>
<script>
const form=document.querySelector('#search-form'), statusNode=document.querySelector('#status'), results=document.querySelector('#results');
const text=(tag,value,cls)=>{const node=document.createElement(tag);node.textContent=value;if(cls)node.className=cls;return node};
form.addEventListener('submit',async event=>{event.preventDefault();results.replaceChildren();const button=document.querySelector('#submit');
  button.disabled=true;statusNode.textContent='Searching the active evidence generation…';
  const modes=[...document.querySelectorAll('[name=mode]:checked')].map(n=>n.value);
  const source_codes=[...document.querySelectorAll('#sources input:checked')].map(n=>n.value);
  const cutoff=document.querySelector('#cutoff').value;
  const body={query:document.querySelector('#query').value,modes,filters:{source_codes,
    published_from:document.querySelector('#from').value||null,published_to:document.querySelector('#to').value||null},
    historical_cutoff:cutoff?{at:new Date(cutoff).toISOString(),mode:'strict'}:null,collapse_versions:true,limit:20,
    save_run:document.querySelector('#save').checked};
  try {const response=await fetch('/v1/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await response.json();if(!response.ok)throw new Error(data.detail||'Search failed');
    const cutoffLabel=cutoff?`strict cutoff ${new Date(cutoff).toLocaleString()}`:'current corpus';
    statusNode.textContent=`${data.results.length} results · ${cutoffLabel} · generation ${data.index_generation_id}`;
    data.warnings.forEach(value=>results.append(text('p',value,'warning')));
    data.omitted_channels.forEach(value=>results.append(text('p',`Omitted channel: ${value}`,'warning')));
    data.results.forEach(item=>{const card=document.createElement('article');const heading=text('h2',`${item.rank}. ${item.title}`);
      const link=document.createElement('a');link.href=item.source_url;link.target='_blank';link.rel='noopener noreferrer';link.append(heading);card.append(link);
      card.append(text('p',[item.authors.join(', '),item.published_at,item.source_code].filter(Boolean).join(' · '),'meta'));
      card.append(text('p',`Eligible from ${new Date(item.temporal.eligible_from).toLocaleString()} (${item.temporal.date_basis}).`,'explain'));
      if(item.evidence?.text)card.append(text('p',item.evidence.text,'excerpt'));
      const details=document.createElement('details');details.append(text('summary','Retrieval and provenance details'));
      details.append(text('p',item.version_selection_reason));details.append(text('code',item.document_version_id));
      details.append(text('p',item.contributions.map(c=>`${c.channel}: rank ${c.rank}, +${c.contribution.toFixed(5)}`).join(' · '),'explain'));
      if(item.evidence)details.append(text('p',`Evidence ${item.evidence.evidence_unit_id} · ${item.evidence.locator} · asset ${item.evidence.acquired_asset_id}`,'explain'));
      card.append(details);results.append(card);});
  } catch(error){statusNode.textContent=error instanceof Error?error.message:'Search failed';statusNode.className='warning';}
  finally{button.disabled=false;}});
</script>
</body></html>"""


PROJECTS_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Projects · Economic Evidence Graph</title><style>
:root{--ink:#17252f;--muted:#5d6a72;--paper:#f6f4ed;--card:#fff;--accent:#145c52;--line:#d8d5ca;--warn:#8c4a16}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 ui-sans-serif,system-ui}
header,main{width:min(1180px,calc(100% - 2rem));margin:auto}header{padding:2rem 0 1rem}h1{font:700 2.4rem/1 ui-serif,Georgia}
a{color:var(--accent)}.layout{display:grid;grid-template-columns:minmax(250px,1fr) minmax(0,2fr);gap:1rem;align-items:start}
.card,form{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1rem;margin:0 0 1rem}
form{display:grid;gap:.65rem}label{font-weight:650}input,textarea,select{width:100%;padding:.55rem;border:1px solid #aaa69b;border-radius:5px;font:inherit}
button{border:0;border-radius:5px;padding:.6rem .9rem;background:var(--accent);color:#fff;font-weight:700;cursor:pointer}
button:disabled{opacity:.55}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.65rem}.meta{color:var(--muted)}
#status{min-height:2rem}.warning{color:var(--warn)}details{border-top:1px solid var(--line);padding:.7rem 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.78rem}
@media(max-width:760px){.layout{grid-template-columns:1fr}}
</style></head><body><header><p><a href="/">← Search evidence</a></p><h1>Research projects</h1>
<p class="meta">Screen literature, hand off saved runs, build anchored comparison tables, review values, and export reproducible evidence.</p></header>
<main><div class="layout"><aside>
<form id="identity"><label for="actor">Local actor identifier</label><input id="actor" value="local-user" maxlength="255" required>
<button type="button" id="refresh">Refresh my projects</button></form>
<form id="create"><h2>Create project</h2><label>Name<input id="project-name" maxlength="200" required></label>
<label>Description<textarea id="project-description" maxlength="4000"></textarea></label><button>Create</button></form>
<section class="card"><h2>My projects</h2><div id="project-list"></div></section></aside><section>
<div id="status" role="status" aria-live="polite"></div><section id="workspace" hidden>
<div class="card"><h2 id="active-name"></h2><p id="active-meta" class="meta"></p><button id="reload">Reload handoff</button></div>
<div class="grid">
<form id="attach"><h3>Attach saved run</h3><label>Search run ID<input id="run-id" required></label><button>Attach</button></form>
<form id="screen"><h3>Screen work</h3><label>Work ID<input id="screen-work" required></label><label>Version ID<input id="screen-version"></label>
<label>Decision<select id="screen-outcome"><option>include</option><option>maybe</option><option>exclude</option></select></label>
<label>Exclusion reason<input id="screen-reason" placeholder="out_of_scope"></label><button>Record decision</button></form>
</div>
<details><summary>Create one anchored evidence record</summary><form id="evidence"><div class="grid">
<label>Work ID<input id="evidence-work" required></label><label>Version ID<input id="evidence-version" required></label>
<label>Record type<input id="record-type" value="estimate" required></label><label>Field name<input id="field-name" value="estimate" required></label>
<label>Display value<input id="display-value" required></label><label>Normalized number<input id="normalized-value" type="number" step="any"></label>
<label>Units<input id="units"></label><label>Applicability<input id="applicability" value="reported specification" required></label>
<label>Evidence unit ID<input id="evidence-unit" required></label><label>Exact quote<input id="evidence-quote" maxlength="600" required></label>
</div><button>Create evidence record</button></form></details>
<details><summary>Create a comparison table</summary><form id="table"><label>Name<input id="table-name" required></label>
<label>Column key<input id="column-key" value="estimate" required></label><label>Column label<input id="column-label" value="Estimate" required></label>
<label>Value type<select id="column-type"><option>number</option><option>text</option><option>integer</option><option>boolean</option></select></label><button>Create table</button></form></details>
<details><summary>Add a comparison row</summary><form id="row"><label>Table ID<input id="row-table" required></label>
<label>Evidence record ID<input id="row-record" required></label><label>Row label<input id="row-label" required></label>
<label>Column key<input id="row-column" value="estimate" required></label><label>Evidence field ID<input id="row-field" required></label><button>Add row</button></form></details>
<details><summary>Review evidence or a policy link</summary><form id="review"><label>Target type<select id="review-type"><option>evidence_field</option><option>policy_link</option></select></label>
<label>Target ID<input id="review-target" required></label><label>Decision<select id="review-state"><option>approved</option><option>uncertain</option><option>rejected</option><option>superseded</option></select></label>
<label>Reason<input id="review-reason" required></label><button>Submit review</button></form></details>
<details><summary>Export an evidence table</summary><form id="export"><label>Table ID<input id="export-table" required></label>
<label>Format<select id="export-format"><option>json</option><option>csv</option><option>markdown</option></select></label><button>Create export</button></form></details>
<section class="card"><h2>Project handoff</h2><div id="summary"></div></section></section></section></div></main><script>
let bundle=null;const statusNode=document.querySelector('#status'),workspace=document.querySelector('#workspace');
const actor=()=>document.querySelector('#actor').value;const key=prefix=>`${prefix}-${crypto.randomUUID()}`;
async function api(path,options={}){const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-EEG-Actor':actor(),...(options.headers||{})}});
 const data=await response.json();if(!response.ok)throw new Error(data.detail||'Request failed');return data}
async function projects(){try{const data=await api('/v1/projects');const root=document.querySelector('#project-list');root.replaceChildren();
 data.forEach(item=>{const button=document.createElement('button');button.textContent=`${item.name} · r${item.revision}`;button.addEventListener('click',()=>load(item.id));root.append(button,document.createElement('br'))})}
 catch(error){show(error)}}
async function load(id){try{bundle=await api(`/v1/projects/${id}`);workspace.hidden=false;document.querySelector('#active-name').textContent=bundle.project.name;
 document.querySelector('#active-meta').textContent=`revision ${bundle.project.revision} · baseline ${bundle.project.baseline_corpus_snapshot_id||'not yet indexed'}`;render();statusNode.textContent='Project handoff loaded.'}catch(error){show(error)}}
function render(){const root=document.querySelector('#summary');root.replaceChildren();for(const [label,name] of [['Collaborators','members'],['Saved runs','search_runs'],['Screening history','screening_events'],['Evidence records','evidence_records'],['Evidence tables','evidence_tables'],['Table rows','evidence_table_rows'],['Policy links','policy_links'],['Review history','review_events'],['Exports','exports']]){
 const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent=`${label} (${bundle[name].length})`;pre.textContent=JSON.stringify(bundle[name],null,2);details.append(summary,pre);root.append(details)}}
function show(error){statusNode.textContent=error instanceof Error?error.message:String(error);statusNode.className='warning'}
async function mutate(path,body){try{const project=await api(path,{method:'POST',body:JSON.stringify({...body,expected_revision:bundle.project.revision,idempotency_key:key('ui')})});await load(project.id);await projects()}catch(error){show(error)}}
document.querySelector('#refresh').addEventListener('click',projects);document.querySelector('#reload').addEventListener('click',()=>load(bundle.project.id));
document.querySelector('#create').addEventListener('submit',async event=>{event.preventDefault();try{const item=await api('/v1/projects',{method:'POST',body:JSON.stringify({name:document.querySelector('#project-name').value,description:document.querySelector('#project-description').value,idempotency_key:key('create')})});await projects();await load(item.id)}catch(error){show(error)}});
document.querySelector('#attach').addEventListener('submit',event=>{event.preventDefault();mutate(`/v1/projects/${bundle.project.id}/search-runs`,{search_run_id:document.querySelector('#run-id').value})});
document.querySelector('#screen').addEventListener('submit',event=>{event.preventDefault();const outcome=document.querySelector('#screen-outcome').value;mutate(`/v1/projects/${bundle.project.id}/screening`,{work_id:document.querySelector('#screen-work').value,document_version_id:document.querySelector('#screen-version').value||null,outcome,reason_code:outcome==='exclude'?document.querySelector('#screen-reason').value:null,note:null})});
document.querySelector('#evidence').addEventListener('submit',event=>{event.preventDefault();const numeric=document.querySelector('#normalized-value').value;mutate(`/v1/projects/${bundle.project.id}/evidence-records`,{work_id:document.querySelector('#evidence-work').value,document_version_id:document.querySelector('#evidence-version').value,record_type:document.querySelector('#record-type').value,fields:[{field_name:document.querySelector('#field-name').value,display_value:document.querySelector('#display-value').value,normalized_value:numeric?Number(numeric):null,units:document.querySelector('#units').value||null,applicability:document.querySelector('#applicability').value,missingness_reason:null,state:'observed',lineage_method:'observed_transcription',confidence:1,anchors:[{evidence_unit_id:document.querySelector('#evidence-unit').value,quote:document.querySelector('#evidence-quote').value}]}]})});
document.querySelector('#table').addEventListener('submit',event=>{event.preventDefault();mutate(`/v1/projects/${bundle.project.id}/evidence-tables`,{name:document.querySelector('#table-name').value,description:'',columns:[{key:document.querySelector('#column-key').value,label:document.querySelector('#column-label').value,value_type:document.querySelector('#column-type').value,units:null}]})});
document.querySelector('#row').addEventListener('submit',event=>{event.preventDefault();const table=document.querySelector('#row-table').value;mutate(`/v1/projects/${bundle.project.id}/evidence-tables/${table}/rows`,{evidence_record_id:document.querySelector('#row-record').value,label:document.querySelector('#row-label').value,cells:[{column_key:document.querySelector('#row-column').value,evidence_field_id:document.querySelector('#row-field').value,missingness_reason:null}]})});
document.querySelector('#review').addEventListener('submit',event=>{event.preventDefault();mutate(`/v1/projects/${bundle.project.id}/reviews`,{target_type:document.querySelector('#review-type').value,target_id:document.querySelector('#review-target').value,new_state:document.querySelector('#review-state').value,reason:document.querySelector('#review-reason').value})});
document.querySelector('#export').addEventListener('submit',async event=>{event.preventDefault();const table=document.querySelector('#export-table').value;try{const item=await api(`/v1/projects/${bundle.project.id}/evidence-tables/${table}/exports`,{method:'POST',body:JSON.stringify({format:document.querySelector('#export-format').value,expected_revision:bundle.project.revision,idempotency_key:key('export')})});const response=await fetch(`/v1/project-exports/${item.id}/data`,{headers:{'X-EEG-Actor':actor()}});if(!response.ok)throw new Error('Export download failed');const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download=`${item.id}.${item.format==='markdown'?'md':item.format}`;link.click();URL.revokeObjectURL(url);await load(bundle.project.id)}catch(error){show(error)}});projects();
</script></body></html>"""
