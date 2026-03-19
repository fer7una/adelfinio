# Arquitectura actual

Este documento describe solo el flujo vigente del repo tras la retirada del pipeline heuristico basado en `daily_plan`.

## Diagrama general

```mermaid
flowchart TD
    subgraph Source["1. Fuente canonica"]
        direction TB
        Chronicle["Fuente historica<br/>docs/chronicles/*.pdf|*.txt|*.md|*.json"]
        SourcePack["scripts/build_source_pack.py<br/>sin LLM<br/>req: --source<br/>opt: --output --derived-events-output --artifacts-root --start-page --end-page --review-threshold --overwrite"]
        Raster["artifacts/source_pack/{source}/pages/*.png"]
        Ocr["artifacts/source_pack/{source}/ocr/*.txt|*.tsv"]
        Pack["data/source/{source}.source_pack.json"]
        Timeline["data/timeline/source_events.json<br/>artefacto derivado"]
        Review["scripts/review_source_pack.py<br/>sin LLM<br/>req: --source-pack<br/>opt: --approve --reject --summary --reviewer --notes"]

        Chronicle --> SourcePack
        SourcePack --> Raster
        SourcePack --> Ocr
        SourcePack --> Pack
        SourcePack --> Timeline
        Pack --> Review
    end

    subgraph Narrative["2. Pipeline narrativo"]
        direction TB
        CharBible["scripts/generate_character_bible.py<br/>LLM<br/>req: none<br/>opt: --source-pack --output --characters-dir --timelines-dir --dotenv --model --reasoning-effort --overwrite"]
        Bible["data/characters/{source}.character_bible.json"]
        Characters["data/characters/*.json<br/>compatibilidad render"]
        CharTimelines["data/characters/timelines/*.json<br/>compatibilidad render"]
        ValidateChars["scripts/validate_generated_characters.py<br/>sin LLM<br/>req: none<br/>opt: --characters-dir --timelines-dir"]

        StoryCatalog["scripts/generate_story_catalog.py<br/>LLM<br/>req: none<br/>opt: --source-pack --character-bible --output --dotenv --model --reasoning-effort --overwrite"]
        Catalog["data/story/{source}.story_catalog.json"]
        ManualSelect["Seleccion manual de story_id"]

        EpisodeGen["scripts/generate_episode_from_story.py<br/>LLM + revision lingüistica LLM<br/>req: --story-id<br/>opt: --source-pack --character-bible --story-catalog --output --output-dir --dotenv --model --reasoning-effort --overwrite"]
        Episodes["data/episodes/generated/{source}/*.json"]
        ValidateStory["scripts/validate_story_pipeline.py<br/>sin LLM<br/>req: --source-pack --character-bible --story-catalog<br/>opt: --episode"]
        ValidateEpisodes["scripts/validate_generated_episodes.py<br/>sin LLM<br/>req: --dir<br/>opt: none"]

        Review -->|approve required| CharBible
        Pack --> CharBible
        CharBible --> Bible
        CharBible --> Characters
        CharBible --> CharTimelines
        Characters --> ValidateChars
        CharTimelines --> ValidateChars

        Pack --> StoryCatalog
        Bible --> StoryCatalog
        StoryCatalog --> Catalog
        Catalog --> ManualSelect

        Pack --> EpisodeGen
        Bible --> EpisodeGen
        Catalog --> EpisodeGen
        ManualSelect --> EpisodeGen
        EpisodeGen --> Episodes
        Pack --> ValidateStory
        Bible --> ValidateStory
        Catalog --> ValidateStory
        Episodes --> ValidateStory
        Episodes --> ValidateEpisodes
    end

    subgraph Render["3. Render final"]
        direction TB
        FinalWrapper["bash scripts/run_final_ai_video_pipeline.sh<br/>req: <episode_json_or_directory><br/>opt: --mock --fallback-mock-on-billing-error"]
        Assets["scripts/generate_scene_assets.py<br/>LLM solo para imagen/TTS reales<br/>req: --episode<br/>opt: --assets-dir --dotenv --characters-dir --image-model --image-size --image-quality --tts-model --tts-voice --mock --fallback-mock-on-billing-error"]
        Layout["scripts/layout_analysis.py<br/>LLM multimodal opcional<br/>invocado dentro de generate_scene_assets.py<br/>req: --image --scene-json --phases-json --output<br/>opt: --dotenv --model --reasoning-effort --mock"]
        Compose["scripts/compose_final_video.py<br/>sin LLM<br/>req: --episode<br/>opt: --assets-dir --output-video --output-srt --fps --width --height --overlay-assets-dir --font-file --burn-subtitles --no-burn-subtitles"]
        SceneAssets["artifacts/scene_assets/{episode_id}/<br/>manifest.json + scene_image + text_phases + MP3 + prompts"]
        SceneImage["1 imagen base por escena"]
        SceneAudio["audio continuo por escena<br/>compuesto desde fases"]
        LayoutData["focus_bbox + protected_regions + overlay_bbox + camera_track"]
        FinalVideo["artifacts/videos/final/*.mp4"]
        FinalSubs["artifacts/subtitles/final/*.srt"]

        Episodes --> FinalWrapper
        FinalWrapper --> Assets
        Assets --> SceneImage
        Assets --> SceneAudio
        SceneImage --> Layout
        Assets --> SceneAssets
        Layout --> LayoutData
        SceneAudio --> SceneAssets
        LayoutData --> SceneAssets
        SceneAssets --> Compose
        Compose --> FinalVideo
        Compose --> FinalSubs
    end

    subgraph Ops["4. Wrapper principal"]
        direction TB
        Wrapper["bash scripts/run_story_pipeline_from_source.sh<br/>req: SOURCE<br/>opt: [story_id] [--approve-source reviewer] [--mock] [--fallback-mock-on-billing-error] [--overwrite]"]
        Wrapper --> SourcePack
        Wrapper --> Review
        Wrapper --> CharBible
        Wrapper --> StoryCatalog
        Wrapper --> EpisodeGen
        Wrapper --> FinalWrapper
    end

    Legend["Leyenda<br/>req: argumentos obligatorios<br/>opt: argumentos opcionales"]

    classDef source fill:#E8F7E8,stroke:#1B5E20,color:#123524,stroke-width:2px;
    classDef narrative fill:#FFF4D6,stroke:#B7791F,color:#5F370E,stroke-width:2px;
    classDef data fill:#E8F1FF,stroke:#1D4ED8,color:#102A43,stroke-width:2px;
    classDef validate fill:#FDE68A,stroke:#92400E,color:#451A03,stroke-width:2px;
    classDef render fill:#F3E8FF,stroke:#7C3AED,color:#2E1065,stroke-width:2px;
    classDef wrapper fill:#E0F2FE,stroke:#0369A1,color:#082F49,stroke-width:2px;
    classDef review fill:#FFE4E6,stroke:#BE123C,color:#881337,stroke-width:2px;

    class Chronicle,SourcePack,Raster,Ocr,Legend source;
    class CharBible,StoryCatalog,EpisodeGen,ManualSelect narrative;
    class Pack,Timeline,Bible,Characters,CharTimelines,Catalog,Episodes,SceneAssets,FinalVideo,FinalSubs data;
    class ValidateChars,ValidateStory,ValidateEpisodes validate;
    class FinalWrapper,Assets,Compose render;
    class Wrapper wrapper;
    class Review review;
```

