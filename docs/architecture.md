
## 1. Project Overview

The system is an **on-premise AI pipeline for automatic hospital meeting documentation**.

Its purpose is to transform a meeting recording into a structured **Minutes of Meeting (MoM)** document containing:

- meeting metadata;
    
- concise meeting summary;
    
- important discussion points;
    
- decisions made;
    
- action items;
    
- responsible persons;
    
- deadlines;
    
- optional speaker attribution;
    
- automatic delivery to a predefined participant distribution list.
    

The key requirement is that **all processing happens inside the hospital's internal infrastructure**. Audio, transcripts, documents, and extracted information must never be sent to external AI APIs, cloud storage, or external email providers.

The system must support the multilingual environment of Moldova, particularly **Romanian, Russian, and English**, including:

- code-switching between languages;
    
- Romanian regional expressions;
    
- English business terminology;
    
- Russian phrases;
    
- medical terminology;
    
- names, abbreviations, drugs, procedures, departments, and other hospital-specific terminology.
    

---

# 2. High-Level Business Flow

```text
Meeting
   │
   ▼
Audio recording / live audio
   │
   ▼
Internal Web Application
   │
   ▼
Meeting Type Selection
(Medical / Executive / Administrative)
   │
   ▼
Audio Processing
   │
   ▼
Local ASR
   │
   ▼
Raw Multilingual Transcript
   │
   ▼
Local LLM
   │
   ├── Summary
   ├── Decisions
   ├── Action Items
   ├── Responsible Persons
   └── Deadlines
   │
   ▼
Structured MoM
   │
   ▼
Document Generator
   │
   ▼
Automation / Routing Engine
   │
   ▼
Local SMTP Server
   │
   ▼
Participants' Mailboxes
```

For development and offline demonstration, the final SMTP component should be replaced with **MailHog or Mailpit**, so emails remain completely inside the local environment.

---

# 3. Main System Components

## 3.1 Internal Web Application

The frontend is the entry point for hospital personnel.

### Responsibilities

The user should be able to:

1. Upload a meeting audio file.
    
2. Start/stop live recording.
    
3. Select the meeting type:
    
    - Medical
        
    - Executive
        
    - Administrative
        
4. Optionally enter meeting metadata:
    
    - title;
        
    - date;
        
    - participants;
        
    - department;
        
    - predefined email distribution list.
        
5. Start processing.
    
6. See processing status.
    
7. View or download the generated MoM.
    
8. See whether email delivery succeeded.
    

### Example UI flow

```text
Create Meeting
    │
    ├── Meeting title
    ├── Meeting type
    ├── Date
    ├── Participants
    ├── Audio file / Record
    └── Distribution list
              │
              ▼
          Process Meeting
              │
              ▼
        Processing Status
              │
              ▼
          Generated MoM
              │
              ▼
         Email Sent
```

---

# 4. Backend / API Server

A Python **FastAPI** backend is a natural choice because the AI ecosystem is predominantly Python-based.

The backend acts as the central application service.

### Responsibilities

- authentication / authorization;
    
- meeting creation;
    
- receiving audio;
    
- storing temporary files;
    
- validating requests;
    
- starting processing jobs;
    
- communicating with ASR;
    
- communicating with the local LLM;
    
- storing transcripts and meeting results;
    
- generating MoM documents;
    
- triggering automation;
    
- exposing processing status.
    

### Suggested modules

```text
backend/
├── auth/
├── meetings/
├── audio/
├── transcription/
├── summarization/
├── action-items/
├── documents/
├── notifications/
├── workflow/
└── system/
```

The backend should not contain all AI logic directly. AI processing should be isolated behind well-defined services.

---

# 5. Audio Processing Module

Before sending audio to ASR, the system should normalize it.

### Responsibilities

- convert audio to a supported format;
    
- normalize sample rate;
    
- normalize channels;
    
- optionally reduce noise;
    
- split long recordings into chunks;
    
- detect periods without speech;
    
- prepare audio for ASR.
    

A **VAD (Voice Activity Detection)** component can remove long silent portions and improve processing efficiency.

