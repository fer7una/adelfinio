# Adelfinio - TikTok Historia de Espana (MVP tecnico)

Base tecnica para un sistema de produccion diaria de 4 videos (2 principales + 2 teasers) con revision humana previa al envio para revision manual en TikTok.

Duraciones objetivo:
- Principales: 90-120s (maximo 180s)
- Teasers: 15-30s

## Stack

- Docker + Docker Compose
- n8n self-hosted
- PostgreSQL (persistencia)
- Redis (colas/locks)
- MinIO (assets)
- Python 3.11+ (utilidades)

## Estructura

- `workflows/`: workflows de n8n versionables
- `schemas/`: JSON Schemas del dominio
- `data/`: estado de personajes, timeline, episodios y planes diarios
- `prompts/`: prompts y plantillas de generacion
- `scripts/`: utilidades operativas
- `docs/`: arquitectura, roadmap, operating model y supuestos
- `website/`: sitio legal minimo (home/privacy/terms) para requisitos de integraciones

## Requisitos previos

1. Docker Engine 24+ y Docker Compose v2
2. Python 3.11+
3. Git
4. Dependencia para IA (fase de video final): `python3 -m pip install openai`

## Arranque en frio

1. Clonar o abrir este repo en VS Code.
2. Copiar variables de entorno:

```bash
cp .env.example .env
```

3. Completar secretos en `.env` (todos los `REPLACE_WITH_*` y `TODO_*`).
4. Levantar servicios:

```bash
docker compose up -d
```

5. Comprobar estado:

```bash
docker compose ps
```

6. Acceder a:
- n8n: `http://localhost:5678`
- MinIO API: `http://localhost:9000`
- MinIO Console: `http://localhost:9001`

## Scripts utiles

- Validar JSON contra schemas:

```bash
python3 scripts/validate_json.py
```

- Generar plan diario (2 principales + 2 teasers):

```bash
python3 scripts/generate_daily_plan.py --date 2026-03-15 --reset-progression
```

- Extraer eventos desde cronicas:

```bash
python3 scripts/extract_source_events.py --sources docs/chronicles/01-La\\ gran\\ aventura\\ del\\ reino\\ de\\ Asturias.pdf --overwrite
```

- OCR de PDF escaneado a sidecar texto:

```bash
bash scripts/ocr_pdf_to_sidecar.sh \
  \"docs/chronicles/01-La gran aventura del reino de Asturias.pdf\" \
  \"docs/chronicles/sidecar_text/01-La gran aventura del reino de Asturias.txt\" \
  1 120
```

- Construir character bible + timeline emocional/arco:

```bash
python3 scripts/build_character_bible.py --events data/timeline/source_events.json --overwrite
```

- Generar episodios (JSON) desde un plan diario:

```bash
python3 scripts/generate_episodes_from_plan.py --plan data/daily_plan/plan-20260315.json
```

- Validar episodios generados:

```bash
python3 scripts/validate_generated_episodes.py --dir data/episodes/generated/plan-20260315
```

- Validar personajes y timelines generados:

```bash
python3 scripts/validate_generated_characters.py
```

- Generar assets por escena con IA (imagen + voz):

```bash
python3 scripts/generate_scene_assets.py --episode data/episodes/generated/plan-20260315/main-20260315-linea_01.json
```

- Componer MP4 final con assets (subtitulos incrustados):

```bash
python3 scripts/compose_final_video.py --episode data/episodes/generated/plan-20260315/main-20260315-linea_01.json
```

SVG opcionales para overlays del montaje final:

- `assets/video_overlays/narration.svg`
- `assets/video_overlays/dialogue.svg`
- `assets/video_overlays/shout.svg`
- Si no existen, `compose_final_video.py` usa el estilo legacy generado con filtros de ffmpeg.

- Ejecutar pipeline final en lote para un directorio de episodios:

```bash
bash scripts/run_final_ai_video_pipeline.sh data/episodes/generated/plan-20260315
```

- Ejecutar pipeline final directamente desde un plan diario:

```bash
bash scripts/run_final_ai_video_pipeline_from_plan.sh data/daily_plan/plan-20260315.json
```

- Prueba sin APIs (mock local con ffmpeg):

```bash
bash scripts/run_final_ai_video_pipeline.sh data/episodes/generated/plan-20260315 --mock
```

- Ejecutar un smoke test completo (plan -> episodios -> 1 main + 1 teaser renderizados):

```bash
bash scripts/run_local_first_batch.sh 2026-03-15
```

- Pipeline grafico completo desde libro:

```bash
bash scripts/run_graphic_story_from_book.sh \
  \"docs/chronicles/01-La gran aventura del reino de Asturias.pdf\" \
  2026-03-15 \
  --reset-progression
```

- Bootstrap rapido:

```bash
bash scripts/bootstrap.sh
```

## Workflows n8n

Los archivos en `workflows/` son esqueletos importables y versionables.

- Import manual: UI de n8n -> Workflows -> Import from file.
- Import/export por API: ver `scripts/import_workflows.sh` y `scripts/export_workflows.sh`.

## Website legal (TikTok Developers)

- Home: `website/index.html`
- Privacy Policy: `website/privacy-policy.html`
- Terms of Service: `website/terms-of-service.html`

## Git flow recomendado

- Rama estable: `main`
- Rama de integracion: `develop`
- Features: `feature/<scope>`
- Hotfixes: `hotfix/<scope>`

Detalles en `CONTRIBUTING.md`.

## Primer Video (sin TikTok API)

Guia rapida en `docs/local_video_pipeline.md`.

## Modo estricto de fuentes

- Sin eventos validos en `data/timeline/source_events.json`, no se genera plan ni episodios.
- La planificacion diaria avanza de forma lineal entre dias mediante:
  - `data/timeline/progression_state.json` (estado runtime, no versionado)
- Sin `data/characters/<id>.json` y `data/characters/timelines/<id>.json` para los actores de cada evento, no se generan episodios.
