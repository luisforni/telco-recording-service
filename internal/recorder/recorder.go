package recorder

import (
	"bytes"
	"context"
	"encoding/binary"
	"fmt"
	"sync"
	"time"

	"go.uber.org/zap"

	"telco-recording-service/internal/storage"
)

type RecordingStatus string

const (
	StatusRecording RecordingStatus = "recording"
	StatusStopped   RecordingStatus = "stopped"
	StatusUploading RecordingStatus = "uploading"
	StatusComplete  RecordingStatus = "complete"
	StatusError     RecordingStatus = "error"
)

type Recording struct {
	ID        string          `json:"id"`
	CallID    string          `json:"call_id"`
	Status    RecordingStatus `json:"status"`
	StartTime time.Time       `json:"start_time"`
	EndTime   *time.Time      `json:"end_time,omitempty"`
	S3URI     string          `json:"s3_uri,omitempty"`
	Format    string          `json:"format"`
	SizeBytes int             `json:"size_bytes"`
}

type session struct {
	recording Recording
	buf       bytes.Buffer
	mu        sync.Mutex
}

type Recorder struct {
	sessions sync.Map       
	storage  *storage.S3Storage
	log      *zap.Logger
}

func New(s *storage.S3Storage, log *zap.Logger) *Recorder {
	return &Recorder{storage: s, log: log}
}

func (r *Recorder) Start(callID string) error {
	rec := Recording{
		ID:        fmt.Sprintf("rec-%s-%d", callID, time.Now().UnixMilli()),
		CallID:    callID,
		Status:    StatusRecording,
		StartTime: time.Now(),
		Format:    "wav",
	}
	s := &session{recording: rec}
	
	s.buf.Write(wavHeaderPlaceholder())
	r.sessions.Store(callID, s)
	r.log.Info("recording started", zap.String("call_id", callID), zap.String("recording_id", rec.ID))
	return nil
}

func (r *Recorder) WriteAudio(callID string, chunk []byte) error {
	val, ok := r.sessions.Load(callID)
	if !ok {
		return fmt.Errorf("no active recording for call %s", callID)
	}
	s := val.(*session)
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.recording.Status != StatusRecording {
		return fmt.Errorf("recording not active for call %s", callID)
	}
	s.buf.Write(chunk)
	return nil
}

func (r *Recorder) Stop(ctx context.Context, callID string) (*Recording, error) {
	val, ok := r.sessions.LoadAndDelete(callID)
	if !ok {
		return nil, fmt.Errorf("no active recording for call %s", callID)
	}
	s := val.(*session)
	s.mu.Lock()

	now := time.Now()
	s.recording.EndTime = &now
	s.recording.Status = StatusUploading

	
	data := patchWAVHeader(s.buf.Bytes())
	s.recording.SizeBytes = len(data)
	s.mu.Unlock()

	s3URI, err := r.storage.Upload(ctx, callID, s.recording.StartTime, "wav", data)
	if err != nil {
		s.recording.Status = StatusError
		return &s.recording, fmt.Errorf("upload: %w", err)
	}
	s.recording.S3URI = s3URI
	s.recording.Status = StatusComplete
	r.log.Info("recording complete",
		zap.String("call_id", callID),
		zap.String("s3_uri", s3URI),
		zap.Int("bytes", len(data)),
	)
	return &s.recording, nil
}

func wavHeaderPlaceholder() []byte {
	
	h := make([]byte, 44)
	copy(h[0:4], "RIFF")
	copy(h[8:12], "WAVE")
	copy(h[12:16], "fmt ")
	binary.LittleEndian.PutUint32(h[16:], 16)   
	binary.LittleEndian.PutUint16(h[20:], 1)    
	binary.LittleEndian.PutUint16(h[22:], 1)    
	binary.LittleEndian.PutUint32(h[24:], 8000) 
	binary.LittleEndian.PutUint32(h[28:], 8000) 
	binary.LittleEndian.PutUint16(h[32:], 1)    
	binary.LittleEndian.PutUint16(h[34:], 8)    
	copy(h[36:40], "data")
	return h
}

func patchWAVHeader(data []byte) []byte {
	result := make([]byte, len(data))
	copy(result, data)
	dataSize := uint32(len(data) - 44)
	binary.LittleEndian.PutUint32(result[4:], uint32(len(data)-8))
	binary.LittleEndian.PutUint32(result[40:], dataSize)
	return result
}
