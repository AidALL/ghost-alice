# Design System Inspired by Renault
## Contents

- [1. Visual Theme & Atmosphere](#1-visual-theme-atmosphere)
- [2. Color Palette & Roles](#2-color-palette-roles)
  - [Primary](#primary)
  - [Secondary & Accent](#secondary-accent)
  - [Surface & Background](#surface-background)
  - [Neutrals & Text](#neutrals-text)
  - [Semantic & Accent](#semantic-accent)
  - [Gradient System](#gradient-system)
- [3. Typography Rules](#3-typography-rules)
  - [Font Family](#font-family)
  - [Hierarchy](#hierarchy)
  - [Principles](#principles)
- [4. Component Stylings](#4-component-stylings)
  - [Buttons](#buttons)
  - [Cards & Containers](#cards-containers)
  - [Inputs & Forms](#inputs-forms)
  - [Navigation](#navigation)
  - [Image Treatment](#image-treatment)
  - [Carousel Component](#carousel-component)
- [5. Layout Principles](#5-layout-principles)
  - [Spacing System](#spacing-system)
  - [Grid & Container](#grid-container)
  - [Whitespace Philosophy](#whitespace-philosophy)
  - [Border Radius Scale](#border-radius-scale)
- [6. Depth & Elevation](#6-depth-elevation)
  - [Shadow Philosophy](#shadow-philosophy)
  - [Decorative Depth](#decorative-depth)
- [7. Do's and Don'ts](#7-dos-and-donts)
  - [Do](#do)
  - [Don't](#dont)
- [8. Responsive Behavior](#8-responsive-behavior)
  - [Breakpoints](#breakpoints)
  - [Touch Targets](#touch-targets)
  - [Collapsing Strategy](#collapsing-strategy)
  - [Image Behavior](#image-behavior)
- [9. Agent Prompt Guide](#9-agent-prompt-guide)
  - [Quick Color Reference](#quick-color-reference)
  - [Example Component Prompts](#example-component-prompts)
  - [Iteration Guide](#iteration-guide)


## 1. Visual Theme & Atmosphere

Renault's website is a vibrant digital showroom that balances French automotive elegance with bold, forward-leaning energy. It is a departure from the monochromatic austerity of German or Italian luxury brands. The page opens with a full-screen hero that washes the viewport in a sweeping aurora gradient. Ribbons of magenta, violet, and teal bleed across the frame behind a dramatically lit vehicle. This chromatic expressiveness is the site's signature: while the interface structure is disciplined (NouvelR typography, black-and-white CTA framework, zero-radius buttons), the content is alive with color. Gradient washes on hero slides, saturated vehicle photography, and splashes of Renault Yellow (`#EFDF00`) on accent CTAs carry that energy. The effect is a showroom that feels energized rather than hushed.

The layout follows a card-based editorial rhythm. Below the hero carousel, content is organized into a grid of PromoCards. Each a full-bleed photographic panel with a dark gradient overlay at top (fading from `rgba(0,0,0,0.6)` to transparent) to ensure white heading text remains legible over vivid imagery. These cards alternate between light and dark modes: white editorial panels with black text sit beside black `is-alternative-mode` sections with white text, creating a chessboard-like visual cadence. The grid is generous. Large card formats dominate, giving each vehicle or campaign its own visual territory. The lower sections shift to a fully dark canvas (Absolute Black backgrounds) for the E-Tech electric and technology showcases, establishing a deliberate mood shift: electrification lives in darkness, tradition in light.

Typography is unified under NouvelR, a proprietary geometric sans-serif designed by Black[Foundry] exclusively for Renault's rebrand. The typeface features a distinctive "radical r" with a terminal cut at 28 degrees to echo the Renault diamond logo's angles. Available in 6 weights from Light to Extrabold, the site primarily uses Bold (700) for headings and Regular (400) for body. Display headlines run large at 56px/0.95 line-height for hero titles, creating dense, impactful text blocks that sit tight against each other. The font supports Latin, Greek, Cyrillic, Hebrew, Arabic, and Korean, reflecting Renault's global market reach. All text rendering feels precise and engineered, with the geometric proportions lending a sense of modernity that aligns with Renault's electric-first brand positioning.

**Key Characteristics:**
- Full-screen hero carousel with vivid aurora gradient backgrounds (magenta/violet/teal) behind vehicle imagery
- NouvelR proprietary typeface with 28-degree "radical r" cut matching the diamond logo geometry
- Renault Yellow (`#EFDF00`) as the super-primary accent is used sparingly for highest-priority CTAs.
- Zero border-radius on all buttons. It is sharp rectangular forms expressing precision engineering.
- Card-based editorial grid with full-bleed photography and dark gradient overlays
- Binary black/white CTA system: primary (black bg/white text) and ghost (transparent/white border)
- PromoCard dark-mode alternation creating a chessboard rhythm between light and dark sections
- PrimeReact (21 components) + Element Plus (19 components) powering interactive elements
- Link hover state in Renault Blue (`#1883FD`). This means the sole chromatic interaction color.

## 2. Color Palette & Roles

### Primary
- **Renault Yellow** (`#EFDF00`): The brand's signature Pantone is a vivid, saturated yellow used for super-primary CTAs and the highest-priority action buttons. It appears as `--CtaLink-background-color` on `.is-cta-super-primary` class. It carries the energy of the diamond logo.
- **Absolute Black** (`#000000`): Primary button background, heading text on light surfaces, and the dominant dark section surface. The structural anchor of the entire interface
- **Pure White** (`#FFFFFF`): Primary surface for editorial content, inverted button backgrounds, hero text color, and the dominant light-mode canvas (--rt-color-white)

### Secondary & Accent
- **Soft Yellow** (`#F8EB4C`): Lighter, warmer variant of Renault Yellow It is used for hover/pressed states on yellow CTAs and secondary accent contexts.
- **Renault Blue** (`#1883FD`): Link hover color across all link variants A bright, confident blue that signals interactivity without competing with the yellow brand accent.
- **Warm Gray** (`#D9D9D6`): Subtle warm neutral used for disabled states, inactive UI elements, and soft borders Carries a slight warmth that distinguishes it from cold grays.

### Surface & Background
- **Pure White** (`#FFFFFF`): Page background, light editorial sections, navigation bar, and footer
- **Absolute Black** (`#000000`): Hero backgrounds, PromoCard dark-mode sections (`is-alternative-mode`), and E-Tech showcase areas
- **Charcoal** (`#222222`): Secondary dark surface for text-heavy dark sections and footer sub-regions (--rt-color-dark)
- **Pale Silver** (`#F2F2F2`): Subtle alternate light surface for section differentiation and card borders

### Neutrals & Text
- **Absolute Black** (`#000000`): Primary heading and body text on light surfaces Renault uses true black rather than near-black.
- **Pure White** (`#FFFFFF`): Primary text on dark surfaces Hero headlines, dark-section headings, and inverted button labels.
- **Warm Gray** (`#D9D9D6`): Tertiary text, metadata, and subdued labels
- **Border Gray** (`#D1D1D1`): Input field borders and subtle separators

### Semantic & Accent
- **Success Green** (`#8DC572`): Positive status indicators and confirmation messages (--rt-color-success)
- **Error Rose** (`#BE6464`): Form validation errors and warning states (--rt-color-error)
- **Warning Amber** (`#F0AD4E`): Cautionary alerts and attention-requiring states (--rt-color-warning)
- **Info Blue** (`#337AB7`): Informational callouts and neutral status messaging (--rt-color-info)

### Gradient System
- **Hero Aurora**: Sweeping multi-color gradients (magenta → violet → teal) applied to hero slide backgrounds The site's most distinctive visual element. These are photographic/composited rather than CSS gradients.
- **PromoCard Overlay**: `linear-gradient(rgba(0,0,0,0.6) 0%, rgba(0,0,0,0) 40%)` Applied to card tops to ensure heading text legibility over photography.
- No flat CSS gradients on surfaces is Depth comes from photographic treatment and the black/white alternation.

## 3. Typography Rules

### Font Family
- **NouvelR**: The sole typeface. It is a proprietary geometric sans-serif designed by Black[Foundry] for Renault's 2021+ rebrand. Features a distinctive "radical r" with a 28-degree terminal cut matching the diamond logo angle. Available in 6 weights (Light to Extrabold), supports 6 writing systems. Fallback: `sans-serif`. Declared as `"NouvelR, sans-serif"` in CSS
- **No secondary typeface**: Unlike Ferrari (FerrariSans + Body-Font) or Lamborghini (LamboType + Open Sans), Renault uses a single font family for all text. Headings, body, buttons, captions, and navigation all use that family.

### Hierarchy

| Role | Size | Weight | Line Height | Letter Spacing | Notes |
|------|------|--------|-------------|----------------|-------|
| Hero Title | 56px (3.50rem) | 700 | 0.95 (53.2px) | normal | NouvelR, white on dark hero, all-caps model names |
| Section Heading | 40px (2.50rem) | 700 | 0.95 (38px) | normal | NouvelR, PromoCard headings on dark/light sections |
| Card Heading | 32px (2.00rem) | 700 | 0.95 | normal | NouvelR, medium-scale card headings |
| Subheading | 24px (1.50rem) | 700 | 0.95 | normal | NouvelR, section sub-titles |
| Module Title | 21.92px (1.37rem) | 600 | 1.20 | normal | NouvelR, component headings |
| Content Title | 20px (1.25rem) | 700 | 0.95 | normal | NouvelR, smaller section titles |
| UI Heading | 19.2px (1.20rem) | 600 | 1.30 | normal | NouvelR, card UI headings |
| Emphasis | 18px (1.13rem) | 700 | 1.00 | normal | NouvelR, emphasized inline text and links |
| Body Heading | 16px (1.00rem) | 700 | 1.40 | normal | NouvelR, paragraph-level headings |
| Body Text | 14px (0.88rem) | 400 | 1.40 | normal | NouvelR, paragraph and descriptive content |
| Body Bold | 14px (0.88rem) | 700 | 1.57 | normal | NouvelR, emphasized body text |
| Button Label | 14.4px (0.90rem) | 700 | 1.00 | 0.144px | NouvelR, primary button text |
| Nav Link | 13px (0.81rem) | 700 | 1.50 | normal | NouvelR, navigation and footer links |
| Caption | 12.8px (0.80rem) | 400 | 1.10 | normal | NouvelR, small descriptive text |
| Small Label | 12px (0.75rem) | 700 | 1.00 | normal | NouvelR, labels and tags |
| Micro Text | 10px (0.63rem) | 700 | 1.45 | normal | NouvelR, smallest UI text, legal fine print |
| Micro Caption | 8.5px (0.53rem) | 400 | normal | normal | NouvelR, absolute smallest text (legal) |

### Principles
- **Single-family discipline**: NouvelR handles everything from 56px hero headlines to 8.5px legal captions The font's geometric precision allows it to scale across this extreme range without losing character.
- **Bold-default headings**: Weight 700 dominates the heading hierarchy. Unlike brands that use medium (500) for headings, Renault's Bold weight creates a more assertive, energetic reading experience
- **Ultra-tight display line-heights**: 0.95 line-height on hero and section headings. The lines nearly collide, creating a compressed, punchy typographic texture that feels urgent and modern.
- **28-degree radical r**: The typeface's signature detail. The lowercase "r" terminal is cut at precisely 28 degrees to mirror the angles of the Renault diamond logo, embedding brand identity into every word.
- **Capitalize transform on captions**: Some caption text uses `text-transform: capitalize` for editorial labeling, while micro text uses `lowercase` A deliberate inversion for hierarchy signaling.

## 4. Component Stylings

### Buttons
Renault's buttons are sharp-edged rectangles with zero border-radius. The industrial precision of a pressed metal body panel.

**Super Primary (Yellow)**. The highest-emphasis CTA:
- Default: bg `#EFDF00` (Renault Yellow), text `#000000`, borderRadius 0px, padding 10px 15px, border 1px solid `#EFDF00`
- Inverted: bg `#EFDF00`, text `#000000` The same yellow on dark backgrounds.
- fontSize 16px (NouvelR), fontWeight 700, minHeight 46px, minWidth 46px
- Used for: Primary conversion actions (configure, buy now)

**Primary (Black)**. The default action button:
- Default: bg `#000000`, text `#FFFFFF`, borderRadius 0px, padding 10px 15px, border 1px solid `#000000`
- Inverted: bg `#FFFFFF`, text `#000000`, border 1px solid `#FFFFFF` White fill on dark backgrounds.
- fontSize 16px (NouvelR), fontWeight 700
- Used for: "keşfedin" (explore), secondary conversion actions

**Ghost**. Transparent outline button:
- Default (on dark): bg transparent, text `#FFFFFF`, border 1px solid `#FFFFFF`, borderRadius 0px, padding 10px 15px
- Default (on light): bg transparent, text `#000000`, border 1px solid `#000000`
- fontSize 16px (NouvelR), fontWeight 700
- Used for: "ilk sen öğren" (be the first to know), "satın alın" (buy), secondary actions

**Text Link**. Inline navigation:
- Default (light): text `#000000`, no border, no background
- Default (dark): text `#FFFFFF`
- Hover: color shifts to `#1883FD` (Renault Blue), text-decoration none
- All link variants hover to the same blue is Consistent interactive feedback.

### Cards & Containers

**PromoCard (Light)**. Editorial content card:
- Background: white or transparent
- Full-bleed photography with dark gradient overlay at top: `linear-gradient(rgba(0,0,0,0.6) 0%, rgba(0,0,0,0) 40%)`
- Heading: NouvelR 40px/700, white text positioned over gradient
- Border-radius: 0px It is sharp rectangular containers.
- No shadow, no visible border

**PromoCard (Dark / `is-alternative-mode`)**. Cinematic card:
- Background: `#000000` (Absolute Black)
- Same gradient overlay treatment
- Heading: white NouvelR text
- CTA buttons: inverted primary (white bg) or ghost (white border)

**VehicleRangeCard**. Vehicle showcase:
- Background: transparent
- Vehicle image above, model name and price/spec below
- No shadow, no border, clean flat treatment
- Spacing between cards via grid gap

### Inputs & Forms

**Search/Text Input:**
- Background: `#FFFFFF`
- Text: `#000000`
- Border: 1px solid `#D1D1D1` (Border Gray)
- Border-radius: 50px (pill-shaped Unusual deviation from the zero-radius button system).
- Padding: 6px 35px 6px 15px (extra right padding for search icon)
- Font: NouvelR, 12.8px
- Focus: standard browser focus ring

### Navigation
- **Desktop**: Renault diamond logo centered/left, horizontal nav links, sticky positioning
- **Background**: white, no shadow at rest
- **Links**: NouvelR, 13px, weight 700, black text
- **Hover**: color shifts to `#1883FD` (Renault Blue)
- **Mobile**: Hamburger collapse to full-screen navigation drawer
- **CTA in nav**: Primary black button for main conversion action

### Image Treatment
- **Hero**: Full-viewport carousel with dramatic aurora-gradient backgrounds and art-directed vehicle photography. It is edge-to-edge, no padding.
- **PromoCards**: Full-bleed photography within card bounds, dark gradient overlay at top for text legibility
- **Vehicle images**: Transparent-background renders on neutral/gradient backgrounds
- **Aspect ratios**: Mixed Hero at roughly 16:9 viewport, promo cards at various ratios from square to wide panoramic.
- **Lazy loading**: Below-fold sections use lazy loading (framework-handled)

### Carousel Component
- Full-screen hero carousel with auto-advancing slides
- Each slide: background gradient/photo + vehicle image + headline + CTA buttons
- Dot indicators for slide position
- Navigation arrows at edges

## 5. Layout Principles

### Spacing System
- **Base unit**: 8px (detected system base)
- **Scale**: 1px, 4px, 5px, 6px, 6.25px, 8px, 10px, 12px, 13px, 15px, 16px, 20px, 24px, 32px, 40px
- **Button padding**: 10px 15px Consistent across all button variants.
- **Section padding**: Generous vertical spacing (40–80px) between major content blocks
- **Card gaps**: 16–24px between grid items
- **Minimum interactive size**: 46px (minWidth and minHeight on all buttons)

### Grid & Container
- **Max width**: 1440px (largest defined breakpoint)
- **Hero**: Full-bleed, edge-to-edge, viewport-height
- **PromoCard grid**: 2-up and 3-up layouts with mixed card sizes
- **Vehicle range**: Horizontal scrollable card row or grid
- **Footer**: Multi-column layout on white background

### Whitespace Philosophy
Renault uses whitespace moderately. More generously than Ferrari but less extremely than Tesla. The card-based layout means content is organized into defined containers rather than floating in void. The visual breathing room comes primarily from the large card formats and the full-bleed hero carousel, which gives each vehicle its own cinematic moment. Between sections, spacing is consistent (32–40px) creating a rhythmic scroll experience. The alternation between light and dark sections also creates perceived whitespace. The mode switch itself acts as a visual separator.

### Border Radius Scale
| Value | Context |
|-------|---------|
| 0px | All buttons, PromoCards, most containers. The zero-radius default|
| 2px | Small UI elements (region controls) |
| 3px | Content panels (div, tabpanel) |
| 4px | Labels and tag elements |
| 46px | Pill-shaped elements (search input, filter chips) |
| 50px | Full pill for search/input fields |

## 6. Depth & Elevation

| Level | Treatment | Use |
|-------|-----------|-----|
| Level 0 (Flat) | No shadow | Default for PromoCards, buttons, most containers |
| Level 1 (Soft) | `rgba(0,0,0,0.2) 0px 4px 8px` | Card hover states, subtle lift effect |
| Level 2 (Medium) | `rgba(0,0,0,0.2) 0px 0px 18px` | Floating UI elements, dropdown menus |
| Level 3 (Layered) | `rgba(0,0,0,0) 0px 2px 4px, rgba(50,50,93,0.1) 0px 7px 14px` | Compound shadow for elevated cards and modals |
| Level 4 (Deep) | `rgba(0,0,0,0.15) 0px 40px 80px` | Large floating panels, configurator overlays |
| Level 5 (Directional) | `rgba(0,0,0,0.2) 5px 5px 8px` | Offset directional shadow for specific components |
| Level 6 (Ambient) | `rgb(199,197,199) 0px 0px 12px 2px` | Ambient glow effect for highlighted elements |

### Shadow Philosophy
Renault uses a richer shadow system than Ferrari or Tesla. Seven distinct shadow tokens reflecting a more layered, dimensional interface. The shadows progress from subtle 4px hover lifts to dramatic 80px deep panels. The compound shadow (Level 3) with its dual-layer approach (a tight dark shadow plus a wider purple-tinted one from `rgba(50,50,93,0.1)`) is particularly refined. It creates a photorealistic floating effect. The ambient glow (Level 6) in warm gray adds a unique touch that connects to Renault's warmer color personality.

### Decorative Depth
- **Hero aurora gradients**: The primary decorative depth element Vivid color gradients create atmospheric depth behind vehicle imagery.
- **PromoCard overlays**: `linear-gradient(rgba(0,0,0,0.6) → transparent)` creates depth within cards through transparency
- **No blur effects** on UI elements is Depth is communicated through shadow and color contrast.

## 7. Do's and Don'ts

### Do
- Use Renault Yellow (`#EFDF00`) exclusively for super-primary CTAs. It carries the full weight of the diamond logo's identity.
- Maintain zero border-radius on all buttons. It is sharp edges are non-negotiable in the Renault system.
- Use NouvelR Bold (700) as the default heading weight. The assertive weight is central to the brand's energetic personality.
- Apply the dark gradient overlay (`rgba(0,0,0,0.6) → transparent`) on PromoCards to ensure text legibility over photography
- Keep hero line-heights ultra-tight (0.95) for display text. The compressed texture feels urgent and modern.
- Alternate between black and white sections to create the signature chessboard rhythm
- Use `#1883FD` (Renault Blue) consistently for all link hover states. One interactive color signal.
- Set minimum interactive size at 46×46px for all buttons is Accessibility built into the component spec.
- Reserve pill-shaped radius (46–50px) exclusively for search inputs and filter elements. Do not for buttons.
- Use the PromoCard gradient overlay on every card that has text over photography

### Don't
- Apply Renault Yellow as a background color for sections or surfaces. It's a CTA signal, not an atmosphere color.
- Add border-radius to buttons. The zero-radius rectangle is a core brand marker.
- Use any typeface besides NouvelR. The single-family discipline is a brand pillar.
- Mix multiple chromatic accent colors in a single section. This means the palette is monochrome-plus-yellow.
- Soften heading weights to 400 or 500 is NouvelR Bold is the brand voice, lighter weights read as off-brand.
- Add decorative borders to PromoCards or content containers. Separation comes from background color alternation.
- Use the semantic colors (Success Green, Error Rose) for decorative purposes. They're reserved for form states.
- Apply the 56px hero size to anything below the fold. Hero typography scale is reserved for the carousel.
- Create rounded-pill buttons. Pill shapes are reserved for inputs, never for action elements.
- Use flat CSS gradients on UI surfaces. It is the only gradients should be the photographic hero auroras and the text-legibility overlays.

## 8. Responsive Behavior

### Breakpoints
| Name | Width | Key Changes |
|------|-------|-------------|
| Mobile Small | ≤425px | Single-column, full-width cards, hero text scales to ~32px, stacked CTAs, hamburger nav |
| Mobile | 426–640px | Single-column, slightly larger cards, hero text at 32–40px |
| Tablet Small | 641–768px | 2-column PromoCard grid begins, hero maintains full-width |
| Tablet | 769–896px | Full 2-column layout, vehicle range shows 2–3 cards |
| Desktop Small | 897–1024px | Navigation fully expanded, hero at 56px, 2-up card grid |
| Desktop | 1025–1280px | Full layout, 3-up card grid, generous whitespace |
| Large Desktop | 1281–1440px | Maximum content width, centered container, hero at full cinematic scale |

### Touch Targets
- All buttons: minimum 46×46px (`minWidth: 46px, minHeight: 46px`) Exceeds WCAG AAA 44×44px requirement.
- Search input pill: adequate touch target with 50px border-radius creating a large tappable area
- Navigation links: NouvelR 13px with adequate spacing between items
- Carousel navigation: large arrow targets at viewport edges

### Collapsing Strategy
- **Navigation**: Full horizontal nav collapses to Renault diamond logo + hamburger menu on mobile
- **Hero carousel**: Full-width at all breakpoints, headline scales from 56px (desktop) to ~32px (mobile)
- **PromoCard grid**: 3-up → 2-up → single-column as viewport narrows
- **Vehicle range**: Horizontal scroll maintained at all sizes, visible cards reduce
- **CTA pairs**: Side-by-side buttons stack vertically on mobile
- **Footer**: Multi-column collapses to single-column accordion on mobile

### Image Behavior
- Hero images: full-bleed at all breakpoints with `object-fit: cover`
- PromoCard images: responsive within card containers, gradient overlay scales proportionally
- Vehicle images: transparent-background renders scale proportionally within grid cells
- Art direction: mobile may crop to tighter vehicle views, reducing environmental context

## 9. Agent Prompt Guide

### Quick Color Reference
- Primary CTA (Super): "Renault Yellow (#EFDF00)"
- Primary CTA (Default): "Absolute Black (#000000)"
- Background Light: "Pure White (#FFFFFF)"
- Background Dark: "Absolute Black (#000000)"
- Secondary Dark: "Charcoal (#222222)"
- Heading text (light bg): "Absolute Black (#000000)"
- Body text: "Absolute Black (#000000)"
- Link Hover: "Renault Blue (#1883FD)"
- Border: "Pale Silver (#F2F2F2)"
- Semantic Error: "Error Rose (#BE6464)"

### Example Component Prompts
- "Create a hero section with a full-viewport aurora gradient background (magenta to violet to teal), a centered vehicle image, a NouvelR Bold headline at 56px with 0.95 line-height in white, and two buttons: a Primary (white bg, black text, 0px radius) 'Explore' and a Ghost (transparent bg, white border, white text, 0px radius) 'Learn More'"
- "Design a PromoCard with a full-bleed photography background, a dark gradient overlay (rgba(0,0,0,0.6) top to transparent at 40%), a NouvelR Bold 40px white heading, a 14px body text line in white, and a Primary inverted button (white bg, black text, 0px radius, 10px 15px padding)"
- "Build a vehicle range grid with 3 columns on white background, each card showing a transparent-background car render above a NouvelR Bold 24px model name in black, a 14px price caption, and a ghost button (black border, black text, 0px radius) labeled 'Configure'"
- "Create a dark E-Tech section on Absolute Black (#000000) with a NouvelR Bold 40px white heading 'E-Tech electric powertrain', a 14px subtitle in white, and a Renault Yellow (#EFDF00) super-primary button with black text, 0px radius, and 10px 15px padding"
- "Design a search input as a pill-shaped field (50px border-radius) with white background, 1px solid #D1D1D1 border, NouvelR 12.8px text, 6px 35px 6px 15px padding, and a search icon positioned inside the right padding area"

### Iteration Guide
When refining existing screens generated with this design system:
1. Focus on ONE component at a time is Renault's system has clear component boundaries (PromoCard, VehicleRangeCard, CTA variants).
2. Reference specific color names and hex codes. This means the palette is small but each color has a precise function.
3. Use natural language descriptions, not CSS values. "Sharp zero-radius rectangle" conveys intent better than "border-radius: 0".
4. Describe the desired "feel" alongside specific measurements is "Assertive automotive energy" communicates the NouvelR Bold heading personality better than "font-weight: 700".
5. Always check whether a section should be light or dark. The chessboard alternation is a core pattern.
6. Reserve Renault Yellow for ONE button per screen. If yellow appears in more than one CTA, the hierarchy collapses.
