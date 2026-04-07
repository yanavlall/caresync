import React, { useCallback, useEffect, useRef, useState } from 'react';

/**
 * CareSync frontend
 *
 * Single-page flow:
 *   1. Click "New Encounter" -> POST /encounters/ -> { encounter_id, pcr_id }
 *   2. Click "Start Recording" -> MediaRecorder captures audio, live level meter + timer
 *   3. Click "Stop" -> POST /encounters/{id}/extract (multipart, field "audio") -> { job_id }
 *   4. Poll GET /jobs/{job_id} every 800ms until status is completed | failed
 *   5. Render the PCRExtraction fields + transcript
 *
 * On mount and after each completed extraction we refresh GET /pcrs/.
 */

// Read API base URL from REACT_APP_API_URL if the bundler inlined it, otherwise
// default to localhost. We shim `process` so the file type-checks without
// @types/node; webpack/babel can still replace process.env.* at build time.
declare const process: { env: Record<string, string | undefined> } | undefined;
const API_URL: string =
  (typeof process !== 'undefined' && process?.env?.REACT_APP_API_URL) ||
  'http://localhost:8000';

type JobStatus = 'queued' | 'transcribing' | 'extracting' | 'completed' | 'failed';

interface PCRExtraction {
  patient_name?: string | null;
  patient_age?: number | null;
  patient_sex?: 'F' | 'M' | 'O' | null;
  blood_pressure?: string | null;
  heart_rate?: number | null;
  respiratory_rate?: number | null;
  spo2?: number | null;
  temperature?: number | null;
  gcs?: number | null;
  chief_complaint?: string | null;
  hpi?: string | null;
  assessment?: string | null;
  treatment?: string | null;
  confidence: number;
}

interface JobResponse {
  job_id: number;
  encounter_id: number;
  status: JobStatus;
  transcript?: string | null;
  extraction?: PCRExtraction | null;
  error?: string | null;
  created_at: string;
  updated_at: string;
}

interface PCRListItem {
  pcr_id: number;
  encounter_id: number;
  status: string;
  chief_complaint?: string | null;
  patient_name?: string | null;
  extracted_at?: string | null;
  created_at: string;
}

type UiState = 'idle' | 'recording' | 'uploading' | 'processing' | 'done' | 'error';

const STATUS_PILL: Record<UiState, { cls: string; label: string }> = {
  idle:       { cls: 'pill pill-idle', label: 'Idle' },
  recording:  { cls: 'pill pill-rec',  label: 'Recording' },
  uploading:  { cls: 'pill pill-work', label: 'Uploading' },
  processing: { cls: 'pill pill-work', label: 'Processing' },
  done:       { cls: 'pill pill-done', label: 'Done' },
  error:      { cls: 'pill pill-err',  label: 'Error' },
};

function fmtTimer(sec: number): string {
  const m = Math.floor(sec / 60).toString().padStart(2, '0');
  const s = Math.floor(sec % 60).toString().padStart(2, '0');
  return `${m}:${s}`;
}

