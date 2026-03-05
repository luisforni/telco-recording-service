package storage

import (
	"bytes"
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/s3/types"
	"go.uber.org/zap"
)

type StorageConfig struct {
	Endpoint        string
	Region          string
	Bucket          string
	AccessKeyID     string
	SecretAccessKey string
	UsePathStyle    bool
	EncryptionKeyID string 
}

type S3Storage struct {
	client  *s3.Client
	presign *s3.PresignClient
	cfg     StorageConfig
	log     *zap.Logger
}

func NewS3Storage(sc StorageConfig, log *zap.Logger) (*S3Storage, error) {
	var opts []func(*config.LoadOptions) error
	opts = append(opts, config.WithRegion(sc.Region))

	if sc.AccessKeyID != "" {
		opts = append(opts, config.WithCredentialsProvider(
			credentials.NewStaticCredentialsProvider(sc.AccessKeyID, sc.SecretAccessKey, ""),
		))
	}

	cfg, err := config.LoadDefaultConfig(context.Background(), opts...)
	if err != nil {
		return nil, fmt.Errorf("load aws config: %w", err)
	}

	clientOpts := []func(*s3.Options){}
	if sc.Endpoint != "" {
		clientOpts = append(clientOpts, func(o *s3.Options) {
			o.BaseEndpoint = aws.String(sc.Endpoint)
			o.UsePathStyle = sc.UsePathStyle
		})
	}

	client := s3.NewFromConfig(cfg, clientOpts...)
	return &S3Storage{
		client:  client,
		presign: s3.NewPresignClient(client),
		cfg:     sc,
		log:     log,
	}, nil
}

func (s *S3Storage) ObjectKey(callID string, t time.Time, codec string) string {
	return fmt.Sprintf("recordings/%04d/%02d/%02d/%s/audio.%s",
		t.Year(), t.Month(), t.Day(), callID, codec)
}

func (s *S3Storage) Upload(ctx context.Context, callID string, startTime time.Time, codec string, data []byte) (string, error) {
	key := s.ObjectKey(callID, startTime, codec)
	contentType := "audio/wav"
	if codec != "wav" {
		contentType = "audio/" + codec
	}
	input := &s3.PutObjectInput{
		Bucket:      aws.String(s.cfg.Bucket),
		Key:         aws.String(key),
		Body:        bytes.NewReader(data),
		ContentType: aws.String(contentType),
	}

	if s.cfg.EncryptionKeyID != "" {
		input.ServerSideEncryption = types.ServerSideEncryptionAwsKms
		input.SSEKMSKeyId = aws.String(s.cfg.EncryptionKeyID)
	} else {
		input.ServerSideEncryption = types.ServerSideEncryptionAes256
	}

	_, err := s.client.PutObject(ctx, input)
	if err != nil {
		return "", fmt.Errorf("s3 put object: %w", err)
	}

	s3url := fmt.Sprintf("s3://%s/%s", s.cfg.Bucket, key)
	s.log.Info("recording uploaded", zap.String("key", key), zap.Int("bytes", len(data)))
	return s3url, nil
}

func (s *S3Storage) PresignURL(ctx context.Context, key string, expiry time.Duration) (string, error) {
	req, err := s.presign.PresignGetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(s.cfg.Bucket),
		Key:    aws.String(key),
	}, s3.WithPresignExpires(expiry))
	if err != nil {
		return "", fmt.Errorf("presign: %w", err)
	}
	return req.URL, nil
}

func (s *S3Storage) KeyFromURL(s3url string) string {
	prefix := fmt.Sprintf("s3://%s/", s.cfg.Bucket)
	return strings.TrimPrefix(s3url, prefix)
}
