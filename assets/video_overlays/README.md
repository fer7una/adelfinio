Coloca aqui los SVG que usa el montaje final del video:

- `assets/video_overlays/narration.svg`
- `assets/video_overlays/dialogue.svg`
- `assets/video_overlays/shout.svg`

Reglas practicas para que encajen con el compositor actual:

- `narration.svg`: el compositor lo escala a 760 px de ancho preservando proporcion
- `dialogue.svg`: el compositor lo escala a 660 px de ancho preservando proporcion
- `shout.svg`: el compositor lo escala a 660 px de ancho preservando proporcion
- Fondo transparente
- El texto no debe ir incrustado dentro del SVG; el pipeline lo dibuja encima
- Deja una zona segura interior amplia; el compositor desplaza el texto segun el tamano final escalado del SVG

Si faltan estos archivos, el pipeline cae al estilo legacy dibujado con ffmpeg.