## Lectura del diagrama

- Bloque 1: la fuente canonica se transforma en un `source_pack` con OCR persistido, chunks trazables y un `source_events.json` derivado.
- Bloque 2: el pipeline narrativo solo arranca cuando `source_pack.review.status=approved`; desde ahi genera `character_bible`, `story_catalog` y finalmente el `episode.json` seleccionado. El episodio pasa por una segunda revision lingüistica automatica antes de escribirse en disco. Es la parte principal que usa LLM de texto estructurado.
- Bloque 3: el render sigue consumiendo episodios JSON validados, pero ahora genera una sola imagen base por escena, monta el audio continuo de la escena desde varias fases, ejecuta un analisis visual para obtener foco y regiones protegidas, y compone el video con `text_phases` secuenciales sobre esa misma imagen. Aqui el uso de LLM es opcional en layout y obligatorio solo si no estas en `--mock` para imagen/TTS.
- Bloque 4: el wrapper principal orquesta toda la secuencia y se detiene si falta aprobacion de fuente o seleccion de `story_id`. Importante: `--mock` en este wrapper solo afecta al render final; no mockea `character_bible`, `story_catalog` ni `episode`.

## Warning sobre `--mock`

- `--mock` existe en el render: `scripts/run_final_ai_video_pipeline.sh`, `scripts/generate_scene_assets.py` y `scripts/layout_analysis.py`.
- `--mock` no existe en `scripts/generate_character_bible.py`, `scripts/generate_story_catalog.py` ni `scripts/generate_episode_from_story.py`.
- Por tanto, `bash scripts/run_story_pipeline_from_source.sh SOURCE ... --mock` sigue haciendo llamadas LLM en la fase narrativa; solo evita las llamadas OpenAI de imagen/TTS/layout del render.