Possible local technologies:

- FFmpeg
    
- Silero VAD
    
- WebRTC VAD
    

---

# 6. Hybrid ASR Module

This is one of the most important components.

The ASR system converts speech to text.

### Primary candidate

**Whisper running locally**

Possible implementations:

- `faster-whisper`
    
- Whisper through `transformers`
    
- another optimized local Whisper runtime
    

The architecture should keep the ASR implementation replaceable so the team can benchmark alternative models.

```text
Audio
  │
  ▼
VAD / Chunking
  │
  ▼
ASR
  │
  ▼
Transcript segments
```

Each transcript segment should ideally contain:

```json
{
  "start": 125.4,
  "end": 132.8,
  "text": "..."
}
```

A richer representation can additionally contain:

```json
{
  "start": 125.4,
  "end": 132.8,
  "speaker": "SPEAKER_02",
  "language": "ro",
  "text": "..."
}
```

---

# 7. Handling Romanian / Russian / English Code-Switching

This is a major differentiator of the project.

The system should not assume that an entire meeting is in one language.

Instead, it should process the transcript at **segment level**.

For example:

```text
Speaker 1:
"Trebuie să discutăm despre onboarding pentru
new medical staff..."

Speaker 2:
"Да, я согласен, но нам нужно сначала
проверить расписание."

Speaker 3:
"Putem aproba bugetul pentru acest trimestru."
```

The pipeline should preserve the original wording instead of translating everything into one language.

### Recommended architecture

```text
Audio
  │
  ▼
ASR
  │
  ▼
Timestamped segments
  │
  ▼
Language detection / language metadata
  │
  ▼
Code-switching-aware transcript
  │
  ▼
LLM
```

The LLM should receive the original multilingual transcript and be instructed to preserve important names, medical terms, acronyms, and terminology.

---

# 8. Speaker Diarization

Speaker diarization is optional according to the challenge but provides significant value.

It answers:

> "Who was speaking when?"

Possible local solution:

- pyannote.audio
    

Output:

```text
[00:02:13] SPEAKER_01:
We need to approve the new protocol.

[00:02:19] SPEAKER_03:
I can prepare the documentation by Friday.
```

This becomes particularly useful when extracting action items.

Instead of:

```text
Prepare the documentation by Friday.
```

the system can produce:

```text
Owner: SPEAKER_03
Action: Prepare documentation
Deadline: Friday
```

A later stage can map speakers to actual participant names when participant information is available.

---

# 9. Local LLM Module

The LLM processes the transcript and converts unstructured conversation into structured information.

Candidate models could include:

- Llama-family models;
    
- Mistral-family models;
    
- Qwen-family models;
    
- Phi-family models.
    

The exact model should be selected based on **quality, multilingual performance, and hardware requirements**, especially the 16 GB GPU / 32 GB RAM deployment target.

The LLM should **not simply generate free-form text**.

It should produce a strict structured result.

### Example output

```json
{
  "summary": "...",
  "decisions": [
    {
      "text": "Approve the new patient admission procedure"
    }
  ],
  "action_items": [
    {
      "action": "Prepare updated documentation",
      "owner": "Dr. Popescu",
      "deadline": "2026-10-02"
    }
  ]
}
```

Structured JSON is preferable because it allows the rest of the system to process the result reliably.

---

# 10. Business Logic

The business logic is essentially the transformation:

```text
Meeting
   ↓
Conversation
   ↓
Information
   ↓
Decisions
   ↓
Actions
   ↓
Responsibilities
   ↓
Deadlines
   ↓
Official Meeting Minutes
```

The LLM should identify several categories.

### Decisions

Things that were actually approved, rejected, agreed upon, or changed.

Example:

```text
Decision:
"The department approved the new admission workflow."
```

### Action Items

Something someone must do.

```text
Action:
"Prepare the revised procedure."
```

### Owner

The person responsible for the action.

```text
Owner:
"Dr. Popescu"
```

### Deadline

When the task must be completed.

```text
Deadline:
"October 2, 2026"
```

### Important discussion points

