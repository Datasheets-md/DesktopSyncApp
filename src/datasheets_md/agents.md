# dsmd for agents

`dsmd` keeps a folder in sync with a datasheets.md workspace. Use it from a
shell; add `--json` to any command for machine-readable output.

## Loop

1. `dsmd pull` -- bring the working copy up to date.
2. Find what to change with grep, e.g. `grep -rn '"VOH"' parts/`.
3. Edit only these files:
   - `parts/<MFR>/<PN>/parameters.jsonl` -- one parameter row per line.
   - `parts/<MFR>/<PN>/pins.csv` -- one physical pin per line.
   - `parts/<MFR>/<PN>/cad.yaml` -- the part's CAD configuration.
4. `dsmd diff` -- and show the diff to the user before pushing.
5. `dsmd push -m "<what changed and why>"`.
6. Exit code 2 means some parts were not applied: read the per-part lines
   (`stale` -> run `dsmd pull`, then push again; `rejected` -> fix the named
   `file:line` and push again).

## Rules

- Keep `k` (parameters.jsonl) and `id` (pins.csv) unchanged on rows you edit;
  they are how the server knows which row you mean. A new row has no `k`; a
  new pin has an empty `id`.
- When you correct a value from the PDF, set `source` to the element you read
  it from: `"source": {"element_id": "<id>"}`. `dsmd pdf <part>` downloads
  the PDF; `datasheet.md` is its digitised text.
- To confirm a row is right, change its `review` from `unchecked` to
  `confirmed`. Unconfirming is not supported.
- Never edit `library/` (generated), `part.yaml`, `datasheet.md` or
  `settings/` (read-only); `push` refuses them. `dsmd restore <path>` undoes
  a local edit.
- Conflict markers (`<<<<<<< local` / `=======` / `>>>>>>> server`) after a
  pull surround one row: keep the right line, delete the markers, push.
