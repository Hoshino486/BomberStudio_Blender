# Blender Source Tools 3.4.3 — BomberStudio integration

Source: the user's local `io_scene_valvesource` directory, copied on 2026-10-03.
Original author: Tom Edwards. Original SHA256 hashes: `PROVENANCE.json`.
The user's original directory is not modified.

Upstream modules are GPL-2.0-or-later; this combined distribution exercises
the GPL-3.0-or-later option. The full GPLv3 is included as `LICENSE.txt`.
`datamodel.py` is MIT (full notice retained in that file). `ordered_set.py`
is MIT; see `LICENSE-ordered-set.txt`. All original file notices are retained.

Changes on 2026-10-03:
- Scoped, transactional registration; no disabling of another installed add-on.
- Idempotent handler removal; pointers removed before unregistering RNA classes.
- Nested updater delegates to the parent package's explicit check operation.
- Import restores user edit preferences on failure and handles headless contexts.
- Empty/failed imports and exports return CANCELLED instead of success.

Provider code requires Blender 4.1+. The parent integration module remains
Python 3.5-compatible and never imports this directory on older versions.
Already active standalone Source Tools is reused and not unregistered by us.

- Reference baking pins Basis before evaluating modifiers; active nonzero shape values cannot pollute the reference mesh (verified on Blender 5.2).

- The nine dynamic vertex-map operators are collected, not registered at import time; they participate in transactional register/unregister.
