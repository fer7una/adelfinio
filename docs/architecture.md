# Arquitectura actual

Este documento describe solo el flujo vigente del repo tras la retirada del pipeline heuristico basado en `daily_plan`.

## Diagrama general

```mermaid
flowchart TD
    subgraph Source["1. Fuente canonica"]
        direction TB
        Chronicle["Fuente historica<br/>docs/chronicles/*.pdf|*.txt|*.md|*.json"]
        SourcePack["scripts/build_source_pack.py"]
        Raster["artifacts/source_pack/{source}/pages/*.png"]
        Ocr["artifacts/source_pack/{source}/ocr/*.txt|*.tsv"]
        Pack["data/source/{source}.source_pack.json"]
        Timeline["data/timeline/source_events.json<br/>artefacto derivado"]
        Review["scripts/review_source_pack.py"]

        Chronicle --> SourcePack
        SourcePack --> Raster
        SourcePack --> Ocr
        SourcePack --> Pack
        SourcePack --> Timeline
        Pack --> Review
    end

    subgraph Narrative["2. Pipeline narrativo"]
        direction TB
        CharBible["scripts/generate_character_bible.py"]
        Bible["data/characters/{source}.character_bible.json"]
        Characters["data/characters/*.json<br/>compatibilidad render"]
        CharTimelines["data/characters/timelines/*.json<br/>compatibilidad render"]
        ValidateChars["scripts/validate_generated_characters.py"]

        StoryCatalog["scripts/generate_story_catalog.py"]
        Catalog["data/story/{source}.story_catalog.json"]
        ManualSelect["Seleccion manual de story_id"]

        EpisodeGen["scripts/generate_episode_from_story.py"]
        Episodes["data/episodes/generated/{source}/*.json"]
        ValidateStory["scripts/validate_story_pipeline.py"]
        ValidateEpisodes["scripts/validate_generated_episodes.py"]

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
        FinalWrapper["bash scripts/run_final_ai_video_pipeline.sh"]
        Assets["scripts/generate_scene_assets.py"]
        Compose["scripts/compose_final_video.py --no-burn-subtitles"]
        SceneAssets["artifacts/scene_assets/{episode_id}/<br/>manifest.json + PNG + MP3 + prompts"]
        FinalVideo["artifacts/videos/final/*.mp4"]
        FinalSubs["artifacts/subtitles/final/*.srt"]

        Episodes --> FinalWrapper
        FinalWrapper --> Assets
        Assets --> SceneAssets
        SceneAssets --> Compose
        Compose --> FinalVideo
        Compose --> FinalSubs
    end

    subgraph Ops["4. Wrapper principal"]
        direction TB
        Wrapper["bash scripts/run_story_pipeline_from_source.sh SOURCE [story_id] [--approve-source reviewer] [--mock]"]
        Wrapper --> SourcePack
        Wrapper --> Review
        Wrapper --> CharBible
        Wrapper --> StoryCatalog
        Wrapper --> EpisodeGen
        Wrapper --> FinalWrapper
    end

    classDef source fill:#E8F7E8,stroke:#1B5E20,color:#123524,stroke-width:2px;
    classDef narrative fill:#FFF4D6,stroke:#B7791F,color:#5F370E,stroke-width:2px;
    classDef data fill:#E8F1FF,stroke:#1D4ED8,color:#102A43,stroke-width:2px;
    classDef validate fill:#FDE68A,stroke:#92400E,color:#451A03,stroke-width:2px;
    classDef render fill:#F3E8FF,stroke:#7C3AED,color:#2E1065,stroke-width:2px;
    classDef wrapper fill:#E0F2FE,stroke:#0369A1,color:#082F49,stroke-width:2px;
    classDef review fill:#FFE4E6,stroke:#BE123C,color:#881337,stroke-width:2px;

    class Chronicle,SourcePack,Raster,Ocr source;
    class CharBible,StoryCatalog,EpisodeGen,ManualSelect narrative;
    class Pack,Timeline,Bible,Characters,CharTimelines,Catalog,Episodes,SceneAssets,FinalVideo,FinalSubs data;
    class ValidateChars,ValidateStory,ValidateEpisodes validate;
    class FinalWrapper,Assets,Compose render;
    class Wrapper wrapper;
    class Review review;
```

## Lectura del diagrama

- Bloque 1: la fuente canonica se transforma en un `source_pack` con OCR persistido, chunks trazables y un `source_events.json` derivado.
- Bloque 2: el pipeline narrativo solo arranca cuando `source_pack.review.status=approved`; desde ahi genera `character_bible`, `story_catalog` y finalmente el `episode.json` seleccionado.
- Bloque 3: el render sigue consumiendo episodios JSON validados y genera assets, subtitulos y MP4.
- Bloque 4: el wrapper principal orquesta toda la secuencia y se detiene si falta aprobacion de fuente o seleccion de `story_id`.

## Contratos y artefactos

- Fuente canónica revisable: `data/source/{source}.source_pack.json`
- Timeline derivado: `data/timeline/source_events.json`
- Character bible consolidado: `data/characters/{source}.character_bible.json`
- Personajes compatibles con render: `data/characters/*.json`
- Timelines compatibles con render: `data/characters/timelines/*.json`
- Catalogo lineal de historias: `data/story/{source}.story_catalog.json`
- Episodios finales: `data/episodes/generated/{source}/*.json`
- Assets por escena: `artifacts/scene_assets/{episode_id}/`
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
- `scripts/generate_story_catalog.py` depende de:
  - `data/source/{source}.source_pack.json` aprobado
  - `data/characters/{source}.character_bible.json`
  - OpenAI API
- `scripts/generate_episode_from_story.py` depende de:
  - `data/source/{source}.source_pack.json` aprobado
  - `data/characters/{source}.character_bible.json`
  - `data/story/{source}.story_catalog.json`
  - un `story_id` valido
  - OpenAI API
- `scripts/run_final_ai_video_pipeline.sh` ejecuta, por cada episodio:
  1. `scripts/generate_scene_assets.py`
  2. `scripts/compose_final_video.py --no-burn-subtitles`

## Fuera de este documento

No se documentan aqui como parte del flujo local vigente:

- n8n como orquestador principal
- PostgreSQL
- Redis
- MinIO
- workflows legacy no conectados al pipeline fuente-first
