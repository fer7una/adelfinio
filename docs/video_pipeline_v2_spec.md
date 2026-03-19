# Video Pipeline V2 Spec

Objetivo: redefinir el pipeline de render para que la postproduccion se haga sobre assets limpios y la composicion final se construya por escenas continuas, no por bloques de texto.

## Estado

Estado del documento: aprobado para implementacion.

Supuestos cerrados:

- Una escena usa una sola imagen base continua.
- No existe fade de escena dentro de una misma imagen.
- El cambio entre paginas se resuelve solo con overlay.
- Audio por `utterance`, no por escena.
- Un `event` equivale a una pagina visible ya paginada.
- La sincronizacion visible se hace con alineacion real palabra-audio.
- La respiracion solo aplica a dialogo.
- La respiracion solo aparece si el bocadillo tapa un secundario relevante.
- La respiracion siempre va antes de hablar.
- La escena puede crecer si el tiempo real lo necesita.
- Nunca se acelera audio.
- La camara es adaptable por cambios de texto visible.
- El zoom es monotono y solo entra.
- El zoom se aplica al frame ya compuesto.

## Problemas del pipeline actual

- El render heredado mezclaba en una sola fase:
  - render clean
  - overlays
  - movimiento de camara
  - concatenacion
- El render se hace por bloques de texto, no por escenas continuas.
- El cambio de pagina puede introducir transiciones no deseadas en una misma escena.
- El audio de escena es monolitico y no es la fuente canonica del timeline.
- Layout, tiempo y camara no estan desacoplados.

## Objetivos de V2

- Separar contenido editorial de artefactos de produccion.
- Hacer el render base por escena limpia continua.
- Hacer el audio por `utterance`.
- Tener artefactos intermedios trazables y revisables.
- Mantener una sola fuente de verdad por capa:
  - `episode.json`: narrativa/editorial
  - `events.json`: unidades visuales renderizables
  - `utterances.json`: unidades de locucion
  - `audio_plan.json`: tiempo real de audio
  - `overlay_timeline.json`: composicion de overlays
  - `camera_plan.json`: recorrido de camara

## No objetivos

- No redisenar el modelo editorial del episodio.
- No introducir aceleracion automatica de audio.
- No permitir fades de escena entre paginas de una misma imagen.

## Glosario

- `scene`: unidad visual continua apoyada en una sola imagen base.
- `event`: una pagina visible de texto dentro de una escena.
- `utterance`: una locucion continua, posiblemente repartida en varias paginas.
- `overlay`: bocadillo, caja de narracion o shout visible sobre la escena.
- `pre_roll_clean_s`: segundos limpios antes de un dialogo que tapa un secundario relevante.

## Arquitectura objetivo

```text
episode.json
  -> scene_assets/manifest.json
  -> render_plan/scene_XX.events.json
  -> render_plan/scene_XX.utterances.json
  -> audio_events/scene_XX/utt_YYY.mp3
  -> render_plan/scene_XX.alignment.json
  -> render_plan/scene_XX.audio_plan.json
  -> videos/clean/scene_XX.clean.mp4
  -> render_plan/scene_XX.overlay_timeline.json
  -> render_plan/scene_XX.camera_plan.json
  -> videos/composited/scene_XX.composited.mp4
  -> videos/final/<episode_id>.mp4
  -> subtitles/final/<episode_id>.srt
```

## Arbol de artefactos

```text
artifacts/
  scene_assets/
    <episode_id>/
      manifest.json
      scenes/
        scene_01.png
        scene_01.prompt.txt
        scene_01.image.prompt.txt

  audio_events/
    <episode_id>/
      scene_01/
        utt_001.mp3
        utt_002.mp3

  render_plan/
    <episode_id>/
      scene_01.events.json
      scene_01.utterances.json
      scene_01.alignment.json
      scene_01.audio_plan.json
      scene_01.overlay_timeline.json
      scene_01.camera_plan.json

  videos/
    clean/
      <episode_id>/
        scene_01.clean.mp4
    composited/
      <episode_id>/
        scene_01.composited.mp4
    final/
      <episode_id>.mp4

  subtitles/
    final/
      <episode_id>.srt
```

## Contratos

### 1. `scene_XX.events.json`

Responsabilidad: contrato canonico de eventos visuales renderizables. Un evento por pagina visible.

Campos minimos:

- `episode_id`
- `scene_index`
- `source_scene_image_path`
- `initial_scene_estimate_s`
- `events[]`

Campos por evento:

- `event_id`
- `order`
- `kind`: `narration|dialogue|shout`
- `speaker`
- `delivery`
- `text`
- `page_index`
- `page_count_in_group`
- `group_id`
- `primary_actor`
- `focus_bbox`
- `overlay_candidate_bbox`
- `requires_pre_roll_clean`

