# steward (python)

Python port of steward. See `../design.md` for the spec and `../CHANGELOG.md` for slice history.

## Dev setup

```
cd python
uv sync --extra dev
uv run pytest
```

Slices are ported in the same order as the TS CHANGELOG. Parity target: the same behaviours, the same tests, the same journal format.