## Uso de LLM por etapa

- `scripts/build_source_pack.py`: no usa LLM. Hace OCR, limpieza y empaquetado de fuente.
- `scripts/review_source_pack.py`: no usa LLM. Es revision/aprobacion humana.
- `scripts/generate_character_bible.py`: usa LLM de texto estructurado.
- `scripts/generate_story_catalog.py`: usa LLM de texto estructurado.
- `scripts/generate_episode_from_story.py`: usa LLM de texto estructurado para generar el episodio y una segunda pasada LLM para corregir ortografia, gramatica, puntuacion y frases incoherentes sin tocar IDs ni trazabilidad.
- `scripts/generate_scene_assets.py`: usa modelos generativos solo en modo real.
  - imagen: LLM multimodal/generativo de imagen
  - voz: TTS
  - en `--mock` no usa LLM
- `scripts/layout_analysis.py`: puede usar LLM multimodal para detectar `focus_target` y `protected_regions`; si falla o estas en `--mock`, cae a heuristica local.
- `scripts/compose_final_video.py`: no usa LLM. Solo FFmpeg y layout ya calculado.

## Principios operativos del render

- Una escena ya no equivale a una sola caja de texto ni a una sola imagen por bloque.
- La unidad visual es `scene_image_path`: una ilustracion base reutilizada durante toda la escena.
- La unidad temporal visible es `text_phases`: fragmentos secuenciales de narracion o dialogo con `phase_start_s` y `phase_end_s`.
- El zoom se calcula desde `camera_track.focus_bbox`, no desde un ancla fija en el centro.
- La posicion del overlay se calcula desde `overlay_bbox` para evitar tapar `focus_target` y `protected_regions`.
- Los subtitulos `.srt` se exportan por fases, pero no se queman en el MP4 por defecto.

## Contratos y artefactos

- Fuente canónica revisable: `data/source/{source}.source_pack.json`
- Timeline derivado: `data/timeline/source_events.json`
- Character bible consolidado: `data/characters/{source}.character_bible.json`
- Personajes compatibles con render: `data/characters/*.json`
- Timelines compatibles con render: `data/characters/timelines/*.json`
- Catalogo lineal de historias: `data/story/{source}.story_catalog.json`
- Episodios finales: `data/episodes/generated/{source}/*.json`
- Assets por escena: `artifacts/scene_assets/{episode_id}/`
  - `scene_image_path`: ilustracion base de la escena
  - `audio_path`: audio continuo de la escena, derivado de varias fases
  - `text_phases`: fases secuenciales de narracion/dialogo con tiempos propios
  - `layout_analysis.focus_target`: sujeto u objeto principal detectado visualmente
  - `layout_analysis.protected_regions`: regiones que no deben taparse
  - `overlay_bbox`: caja donde cabe cada overlay sin tapar el foco
  - `camera_track.focus_bbox`: foco real para el zoom
