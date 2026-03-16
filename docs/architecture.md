# Arquitectura actual

Este documento describe solo el flujo que existe hoy en el repo. Lo obsoleto o lo que no forma parte del camino real de ejecucion actual queda fuera.

## Diagrama general

```mermaid
flowchart TD
    subgraph Narrative["1. Flujo narrativo"]
        direction TB
        Source["Fuente historica<br/>docs/chronicles/*.pdf|*.txt|*.md"]
        Sidecar["OCR sidecar opcional<br/>docs/chronicles/sidecar_text/*.txt"]
        Extract["scripts/extract_source_events.py"]
        Timeline["data/timeline/source_events.json"]

        CharBible["scripts/build_character_bible.py"]
        Characters["data/characters/*.json"]
        CharTimelines["data/characters/timelines/*.json"]
        ValidateChars["scripts/validate_generated_characters.py"]

        StoryEngine["scripts/story_engine.py"]
        PlanGen["scripts/generate_daily_plan.py"]
        DailyPlan["data/daily_plan/plan-YYYYMMDD.json<br/>incluye storytelling.scene_outline"]

        EpisodeGen["scripts/generate_episodes_from_plan.py"]
        Episodes["data/episodes/generated/plan-YYYYMMDD/*.json"]
        ValidateEpisodes["scripts/validate_generated_episodes.py"]
        Storyboard["scripts/print_episode_storyboard.py"]

        Source --> Extract
        Sidecar -.-> Extract
        Extract --> Timeline

        Timeline --> CharBible
        CharBible --> Characters
        CharBible --> CharTimelines
        Characters --> ValidateChars
        CharTimelines --> ValidateChars

        Timeline --> StoryEngine
        StoryEngine --> PlanGen
        Timeline --> PlanGen
        PlanGen --> DailyPlan

        DailyPlan --> EpisodeGen
        Timeline --> EpisodeGen
        Characters --> EpisodeGen
        CharTimelines --> EpisodeGen
        StoryEngine -. fallback teaser .-> EpisodeGen
        EpisodeGen --> Episodes
        Episodes --> ValidateEpisodes
        Episodes --> Storyboard
    end

    subgraph Render["2. Flujo de render"]
        direction TB
        FinalWrapper["bash scripts/run_final_ai_video_pipeline.sh"]
        Mode{"Modo"}
        AssetsReal["scripts/generate_scene_assets.py --episode ..."]
        AssetsMock["scripts/generate_scene_assets.py --episode ... --mock"]
        AssetsFallback["scripts/generate_scene_assets.py --episode ... --fallback-mock-on-billing-error"]
        SceneAssets["artifacts/scene_assets/{episode_id}/<br/>manifest.json + PNG + MP3 + prompts"]
        Compose["scripts/compose_final_video.py --episode ... --no-burn-subtitles"]
        FinalVideo["artifacts/videos/final/*.mp4"]
        FinalSubs["artifacts/subtitles/final/*.srt"]

        FinalWrapper --> Mode
        Mode -->|real| AssetsReal
        Mode -->|mock| AssetsMock
        Mode -->|fallback| AssetsFallback
        AssetsReal --> SceneAssets
        AssetsMock --> SceneAssets
        AssetsFallback --> SceneAssets
        SceneAssets --> Compose
        Compose --> FinalVideo
        Compose --> FinalSubs
    end

    subgraph Ops["3. Wrappers operativos"]
        direction TB
        Book["bash scripts/run_graphic_story_from_book.sh SOURCE DATE [--reset-progression]"]
        PlanWrapper["bash scripts/run_final_ai_video_pipeline_from_plan.sh plan.json [modo]"]
        Smoke["bash scripts/run_local_first_batch.sh DATE [--reset-progression]"]

        BookExtract["extraer eventos"]
        BookChars["build_character_bible + validate_generated_characters"]
        BookPlan["generate_daily_plan"]
        BookEpisode["generate_episodes_from_plan + validate_generated_episodes"]
        BookRender["render mock si ffmpeg existe"]

        PlanEpisode["generate_episodes_from_plan + validate_generated_episodes"]
        PlanRender["run_final_ai_video_pipeline.sh"]

        SmokePlan["generate_daily_plan"]
        SmokeChars["build_character_bible + validate_generated_characters"]
        SmokeEpisode["generate_episodes_from_plan"]
        SmokeRender["render mock de 1 main + 1 teaser"]

        Book --> BookExtract --> BookChars --> BookPlan --> BookEpisode --> BookRender
        PlanWrapper --> PlanEpisode --> PlanRender
        Smoke --> SmokePlan --> SmokeChars --> SmokeEpisode --> SmokeRender
    end

    ValidateEpisodes --> FinalWrapper
    Episodes -. entrada .-> FinalWrapper
    Storyboard --> Review["Revision humana"]
    FinalVideo --> Review
    FinalSubs --> Review
    Review -->|ajustar storytelling| PlanGen
    Review -->|ajustar episodio JSON| EpisodeGen
    Review -->|regenerar assets o montaje| FinalWrapper

    BookExtract -. usa .-> Extract
    BookChars -. usa .-> CharBible
    BookPlan -. usa .-> PlanGen
    BookEpisode -. usa .-> EpisodeGen
    BookRender -. usa .-> FinalWrapper

    PlanEpisode -. usa .-> EpisodeGen
    PlanRender -. usa .-> FinalWrapper

    SmokePlan -. usa .-> PlanGen
    SmokeChars -. usa .-> CharBible
    SmokeEpisode -. usa .-> EpisodeGen
    SmokeRender -. usa .-> FinalWrapper

    classDef source fill:#E8F7E8,stroke:#1B5E20,color:#123524,stroke-width:2px;
    classDef engine fill:#FFF4D6,stroke:#B7791F,color:#5F370E,stroke-width:2px;
    classDef data fill:#E8F1FF,stroke:#1D4ED8,color:#102A43,stroke-width:2px;
    classDef validate fill:#FDE68A,stroke:#92400E,color:#451A03,stroke-width:2px;
    classDef render fill:#F3E8FF,stroke:#7C3AED,color:#2E1065,stroke-width:2px;
    classDef wrapper fill:#E0F2FE,stroke:#0369A1,color:#082F49,stroke-width:2px;
    classDef review fill:#FFE4E6,stroke:#BE123C,color:#881337,stroke-width:2px;

    class Source,Sidecar,Extract source;
    class StoryEngine,PlanGen,EpisodeGen,CharBible engine;
    class Timeline,DailyPlan,Characters,CharTimelines,Episodes,SceneAssets,FinalVideo,FinalSubs data;
    class ValidateChars,ValidateEpisodes,Storyboard validate;
    class FinalWrapper,Mode,AssetsReal,AssetsMock,AssetsFallback,Compose render;
    class Book,PlanWrapper,Smoke,BookExtract,BookChars,BookPlan,BookEpisode,BookRender,PlanEpisode,PlanRender,SmokePlan,SmokeChars,SmokeEpisode,SmokeRender wrapper;
    class Review review;
```

