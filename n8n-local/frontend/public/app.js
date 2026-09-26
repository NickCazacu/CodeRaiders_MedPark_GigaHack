const form = document.querySelector('#upload-form');
const input = document.querySelector('#audio');
const nameLabel = document.querySelector('#file-name');
const status = document.querySelector('#status');
const submit = document.querySelector('#submit');

input.addEventListener('change', () => {
  nameLabel.textContent = input.files[0] ? input.files[0].name : 'No file selected';
  status.textContent = '';
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const file = input.files[0];
  if (!file) return;

  submit.disabled = true;
  status.textContent = 'Sending audio to n8n…';
  try {
    const body = new FormData();
    body.append('audio', file, file.name);
    const response = await fetch('/api/upload', { method: 'POST', body });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || 'The upload was rejected');
    status.textContent = `Received by n8n: ${payload.uploadName || file.name}`;
    form.reset();
    nameLabel.textContent = 'No file selected';
  } catch (error) {
    status.textContent = `Upload failed: ${error.message}`;
  } finally {
    submit.disabled = false;
  }
});
