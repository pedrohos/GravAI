# Frontend styles and reusable UI

`app/globals.css` is the import entry point. Keep theme tokens, resets, typography,
and text utilities in `foundation.css`. Feature and component rules belong in
the focused stylesheets in this directory. Keep the import order stable because
feature styles refine shared primitives.

Use the components in `components/ui` instead of reproducing their markup:

- `Button`: native button props and refs, `variant` and `size`; defaults to
  `type="button"`. Use `type="submit"` explicitly for form submission.
- `FormField`, `Input`, `Select`, `Checkbox`: labels, controls, disabled states,
  hints, and validation errors. A field supplies its ID and accessibility
  associations to controls through context. IDs also name the hint and error.
- `Panel`, `SectionHeader`, `PageHeader`: consistent page and section structure.
- `EmptyState`, `ErrorMessage`: loading, empty, and error feedback.
- `Drawer`: shared summary/body structure, controlled expansion, muted and
  unavailable states.
- `InfoTooltip`: the shared information trigger and tooltip.
- `TableScroll`: keyboard-accessible horizontal table scrolling.

`components/DataTable.tsx` owns column widths, column resizing, sortable headers,
and table markup. Pages provide their rows, columns, and sorting state.
`components/Filters.tsx` owns filter fields and removable filter chips.

Put shared appearance in `layout.css`, `buttons.css`, `forms.css`, `tables.css`,
`badges.css`, `feedback.css`, `drawers.css`, `filters.css`, or `icons.css`. Put
feature-specific appearance in `jobs.css`, `recordings.css`, `settings.css`, or
`transcripts.css`. Keep related responsive rules with their owning stylesheet.
Reuse or extend the corresponding component before adding page-specific copies.
