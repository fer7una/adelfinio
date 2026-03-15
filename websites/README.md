# Websites README

Este directorio se usa como punto de referencia para despliegue web y documentacion de GitHub Pages.

## Estado actual

- El sitio legal esta en `website/`.
- Paginas clave:
  - `website/index.html`
  - `website/privacy-policy.html`
  - `website/terms-of-service.html`

## Publicar en GitHub Pages

1. Ir a GitHub -> Settings -> Pages.
2. En "Build and deployment", elegir `Source: GitHub Actions`.
3. Verificar que el workflow `.github/workflows/pages.yml` este en `main`.
4. Ejecutar el workflow manualmente (o hacer push de cambios en `website/` sobre `main`).
5. Esperar a que finalice el job y usar la URL publicada.

## URLs para TikTok Developers

Usa la URL base publicada por GitHub Pages.

- Website URL: `<BASE_URL>/`
- Privacy Policy URL: `<BASE_URL>/privacy-policy.html`
- Terms of Service URL: `<BASE_URL>/terms-of-service.html`

Ejemplo (`<BASE_URL>`):

`https://<usuario>.github.io/<repo>`

## TODO

- Reemplazar datos legales placeholder (email y titular) antes de envio a revision.
