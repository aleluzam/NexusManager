---
name: Unified Command System
colors:
  surface: '#f9f9ff'
  surface-dim: '#cfdaf2'
  surface-bright: '#f9f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f0f3ff'
  surface-container: '#e7eeff'
  surface-container-high: '#dee8ff'
  surface-container-highest: '#d8e3fb'
  on-surface: '#111c2d'
  on-surface-variant: '#4c4450'
  inverse-surface: '#263143'
  inverse-on-surface: '#ecf1ff'
  outline: '#7e7481'
  outline-variant: '#cfc2d1'
  surface-tint: '#7c45a3'
  primary: '#370059'
  on-primary: '#ffffff'
  primary-container: '#511877'
  on-primary-container: '#c287ea'
  inverse-primary: '#e3b5ff'
  secondary: '#b02f00'
  on-secondary: '#ffffff'
  secondary-container: '#ff5722'
  on-secondary-container: '#541100'
  tertiary: '#1f2223'
  on-tertiary: '#ffffff'
  tertiary-container: '#343739'
  on-tertiary-container: '#9ea0a2'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#f3daff'
  primary-fixed-dim: '#e3b5ff'
  on-primary-fixed: '#2f004c'
  on-primary-fixed-variant: '#632c89'
  secondary-fixed: '#ffdbd1'
  secondary-fixed-dim: '#ffb5a0'
  on-secondary-fixed: '#3b0900'
  on-secondary-fixed-variant: '#862200'
  tertiary-fixed: '#e1e2e4'
  tertiary-fixed-dim: '#c5c7c8'
  on-tertiary-fixed: '#191c1e'
  on-tertiary-fixed-variant: '#444749'
  background: '#f9f9ff'
  on-background: '#111c2d'
  surface-variant: '#d8e3fb'
typography:
  headline-xl:
    fontFamily: Plus Jakarta Sans
    fontSize: 36px
    fontWeight: '700'
    lineHeight: 44px
    letterSpacing: -0.02em
  headline-xl-mobile:
    fontFamily: Plus Jakarta Sans
    fontSize: 28px
    fontWeight: '700'
    lineHeight: 36px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 28px
    fontWeight: '700'
    lineHeight: 36px
    letterSpacing: -0.015em
  headline-lg-mobile:
    fontFamily: Plus Jakarta Sans
    fontSize: 22px
    fontWeight: '700'
    lineHeight: 30px
    letterSpacing: -0.015em
  headline-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Plus Jakarta Sans
    fontSize: 16px
    fontWeight: '600'
    lineHeight: 24px
    letterSpacing: -0.005em
  body-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-sm:
    fontFamily: Plus Jakarta Sans
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
  label-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 13px
    fontWeight: '600'
    lineHeight: 18px
    letterSpacing: 0.01em
  label-sm:
    fontFamily: Plus Jakarta Sans
    fontSize: 11px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.02em
  code-sm:
    fontFamily: Plus Jakarta Sans
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  gutter: 1rem
  gutter-lg: 1.5rem
  margin: 1rem
  margin-md: 1.5rem
  margin-lg: 2rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 0.75rem
  space-lg: 1.25rem
  space-xl: 2rem
---

## Brand & Style

This design system delivers an enterprise-grade, high-velocity operational cockpit designed for small businesses and agencies executing cross-channel campaigns, paid media, and automated video workflows. The aesthetic rejects decorative tech clichés in favor of architectural rigor, functional clarity, and high-density productivity. 

The visual language establishes composure and authority. It treats social distribution and video rendering not as playful social toys, but as mission-critical revenue operations. The interface projects high reliability through crisp low-contrast delineations, structured layouts, and deliberate color discipline. Deep imperial purple commands institutional trust and brand stature, while deliberate flashes of energetic coral drive operational cadence and conversions.

Every visual decision favors immediate cognitive parsing:
- High data legibility over decorative embellishments.
- Strict rectangular discipline with moderate corner radii.
- Systematic visual hierarchy that eliminates ambiguous UI states.
- Clean planar structural separation rather than muddy gradient surfaces or synthetic neon accents.

## Colors

The palette uses a crisp, high-contrast light model calibrated for long-form operational usage and rigorous data density.