## Lectura del diagrama

- Bloque 1: el pipeline narrativo convierte la fuente en `source_events.json`, construye personajes y timelines, genera un `plan` con storytelling por escena y materializa episodios JSON.
- Bloque 2: el pipeline de render toma episodios validados, genera assets por escena y compone el MP4 y el SRT final.
- Bloque 3: los wrappers no introducen logica nueva; solo orquestan tramos del pipeline general para casos de uso concretos.

## Contratos y artefactos

- Timeline normalizado: `data/timeline/source_events.json`
- Estado de progresion: `data/timeline/progression_state.json`
- Personajes: `data/characters/*.json`
- Timelines de personaje: `data/characters/timelines/*.json`
- Plan diario: `data/daily_plan/plan-YYYYMMDD.json`
- Episodios: `data/episodes/generated/plan-YYYYMMDD/*.json`
- Assets por escena: `artifacts/scene_assets/{episode_id}/`
- Videos finales: `artifacts/videos/final/*.mp4`
- Subtitulos finales: `artifacts/subtitles/final/*.srt`

## Dependencias exactas

- `scripts/generate_daily_plan.py` depende de `data/timeline/source_events.json`.
- `scripts/build_character_bible.py` depende de `data/timeline/source_events.json`.
- `scripts/generate_episodes_from_plan.py` depende de:
  - `data/timeline/source_events.json`
  - `data/daily_plan/plan-YYYYMMDD.json`
  - `data/characters/*.json`
  - `data/characters/timelines/*.json`
- `scripts/run_final_ai_video_pipeline_from_plan.sh` ejecuta, en este orden:
  1. `scripts/generate_episodes_from_plan.py`
  2. `scripts/validate_generated_episodes.py`
  3. `scripts/run_final_ai_video_pipeline.sh`
- `scripts/run_final_ai_video_pipeline.sh` ejecuta, por cada episodio:
  1. `scripts/generate_scene_assets.py`
  2. `scripts/compose_final_video.py --no-burn-subtitles`

## Fuera de este documento

No se documentan aqui como parte del flujo actual:

- `n8n`
- `PostgreSQL`
- `Redis`
- `MinIO`
- workflows futuros o no conectados al camino real local-first
