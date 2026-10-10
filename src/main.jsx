import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ArrowRight, ArrowDown, Check, FileCsv, UploadSimple, DownloadSimple, SlidersHorizontal, Robot, WarningCircle, X, ArrowClockwise, Table, ListChecks } from '@phosphor-icons/react';
import './styles.css';

const DEFAULTS = { trim_whitespace: true, normalize_headers: true, normalize_missing: true, normalize_numbers: false, remove_empty_columns: true, quarantine_sparse_columns: true, remove_blank_rows: true, remove_duplicates: false };
const RULES = [
  ['trim_whitespace', 'Trim extra spaces', 'Remove spaces at the start and end of cells.'],
  ['normalize_headers', 'Clean column headers', 'Trim labels and give blank or repeated labels unique names.'],
  ['normalize_missing', 'Standardize missing markers', 'Replace your exact markers with empty cells.'],
  ['normalize_numbers', 'Clean numeric separators', 'Turn 1,200 into 1200 in fully numeric columns. IDs stay intact.'],
  ['remove_empty_columns', 'Remove fully empty columns', 'Remove columns that contain no values anywhere in the file.'],
  ['quarantine_sparse_columns', 'Archive unnamed sparse columns', 'Move unnamed columns with under 1% populated cells to a separate review file.'],
  ['remove_blank_rows', 'Remove empty rows', 'Remove records with no values after cleaning.'],
  ['remove_duplicates', 'Remove identical rows', 'Keep the first copy. Enable only if repeats are unwanted.'],
];
const TOOL_LABELS = Object.fromEntries(RULES.map(([key, label]) => [key, label]));

function download(content, filename, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = filename; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function fileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',')[1]);
    reader.onerror = () => reject(new Error('Could not read this file. Try choosing it again.'));
    reader.readAsDataURL(file);
  });
}

