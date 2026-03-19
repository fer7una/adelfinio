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
- El episodio ya sale con una revision automatica de ortografia, gramatica, puntuacion y coherencia local antes de guardarse

Validar artefactos y episodio:

```bash
python3 scripts/validate_story_pipeline.py \
  --source-pack data/source/source_pack.json \
  --character-bible data/characters/character_bible.json \
  --story-catalog data/story/story_catalog.json \
  --episode data/episodes/generated/story-catalog/<episode>.json
```

## Paso 7: generar video final (imagen IA + voz IA + layout + montaje)

Generar assets por escena (OpenAI):

```bash
python3 scripts/generate_scene_assets.py \
  --episode data/episodes/generated/story-catalog/<episode>.json \
  --image-quality medium
```

Render V2 completo:

```bash
bash scripts/run_final_ai_video_pipeline_v2.sh \
  data/episodes/generated/story-catalog/<episode>.json
```

SVG opcionales para narracion/dialogo/grito:

- `assets/video_overlays/narration.svg`
- `assets/video_overlays/dialogue.svg`
- `assets/video_overlays/shout.svg`
- Tambien puedes cambiar la carpeta con `--overlay-assets-dir` o `VIDEO_OVERLAY_ASSETS_DIR`.

Salida esperada:

- `artifacts/scene_assets/<episode_id>/scenes/*.png`
- `artifacts/scene_assets/<episode_id>/manifest.json` con `episode_brief`, `scene_brief`, `character_face_lines`, `scene_image_path`, `text_phases`, `overlay_bbox`, `focus_bbox` y `camera_track`
- `artifacts/audio_events/<episode_id>/scene_XX/utt_YYY.mp3`
- `artifacts/render_plan/<episode_id>/scene_XX.events.json`
- `artifacts/render_plan/<episode_id>/scene_XX.utterances.json`
- `artifacts/render_plan/<episode_id>/scene_XX.alignment.json`
- `artifacts/render_plan/<episode_id>/scene_XX.audio_plan.json`
- `artifacts/render_plan/<episode_id>/scene_XX.overlay_timeline.json`
- `artifacts/render_plan/<episode_id>/scene_XX.camera_plan.json`
- `artifacts/videos/clean/<episode_id>/scene_XX.clean.mp4`
- `artifacts/videos/composited/<episode_id>/scene_XX.composited.mp4`
- `artifacts/videos/final/<episode_id>.mp4`
- `artifacts/subtitles/final/<episode_id>.srt`

Control de coste/calidad:

- `OPENAI_IMAGE_QUALITY=low|medium|high|auto`
- Si no se define, el script deja que la API use `auto`.
- `OPENAI_LAYOUT_MODEL` controla el analisis visual posterior a la imagen.
- `OPENAI_LAYOUT_REASONING_EFFORT=low|medium|high` controla coste/latencia del layout.
- `OPENAI_EPISODE_REVIEW_MODEL` permite separar el modelo usado en la revision lingüistica del episodio; si no se define, se reutiliza `OPENAI_EPISODE_MODEL`.
- `OPENAI_EPISODE_REVIEW_REASONING_EFFORT=low|medium|high` controla el coste de la pasada de correccion; por defecto queda en `low`.
- `OPENAI_EPISODE_REVIEW_SCOPE=full|render` limita el alcance de la revision; `render` revisa solo `title`, `narration` y `dialogue`.

Comportamiento del render final:

- Se genera una sola imagen base por escena.
- La unidad visible pasa a ser `events.json`, un evento por pagina ya validada.
- El audio se genera por `utterance`, no por escena monolitica.
- El prompt de imagen usa una idea global del episodio y un resumen completo de escena, no fragmentos paginados.
- Los personajes reutilizan una identidad facial canonica entre escenas.
- La narracion puede paginarse si no cabe, pero las paginas consecutivas no se funden con fade-out.
- El render limpio va primero; overlays y camara se aplican despues.
- El zoom final se aplica sobre el frame ya compuesto y usa un ancla fija por escena, no un paneo por pagina.

Ejecucion en lote (todos los episodios del plan):

```bash
bash scripts/run_final_ai_video_pipeline_v2.sh data/episodes/generated/story-catalog
```

Modo mock sin llamadas a API (solo para test de pipeline):

```bash
bash scripts/run_final_ai_video_pipeline_v2.sh data/episodes/generated/story-catalog --mock
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
   - incluye una segunda pasada automatica de correccion lingüistica
6. Render mock u OpenAI real

## Que valida este pipeline

- Estructura de datos y consistencia de IDs
- Presencia de `source_evidence`, `plot_twist` y continuidad dramatica en episodios `main`
- Continuidad emocional y de arco por personaje (`character_beats`)
- Revision automatica de ortografia, gramatica, puntuacion y frases torpes antes de persistir el episodio
- Generacion de assets por escena y composicion final
- Punto de integracion para voz, imagen IA y subida TikTok
