package main

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"go.uber.org/zap"

	"telco-recording-service/internal/recorder"
	"telco-recording-service/internal/storage"
)

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func main() {
	log, _ := zap.NewProduction()
	defer log.Sync()

	storageCfg := storage.StorageConfig{
		Endpoint:        getEnv("S3_ENDPOINT", ""),
		Region:          getEnv("AWS_REGION", "us-east-1"),
		Bucket:          getEnv("S3_BUCKET", "telco-recordings"),
		AccessKeyID:     getEnv("AWS_ACCESS_KEY_ID", ""),
		SecretAccessKey: getEnv("AWS_SECRET_ACCESS_KEY", ""),
		UsePathStyle:    getEnv("S3_PATH_STYLE", "false") == "true",
		EncryptionKeyID: getEnv("KMS_KEY_ID", ""),
	}

	s3Store, err := storage.NewS3Storage(storageCfg, log)
	if err != nil {
		log.Fatal("s3 storage init", zap.Error(err))
	}

	rec := recorder.New(s3Store, log)

	gin.SetMode(gin.ReleaseMode)
	r := gin.New()
	r.Use(gin.Recovery())

	r.GET("/healthz", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "ok"})
	})
	r.GET("/metrics", gin.WrapH(promhttp.Handler()))

	v1 := r.Group("/api/v1/recordings")
	{
		
		v1.POST("/start", func(c *gin.Context) {
			var body struct {
				CallID string `json:"call_id" binding:"required"`
			}
			if err := c.ShouldBindJSON(&body); err != nil {
				c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
				return
			}
			if err := rec.Start(body.CallID); err != nil {
				c.JSON(http.StatusConflict, gin.H{"error": err.Error()})
				return
			}
			c.JSON(http.StatusCreated, gin.H{"call_id": body.CallID, "status": "recording"})
		})

		
		v1.POST("/:call_id/audio", func(c *gin.Context) {
			callID := c.Param("call_id")
			chunk, err := c.GetRawData()
			if err != nil {
				c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
				return
			}
			if err := rec.WriteAudio(callID, chunk); err != nil {
				c.JSON(http.StatusNotFound, gin.H{"error": err.Error()})
				return
			}
			c.Status(http.StatusNoContent)
		})

		
		v1.POST("/:call_id/stop", func(c *gin.Context) {
			callID := c.Param("call_id")
			recording, err := rec.Stop(c.Request.Context(), callID)
			if err != nil {
				c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
				return
			}
			c.JSON(http.StatusOK, recording)
		})

		
		v1.GET("/:call_id/url", func(c *gin.Context) {
			_ = json.NewEncoder(c.Writer) 
			c.JSON(http.StatusNotImplemented, gin.H{"message": "use S3 presigner"})
		})
	}

	srv := &http.Server{Addr: ":8080", Handler: r}
	go srv.ListenAndServe()

	log.Info("recording service started")
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	srv.Shutdown(ctx)
}