Topics discussed but not necessarily decided.

The system should **not turn every statement into a decision**.

This distinction is important for the quality of the MoM.

---

# 11. Meeting-Type Routing

The selected meeting type determines how the generated result is processed.

```text
                    Meeting
                       │
                       ▼
                 Meeting Type
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
     Medical       Executive    Administrative
        │              │              │
        ▼              ▼              ▼
   Medical MoM    Executive MoM   Admin MoM
```

The meeting type can influence:

- prompt/template selection;
    
- important fields;
    
- distribution list;
    
- document formatting;
    
- email subject;
    
- workflow destination.
    

For example:

```text
Medical
→ clinical decisions
→ patient-care procedures
→ medical board participants

Executive
→ strategic decisions
→ budgets
→ management responsibilities

Administrative
→ procurement
→ staffing
→ operational tasks
```

The underlying AI pipeline remains the same.

---

# 12. MoM Generator

Once structured information is extracted, the system generates a formal document.

Example:

```text
MEDPARK
MINUTES OF MEETING

Meeting:
Medical Board Meeting

Date:
25 September 2026

Participants:
...

SUMMARY
...

KEY DISCUSSION POINTS
...

DECISIONS
1. ...
2. ...

ACTION ITEMS

| Action | Owner | Deadline |
|--------|-------|----------|
| ...    | ...   | ...      |

Generated automatically by the On-Premise Meeting Intelligence System.
```

Possible formats:

- PDF
    
- DOCX
    
- HTML
    

For the prototype, **PDF + HTML** or **DOCX + PDF** would be sufficient.

---

# 13. Automation / Routing Engine

The challenge explicitly mentions **n8n**.

n8n should coordinate the workflow instead of containing the core AI logic.

Example:

```text
Backend
   │
   │ "Meeting processing complete"
   ▼
n8n
   │
   ├── Check meeting type
   │
   ├── Select distribution list
   │
   ├── Prepare email
   │
   └── Send through local SMTP
   │
   ▼
MailHog / Mailpit
```

This creates a clean separation:

**Backend = business logic**

**n8n = workflow orchestration**

**MailHog/Mailpit = local email infrastructure**

---

# 14. Offline Email Delivery

During the demo, no external email provider should be used.

Instead:

```text
Application
    │
    │ SMTP
    ▼
MailHog / Mailpit
    │
    ▼
Local mailbox simulation
```

For example:

```env
SMTP_HOST=mailhog
SMTP_PORT=1025
SMTP_USER=
SMTP_PASSWORD=
```

The email appears in the local MailHog interface.

This demonstrates that the application can complete the entire process without contacting the public internet.

---

# 15. Data Storage

A relational database such as PostgreSQL can store the system state.

Suggested entities:

```text
User
Meeting
Participant
AudioFile
Transcript
TranscriptSegment
Decision
ActionItem
Document
DistributionList
WorkflowExecution
```

Possible relationship:

```text
Meeting
 ├── AudioFile
 ├── Transcript
 │    └── TranscriptSegment
 ├── Participants
 ├── Decisions
 ├── ActionItems
 └── GeneratedDocuments
```

Large audio files should preferably be stored on local filesystem/object storage rather than directly in PostgreSQL.

---

# 16. Processing Architecture

Because transcription and LLM processing can take a long time, processing should be asynchronous.

Do not make the frontend wait for one HTTP request to remain open for the entire meeting.

Instead:

```text
POST /meetings
        │
        ▼
Create processing job
        │
        ▼
Return job ID
        │
        ▼
Background worker
        │
        ├── Audio processing
        ├── ASR
        ├── Diarization
        ├── LLM
        ├── MoM generation
        └── Workflow trigger
```

Frontend can then query:

```text
GET /meetings/{id}/status
```

Possible states:

```text
uploaded
processing_audio
transcribing
diarizing
analyzing
generating_document
sending_email
completed
failed
```

---

# 17. Recommended Deployment Architecture

For the target hospital infrastructure:

```text
┌───────────────────────────────────────────────┐
│              HOSPITAL INTERNAL NETWORK        │
│                                               │
│  ┌───────────────┐                            │
│  │ Internal User │                            │
│  │ Browser       │                            │
│  └───────┬───────┘                            │
│          │                                    │
│          ▼                                    │
│  ┌───────────────┐                            │
│  │ Frontend      │                            │
│  │ Web App       │                            │
│  └───────┬───────┘                            │
│          │ HTTP                               │
│          ▼                                    │
│  ┌─────────────────────────────────────────┐  │
│  │ Backend / API                           │  │
│  │ FastAPI                                 │  │
│  └───────┬─────────────────────────────────┘  │
│          │                                    │
│     ┌────┼──────────────┬──────────────┐      │
│     ▼    ▼              ▼              ▼      │
│   ASR  Diarization      LLM         PostgreSQL│
│     │    │              │                    │
│     └────┴──────────────┴────────────┘       │
│                       │                       │
│                       ▼                       │
│                Structured MoM                │
│                       │                       │
│                       ▼                       │
│                      n8n                     │
│                       │                       │
│                       ▼                       │
│                 Local SMTP                   │
│              MailHog / Mailpit               │
│                                               │
└───────────────────────────────────────────────┘
```

A production installation would replace the demo mail catcher with an **internal hospital SMTP server**, while still remaining entirely inside the organization's network.

---

# 18. Docker Architecture

For reproducibility, Docker Compose is suitable:

```text
docker-compose.yml

services:

  frontend
  backend
  worker
  postgres
  n8n
  mailhog
```

The AI models can either be mounted from local storage or packaged/configured separately because model files can be several gigabytes.

GPU access should be enabled for the ASR and LLM containers when a GPU is available.

---

# 19. Security Principles

Security is a fundamental part of this project rather than an additional feature.

The system should guarantee:

```text
Audio
  ↓
Local processing
  ↓
Local transcript
  ↓
Local AI
  ↓
Local document
  ↓
Internal email
```

There should be **no**:

- OpenAI API calls;
    
- Google APIs;
    
- cloud LLM APIs;
    
- external SMTP;
    
- cloud storage;
    
- external telemetry containing meeting content.
    

The system should also provide:

- role-based access;
    
- authentication;
    
- encrypted internal communication where appropriate;
    
- local audit logs;
    
- controlled file access;
    
- temporary-file cleanup;
    
- configurable retention policies.
    

---

# 20. Recommended Repository Structure

```text
meeting-ai/
│
├── frontend/
│   └── Next.js
│
├── backend/
│   ├── api/
│   ├── services/
│   │   ├── asr/
│   │   ├── diarization/
│   │   ├── llm/
│   │   ├── document/
│   │   └── email/
│   └── workers/
│
├── workflows/
│   └── n8n/
│
├── models/
│   ├── asr/
│   └── llm/
│
├── infrastructure/
│   ├── docker/
│   └── compose/
│
├── data/
│   └── sample/
│
└── README.md
```

---

# 21. MVP Pipeline

For the hackathon/prototype, the minimum viable implementation should be:

```text
Upload audio
      ↓
Whisper
      ↓
Transcript
      ↓
Local LLM
      ↓
Structured JSON
      ↓
MoM document
      ↓
n8n
      ↓
MailHog
      ↓
Email visible in local UI
```

Speaker diarization, live recording, advanced monitoring, and sophisticated authentication can be added after this pipeline works reliably.

---

# 22. Core Success Criteria

The prototype is successful when it can demonstrate this entire chain **with the network disconnected**:

```text
60-minute meeting recording
        ↓
Local transcription
        ↓
Romanian/Russian/English handling
        ↓
Medical terminology
        ↓
Decision extraction
        ↓
Owner extraction
        ↓
Deadline extraction
        ↓
Structured MoM
        ↓
Local email delivery
```

The most important architectural principle is:

> **Every component required to understand, process, store, and deliver meeting information must operate inside the hospital's trusted network.**

This makes the project more than an AI transcription demo: it is an **end-to-end private meeting-intelligence system** combining ASR, local LLM processing, document generation, workflow automation, and internal communication.