Reglas:

- `order` es estricto y secuencial.
- `text` es la pagina visible final, no el bloque completo.
- `requires_pre_roll_clean` puede quedar `null` en dialogo hasta el analisis de oclusion.
- `overlay_candidate_bbox` es sugerencia; la caja definitiva se fija en `overlay_timeline.json`.

### 2. `scene_XX.utterances.json`

Responsabilidad: contrato de locuciones continuas antes de TTS.

Campos minimos:

- `episode_id`
- `scene_index`
- `utterances[]`

Campos por utterance:

- `utterance_id`
- `kind`
- `speaker`
- `delivery`
- `full_text`
- `event_ids[]`
- `tts_voice`

Reglas:

- Varias paginas pueden compartir una misma `utterance`.
- Narracion paginada suele mapear muchas paginas a una locucion.
- Dialogo normal suele mapear una locucion por parlamento.

### 3. `scene_XX.alignment.json`

Responsabilidad: marcas palabra-audio por `utterance`.

Campos minimos:

- `scene_index`
- `utterances[]`

Campos por utterance:

- `utterance_id`
- `audio_clip_path`
- `words[]`

Campos por palabra:

- `index`
- `text`
- `start_s`
- `end_s`

Reglas:

- Se usa para resolver el cambio exacto de pagina dentro de una locucion continua.
- Sustituye reparto heuristico por palabras.

### 4. `scene_XX.audio_plan.json`

Responsabilidad: timeline real de audio y duracion final de escena.

Campos minimos:

- `episode_id`
- `scene_index`
- `scene_duration_final_s`
- `utterances[]`
- `event_bindings[]`

Campos por utterance:

- `utterance_id`
- `audio_clip_path`
- `audio_duration_s`
- `scene_audio_start_s`
- `scene_audio_end_s`

Campos por binding:

- `event_id`
- `utterance_id`
- `speech_start_s`
- `speech_end_s`
- `visible_start_s`
- `visible_end_s`
- `audio_start_s`
- `audio_end_s`
- `pre_roll_clean_s`

Reglas:

- La escena puede crecer.
- Nunca se acelera audio.
- `scene_duration_final_s` deja de ser una restriccion y pasa a ser resultado.
- `visible_end_s = max(audio_end_s, duracion_minima_legible)`.

### 5. `scene_XX.overlay_timeline.json`

Responsabilidad: layout final, visibilidad y transiciones de overlays.

Campos minimos:

- `episode_id`
- `scene_index`
- `scene_duration_s`
- `canvas`
- `source_image_path`
- `source_clean_video_path`
- `events[]`

Campos por evento:

- `event_id`
- `kind`
- `speaker`
- `delivery`
- `text`
- `start_s`
- `end_s`
- `fade_in_s`
- `fade_out_s`
- `crossfade_text_s`
- `pre_roll_clean_s`
- `region`
- `layout`
- `visibility_policy`
- `occlusion_analysis`

Campos de `region`:

- `box_norm`
- `tail_anchor_norm`
- `size_mode`

Campos de `layout`:

- `font_px`
- `line_count`
- `padding_x_px`
- `padding_y_px`

Campos de `occlusion_analysis`:

- `covers_speaker`
- `covers_focus`
- `covers_listener_face`
- `requires_pre_roll_clean`

Reglas:

- Solo una unidad textual visible a la vez.
- Dentro de la misma imagen no hay fade de escena.
- Entre paginas hay `micro-crossfade` solo del overlay.
- Narracion usa caja fija por escena.
- Dialogo y shout usan caja adaptable dentro de limites.

### 6. `scene_XX.camera_plan.json`

Responsabilidad: recorrido final de camara sobre el frame ya compuesto.

Campos minimos:

- `scene_index`
- `duration_s`
- `mode = full_frame_zoom`
- `keyframes[]`
- `constraints`

Campos por keyframe:

- `time_s`
- `zoom`
- `focus_norm`
- `active_event_id`

Campos de `constraints`:

- `keep_active_overlay_inside_frame`
- `zoom_min`
- `zoom_max`
- `monotonic_zoom`

Reglas:

- Keyframe al inicio de escena.
- Keyframe en cada cambio de texto visible o caja.
- Keyframe al final de escena.
- Zoom monotono: nunca se aleja.
- El frame debe contener overlay activo completo.
- Si hay conflicto, prioridad:
  1. no cortar bocadillo
  2. no tapar hablante
  3. mantener foco visual

## Reglas de composicion

### Escena

- Una sola imagen base por escena.
- La imagen no hace fade interno.
- El clean video es continuo.

