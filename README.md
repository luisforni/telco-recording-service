# Telco Recording Service

Microservice for call recording, storage, retrieval, and playback in the telco platform.

## Features

- **Recording Control**: Start, stop, pause, resume call recordings
- **Storage Management**: Efficient audio storage with MinIO/S3
- **Audio Streaming**: Stream recordings for real-time playback
- **Signed URLs**: Time-limited secure download links
- **Metadata Management**: Rich tagging, notes, and custom fields
- **Search & Filter**: Filter by call ID, agent, date range, tags
- **Kafka Integration**: Event-driven recording triggers and notifications
- **Auto-Transcription**: Trigger ASR service after recording completes
- **Retention Policies**: Automatic cleanup based on configurable retention period
- **Prometheus Metrics**: Active recordings, storage usage, duration stats
- **Encryption**: Server-side encryption at rest in MinIO/S3

## API Endpoints

### Recording Control

| Method | Path | Description |
|--------|------|-------------|
| POST | `/recordings/start` | Start recording a call |
| POST | `/recordings/{recording_id}/stop` | Stop a recording |
| POST | `/recordings/{recording_id}/pause` | Pause a recording |
| POST | `/recordings/{recording_id}/resume` | Resume a paused recording |
| GET | `/recordings/{recording_id}` | Get recording metadata |
| DELETE | `/recordings/{recording_id}` | Delete a recording |

### Recording Retrieval

| Method | Path | Description |
|--------|------|-------------|
| GET | `/recordings/{recording_id}/download` | Download recording file |
| GET | `/recordings/{recording_id}/stream` | Stream recording audio |
| GET | `/recordings/{recording_id}/url` | Get signed URL for recording |

### Search & Query

| Method | Path | Description |
|--------|------|-------------|
| GET | `/recordings` | List recordings (with filters) |
| GET | `/recordings/search?q=` | Search recordings by query |
| GET | `/recordings/call/{call_id}` | Get recordings for a call |
| GET | `/recordings/agent/{agent_id}` | Get recordings by agent |
| GET | `/recordings/date-range?start=&end=` | Get recordings in date range |

### Metadata & Analytics

| Method | Path | Description |
|--------|------|-------------|
| PUT | `/recordings/{recording_id}/metadata` | Update recording metadata |
| GET | `/recordings/{recording_id}/transcript` | Get transcript |
| POST | `/recordings/{recording_id}/tag` | Add tag to recording |
| GET | `/recordings/stats` | Get recording statistics |

### System

| Method | Path | Description |
|--------|------|-------------|
| GET | `/healthz` | Health check |
| GET | `/metrics` | Prometheus metrics |

## Storage Architecture

Recordings are stored in MinIO/S3 with the following key structure:

```
call-recordings/{year}/{month}/{day}/{call_id}_{HHmmss}.{ext}
```

Example: `call-recordings/2024/01/15/call-abc123_143022.wav`

### Supported Formats

- WAV (default)
- MP3
- OGG

## Retention Policies

Recordings are automatically deleted after `RETENTION_DAYS` (default: 90 days) via the retention enforcement logic. Only recordings with status `completed` are eligible for automatic deletion.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MINIO_ENDPOINT` | `minio:9000` | MinIO server endpoint |
| `MINIO_ACCESS_KEY` | `minioadmin` | MinIO access key |
| `MINIO_SECRET_KEY` | `minioadmin` | MinIO secret key |
| `MINIO_SECURE` | `false` | Use TLS for MinIO connection |
| `RECORDINGS_BUCKET` | `call-recordings` | Bucket name for recordings |
| `KAFKA_BROKERS` | `kafka:9092` | Kafka broker addresses |
| `SIP_GATEWAY_URL` | `http://sip-gateway:8002` | SIP Gateway base URL |
| `ASR_SERVICE_URL` | `http://asr-service:8004` | ASR Service base URL |
| `RETENTION_DAYS` | `90` | Days to retain recordings |
| `AUTO_TRANSCRIBE` | `true` | Enable automatic transcription |
| `PORT` | `8009` | HTTP server port |

## Running

### Local Development

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8009 --reload
```

### Docker

```bash
docker build -t telco-recording-service .
docker run -p 8009:8009 \
  -e MINIO_ENDPOINT=localhost:9000 \
  -e MINIO_ACCESS_KEY=minioadmin \
  -e MINIO_SECRET_KEY=minioadmin \
  -e KAFKA_BROKERS=localhost:9092 \
  telco-recording-service
```

## Integration Examples

### Start a recording

```bash
curl -X POST http://localhost:8009/recordings/start \
  -H "Content-Type: application/json" \
  -d '{"call_id": "call-123", "agent_id": "agent-456"}'
```

### Stop a recording

```bash
curl -X POST http://localhost:8009/recordings/{recording_id}/stop
```

### Get a signed download URL

```bash
curl http://localhost:8009/recordings/{recording_id}/url?expires_hours=2
```

### Search recordings

```bash
curl "http://localhost:8009/recordings/search?q=agent-456"
```

## Kafka Topics

| Direction | Topic | Events |
|-----------|-------|--------|
| Produce | `recording-events` | `recording_started`, `recording_stopped`, `recording_completed`, `recording_failed`, `recording_deleted` |
| Consume | `call-events` | `call_started` → auto-start recording, `call_ended` → auto-stop |
| Consume | `orchestrator-events` | Orchestration triggers |

## Security Considerations

- **Encryption at rest**: MinIO server-side encryption for stored recordings
- **Signed URLs**: Time-limited presigned URLs for secure file access (1–24 hours)
- **Access control**: Integrate with an API Gateway for authentication/authorization
- **TLS**: Enable `MINIO_SECURE=true` in production for encrypted transport to MinIO
- **Retention**: Automatic deletion prevents indefinite data accumulation

## Prometheus Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `recording_active_total` | Gauge | Currently active recordings |
| `recording_requests_total` | Counter | Total recording requests by status |
| `recording_duration_seconds` | Histogram | Distribution of recording durations |
| `recording_storage_bytes` | Gauge | Total storage used by recordings |
| `recording_download_requests_total` | Counter | Download/stream requests |

