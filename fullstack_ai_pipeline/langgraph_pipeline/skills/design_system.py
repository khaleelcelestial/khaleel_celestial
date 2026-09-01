"""
Deterministic frontend design system - written by the PIPELINE itself
(never LLM-generated), injected identically into every React+Vite project.

Why this exists: the Frontend Agent's prompt used to explicitly tell the
model to "pick a deliberate font pairing for THIS project, not a default" and
invent its own colors/spacing per page - a direct, structural cause of every
consistency complaint (inconsistent colors/spacing/radius/shadows, sidebar
overlapping content, cards with mismatched heights). Asking an LLM to
reinvent a design system from scratch on every single generation call,
across every round of every project, will never converge on consistency -
there's no persisted source of truth for it to stay faithful to.

This file is the fix: one canonical set of CSS custom properties and base
component/layout classes, written unconditionally by FrontendCapability.run()
after every generation round (see capabilities/frontend.py), so it's
identical every time regardless of what the model did that round. The
prompt (FRONTEND_SYSTEM_PROMPT) requires using these variables/classes
instead of inventing new ones - the model still has real creative room (an
accent color, per-page content/layout decisions), just not over the
foundational tokens that consistency actually depends on.
"""

DESIGN_SYSTEM_CSS = """/* ============================================================================
   DESIGN SYSTEM - written by the pipeline, not the Frontend Agent.
   Do not redefine these variables/classes elsewhere - override the two
   accent variables in your own index.css if a project-specific brand color
   is warranted, everything else should stay as-is for consistency.
   ============================================================================ */

:root {
  /* ---- Color: primary/secondary are the ONLY tokens meant to be
     overridden per project (e.g. index.css re-declaring --color-primary for
     a domain-appropriate hue) - every other token stays fixed. ---- */
  --color-primary: #2563eb;
  --color-primary-hover: #1d4ed8;
  --color-primary-light: #dbeafe;
  --color-secondary: #64748b;
  --color-success: #16a34a;
  --color-success-light: #dcfce7;
  --color-warning: #d97706;
  --color-warning-light: #fef3c7;
  --color-danger: #dc2626;
  --color-danger-light: #fee2e2;

  /* Neutral scale - text, borders, backgrounds. Named by role, not just
     shade number, so usage stays obvious without cross-referencing a key. */
  --color-text: #0f172a;
  --color-text-muted: #64748b;
  --color-text-inverse: #f8fafc;
  --color-border: #e2e8f0;
  --color-bg: #f8fafc;
  --color-surface: #ffffff;
  --color-surface-hover: #f1f5f9;

  /* Typography - a real, deliberate scale (1.25 ratio), one font family
     used consistently rather than a fresh pairing invented per project. */
  --font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  --text-xs: 0.75rem;
  --text-sm: 0.875rem;
  --text-base: 1rem;
  --text-lg: 1.25rem;
  --text-xl: 1.5rem;
  --text-2xl: 1.875rem;
  --text-3xl: 2.25rem;
  --font-weight-normal: 400;
  --font-weight-medium: 500;
  --font-weight-semibold: 600;
  --font-weight-bold: 700;
  --line-height-tight: 1.25;
  --line-height-normal: 1.5;

  /* Spacing scale - an 8px base unit. Every margin/padding/gap in generated
     code should be one of these, not an arbitrary value like "13px". */
  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 0.75rem;
  --space-4: 1rem;
  --space-5: 1.5rem;
  --space-6: 2rem;
  --space-8: 3rem;

  /* Radius/shadow - fixed so cards/buttons/inputs never drift between
     pages generated in different rounds. */
  --radius-sm: 0.25rem;
  --radius-md: 0.5rem;
  --radius-lg: 0.75rem;
  --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
  --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
  --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1);

  /* Layout - fixed sidebar/header sizing so every project's shell behaves
     identically, and the two breakpoints components should design against. */
  --sidebar-width: 260px;
  --header-height: 64px;
  --breakpoint-tablet: 768px;
  --breakpoint-desktop: 1024px;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: var(--font-family);
  font-size: var(--text-base);
  line-height: var(--line-height-normal);
  color: var(--color-text);
  background: var(--color-bg);
}

h1, h2, h3, h4, h5, h6 {
  margin: 0 0 var(--space-4) 0;
  font-weight: var(--font-weight-semibold);
  line-height: var(--line-height-tight);
}
h1 { font-size: var(--text-3xl); }
h2 { font-size: var(--text-2xl); }
h3 { font-size: var(--text-xl); }
h4 { font-size: var(--text-lg); }
p { margin: 0 0 var(--space-4) 0; }

/* ============================================================================
   LAYOUT SHELL - CSS Grid, not position:absolute. This is what prevents
   "sidebar overlapping content": Grid reserves the sidebar's track
   explicitly, so main content can never render underneath it, at any
   viewport size, without a media query re-templating the whole grid.
   ============================================================================ */

.app-shell {
  display: grid;
  grid-template-columns: var(--sidebar-width) 1fr;
  grid-template-rows: var(--header-height) 1fr;
  grid-template-areas:
    "sidebar header"
    "sidebar main";
  min-height: 100vh;
}

.app-shell__sidebar { grid-area: sidebar; overflow-y: auto; }
.app-shell__header { grid-area: header; }
.app-shell__main {
  grid-area: main;
  padding: var(--space-6);
  overflow-y: auto;
  min-width: 0; /* prevents a wide table/card from blowing out the grid track */
}

@media (max-width: 768px) {
  /* Mobile: sidebar becomes an overlay (off-canvas), not a permanent
     column - toggled via a class the layout component adds/removes, never
     via position:absolute on desktop sizes. */
  .app-shell {
    grid-template-columns: 1fr;
    grid-template-areas:
      "header"
      "main";
  }
  .app-shell__sidebar {
    position: fixed;
    top: 0;
    left: 0;
    bottom: 0;
    width: var(--sidebar-width);
    z-index: 40;
    transform: translateX(-100%);
    transition: transform 0.2s ease;
  }
  .app-shell__sidebar.is-open { transform: translateX(0); }
  .app-shell__main { padding: var(--space-4); }
}

/* ============================================================================
   COMPONENTS - use these classes instead of inventing new ones per page.
   ============================================================================ */

.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  font-weight: var(--font-weight-medium);
  cursor: pointer;
  transition: background-color 0.15s ease, border-color 0.15s ease;
}
.btn:disabled { opacity: 0.6; cursor: not-allowed; }
.btn-primary { background: var(--color-primary); color: var(--color-text-inverse); }
.btn-primary:hover:not(:disabled) { background: var(--color-primary-hover); }
.btn-secondary { background: var(--color-surface); color: var(--color-text); border-color: var(--color-border); }
.btn-secondary:hover:not(:disabled) { background: var(--color-surface-hover); }
.btn-danger { background: var(--color-danger); color: var(--color-text-inverse); }

.card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  padding: var(--space-5);
}

/* A row of equal-height stat/summary cards - the fix for "cards with
   inconsistent heights": grid auto-rows stretches every card in the row to
   match the tallest one, rather than each card sizing to its own content. */
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--space-5);
  align-items: stretch;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-6);
  flex-wrap: wrap;
}
.page-title { font-size: var(--text-2xl); font-weight: var(--font-weight-bold); margin: 0; }

.badge {
  display: inline-flex;
  align-items: center;
  padding: var(--space-1) var(--space-3);
  border-radius: 9999px;
  font-size: var(--text-xs);
  font-weight: var(--font-weight-medium);
}
.badge-success { background: var(--color-success-light); color: var(--color-success); }
.badge-warning { background: var(--color-warning-light); color: var(--color-warning); }
.badge-danger { background: var(--color-danger-light); color: var(--color-danger); }
.badge-neutral { background: var(--color-border); color: var(--color-text-muted); }

.form-group { margin-bottom: var(--space-4); }
.form-label {
  display: block;
  margin-bottom: var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--font-weight-medium);
  color: var(--color-text);
}
.form-input, .form-select, .form-textarea {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  font-family: inherit;
  background: var(--color-surface);
  color: var(--color-text);
}
.form-input:focus, .form-select:focus, .form-textarea:focus {
  outline: none;
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-light);
}
.form-error { color: var(--color-danger); font-size: var(--text-xs); margin-top: var(--space-1); }

/* Table wrapper enables horizontal scroll on narrow viewports instead of
   the table breaking the page layout - the fix for "tables lacking
   professional styling"/responsive overflow. */
.table-wrapper { overflow-x: auto; border: 1px solid var(--color-border); border-radius: var(--radius-lg); }
.table { width: 100%; border-collapse: collapse; font-size: var(--text-sm); }
.table th, .table td { padding: var(--space-3) var(--space-4); text-align: left; }
.table th {
  background: var(--color-bg);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-muted);
  border-bottom: 1px solid var(--color-border);
  white-space: nowrap;
}
.table td { border-bottom: 1px solid var(--color-border); }
.table tbody tr:last-child td { border-bottom: none; }
.table tbody tr:hover { background: var(--color-surface-hover); }

.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgb(15 23 42 / 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
  padding: var(--space-4);
}
.modal {
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  max-width: 480px;
  width: 100%;
  max-height: 90vh;
  overflow-y: auto;
  padding: var(--space-6);
}

/* Chart container - the fix for "empty or poorly sized charts": a chart
   library sizing to 0x0 (its default with no explicit dimensions) is a
   real, common failure mode. This class gives it a real, responsive box to
   fill instead. */
.chart-container { position: relative; width: 100%; height: 320px; }

.loading-container {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
}
.spinner {
  width: 40px;
  height: 40px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
"""
