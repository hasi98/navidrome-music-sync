---
name: Luminous Audio
colors:
  surface: '#fbf8ff'
  surface-dim: '#dad9e3'
  surface-bright: '#fbf8ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f4f2fd'
  surface-container: '#eeedf7'
  surface-container-high: '#e8e7f1'
  surface-container-highest: '#e3e1ec'
  on-surface: '#1a1b22'
  on-surface-variant: '#5d3f3f'
  inverse-surface: '#2f3038'
  inverse-on-surface: '#f1effa'
  outline: '#916e6e'
  outline-variant: '#e6bdbc'
  surface-tint: '#bf002c'
  primary: '#ba002b'
  on-primary: '#ffffff'
  primary-container: '#e5183b'
  on-primary-container: '#fffbff'
  inverse-primary: '#ffb3b2'
  secondary: '#8234c6'
  on-secondary: '#ffffff'
  secondary-container: '#b86dfd'
  on-secondary-container: '#410070'
  tertiary: '#a5304c'
  on-tertiary: '#ffffff'
  tertiary-container: '#c54963'
  on-tertiary-container: '#fffbff'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#ffdad9'
  primary-fixed-dim: '#ffb3b2'
  on-primary-fixed: '#410008'
  on-primary-fixed-variant: '#92001f'
  secondary-fixed: '#f1dbff'
  secondary-fixed-dim: '#deb7ff'
  on-secondary-fixed: '#2d0050'
  on-secondary-fixed-variant: '#680eac'
  tertiary-fixed: '#ffd9dd'
  tertiary-fixed-dim: '#ffb2bc'
  on-tertiary-fixed: '#400013'
  on-tertiary-fixed-variant: '#881938'
  background: '#fbf8ff'
  on-background: '#1a1b22'
  surface-variant: '#e3e1ec'
typography:
  display:
    fontFamily: Plus Jakarta Sans
    fontSize: 48px
    fontWeight: '800'
    lineHeight: 56px
    letterSpacing: -0.03em
  display-mobile:
    fontFamily: Plus Jakarta Sans
    fontSize: 36px
    fontWeight: '800'
    lineHeight: 44px
    letterSpacing: -0.025em
  headline-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-lg-mobile:
    fontFamily: Plus Jakarta Sans
    fontSize: 26px
    fontWeight: '700'
    lineHeight: 32px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 22px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.015em
  headline-sm:
    fontFamily: Plus Jakarta Sans
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
    letterSpacing: -0.005em
  body-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
    letterSpacing: 0em
  label-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.02em
  label-sm:
    fontFamily: Plus Jakarta Sans
    fontSize: 11px
    fontWeight: '600'
    lineHeight: 14px
    letterSpacing: 0.04em
rounded:
  sm: 0.5rem
  DEFAULT: 1rem
  md: 1.5rem
  lg: 2rem
  xl: 3rem
  full: 9999px
spacing:
  gutter: 1.25rem
  gutter-desktop: 1.75rem
  margin: 1.25rem
  margin-desktop: 2.5rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 1rem
  space-lg: 1.5rem
  space-xl: 2.25rem
---

## Brand & Style

This design system blends pristine Apple Human Interface Guidelines discipline with a modern, high-fidelity music streaming aesthetic. It communicates clarity, tactile responsiveness, and artistic reverence for album artwork and media metadata. Designed for an enthusiast self-hosted audio audience, the experience eliminates visual clutter and transforms library administration into a sensory, audio-first sanctuary.

The style synthesizes modern iOS translucency, crisp architectural typography, and vibrant chromatic energy:
- **Luminosity & Translucency:** Interfaces utilize ultra-fine frosted glass surfaces (subtle blur, low specular reflections) to let vibrant album art diffuse softly across structural layers.
- **Airy Precision:** Generous whitespace, razor-thin structural borders, and calm platinum surfaces counterbalance hyper-saturated focal moments.
- **iOS Tactility:** High-curvature continuous corners, floating floating utility pills, and spring-weighted micro-interactions ground digital playback in physical elegance.

## Colors

The palette embraces Apple's signature electric musical vitality layered over clinical, ultra-clean neutral planes:

- **Primary (`#FA2D48` - Audio Red/Coral):** The driving interactive catalyst. Applied to playback controls, active state indicators, primary action triggers, and active waveforms.
- **Secondary (`#7B2CBF` - Electric Violet):** Harmonic companion used for curation badges, dynamic queue indicators, multi-select states, and contextual action highlights.
- **Tertiary (`#FF758F` - Soft Rose):** Ambient gradient transitions, subtle pill glows, and secondary progress track accents.
- **Neutral Core:**
  - Canvas Base: `#FFFFFF` (pure white sheet layers) and `#F8F9FA` (soft zinc/platinum background canvas).
  - Surface Substrates: `#F1F3F5` for inactive rails, chips, and muted track slots.
  - Borders: Crisp hairline separators rendered at `#E5E7EB` with 60%–80% opacity.
  - Typographic Hierarchy: Deep obsidian `#09090B` for high-impact titles, neutral slate `#52525B` for secondary artist metadata, and `#A1A1AA` for tertiary audio codecs (FLAC, ALAC, bitrates).

## Typography

The typographic hierarchy relies on **Plus Jakarta Sans** across all levels, capturing Apple's clean, modern geometry with warm, open counters. 