function fmtDate(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

const App: React.FC = () => {
  // --- flow state -----------------------------------------------------------
  const [ui, setUi] = useState<UiState>('idle');
  const [error, setError] = useState<string | null>(null);

  const [encounterId, setEncounterId] = useState<number | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null);
  const [transcript, setTranscript] = useState<string | null>(null);
  const [extraction, setExtraction] = useState<PCRExtraction | null>(null);

  // --- recording state ------------------------------------------------------
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(0); // 0..1
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef<number | null>(null);
  const timerRef = useRef<number | null>(null);

  // --- recent PCRs ----------------------------------------------------------
  const [pcrs, setPcrs] = useState<PCRListItem[]>([]);

  const refreshPcrs = useCallback(async () => {
    try {
      const r = await fetch(`${API_URL}/pcrs/?limit=20`);
      if (!r.ok) return;
      const data: PCRListItem[] = await r.json();
      setPcrs(data);
    } catch {
      // Non-fatal; list is best-effort.
    }
  }, []);

  useEffect(() => {
    refreshPcrs();
  }, [refreshPcrs]);

  // --- cleanup on unmount ---------------------------------------------------
  useEffect(() => {
    return () => {
      stopRecordingTeardown();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function stopRecordingTeardown() {
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    if (timerRef.current != null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    analyserRef.current = null;
    mediaRecorderRef.current = null;
  }

  // --- step 1: new encounter -----------------------------------------------
  async function newEncounter() {
    setError(null);
    setTranscript(null);
    setExtraction(null);
    setJobId(null);
    setJobStatus(null);
    setUi('idle');
    try {
      const r = await fetch(`${API_URL}/encounters/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (!r.ok) throw new Error(`POST /encounters/ -> ${r.status}`);
      const data = await r.json();
      setEncounterId(data.encounter_id);
    } catch (e: any) {
      setError(e.message || String(e));
      setUi('error');
    }
  }

  // --- step 2: start / stop recording --------------------------------------
  async function startRecording() {
    if (encounterId == null) return;
    setError(null);
    setElapsed(0);
    setLevel(0);
    audioChunksRef.current = [];

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      // Live level meter via WebAudio AnalyserNode.
      const AudioCtx =
        (window as any).AudioContext || (window as any).webkitAudioContext;
      const ctx: AudioContext = new AudioCtx();
      audioCtxRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      analyserRef.current = analyser;

      const data = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        if (!analyserRef.current) return;
        analyserRef.current.getByteTimeDomainData(data);
        // RMS of samples, centered at 128.
        let sum = 0;
        for (let i = 0; i < data.length; i++) {
          const v = (data[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / data.length);
        // Smooth-ish scaling. Clamp to [0,1].
        setLevel(Math.min(1, rms * 2.5));
        rafRef.current = requestAnimationFrame(tick);
      };
      rafRef.current = requestAnimationFrame(tick);

      // MediaRecorder.
      const mr = new MediaRecorder(stream);
      mediaRecorderRef.current = mr;
      mr.ondataavailable = (ev) => {
        if (ev.data && ev.data.size > 0) audioChunksRef.current.push(ev.data);
      };
      mr.onstop = handleRecordingStop;
      mr.start();

      // Wall-clock timer.
      const startedAt = Date.now();
      timerRef.current = window.setInterval(() => {
        setElapsed((Date.now() - startedAt) / 1000);
      }, 200);

      setUi('recording');
    } catch (e: any) {
      setError(e.message || 'Microphone access denied');
      setUi('error');
      stopRecordingTeardown();
    }
  }

  function stopRecording() {
    const mr = mediaRecorderRef.current;
    if (mr && mr.state !== 'inactive') {
      mr.stop(); // triggers onstop -> handleRecordingStop
    }
  }

  // --- step 3: upload -> job ------------------------------------------------
  async function handleRecordingStop() {
    // Assemble blob first, THEN tear down the stream / analyser.
    const mimeType = mediaRecorderRef.current?.mimeType || 'audio/webm';
    const blob = new Blob(audioChunksRef.current, { type: mimeType });
    stopRecordingTeardown();
    setLevel(0);

    if (encounterId == null) {
      setError('No active encounter');
      setUi('error');
      return;
    }
    if (blob.size === 0) {
      setError('Empty recording');
      setUi('error');
      return;
    }

    setUi('uploading');
    try {
      const fd = new FormData();
      // Field name MUST be "audio" to match the backend UploadFile param.
      fd.append('audio', blob, 'recording.webm');
      const r = await fetch(`${API_URL}/encounters/${encounterId}/extract`, {
        method: 'POST',
        body: fd,
      });
      if (!r.ok) throw new Error(`POST /extract -> ${r.status}`);
      const data = await r.json();
      setJobId(data.job_id);
      setJobStatus('queued');
      setUi('processing');
    } catch (e: any) {
      setError(e.message || String(e));
      setUi('error');
    }
  }

  // --- step 4: poll job -----------------------------------------------------
  useEffect(() => {
    if (jobId == null) return;
    if (ui !== 'processing') return;

    let cancelled = false;
    const poll = async () => {
      try {
        const r = await fetch(`${API_URL}/jobs/${jobId}`);
        if (!r.ok) throw new Error(`GET /jobs/${jobId} -> ${r.status}`);
        const data: JobResponse = await r.json();
        if (cancelled) return;
        setJobStatus(data.status);
        if (data.transcript) setTranscript(data.transcript);
        if (data.status === 'completed') {
          setExtraction(data.extraction ?? null);
          setUi('done');
          refreshPcrs();
        } else if (data.status === 'failed') {
          setError(data.error || 'Job failed');
          setUi('error');
        }
      } catch (e: any) {
        if (cancelled) return;
        setError(e.message || String(e));
        setUi('error');
      }
    };

    poll(); // fire immediately, then on interval
    const id = window.setInterval(poll, 800);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [jobId, ui, refreshPcrs]);

  // --- render helpers -------------------------------------------------------
  const pill = STATUS_PILL[ui];

  const Field: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => {
    const empty =
      value == null || value === '' || (typeof value === 'number' && Number.isNaN(value));
    return (
      <div className="field">
        <label>{label}</label>
        <div className={`value${empty ? ' empty' : ''}`}>{empty ? '—' : value}</div>
      </div>
    );
  };

  const ex = extraction;

  return (
    <div className="app-shell">
      <div className="header">
        <h1>CareSync — Voice to PCR</h1>
        <span className={pill.cls}>{pill.label}</span>
      </div>

      {/* Encounter + recording controls */}
      <div className="card">
        <h3>Encounter</h3>
        <div className="status-row" style={{ marginBottom: 16 }}>
          {encounterId != null ? (
            <>
              <span>Encounter ID: <strong>#{encounterId}</strong></span>
              {jobId != null && (
                <>
                  <span>·</span>
                  <span>Job #{jobId}</span>
                </>
              )}
              {jobStatus && ui === 'processing' && (
                <>
                  <span>·</span>
                  <span className="spinner" />
                  <span>{jobStatus}</span>
                </>
              )}
            </>
          ) : (
            <span>No active encounter. Click "New Encounter" to start.</span>
          )}
        </div>

        <div className="timer">{fmtTimer(elapsed)}</div>
        <div className="level-bar">
          <div className="level-fill" style={{ width: `${Math.round(level * 100)}%` }} />
        </div>

        <div className="controls">
          <button
            className="btn btn-ghost"
            onClick={newEncounter}
            disabled={ui === 'recording' || ui === 'uploading' || ui === 'processing'}
          >
            New Encounter
          </button>
          {ui !== 'recording' ? (
            <button
              className="btn btn-primary"
              onClick={startRecording}
              disabled={
                encounterId == null ||
                ui === 'uploading' ||
                ui === 'processing'
              }
            >
              ● Start Recording
            </button>
          ) : (
            <button className="btn btn-danger" onClick={stopRecording}>
              ■ Stop
            </button>
          )}
        </div>

        {error && (
          <div className="status-row" style={{ marginTop: 16, color: '#991b1b' }}>
            {error}
          </div>
        )}
      </div>

      {/* Transcript + extracted PCR */}
      {(transcript || ex) && (
        <div className="grid-2">
          <div className="card">
            <h3>Transcript</h3>
            {transcript ? (
              <div className="transcript-box">{transcript}</div>
            ) : (
              <div className="status-row">
                <span className="spinner" />
                <span>Waiting for transcription…</span>
              </div>
            )}
          </div>

          <div className="card">
            <h3>
              Extracted PCR{' '}
              {ex && (
                <span className="confidence">
                  confidence {Math.round(ex.confidence * 100)}%
                </span>
              )}
            </h3>
            {ex ? (
              <>
                <Field label="Patient" value={ex.patient_name} />
                <div className="grid-2">
                  <Field label="Age" value={ex.patient_age} />
                  <Field label="Sex" value={ex.patient_sex} />
                </div>
                <Field label="Chief complaint" value={ex.chief_complaint} />
                <div className="grid-2">
                  <Field label="BP" value={ex.blood_pressure} />
                  <Field label="HR" value={ex.heart_rate} />
                  <Field label="RR" value={ex.respiratory_rate} />
                  <Field label="SpO₂" value={ex.spo2 != null ? `${ex.spo2}%` : null} />
                  <Field label="Temp (°F)" value={ex.temperature} />
                  <Field label="GCS" value={ex.gcs} />
                </div>
                <Field label="HPI" value={ex.hpi} />
                <Field label="Assessment" value={ex.assessment} />
                <Field label="Treatment" value={ex.treatment} />
              </>
            ) : (
              <div className="status-row">
                <span className="spinner" />
                <span>Waiting for extraction…</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Recent PCRs */}
      <div className="card">
        <h3>Recent PCRs</h3>
        {pcrs.length === 0 ? (
          <div className="status-row">No PCRs yet.</div>
        ) : (
          <div className="pcr-list">
            {pcrs.map((p) => (
              <div key={p.pcr_id} className="pcr-item">
                <span className="pcr-id">#{p.pcr_id}</span>
                <span className="pcr-name">
                  {p.patient_name || p.chief_complaint || `Encounter ${p.encounter_id}`}
                </span>
                <span className="pcr-status">{p.status}</span>
                <span className="pcr-date">{fmtDate(p.extracted_at || p.created_at)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default App;