### Overlay

- El cambio entre paginas usa `micro-crossfade` solo del overlay.
- `fade_in_s = 0.10`
- `fade_out_s = 0.10`
- `crossfade_text_s = 0.10`

### Narracion

- Caja fija por escena.
- Maximo 3 lineas.
- Crece antes de reducir tipografia.

### Dialogo

- Caja cerca del hablante, con flexibilidad moderada.
- Nunca puede tapar al hablante.
- Nunca puede tapar el foco principal.
- Puede tapar al oyente o secundario si luego se permite respiracion previa.

### Shout

- Reglas propias.
- Cerca del hablante o foco, pero sin taparlos.
- No debe derivar en bocadillos minusculos ni gigantes.

## Reglas de layout

Orden de escape:

1. crecer el bocadillo
2. recolocar la caja
3. repaginar
4. reducir tipografia

Principios:

- Prioridad a legibilidad.
- Evitar mucho blanco inutil.
- Proteger `padding` interno antes que margen exterior.
- Margen exterior al frame pequeno, pero seguro.

Limites iniciales para `1080x1920`:

- Narracion:
  - ancho `620-920`
  - alto `180-360`
  - tipografia `30-44`
  - max `3` lineas
- Dialogo:
  - ancho `420-760`
  - alto `140-300`
  - tipografia `28-42`
  - max `3` lineas
- Shout:
  - ancho `360-820`
  - alto `130-320`
  - tipografia `32-48`
  - max `3` lineas
- Margen exterior minimo al frame: `24 px`

## Reglas de respiracion

- Solo aplica a `dialogue`.
- Solo si el bocadillo tapa un secundario relevante o su cara.
- Nunca compensa un mal layout sobre hablante o foco.
- Siempre va antes de hablar.
- Sale del tiempo de la escena, pero la escena puede crecer si hace falta.

Valores iniciales:

- leve: `0.4s`
- media: `0.6s`
- fuerte: `0.9s`

## Reglas de tiempo

Duracion minima visible por evento:

- `narration = palabras / 2.6`
- `dialogue = palabras / 2.3`
- `shout = palabras / 2.0`

Pausas:

- `, ; :` => `+0.12s`
- `. ? !` => `+0.22s`
- `...` => `+0.30s`

Suelos minimos:

- narration `>= 1.4s`
- dialogue `>= 1.2s`
- shout `>= 0.9s`

Regla final:

`duracion_evento = max(duracion_legible, duracion_audio_asociada)`

## Scripts

### Mantener y refactorizar

- `/mnt/c/Users/ferna/Desktop/Proyectos/adelfinio/scripts/generate_scene_assets.py`
- `/mnt/c/Users/ferna/Desktop/Proyectos/adelfinio/scripts/video_pipeline_v2.py`
- `/mnt/c/Users/ferna/Desktop/Proyectos/adelfinio/scripts/run_final_ai_video_pipeline_v2.sh`
- `/mnt/c/Users/ferna/Desktop/Proyectos/adelfinio/scripts/video_render_helpers.py`
- `/mnt/c/Users/ferna/Desktop/Proyectos/adelfinio/scripts/video_text_layout.py`
- `/mnt/c/Users/ferna/Desktop/Proyectos/adelfinio/scripts/layout_analysis.py`

### Nuevos scripts propuestos

- `scripts/build_scene_events.py`
- `scripts/build_scene_utterances.py`
- `scripts/synthesize_scene_audio.py`
- `scripts/align_scene_audio.py`
- `scripts/build_scene_audio_plan.py`
- `scripts/render_clean_scene_video.py`
- `scripts/build_overlay_timeline.py`
- `scripts/build_camera_plan.py`
- `scripts/compose_scene_video.py`
- `scripts/assemble_episode_video.py`

## Backlog de implementacion

### Fase 1. Contratos y salida explicita

Objetivo: sacar `events.json` y `utterances.json`.

Tareas:

- Definir funciones y schemas internos para `events` y `utterances`.
- Extraer de `generate_scene_assets.py` la paginacion visible como `events`.
- Crear `build_scene_events.py`.
- Crear `build_scene_utterances.py`.

Salida:

- `scene_XX.events.json`
- `scene_XX.utterances.json`

Aceptacion:

- Una escena piloto genera ambos archivos sin tocar aun el render final.

### Fase 2. Audio por utterance

Objetivo: reemplazar el audio monolitico de escena por clips por locucion.

Tareas:

- Crear TTS por `utterance`.
- Persistir `utt_YYY.mp3`.
- Crear `align_scene_audio.py`.
- Generar `scene_XX.alignment.json`.
- Generar `scene_XX.audio_plan.json`.

