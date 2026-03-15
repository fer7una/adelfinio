# Supuestos adoptados

1. Se usa el directorio actual del workspace como raiz de proyecto porque `PROJECT_DIR` se proporciono como placeholder (`/RUTA/A/TU/NUEVO/PROYECTO`).
2. El objetivo de TikTok en esta fase es enviar contenido para revision manual (no autopublicacion final).
3. Se arranca con `EXECUTIONS_MODE=regular` en n8n para simplificar MVP; Redis queda preparado para migrar a colas en Fase 2/3.
4. MinIO se usa como almacenamiento local S3-compatible para assets; se podra reemplazar por cloud storage sin romper contratos JSON.
5. Los workflows son esqueletos importables con nodos placeholder y TODOs explicitos para credenciales/endpoints.
6. La fuente canonica historica vivira en `docs/chronicles/` y se normalizara a `data/timeline/`.
7. Se requiere aprobacion humana antes de cualquier envio a TikTok.
8. Para video final local se usa pipeline de imagen + voz + ffmpeg (sin Sora), configurable por `.env`.
