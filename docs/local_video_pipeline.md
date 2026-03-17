# Local Video Pipeline (step by step)

Objetivo: validar rapido el flujo local de produccion de video a partir de una fuente canonica revisada, sin depender aun de TikTok API.

Objetivos de duracion:

- Episodio principal: 90-150 segundos (maximo 180)

## Requisitos

1. Python 3.11+
2. ffmpeg instalado en el sistema (`ffmpeg -version`)
3. Dependencia de validacion opcional: `python3 -m pip install jsonschema`
4. Dependencia para generacion IA (imagen + voz): `python3 -m pip install openai`

Instalacion rapida de ffmpeg:

- Ubuntu/WSL: `sudo apt update && sudo apt install -y ffmpeg`
- macOS (Homebrew): `brew install ffmpeg`
- Windows (winget): `winget install Gyan.FFmpeg`

## Paso 1: validar contratos base

```bash
python3 scripts/validate_json.py
```

## Paso 2: construir `source_pack`

```bash
python3 scripts/build_source_pack.py --source \
  docs/chronicles/01-La\\ gran\\ aventura\\ del\\ reino\\ de\\ Asturias.pdf \
  --overwrite
```

Salida esperada:

- `data/source/source_pack.json`
- `data/timeline/source_events.json` (artefacto derivado)
- `artifacts/source_pack/<fuente>/pages/*.png`
- `artifacts/source_pack/<fuente>/ocr/*.txt|*.tsv`

## Paso 3: revisar y aprobar `source_pack`

```bash
python3 scripts/review_source_pack.py \
  --source-pack data/source/source_pack.json \
  --approve \
  --reviewer editor_historia
```

Salida esperada:

- `data/source/source_pack.json` con `review.status=approved`

## Paso 4: construir `character_bible`, personajes y timelines compatibles

```bash
python3 scripts/generate_character_bible.py \
  --source-pack data/source/source_pack.json \
  --output data/characters/character_bible.json
```

Salida esperada:

- `data/characters/character_bible.json`
- `data/characters/<character_id>.json`
- `data/characters/timelines/<character_id>.json`

Validar:

```bash
python3 scripts/validate_generated_characters.py
```

## Paso 5: generar catalogo lineal de historias

```bash
python3 scripts/generate_story_catalog.py \
  --source-pack data/source/source_pack.json \
  --character-bible data/characters/character_bible.json
```

Salida esperada:

- `data/story/story_catalog.json`

## Paso 6: generar episodio final desde `story_id`

```bash
python3 scripts/generate_episode_from_story.py \
  --source-pack data/source/source_pack.json \
  --character-bible data/characters/character_bible.json \
  --story-catalog data/story/story_catalog.json \
  --story-id story-resistencia-pelayo
```

Salida esperada:

- `data/episodes/generated/story-catalog/*.json`

Validar artefactos y episodio:

```bash
python3 scripts/validate_story_pipeline.py \
  --source-pack data/source/source_pack.json \
  --character-bible data/characters/character_bible.json \
  --story-catalog data/story/story_catalog.json \
  --episode data/episodes/generated/story-catalog/<episode>.json
```

## Paso 7: generar video final (imagen IA + voz IA + montaje)

Generar assets por escena (OpenAI):

```bash
python3 scripts/generate_scene_assets.py \
  --episode data/episodes/generated/story-catalog/<episode>.json \
  --image-quality medium
```

Componer MP4 final con subtitulos incrustados:

```bash
python3 scripts/compose_final_video.py \
  --episode data/episodes/generated/story-catalog/<episode>.json
```

SVG opcionales para narracion/dialogo/grito:

- `assets/video_overlays/narration.svg`
- `assets/video_overlays/dialogue.svg`
- `assets/video_overlays/shout.svg`
- Tambien puedes cambiar la carpeta con `--overlay-assets-dir` o `VIDEO_OVERLAY_ASSETS_DIR`.

Salida esperada:

- `artifacts/scene_assets/<episode_id>/scenes/*.png`
- `artifacts/scene_assets/<episode_id>/scenes/*.mp3`
- `artifacts/videos/final/<episode_id>.mp4`
- `artifacts/subtitles/final/<episode_id>.srt`

Control de coste/calidad:

- `OPENAI_IMAGE_QUALITY=low|medium|high|auto`
- Si no se define, el script deja que la API use `auto`.

Ejecucion en lote (todos los episodios del plan):

```bash
bash scripts/run_final_ai_video_pipeline.sh data/episodes/generated/story-catalog
```

Modo mock sin llamadas a API (solo para test de pipeline):

```bash
bash scripts/run_final_ai_video_pipeline.sh data/episodes/generated/story-catalog --mock
```

## Pipeline de un comando

```bash
bash scripts/run_story_pipeline_from_source.sh \
  "docs/chronicles/01-La gran aventura del reino de Asturias.pdf" \
  story-resistencia-pelayo \
  --approve-source editor_historia \
  --mock \
  --overwrite
```

Este comando ejecuta:

1. `source_pack` y OCR persistido
2. Revision humana o aprobacion explicita
3. `character_bible` y salidas compatibles
4. `story_catalog`
5. Episodio final
6. Render mock u OpenAI real

## Que valida este pipeline

- Estructura de datos y consistencia de IDs
- Presencia de `source_evidence`, `plot_twist` y continuidad dramatica en episodios `main`
- Continuidad emocional y de arco por personaje (`character_beats`)
- Generacion de assets por escena y composicion final
- Punto de integracion para voz, imagen IA y subida TikTok
