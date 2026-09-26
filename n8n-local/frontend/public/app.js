const form = document.querySelector('#upload-form');
const input = document.querySelector('#audio');
const nameLabel = document.querySelector('#file-name');
const status = document.querySelector('#status');
const submit = document.querySelector('#submit');
const result = document.querySelector('#result');
const minutesFrame = document.querySelector('#minutes');
const minutesLink = document.querySelector('#minutes-link');

// Stages reported by AI/service.py, in order.
const STAGES = {
  ingest: 'Reading the recording',
  normalize: 'Normalizing audio',
  segment: 'Detecting speakers',
  asr: 'Transcribing speech',
  postprocess: 'Cleaning the transcript',
  pack: 'Preparing the transcript for the LLM',
  llm: 'Writing the minutes (LLM)',
  mom: 'Formatting the minutes',
};
const POLL_MS = 4000;

input.addEventListener('change', () => {
  nameLabel.textContent = input.files[0] ? input.files[0].name : 'No file selected';
  status.textContent = '';
});

function elapsed(since) {
  const s = Math.round((Date.now() - since) / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

async function follow(jobId, since) {
  for (;;) {
    let job;
    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
      job = await response.json();
      if (!response.ok) throw new Error(job.error || `status ${response.status}`);
    } catch (error) {
      status.textContent = `Waiting for status (${error.message})… ${elapsed(since)}`;
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      continue;
    }
    if (job.state === 'failed') throw new Error(job.error || 'processing failed');
    if (job.state === 'done' && job.has_minutes) return job;
    const step = STAGES[job.stage] ? `${job.progress ? `${job.progress} · ` : ''}${STAGES[job.stage]}` : 'Queued';
    status.textContent = `${step}… ${elapsed(since)}`;
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const file = input.files[0];
  if (!file) return;

  submit.disabled = true;
  result.hidden = true;
  document.body.classList.remove('has-result');
  const since = Date.now();
  status.textContent = 'Sending audio to n8n…';
  try {
    const body = new FormData();
    body.append('audio', file, file.name);
    const response = await fetch('/api/upload', { method: 'POST', body });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.job_id) throw new Error(payload.error || payload.message || 'The upload was rejected');

    const job = await follow(payload.job_id, since);
    const url = `/api/jobs/${encodeURIComponent(job.job_id)}/minutes`;
    minutesFrame.src = url;
    minutesLink.href = url;
    result.hidden = false;
    document.body.classList.add('has-result');
    status.textContent = `Minutes ready in ${elapsed(since)} (job ${job.job_id}).`;
    form.reset();
    nameLabel.textContent = 'No file selected';
  } catch (error) {
    status.textContent = `Failed: ${error.message}`;
  } finally {
    submit.disabled = false;
  }
});