- Videos finales: `artifacts/videos/final/*.mp4`
- Subtitulos finales: `artifacts/subtitles/final/*.srt`

## Dependencias exactas

- `scripts/build_source_pack.py` depende de la fuente canonica y genera:
  - `data/source/{source}.source_pack.json`
  - `data/timeline/source_events.json`
  - `artifacts/source_pack/{source}/`
- `scripts/review_source_pack.py` actualiza el bloque `review` del `source_pack`.
- `scripts/generate_character_bible.py` depende de:
  - `data/source/{source}.source_pack.json` aprobado
  - OpenAI API
  - LLM de texto estructurado
  - no tiene `--mock`
- `scripts/generate_story_catalog.py` depende de:
  - `data/source/{source}.source_pack.json` aprobado
  - `data/characters/{source}.character_bible.json`
  - OpenAI API
  - LLM de texto estructurado
  - no tiene `--mock`
- `scripts/generate_episode_from_story.py` depende de:
  - `data/source/{source}.source_pack.json` aprobado
  - `data/characters/{source}.character_bible.json`
  - `data/story/{source}.story_catalog.json`
  - un `story_id` valido
  - OpenAI API
  - LLM de texto estructurado
  - revision linguistica automatica del texto final (`OPENAI_EPISODE_REVIEW_MODEL` opcional; por defecto reutiliza el modelo de episodio, `OPENAI_EPISODE_REVIEW_REASONING_EFFORT` opcional con default `low`, y `OPENAI_EPISODE_REVIEW_SCOPE=full|render`)
  - no tiene `--mock`
- `scripts/generate_scene_assets.py` depende de:
  - `data/episodes/generated/{source}/*.json`
  - OpenAI Images API o `--mock`
  - OpenAI TTS API o `--mock`
  - opcionalmente `OPENAI_LAYOUT_MODEL` para analisis visual posterior
- `scripts/layout_analysis.py` depende de:
  - `scene_image_path`
  - metadata de escena y `text_phases`
  - OpenAI Responses API multimodal o fallback heuristico
- `scripts/compose_final_video.py` depende de:
  - `artifacts/scene_assets/{episode_id}/manifest.json`
  - `scene_image_path`, `audio_path`, `text_phases`, `overlay_bbox` y `camera_track`
  - `ffmpeg`
- `scripts/run_final_ai_video_pipeline.sh` ejecuta, por cada episodio:
  1. `scripts/generate_scene_assets.py`
  2. `scripts/compose_final_video.py --no-burn-subtitles`
- El analisis de layout ocurre dentro de `scripts/generate_scene_assets.py` mediante `layout_analysis.py`; `scripts/analyze_scene_layout.py` queda como wrapper CLI standalone, no como paso separado del wrapper principal de render.

## Orden interno del render por escena

1. `generate_scene_assets.py` parte la escena en `text_phases` segun el espacio disponible en narracion/dialogo.
2. El mismo script genera una unica ilustracion base para la escena.
3. Cada fase genera o mockea su audio; despues se concatena en un `audio_path` continuo por escena.
4. `layout_analysis.py` analiza la imagen base y devuelve foco, regiones protegidas y cajas recomendadas para overlays.
5. El `manifest.json` guarda esa escena con su `scene_image_path`, `audio_path`, `text_phases`, `layout_analysis` y `camera_track`.
6. `compose_final_video.py` recorre las fases, hace zoom continuo sobre el foco y coloca el bocadillo correspondiente en su `overlay_bbox`.
7. Se exporta un `.srt` por fases y un MP4 final sin subtitulos quemados salvo que se pida explicitamente `--burn-subtitles`.

## Fuera de este documento

No se documentan aqui como parte del flujo local vigente:

- n8n como orquestador principal
- PostgreSQL
- Redis
- MinIO
- workflows legacy no conectados al pipeline fuente-first
