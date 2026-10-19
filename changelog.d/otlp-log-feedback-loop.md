Fixed shutdown hang in scan and ocir-cleanup: the OTLP log handler no longer re-exports the exporter's own urllib3/opentelemetry DEBUG logs, which kept the log queue from ever draining
