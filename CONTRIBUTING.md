# Contributing

## Flujo Git

1. Crear branch desde `develop`:

```bash
git checkout develop
git pull origin develop
git checkout -b feature/nombre-corto
```

2. Commits pequenos, atomicos y trazables.
3. Abrir PR hacia `develop`.
4. Merge a `main` solo desde `develop` en releases planificadas.

## Convenciones de commit

Formato recomendado (Conventional Commits):

- `feat: ...`
- `fix: ...`
- `docs: ...`
- `chore: ...`
- `refactor: ...`
- `test: ...`

Ejemplos:

- `feat(workflows): add teaser generation skeleton`
- `fix(schema): enforce plot twist on main episodes`

## Calidad minima antes de PR

1. `python3 scripts/validate_json.py`
2. Revisar que no haya secretos en texto plano.
3. Actualizar documentacion si cambia arquitectura, proceso o contratos JSON.

## Ramas protegidas (recomendado en remoto)

- Proteger `main` y `develop`.
- Requerir al menos 1 aprobacion humana para merge.