### Palette Roles
- **Primary Canvas (`#511877`):** Deep regal purple. Anchors system commands, primary global actions, high-level navigation, selected workspace states, and key navigational landmarks.
- **Secondary Accent (`#FF5722`):** Vibrant energetic coral. Reserved exclusively for direct forward-momentum actions: publishing pipelines, video export confirmations, active campaign scheduling, and critical notifications. It must never be diluted across static decorative accents.
- **App Canvas (`#F8F9FB`):** Cool-tinted slate mist. Forms the foundational application workspace, preventing eye fatigue from pure white while preserving crisp distinction between base canvas and elevated cards.
- **Surface Elevation (`#FFFFFF`):** Pure white. Applied strictly to interactive workspaces, timeline panels, media tracks, metric cards, and modal dialogs.
- **Neutral Hierarchy:**
  - **Headings & Primary Data (`#1E293B`):** Deep slate. High visual weight without the harshness of pitch black.
  - **Secondary Text & Labels (`#475569`):** Medium slate. Used for structural metadata, inactive navigation, column headers, and form labels.
  - **Muted & Tertiary Details (`#94A3B8`):** Light slate. Applied to disabled states, structural borders, timeline grids, and subtle helper notations.
  - **Surface Boundaries (`#E2E8F0`):** Precise slate line work separating toolbars, timeline divisions, and panel docks.

## Typography

The typographic system utilizes Plus Jakarta Sans across all display, body, and UI tiers. Geometric clarity is paired with sculpted apertures to ensure immediate legibility across dense schedules, analytics tables, and multi-track video timelines.

### Typography Rules
- **Headline Discipline:** Headlines must remain uniform in color and styling. Never single out words with contrasting colors, highlights, or synthetic italics. Headlines must be set in sentence case.
- **Label Architecture:** Labels use standard title case or sentence case. All-caps styling is prohibited across section headers, metadata, and badge chips.
- **Numbers and Quantitative Metrics:** Tabular figures are used for metrics, currency representations, and video timestamps to eliminate horizontal layout shifts.

## Layout & Spacing

Layouts adhere to an 8px architectural grid system (supplemented by a 4px subgrid for fine component alignment).

### Layout System
- **Structure:** Modular docking layout. A persistent 240px utility sidebar anchors left-hand navigation, collapsible to 64px for video timelines and asset canvases.
- **Desktop Breakpoint (1280px+):** Fixed-fluid hybrid. Dynamic panels expand within rigid bounding constraints. Content grids rely on 12 fluid columns with 24px (`gutter-lg`) spacing and 32px (`margin-lg`) canvas margins.
- **Tablet Breakpoint (768px - 1279px):** 8-column layout with 16px (`gutter`) gutters and 24px (`margin-md`) margins. Secondary docks collapse into sliding sheets.
- **Mobile Breakpoint (320px - 767px):** 4-column layout with 16px margins (`margin`). Complex timelines and table structures collapse into sequential vertical task cards.
- **Separation Discipline:** Do not use mid-dot (`·`) symbols or vertical pipe separators to delineate metadata items. Rely on clean token gaps (`space-sm` or `space-md`) and structural borders.

## Elevation & Depth

This system avoids blurred, diffuse shadows and tinted glows, relying instead on clean planar layering and 1px structural outlines.

### Elevation Hierarchy
- **Base Canvas (Level 0):** Background surface set to `#F8F9FB`. Holds the entire operational environment.
- **Docked Panels & Cards (Level 1):** Solid `#FFFFFF` fill with a crisp `1px solid #E2E8F0` border. No drop shadows. Depth is communicated strictly via the tone transition from the `#F8F9FB` canvas to `#FFFFFF`.
- **Active / Dragging Workspace Units (Level 2):** Applied when dragging calendar slots, reordering video clip layers, or adjusting queue priorities. Uses a `#FFFFFF` fill, a `1px solid #94A3B8` border, and an offset boundary shadow: `0 4px 12px rgba(30, 41, 59, 0.08)`.
- **Overlays, Popovers, & Dropdowns (Level 3):** Solid `#FFFFFF` with a `1px solid #CBD5E1` border and a structural drop shadow: `0 8px 24px rgba(30, 41, 59, 0.12)`.
- **System Modals & Dialogs (Level 4):** Solid `#FFFFFF` with a `1px solid #94A3B8` border, anchored by a focused scrim: `rgba(15, 23, 42, 0.48)`.

