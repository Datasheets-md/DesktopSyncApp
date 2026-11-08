# dsmd for agents

`dsmd` keeps a folder in sync with a datasheets.md workspace. Use it from a
shell; add `--json` to any command for machine-readable output.

## Loop

1. `dsmd pull` -- bring the working copy up to date.
2. Find what to change with grep, e.g. `grep -rn '"VOH"' parts/`.
3. Edit only these files:
   - `parts/<family>/<PN>/parameters.jsonl` -- one parameter row per line.
   - `parts/<family>/<PN>/pins.csv` -- one physical pin per line; `source` is the
     element id in `datasheet.md` the pin was read from.
   - `parts/<family>/<PN>/cad.yaml` -- the part's CAD configuration.

   `parts/<family>/` is one folder per family (`<family name>_<manufacturer>`),
   holding its parts' folders and the family's `datasheet.md`, `images/` and,
   with `pdf = true` in `dsmd.toml`, its original PDF.
   - `settings/mappings/<id>-<name>.yaml`, `settings/symbol_style.yaml` -- the
     workspace's settings (see below; owner or admin only).
4. `dsmd diff` -- and show the diff to the user before pushing.
5. `dsmd push -m "<what changed and why>"`.
6. Exit code 2 from `clone` or `pull` with a `library/ was not built` warning means the parts and
   datasheets are in place; run `dsmd pull --library` to retry. A clone that stopped continues when you
   run the same command again. Exit code 2 from `push` means some parts were not applied: read the per-part lines
   (`stale` -> run `dsmd pull`, then push again; `rejected` -> fix the named
   `file:line` and push again).

## Rules

- Keep `k` (parameters.jsonl) and `id` (pins.csv) unchanged on rows you edit;
  they are how the server knows which row you mean. A new row has no `k`; a
  new pin has an empty `id`.
- A part's digitised PDF is the `datasheet.md` in its family folder, one
  level above the part's own folder (`datasheet:` in `part.yaml` points at it;
  null until the PDF is digitised), shared by every part of the family. Its
  `{{para:<id> ...}}`, `{{table:<id> ...}}`, `{{row:<id> ...}}` and
  `{{figure:<id> ...}}` markers carry the element ids that a row's
  `source.element_id` cites; figures are PNGs in `images/` next to it.
  `dsmd pdf <part>` prints the path of the original PDF (the family folder's
  copy with `pdf = true`, else a download into `.dsmd/cache/`).
- When you correct a value, set `source` to the element you read it from:
  `"source": {"element_id": "<id>"}` in parameters.jsonl, the bare id in the
  `source` column of pins.csv.
- To confirm a row is right, run `dsmd confirm <part> <k>` (or change its
  `review` from `unchecked` to `confirmed`). Unconfirming a parameter row is
  not supported. Every confirmation carries your name and the time.
- Never edit `library/` (generated), `part.yaml` or `datasheet.md`
  (read-only); `push` refuses them. `dsmd restore <path>` undoes a local edit.
- Conflict markers (`<<<<<<< local` / `=======` / `>>>>>>> server`) after a
  pull surround one row (or, in a YAML file, the whole file): keep the right
  side, delete the markers, push.

## Reviewing a part's CAD

The last lines of `parameters.jsonl` (section `cad_review`, `k` = `cad:<item>`)
are the part's CAD review checklist, the same one the web page shows at the
top of the CAD data tab:

| item | what you check | against |
|---|---|---|
| `package_type` | the part number comes in this package | the ordering / package table in `datasheet.md` and the `Package / Case` row |
| `package_dimensions` | the body and lead dimensions the footprint is built from | the package drawing |
| `pin_table` | every pin's number, name and electrical type | the pin table; `pins.csv` |
| `pin_map` | which pins are on the symbol and which pad each pin is | the pinout figure; `pins.csv` (`on_symbol`), `cad.yaml` (`pad_map`, `pin_map`) |
| `footprint` | the land pattern | the recommended land pattern; `cad.yaml` `footprint` |
| `symbol` | the symbol you place | `cad.yaml` `symbol` |

Only `review` of these lines is yours to change; their `name`, `value` and
`version` say what the item is now. `sources` lists where to check it, most
direct first, each `{element_id, kind, label}` (`kind`: `pin_table`,
`pinout` figure, `package_drawing`, `land_pattern`, `ordering` table,
`package` information, `row`); `source` repeats the first. Find an element
with `grep -n '<element_id>' parts/<family>/datasheet.md`; a figure is also
`images/<element_id>.png` next to it. An empty `sources` means the server
knows no pointer for that item: look it up yourself. A part without CAD or a package drawing
lists fewer items. To review:

1. `dsmd pull`, then `dsmd confirm <part>` lists the items and their state.
2. Compare each item with the datasheet at the elements its `sources` name
   (`datasheet.md`, its figures in `images/`, or `dsmd pdf <part>`).
3. If something is wrong, correct it first, in the file that holds it:
   `pins.csv` for pins, `cad.yaml` for symbol and footprint, the row in
   `parameters.jsonl` for the package type (and `source.element_id` to say
   where you read it). Package dimensions are not in the files yet: when they
   are wrong, leave `package_dimensions` unchecked and tell the user.
4. `dsmd confirm <part> pin_table pin_map ...` for each item you checked and
   found right. Confirming `pin_map` also confirms `pin_table`. Never confirm
   an item you did not compare. `--undo` sets an item back before the push,
   and after it takes back your own confirmation.
5. `dsmd diff`, show it to the user, then `dsmd push -m "CAD review: ..."`.
   Corrections are applied first, so what you confirm is the corrected data.
   The confirmations are recorded under your name with the time; the web
   page shows "Reviewed by <name>" once you have confirmed every item.

A later change to an item's data (a pin renamed, the footprint swapped, a
dimension corrected) reopens that item: after a pull its line says
`unchecked` again with a new `version`. `package_dimensions` is refused while
the drawing misses a dimension the package needs; the push says so.

## Workspace settings

Both files are YAML, pushed with the parts (settings first, so a category you
add is there for the parts in the same push). The server validates them like
the web settings pages and answers `rejected` with the reason.

`settings/mappings/<id>-<name>.yaml` -- one file per mapping, for how the
workspace names and groups things. A workspace can have several, each driving
different outputs, so edit the one that drives the output you are changing:
- `active_for` (read-only): the outputs that really read this mapping. To see
  which file drives what, including the KiCad library (`kicad_export`), run
  `grep -A7 '^active_for' settings/mappings/*`. Check it before editing.
- `used_by`: the outputs this mapping asks to drive (`web_app`, `comparisons`,
  `kicad_export`, `concept_hdl_export`, `altium_export`, `rest_api`,
  `mcp_server`). When several active mappings ask for one output, only one
  gets it: the workspace default, otherwise the most recently saved. So saving
  any mapping that shares an output can move that output to it. Read
  `active_for` after a push.
- `name`, `description`, `status` (`active`, or `draft`, which drives nothing).
  Renaming changes the file's name.
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

To create a mapping, add a YAML file with a `name` to `settings/mappings/`, under
any file name. Copying an existing file is the easy start. It is a `draft`
unless the file sets `status`. After the push the server renames the file to
`<id>-<name>.yaml`.

To delete a mapping, delete its file and push; `dsmd restore <path>` brings it
back before then. The workspace default cannot be deleted. Whatever a deleted
mapping drove moves to the next mapping that lists it in `used_by`, or back to
the catalogue's names, so check its `active_for` first and tell the user.

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