Salida:

- audio por `utterance`
- alineacion palabra-audio
- plan temporal final de escena

Aceptacion:

- Cada pagina queda sincronizada con marcas reales, no heuristicas.

### Fase 3. Render clean por escena

Objetivo: render continuo sin overlays ni zoom final.

Tareas:

- Crear `render_clean_scene_video.py`.
- Montar un clip por escena usando una sola imagen y la duracion final de escena.
- Mantener color/base look consistente con el pipeline actual.
- Eliminar cualquier fade interno por pagina.

Salida:

- `scene_XX.clean.mp4`

Aceptacion:

- Una escena de varias paginas sigue siendo un solo clip continuo.

### Fase 4. Overlay timeline

Objetivo: resolver cajas, transiciones y respiracion.

Tareas:

- Crear `build_overlay_timeline.py`.
- Reutilizar `layout_analysis.py` y `video_text_layout.py`.
- Implementar reglas de narracion/dialogo/shout.
- Implementar `micro-crossfade` solo del overlay.
- Implementar `pre_roll_clean_s` solo para dialogo cuando proceda.

Salida:

- `scene_XX.overlay_timeline.json`

Aceptacion:

- La escena base no cambia; solo cambia el overlay.

### Fase 5. Camera plan

Objetivo: recorrido adaptable sobre frame compuesto.

Tareas:

- Crear `build_camera_plan.py`.
- Generar keyframes en cambios de texto visible.
- Aplicar zoom monotono.
- Validar que el overlay activo cabe completo en frame.

Salida:

- `scene_XX.camera_plan.json`

Aceptacion:

- Ningun keyframe corta bocadillos.

### Fase 6. Composicion de escena y ensamblado final

Objetivo: componer overlays, aplicar camara y ensamblar episodio.

Tareas:

- Crear `compose_scene_video.py`.
- Crear `assemble_episode_video.py`.
- Migrar el runner final para usar el pipeline nuevo.
- Regenerar SRT a partir de tiempos reales.

Salida:

- `scene_XX.composited.mp4`
- `artifacts/videos/final/<episode_id>.mp4`
- `artifacts/subtitles/final/<episode_id>.srt`

Aceptacion:

- El episodio completo sale sin fades internos de escena.

## Orden recomendado de implementacion

1. Especificar y persistir `events`.
2. Especificar y persistir `utterances`.
3. TTS por utterance.
4. Alineacion palabra-audio.
5. Audio plan con duracion real de escena.
6. Render clean por escena.
7. Overlay timeline.
8. Camera plan.
9. Compose scene.
10. Assemble episode.

## Tareas concretas por archivo

### `/scripts/generate_scene_assets.py`

- Mantener generacion de imagen limpia.
- Mantener layout y foco candidatos.
- Dejar de ser responsable del timeline final de video.

### `/scripts/video_pipeline_v2.py`

- Mantener la orquestacion V2 por escenas.
- Coordinar artefactos intermedios y validaciones.
- Mantenerse desacoplado del render heredado eliminado.

### `/docs/local_video_pipeline.md`

- Actualizar guia operativa cuando el pipeline V2 ya funcione.

### `/README.md`

- Mantener el resumen operativo alineado con V2.

## Estrategia de migracion

- Implementar V2 con scripts y artefactos desacoplados por escena.
- Validar una escena piloto end-to-end.
- Validar un episodio completo antes de dar por cerrado el flujo.
- Mantener los artefactos intermedios para depuracion y revisiones visuales.

## Escena piloto

Recomendacion:

- usar una escena con:
  - narracion paginada
  - al menos un dialogo
  - un caso donde el bocadillo tape un secundario
  - al menos un cambio de caja visible

Objetivo:

- validar todo el flujo antes de atacar el episodio entero.

## Riesgos

- Desalineacion entre pagina y locucion.
- Layout demasiado acoplado al pipeline actual.
- Reglas de respiracion mal calibradas.
- Camera plan que degrade composicion al proteger overlays.

Mitigaciones:

- usar `alignment.json` real
- exportar artefactos debug por escena
- validar visualmente una escena piloto antes del ensamblado final

## Criterios de aceptacion global

- Una escena de varias paginas sigue siendo un clip continuo.
- No hay fade de escena por cambio de pagina.
- El overlay cambia con `micro-crossfade`.
- Audio nunca se acelera.
- Si una escena necesita mas tiempo, crece.
- El zoom nunca se aleja.
- Ningun bocadillo tapa hablante o foco principal.
- Dialogo solo usa respiracion si tapa un secundario relevante.
- Todos los tiempos de pagina se basan en alineacion real palabra-audio.
