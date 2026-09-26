# Shared shell smoke review — hv-0gi.1

Reviewed the assembled Vite preview against the existing read-only dashboard on
2026-09-25 local date. `source.json` pins the source file hashes and base commit;
the native bead records the final source and tested integration commits.

The natural viewport was 1280 × 720 before overrides. Final geometry repeats that
size and covers 1600 × 1000, 1000 × 1100, 390 × 844 and both sides of each existing
1300, 1100, 900, 768 and 639 px breakpoint. All 14 measurements have zero page
horizontal overflow. The rail is 216 px at desktop widths; main content starts
40 px inside it (24 px at the intermediate breakpoint). At 390 px the rail becomes
a 161.8 px top region with all project choices visible. Tall, wide and narrow
screenshots retain the inspected final layouts.

The text plan's 216 px rail and 40 px inset override generated mockup dimensions:
at 1600 px the rail boundary and title start normalize to 0.135 and 0.160, versus
about 0.1545 and 0.1828 in the 1586 px reference. The existing card feed, summary,
and detail evidence remain for the following children. No full redesign match is
claimed by this shell increment.

Repaired findings: a narrow single-column grid overflowed because its track used
an intrinsic minimum; role amounts overlapped at 1000 px; narrow project choices
were hidden; small native checkbox bounds inherited field height. The final
geometry and screenshots include the fixes. Cold review additionally caught
history entries with identical queries sharing the largest cached page extent;
cache ownership now includes the history entry and a regression covers it.

`controls.json` records normal-state DOM outer/content rectangles, center offsets,
four clearances and computed border/outline/shadow values for encountered control
families at 1600 px. DOM Range content bounds are typographic boxes, **not tight
visible glyph bounds**, and native field contents are not measurable this way.
An outline whose style is `none` contributes zero visible thickness regardless of
its computed width. Left-aligned navigation deliberately has asymmetric horizontal
clearance. The detail Back link has 44 px height; the keyboard-focused Session link
was observed with a 2 px solid violet outline. Narrow buttons, selects, project
links, checks and disclosure summaries have 44 px minimum target heights.

The browser interface did not expose held-pointer or hover primitives, so pressed
and hover states were inspected in CSS, not claimed as rendered observations.
The complete glyph/contrast/border-stack and state matrix, zoom stress, integrated
cross-screen journeys and all final visual acceptance remain explicitly required
by hv-0gi.5. This is a scoped shell smoke review, not a satisfied verdict for the
whole redesign. Component journeys verify safe direct bead/session/ledger routes,
reload, independently scoped history restoration, canonical project selection and
Disclosure keyboard/focus behavior. Native certification and promoted hot reload
are recorded on the bead after delivery.