- **Display & Headlines:** Heavy weights (`700` and `800`) feature tight negative tracking (`-0.02em` to `-0.03em`) for album showcases, artist feature titles, and section headers.
- **Body & Metadata:** Regular weights (`400`) provide optimal legibility across varying track listing densities, duration indicators, and album notes.
- **Badges & Micro-Labels:** Semibold treatments (`600`) with subtle positive letter spacing (`+0.02em` to `+0.04em`) are strictly reserved for lossless indicators (Hi-Res, Lossless, Dolby Atmos), audio file formats, timestamps, and compact tab bars.

## Layout & Spacing

Layouts adhere to an adaptive fluid-column structure that balances media density with negative space:

- **Desktop & iPad Landscape:** A 12-column dynamic grid with a fixed collapsible frosted sidebar navigation (260px). Gutters set at `1.75rem` (`gutter-desktop`) and screen edge margins set at `2.5rem` (`margin-desktop`).
- **Mobile & Portrait Tablets:** A responsive 4-to-6 column flow utilizing `1.25rem` gutters and outer canvas margins (`margin`), allowing album artwork grids to maximize edge-to-edge sweep while respecting safe-area insets.
- **Vertical Spacing Rhythm:** Component interiors operate on a tight 4px baseline sub-grid (`space-xs` to `space-xl`). Album collections maintain `space-lg` separation between headers and content rails; track tables utilize strict `space-md` row heights for effortless thumb and cursor targeting.

## Elevation & Depth

Visual depth is achieved through translucent material tiers and ultra-soft optical shadows rather than dark physical drop shadows:

- **Material Tier 0 (Canvas):** Underlying base in `#F8F9FA`.
- **Material Tier 1 (Surface Cards):** `#FFFFFF` with hairline ghost borders (`1px solid rgba(229, 231, 235, 0.8)`) and an ultra-diffused ambient shadow: `0 8px 30px rgba(0, 0, 0, 0.04)`.
- **Material Tier 2 (Floating Player & Modals):** Frosted glass surface using `background: rgba(255, 255, 255, 0.78)` backed by `backdrop-filter: blur(28px) saturate(190%)`. Outlined with a top specular white edge (`1px solid rgba(255, 255, 255, 0.9)`) and side/bottom borders of `rgba(229, 231, 235, 0.5)`. Shadow: `0 16px 40px rgba(0, 0, 0, 0.08)`.
- **Artwork Lighting:** Active media artworks cast an atmospheric, blurred pastel bloom: a duplicate blurred layer (`filter: blur(20px)`) positioned behind the artwork at 35% opacity to dynamically reflect the track's palette onto the zinc canvas.

## Shapes

The interface embraces organic curvature (`roundedness: 3`) evocative of modern Apple hardware corners:

- **Cards & Media Art Containers:** Large interactive modules and album art wrappers use deep curvature (`rounded-2xl` at 1.25rem to `rounded-3xl` at 1.75rem). Artwork features inner border protection: `box-shadow: inset 0 0 0 1px rgba(0,0,0,0.05)` to define boundaries against white surfaces.
- **Pills & Floating Action Bars:** Full pill execution (`rounded-full` / 9999px) for play buttons, volume rails, filter chips, and floating playback controls.
- **Micro Surfaces:** Codec tags and small icons use `rounded-md` (0.5rem) to ensure crisp legibility without visual distortion.

## Components

### Buttons & Action Bars
- **Primary Play Button:** Pill-shaped, saturated `#FA2D48` background with crisp white typography/icons. Subtle hover lift with `0 6px 20px rgba(250, 45, 72, 0.35)`.
- **Secondary Actions:** Frosted translucent pill (`rgba(0, 0, 0, 0.04)`) with `#09090B` text. Active press scales down slightly (`scale(0.97)`), mimicking native iOS touch feedback.

### Album & Playlist Cards
- Ultra-smooth `rounded-2xl` artwork with subtle bottom reflection blur.
- Hover/focus states invoke a gentle upward translation (`translateY(-4px)`) and increase the ambient artwork bloom.
- Titles clamp at two lines using `headline-sm`, followed by secondary artist lines in `body-md` slate.

### Track Lists & Queues
- Row heights are calibrated to 56px with generous horizontal cell padding (`space-md`).
- Hovering shows an edge-to-edge soft zinc overlay (`#F1F3F5` at 50% opacity, `rounded-xl`).
- The currently playing track replaces the track number with an animated 3-bar equalizer rendered in `#FA2D48`, shifting track title typography to Semibold `#FA2D48`.

### Floating Miniplayer / Player Sheet
- Anchored at the screen bottom with frosted glass vibrancy (`backdrop-filter: blur(24px)`).
- Left: Compact 44px rounded album cover with soft shadow.
- Center: Track title, artist name, and a slender progress slider with a scrub thumb that expands upon pointer engagement.
- Right: AirPlay, volume, queue controls, and sync badges.

### Badges & Audio Format Chips
- Monospace-styled audio codec labels (e.g., "LOSSLESS", "24-BIT / 96kHz") styled in `label-sm`, enclosed in an ultra-fine border pill (`border: 1px solid #E5E7EB`, `background: rgba(255, 255, 255, 0.6)`).
- Sync Status Badge: Dynamic pulsing dot in `#FA2D48` denoting live Navidrome streaming and caching states.

### Form Inputs & Search Fields
- Rounded pill search container with `#F1F3F5` fill, zero default border, magnifying glass icon in `#71717A`, and smooth expansion on focus backed by a crisp `#FA2D48` halo ring.