## Shapes

The interface maintains crisp, architectural corners using a restrained border-radius scale (Level 1 - Soft).

### Corner Geometry
- **Inputs, Buttons, and Standard Controls:** `0.25rem` (4px). Maintains precision and dense alignment across data-dense rows.
- **Cards, Modals, Video Canvas Containers, and Flyouts:** `0.5rem` (8px). Softens larger surface perimeters without turning bubbly or toy-like.
- **Chips, Pills, and Counter Badges:** `0.25rem` (4px) or fully rounded pill `9999px` strictly for numeric counters and status tags.
- **Video Scrub Heads & Media Handles:** `0.125rem` (2px) for exact frame indicators and cut points.

## Components

### Buttons
- **Primary Action (Brand):** Background `#511877`, text `#FFFFFF`, border radius 4px, font weight 600. Hover state `#3D105A`. Active state `#2B0B40`.
- **Conversion Action (Publish / Process):** Background `#FF5722`, text `#FFFFFF`, border radius 4px, font weight 600. Hover state `#E64A19`. Active state `#D84315`.
- **Secondary Action:** Background `#FFFFFF`, border `1px solid #CBD5E1`, text `#1E293B`. Hover state `#F1F5F9`.
- **Destructive Action:** Background `#EF4444`, text `#FFFFFF`.
- **Rule:** Never append directional arrows (`→`) or glyphs to generic button labels. Labels must be direct, imperative verbs (e.g., "Schedule Post", "Render Video", "Export Analytics").

### Form Inputs & Selectors
- **Text Inputs:** Height 36px (compact) or 40px (standard). Background `#FFFFFF`, border `1px solid #CBD5E1`, border radius 4px, text `#1E293B`, placeholder `#94A3B8`.
- **Focus State:** Border `1px solid #511877` with an inner box-shadow ring `0 0 0 1px #511877`.
- **Error State:** Border `1px solid #DC2626` with immediate inline validation text below.

### Cards & Content Panels
- **Structure:** Solid `#FFFFFF` fill bounded by a continuous `1px solid #E2E8F0` border. Internal padding: 16px or 20px.
- **Header:** Clean division between header and body via a bottom border `1px solid #F1F5F9`. Header text set in `headline-sm` with slate `#1E293B`.
- **No Floating Cards:** Cards must lock cleanly into the structural grid without generic blurred ambient shadows.

### Status Chips & Badges
- **Status Indicators:** 
  - *Scheduled:* Background `#F1F5F9`, text `#475569`, border `1px solid #E2E8F0`.
  - *Live / Active:* Background `#ECFDF5`, text `#047857`, border `1px solid #A7F3D0`.
  - *Processing / AI Generating:* Background `#FAF5FF`, text `#511877`, border `1px solid #E9D5FF`.
  - *Action Required / Failed:* Background `#FEF2F2`, text `#B91C1C`, border `1px solid #FECACA`.
- **Typography:** Set in `label-sm` (11px, 600 weight). No all-caps styling.

### Video Editor Canvas & Timeline Track
- **Canvas Viewport:** Matte slate background `#0F172A` with `#FFFFFF` ruler markings.
- **Timeline Tracks:** Alternating rows using `#FFFFFF` and `#F8F9FB` with horizontal `1px solid #E2E8F0` guides.
- **Playhead / Scrubber:** Line width 2px set to `#FF5722` with a 4px rounded rectangular handle.
- **Audio / Media Blocks:** Solid block surfaces with high-contrast waveform outlines; video clips bordered with `1px solid #CBD5E1`.

### Data Tables & Analytics Grids
- **Header Row:** Background `#F8F9FB`, height 36px, typography `label-sm` in slate `#475569`, border bottom `1px solid #CBD5E1`.
- **Body Rows:** Height 44px, background `#FFFFFF`, alternating hover state `#F8F9FB`, border bottom `1px solid #E2E8F0`.
- **Numbers & Metrics:** Right-aligned with tabular numbers enabled for exact column scannability.