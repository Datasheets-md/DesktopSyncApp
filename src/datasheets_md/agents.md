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
   - `settings/mapping.yaml`, `settings/symbol_style.yaml` -- the workspace's
     settings (see below; owner or admin only).
4. `dsmd diff` -- and show the diff to the user before pushing.
5. `dsmd push -m "<what changed and why>"`.
6. Exit code 2 means some parts were not applied: read the per-part lines
   (`stale` -> run `dsmd pull`, then push again; `rejected` -> fix the named
   `file:line` and push again).

## Rules

- Keep `k` (parameters.jsonl) and `id` (pins.csv) unchanged on rows you edit;
  they are how the server knows which row you mean. A new row has no `k`; a
  new pin has an empty `id`.
- `datasheet.md` is the digitised PDF. Its `{{para:<id> ...}}`,
  `{{table:<id> ...}}`, `{{row:<id> ...}}` and `{{figure:<id> ...}}` markers
  carry the element ids that a row's `source.element_id` cites; figures are
  PNGs in `images/` next to it. `dsmd pdf <part>` downloads the original PDF.
- When you correct a value, set `source` to the element you read it from:
  `"source": {"element_id": "<id>"}`.
- To confirm a row is right, change its `review` from `unchecked` to
  `confirmed`. Unconfirming is not supported.
- Never edit `library/` (generated), `part.yaml` or `datasheet.md`
  (read-only); `push` refuses them. `dsmd restore <path>` undoes a local edit.
- Conflict markers (`<<<<<<< local` / `=======` / `>>>>>>> server`) after a
  pull surround one row (or, in a YAML file, the whole file): keep the right
  side, delete the markers, push.

## Workspace settings

Both files are YAML, pushed with the parts (settings first, so a category you
add is there for the parts in the same push). The server validates them like
the web settings pages and answers `rejected` with the reason.

`settings/mapping.yaml` -- how the workspace names and groups things (it
drives the web app, comparisons, the API, the MCP server and the CAD exports
listed in `used_by`):
- `custom_categories`: your category names; `category_parents`:
  `{child: parent}` to nest them.
- `category_map`: `{catalogue category id: your category}`. `dsmd categories`
  lists the catalogue's ids and names.
- `parameter_map`: `{your category: {parameter: {name, unit, export, ...}}}`
  -- rename a parameter, set its unit, choose whether it is exported; a
  column you invent has `custom: true` and may carry a fixed `value`.
- `custom_parameters`: `{name: {unit, units?, kind?, shortcut?}}`;
  `custom_units`: `[{symbol, name?}]`.
- `cad_fields`: `{kicad: [...]}` -- the properties each KiCad symbol gets.
- `used_by`: which outputs use this mapping (`web_app`, `comparisons`,
  `kicad_export`, `concept_hdl_export`, `altium_export`, `rest_api`,
  `mcp_server`).

`settings/symbol_style.yaml` -- how symbols are generated:
- `style`: the workspace's overrides of `reference.system_style` (only the
  keys you want different; unknown keys are rejected).
- `categories`: `{category: style overrides}` for one category; an empty
  value removes the override. Category names are your mapping's categories
  or catalogue category names.
- `designator_options`: the reference designators the workspace uses,
  `[{id, say, value?}]` (`value` = the parameter shown as the symbol's
  Value), or null for `reference.system_designator_options`.
- `reference_designators`: `{category: designator}`.
- `version` and `reference` are for reading only.

## When something is missing

If a setting, parameter, category or command you need does not exist, or the
server refuses something that should work, tell the datasheets.md team:

    dsmd feedback --kind suggestion "what you tried, what you needed"

Use `--kind bug` for something broken. Then tell the user you sent it.