function UploadPanel({ file, busy, onFile, onSample, delimiter, onDelimiter }) {
  const input = useRef();
  const [dragging, setDragging] = useState(false);
  return <section className="surface p-6">
    <div className="section-title"><span className="step-number">01</span><h2>Your dataset</h2><FileCsv size={20} /></div>
    <button className={`drop-zone ${dragging ? 'dragging' : ''}`} disabled={busy}
      onClick={() => input.current.click()}
      onDragOver={e => { e.preventDefault(); if (!busy) setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={e => { e.preventDefault(); setDragging(false); if (!busy && e.dataTransfer.files[0]) onFile(e.dataTransfer.files[0]); }}>
      <span className="upload-icon"><UploadSimple size={27} /></span>
      <strong>{file ? file.name : 'Drop your CSV here'}</strong>
      <span>{file ? `${(file.size / 1024).toFixed(1)} KB · Click to replace` : 'or click to choose a file'}</span>
      <span className="file-limit">CSV · Up to 5 MB</span>
    </button>
    <input ref={input} type="file" accept=".csv,.tsv,text/csv,text/tab-separated-values" className="sr-only" onChange={e => { if (e.target.files[0]) onFile(e.target.files[0]); e.target.value = ''; }} />
    <div className="flex items-end justify-between gap-3 mt-5">
      <label className="field-label">Column separator
        <select value={delimiter} disabled={busy} onChange={e => onDelimiter(e.target.value)}>
          <option value="auto">Auto-detect</option><option value=",">Comma</option><option value=";">Semicolon</option><option value={'\t'}>Tab</option><option value="|">Pipe</option>
        </select>
      </label>
      <button className="text-button pb-2" disabled={busy} onClick={onSample}>Try sample <ArrowRight size={15} /></button>
    </div>
    <p className="helper mt-4">Your original file stays untouched. Previewing happens locally.</p>
  </section>;
}

function RulesPanel({ options, setOptions, markers, setMarkers, consent, setConsent, disabled }) {
  return <section className="surface p-6">
    <div className="section-title"><span className="step-number">02</span><h2>Cleaning permissions</h2><SlidersHorizontal size={20} /></div>
    <div className="divide-y divide-stone-100">
      {RULES.map(([key, title, description]) => <label key={key} className="rule">
        <input type="checkbox" checked={options[key]} disabled={disabled} onChange={e => setOptions({ ...options, [key]: e.target.checked })} />
        <span><strong>{title}</strong><small>{description}</small></span>
      </label>)}
    </div>
    <label className="field-label mt-4">Your missing-value markers
      <input type="text" value={markers} disabled={disabled || !options.normalize_missing} placeholder="e.g. N/A, NULL" onChange={e => setMarkers(e.target.value)} />
      <small className="helper">Comma-separated and case-sensitive. Leave blank to preserve all text.</small>
    </label>
    <label className="consent"><input type="checkbox" checked={consent} disabled={disabled} onChange={e => setConsent(e.target.checked)} />
      <span>Allow Gemini to read column names and quality statistics. Raw rows stay local.</span>
    </label>
  </section>;
}

function Metric({ label, before, after }) {
  return <div className="metric"><span>{label}</span><div><strong>{before.toLocaleString()}</strong>{after !== undefined && <><ArrowRight size={17} /><strong className="after-value">{after.toLocaleString()}</strong></>}</div></div>;
}

function DataPreview({ inspection, result, view, setView }) {
  const cleaned = view === 'after' && result;
  const data = cleaned ? result : inspection;
  const changed = new Set(result?.report.changes.filter(c => c.column && c.record !== 1).map(c => `${c.record}:${c.column}`));
  return <section className="surface overflow-hidden">
    <div className="preview-heading px-6 py-5"><div><h2>Dataset preview</h2><p>First 20 records · Scroll to see all columns</p></div>
      {result && <div className="segmented" aria-label="Preview version"><button className={view === 'before' ? 'selected' : ''} onClick={() => setView('before')}>Original</button><button className={view === 'after' ? 'selected' : ''} onClick={() => setView('after')}>Cleaned</button></div>}
    </div>
    <div className="table-scroll"><table><thead><tr><th className="record-cell">Record</th>{data.headers.map((h, i) => <th key={i}>{h || '(blank header)'}</th>)}</tr></thead>
      <tbody>{data.rows.map((row, index) => { const record = cleaned ? result.row_ids[index] : index + 2;
        return <tr key={record}><td className="record-cell">{record}</td>{row.map((value, col) => <td key={col} className={cleaned && changed.has(`${record}:${result.column_ids?.[col] ?? col + 1}`) ? 'changed-cell' : ''} title={value}>
          {value === '' ? <span className="empty-cell">empty</span> : <span className="cell-value">{value}</span>}
        </td>)}</tr>;
      })}</tbody></table>{!data.rows.length && <p className="p-6 helper">This dataset contains headers but no data rows.</p>}</div>
    <div className="preview-footer">{cleaned ? <><span className="legend-swatch" /> Highlighted cells were changed. Record numbers refer to your original CSV.</> : 'Values are shown exactly as they appear in your file, including spaces.'}</div>
  </section>;
}

function AgentLog({ events, running, failed }) {
  const logRef = useRef();
  useEffect(() => { if (running) logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' }); }, [events, running]);
  const visible = events.filter(e => e.phase !== 'result');
  return <section className="surface p-6 agent-panel">
    <div className="section-title"><span className="step-number">03</span><h2>Agent activity</h2><Robot size={21} /></div>
    <div className="loop-diagram"><span>Inspect</span><ArrowRight size={13} /><span>Decide</span><ArrowRight size={13} /><span>Act</span><ArrowRight size={13} /><span>Verify</span></div>
    <div className="agent-log" ref={logRef} aria-live="polite" aria-relevant="additions">
      {!visible.length && <div className="log-empty"><ListChecks size={32} /><strong>Every decision, explained.</strong><p>Run the agent to follow its tool choices and verification results here.</p></div>}
      {visible.map((event, index) => <div key={index} className={`log-event ${event.phase}`} style={{ '--index': index }}>
        <span className="event-dot">{event.phase === 'verify' || event.phase === 'finish' ? <Check size={11} weight="bold" /> : event.phase === 'error' ? <X size={11} /> : <span />}</span>
        <div><span className="event-label">{event.phase}{event.tool && ` / ${event.tool.replaceAll('_', ' ')}`}</span><p>{event.message}</p></div>
      </div>)}
      {running && <div className="thinking"><span /><span /><span /><small>Agent working</small></div>}
    </div>
    <div className="log-footer">{running ? 'Live activity' : failed ? 'Stopped · original file preserved' : visible.length ? 'Run complete · export verified' : 'Waiting for a dataset'}</div>
  </section>;
}

function ReviewPanel({ result }) {
  const review = result.report.review;
  return <section className="surface p-6"><div className="section-title"><WarningCircle size={20} /><h2>Needs your review</h2><span className="count-badge">{review.length}</span></div>
    {review.length ? <div className="review-list">{review.map((issue, i) => <div key={i} className="review-item"><strong>{issue.column ? `${issue.column} · ` : ''}{issue.type.replaceAll('_', ' ')}{issue.count !== undefined ? ` (${issue.count})` : ''}</strong><p>{issue.message}</p>{issue.records && <small>Original records: {issue.records.slice(0, 12).join(', ')}{issue.records.length > 12 ? ` and ${issue.records.length - 12} more` : ''}</small>}</div>)}</div> : <p className="helper">No issues were found by the implemented checks. Domain-specific validation may still be needed.</p>}
  </section>;
}

function reportMarkdown(report, filename) {
  return `# Dataset cleaning report\n\nFile: ${filename}\nPlanner: Gemini (${report.model})\n\n## Before and after\n\n| Measure | Before | After |\n|---|---:|---:|\n${[['Rows', 'rows'], ['Columns', 'columns'], ['Missing cells', 'missing_cells'], ['Duplicate rows', 'duplicate_rows'], ['Whitespace cells', 'whitespace_cells']].map(([label, key]) => `| ${label} | ${report.before[key]} | ${report.after[key]} |`).join('\n')}\n\n## Agent actions\n\n${report.actions.length ? report.actions.map(a => `${a.step}. **${a.tool}** — ${a.reason} (${a.changes} changes)`).join('\n') : 'No edits were needed for the enabled tools.'}\n\n## Review findings\n\n${report.review.length ? report.review.map(r => `- ${r.column ? `${r.column}: ` : ''}${r.message}${r.records ? ` Original records: ${r.records.join(', ')}.` : ''}`).join('\n') : 'No issues found by the implemented checks.'}\n\n## Verification\n\nCSV export round-trip: passed.\nEnabled fixes completed: ${report.verification.enabled_fixes_completed}.\n\nInput SHA-256: ${report.input_sha256}\nOutput SHA-256: ${report.output_sha256}\n\n${report.notes.join('\n\n')}\n\nThe JSON audit file contains every cell edit and deleted record.\n`;
}

function App() {
  const [status, setStatus] = useState(null), [file, setFile] = useState(null), [encoded, setEncoded] = useState('');
  const [inspection, setInspection] = useState(null), [result, setResult] = useState(null);
  const [options, setOptions] = useState(DEFAULTS), [markers, setMarkers] = useState(''), [consent, setConsent] = useState(false);
  const [delimiter, setDelimiter] = useState('auto'), [error, setError] = useState(''), [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(false), [running, setRunning] = useState(false), [view, setView] = useState('after');
  const controller = useRef(null), uploadSequence = useRef(0);
  const busy = loading || running;

  async function refreshStatus() {
    try { const response = await fetch('/api/status'); if (!response.ok) throw new Error(); setStatus(await response.json()); }
    catch { setStatus({ configured: false, unavailable: true }); }
  }
  useEffect(() => { refreshStatus(); return () => controller.current?.abort(); }, []);

  async function loadFile(nextFile, nextDelimiter = delimiter) {
    const sequence = ++uploadSequence.current;
    setError(''); setResult(null); setEvents([]); setInspection(null); setEncoded('');
    if (nextFile.size > 5 * 1024 * 1024 || !nextFile.size) { setFile(null); setError('Choose a non-empty CSV of at most 5 MB.'); return; }
    setFile(nextFile); setLoading(true);
    try {
      const content = await fileAsBase64(nextFile);
      const response = await fetch('/api/inspect', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file: content, delimiter: nextDelimiter }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'Could not inspect this CSV.');
      if (sequence === uploadSequence.current) { setEncoded(content); setInspection(body); }
    } catch (e) { if (sequence === uploadSequence.current) setError(e.message); }
    finally { if (sequence === uploadSequence.current) setLoading(false); }
  }

  async function loadSample() {
    try {
      const response = await fetch('/api/sample'); if (!response.ok) throw new Error('Could not load the sample.');
      const blob = await response.blob();
      setOptions({ ...DEFAULTS, normalize_numbers: true, remove_duplicates: true }); setMarkers('N/A'); setDelimiter('auto');
      await loadFile(new File([blob], 'messy_lab_data.csv', { type: 'text/csv' }), 'auto');
    } catch (e) { setError(e.message); }
  }

  async function clean() {
    setError(''); setEvents([]); setResult(null); setRunning(true);
    controller.current = new AbortController();
    let completed = false;
    try {
      const response = await fetch('/api/clean', { method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.current.signal,
        body: JSON.stringify({ file: encoded, delimiter: inspection.delimiter, allow_profile_transfer: consent,
          options: { ...options, missing_markers: markers.split(',').map(s => s.trim()).filter(Boolean) } }) });
      if (!response.ok) { const body = await response.json(); throw new Error(body.error || 'The agent could not start.'); }
      const reader = response.body.getReader(), decoder = new TextDecoder(); let pending = '';
      function consume(line) {
        if (!line.trim()) return;
        const event = JSON.parse(line);
        if (event.phase === 'error') throw new Error(event.message);
        if (event.phase === 'result') { completed = true; setResult(event); setView('after'); }
        else setEvents(previous => [...previous, event]);
      }
      while (true) {
        const { value, done } = await reader.read();
        pending += decoder.decode(value, { stream: !done });
        let newline; while ((newline = pending.indexOf('\n')) >= 0) { consume(pending.slice(0, newline)); pending = pending.slice(newline + 1); }
        if (done) { consume(pending); break; }
      }
      if (!completed) throw new Error('The connection ended before verification completed. Try again.');
    } catch (e) {
      const message = e.name === 'AbortError' ? 'Run stopped. Your original file is unchanged.' : e.message;
      setError(message); setEvents(previous => [...previous, { phase: 'error', message }]); controller.current?.abort();
    } finally { setRunning(false); controller.current = null; }
  }

  const profile = inspection?.profile;
  const stem = (file?.name || 'dataset').replace(/\.(csv|tsv)$/i, '');
  return <div className="min-h-[100dvh]">
    <header className="topbar"><div className="max-w-[1400px] mx-auto px-6 flex items-center justify-between h-full"><a href="/" className="brand"><span className="brand-mark"><Table size={19} weight="bold" /></span>Dataset Cleaning Agent</a><span className="header-caption">GEMINI POWERED <span className="status-dot" /></span></div></header>
    <main className="max-w-[1400px] mx-auto px-5 md:px-8 pb-12">
      <div className="hero"><div><p className="eyebrow">DATA QUALITY / WORKSPACE</p><h1>Messy data.<br /><span>Clear decisions.</span></h1><p className="hero-description">Give the agent a CSV. Follow its decisions, review every change, and take away a cleaner dataset.</p></div><div className="hero-aside"><span>YOUR CLEANING WORKFLOW</span><strong>Inspect. Decide.<br />Act. Verify.</strong><p>One agent. Eight bounded tools.<br />A complete record of every edit.</p></div></div>
      {status && !status.configured && <div className="setup-banner"><Robot size={22} /><div><strong>{status.unavailable ? 'The local server is unavailable' : 'Connect Gemini to start cleaning'}</strong><p>{status.unavailable ? 'Start the Python server, then check the connection.' : <>Copy <code>.env.example</code> to <code>.env</code> and add your <code>GEMINI_API_KEY</code>. You can preview a file now.</>}</p></div><button className="secondary small" onClick={refreshStatus}><ArrowClockwise size={16} /> Check connection</button></div>}
      <div className="workspace-grid">
        <aside className="flex flex-col gap-5">
          <UploadPanel file={file} busy={busy} onFile={loadFile} onSample={loadSample} delimiter={delimiter} onDelimiter={d => { setDelimiter(d); if (file) loadFile(file, d); }} />
          <RulesPanel options={options} setOptions={setOptions} markers={markers} setMarkers={setMarkers} consent={consent} setConsent={setConsent} disabled={busy} />
          <button className="primary run-button" disabled={!inspection || busy || !consent || !status?.configured} onClick={clean}><Robot size={20} />{running ? 'Agent is cleaning…' : 'Run cleaning agent'}<ArrowRight size={18} /></button>
          {running && <button className="text-button justify-center" onClick={() => controller.current?.abort()}>Stop run <X size={14} /></button>}
          <p className="helper text-center -mt-2">{!consent ? 'Enable profile sharing above to run Gemini.' : status?.configured ? `Planner: ${status.model}` : 'A Gemini API key is required to run the agent.'}</p>
        </aside>
        <div className="min-w-0 flex flex-col gap-5">
          {error && <div className="error-banner" role="alert"><WarningCircle size={21} /><p>{error}</p><button aria-label="Dismiss error" onClick={() => setError('')}><X size={17} /></button></div>}
          {loading ? <div className="surface p-6"><p className="eyebrow mb-5">INSPECTING YOUR FILE</p><div className="skeleton h-7 w-2/3 mb-4" /><div className="skeleton h-28 w-full" /></div> : profile ? <>
            <div className="metrics surface"><Metric label="Records" before={profile.rows} after={result?.report.after.rows} /><Metric label="Columns" before={profile.columns} after={result?.report.after.columns} /><Metric label="Empty cells" before={profile.missing_cells} after={result?.report.after.missing_cells} /><Metric label="Identical repeats" before={profile.duplicate_rows} after={result?.report.after.duplicate_rows} /></div>
            <DataPreview inspection={inspection} result={result} view={view} setView={setView} />
          </> : <section className="surface empty-workspace"><div className="empty-table"><div className="empty-table-heading" />{[0, 1, 2, 3].map(i => <div className="empty-table-row" key={i}><span /><span /><span /></div>)}</div><p className="eyebrow">READY WHEN YOU ARE</p><h2>Start with a dataset.</h2><p>Upload your CSV to inspect its shape and preview the records. Or try the lab-data sample to explore the workflow.</p><button className="secondary" onClick={loadSample}>Load sample dataset <ArrowRight size={16} /></button></section>}
          <AgentLog events={events} running={running} failed={events.some(e => e.phase === 'error')} />
          {result && <>
            <section className="export-panel"><div><p className="eyebrow">EXPORT VERIFIED</p><h2>{result.report.changes.length ? 'Your cleaned dataset is ready.' : 'No changes were made.'}</h2><p>{result.report.before.columns - result.report.after.columns} columns removed · {result.report.before.rows - result.report.after.rows} rows removed · {result.report.changes.filter(c => c.record !== 1 && c.after !== null).length} cells edited</p>{!!result.report.quarantine?.length && <p>{result.report.quarantine.length} extra values archived for review.</p>}</div><div className="download-buttons"><button className="primary" onClick={() => download('\uFEFF' + result.csv, `${stem}_cleaned.csv`, 'text/csv;charset=utf-8')}><DownloadSimple size={18} />Cleaned CSV</button><button className="secondary" onClick={() => download(reportMarkdown(result.report, file.name), `${stem}_report.md`, 'text/markdown;charset=utf-8')}><DownloadSimple size={17} />Report</button>{!!result.report.quarantine?.length && <button className="secondary" onClick={() => { const quote = v => `"${String(v).replaceAll('"', '""')}"`; const text = 'original_record,original_column,header,value\n' + result.report.quarantine.map(v => [v.record, v.original_column, v.header, v.value].map(quote).join(',')).join('\n'); download(text, `${stem}_extra_values.csv`, 'text/csv;charset=utf-8'); }}>Review extra values</button>}<button className="text-button" onClick={() => download(JSON.stringify(result.report, null, 2), `${stem}_audit.json`, 'application/json')}><ArrowDown size={15} /> Full audit JSON</button></div></section>
            <ReviewPanel result={result} />
          </>}
        </div>
      </div>
      <footer className="page-footer"><span>Originals preserved. Changes explained.</span><span>CSV / UTF-8 & UTF-16 / 25,000 ROWS MAX</span></footer>
    </main>
  </div>;
}

createRoot(document.getElementById('root')).render(<App />);
