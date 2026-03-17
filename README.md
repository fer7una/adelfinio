# Adelfinio - Pipeline narrativo fuente-first

Base tecnica para producir historias historicas verticales a partir de una fuente canonica revisada, con OCR persistido, modelado de personajes via chat, catalogo lineal de episodios y render final a video.

Duraciones objetivo:
- Episodio principal: 90-150s (maximo 180s)

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
- `data/`: artefactos fuente, personajes, catalogos de historia, timeline y episodios
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

- Construir `source_pack` desde la fuente canonica:

```bash
python3 scripts/build_source_pack.py \
  --source docs/chronicles/01-La\\ gran\\ aventura\\ del\\ reino\\ de\\ Asturias.pdf \
  --output data/source/source_pack.json \
  --overwrite
```

- Revisar y aprobar `source_pack`:

```bash
python3 scripts/review_source_pack.py \
  --source-pack data/source/source_pack.json \
  --approve \
  --reviewer editor_historia
```

- Generar `character_bible` y compatibilidad de personajes/timelines:

```bash
python3 scripts/generate_character_bible.py \
  --source-pack data/source/source_pack.json \
  --output data/characters/character_bible.json
```

- Generar catalogo lineal de historias:

```bash
python3 scripts/generate_story_catalog.py \
  --source-pack data/source/source_pack.json \
  --character-bible data/characters/character_bible.json
```

- Generar episodio final desde un `story_id`:

```bash
python3 scripts/generate_episode_from_story.py \
  --source-pack data/source/source_pack.json \
  --character-bible data/characters/character_bible.json \
  --story-catalog data/story/story_catalog.json \
  --story-id story-resistencia-pelayo
```

- Validar los nuevos artefactos del pipeline:

```bash
python3 scripts/validate_story_pipeline.py \
  --source-pack data/source/source_pack_example.json \
  --character-bible data/characters/character_bible_example.json \
  --story-catalog data/story/story_catalog_example.json
```

- Wrapper principal del flujo fuente-first:

```bash
bash scripts/run_story_pipeline_from_source.sh \
  \"docs/chronicles/01-La gran aventura del reino de Asturias.pdf\"
```

- Componer MP4 final con assets (subtitulos incrustados):

```bash
python3 scripts/compose_final_video.py --episode data/episodes/generated/story-catalog/main-20260317-story-resistencia-pelayo.json
```

SVG opcionales para overlays del montaje final:

- `assets/video_overlays/narration.svg`
- `assets/video_overlays/dialogue.svg`
- `assets/video_overlays/shout.svg`
- Si no existen, `compose_final_video.py` usa el estilo fallback generado con filtros de ffmpeg.

- Ejecutar pipeline final en lote para un directorio de episodios:

```bash
bash scripts/run_final_ai_video_pipeline.sh data/episodes/generated/story-catalog
```

- Prueba sin APIs (mock local con ffmpeg):

```bash
bash scripts/run_final_ai_video_pipeline.sh data/episodes/generated/story-catalog --mock
```

Control de calidad de imagen:

- `OPENAI_IMAGE_QUALITY=low|medium|high|auto`
- Tambien disponible por CLI: `python3 scripts/generate_scene_assets.py --image-quality medium ...`

Separacion de modelos por fase:

- `OPENAI_CHARACTER_MODEL`
- `OPENAI_STORY_CATALOG_MODEL`
- `OPENAI_EPISODE_MODEL`
- Si no se definen, cada script cae primero en `OPENAI_STORY_MODEL` y luego en `gpt-5.4`

- Pipeline grafico completo desde fuente revisada:

```bash
bash scripts/run_graphic_story_from_book.sh \
  \"docs/chronicles/01-La gran aventura del reino de Asturias.pdf\"
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

## Nuevo flujo fuente-first

- `source_pack.json` es la fuente narrativa canonica revisada.
- `data/timeline/source_events.json` sigue existiendo como artefacto derivado.
- El catalogo de historias es lineal y la eleccion del `story_id` es manual.
- `episode.json` sigue siendo el contrato final consumido por el render, ahora ampliado con trazabilidad e inferencias etiquetadas.
- Sin `source_pack.review.status=approved`, no se generan personajes, catalogos ni episodios.
