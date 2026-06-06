# Normalized DESIGN.md
## Contents

- [1. Role and Use](#1-role-and-use)
- [2. Mood Tokens](#2-mood-tokens)
- [3. Color Roles](#3-color-roles)
- [4. Typography Roles](#4-typography-roles)
- [5. Layout and Spacing Rules](#5-layout-and-spacing-rules)
- [6. Component Style Rules](#6-component-style-rules)
  - [Buttons](#buttons)
  - [Cards](#cards)
  - [Inputs](#inputs)
  - [Navigation](#navigation)
- [7. Prohibitions](#7-prohibitions)


## 1. Role and Use

A warm, slow-paced marketplace design for a photo-centric consumer discovery and browsing platform. It uses a single warm coral red accent against a pure white background, applied sparingly to CTAs only. The interface is optimized for the browsing experience rather than task efficiency, and cards always lead with the photo.

- Recommended industries: travel booking platforms, experience and hobby booking, local shop discovery, real estate rentals, lifestyle commerce
- Not recommended for:
  - operations dashboard. Excessive decoration interferes with information density.
  - financial trading UI. It is warmth slows down data scanning.
  - enterprise admin. Excessive rounding conflicts with task focus.
- Tone keywords: warmth, photo-centric, slow browsing, friendliness, travel-magazine pacing

## 2. Mood Tokens

- warmth: warm. It is warm coral red accent plus warm near-black text #222222.
- density: balanced. Moderate gaps between cards, ample whitespace for photo-centric listings.
- contrast: soft. Ultra-subtle three-layer shadow, pure white background, overall rounding.
- formality: casual. Maintained with circular controls and a friendly weight range.

## 3. Color Roles

| Role | HEX | Notes |
|------|------|------|
| primary-surface | #ffffff | Page background, card surface |
| elevated-surface | #f2f2f2 | Circular navigation buttons, secondary surface |
| primary-ink | #222222 | Body text. It is warm near-black. Do not use #000000. |
| focused-ink | #3f3f3f | Focus-state text |
| muted-ink | #6a6a6a | Secondary text, descriptions |
| disabled-ink | rgba(0,0,0,0.24) | Disabled state |
| link-disabled-ink | #929292 | Disabled link |
| primary-accent | #ff385c | CTA, brand accent, active state |
| primary-accent-pressed | #e00b41 | pressed and dark variant |
| danger | #c13515 | error text on light |
| danger-hover | #b32505 | error hover |
| info | #428bff | legal and informational links |
| border-subtle | #c1c1c1 | Card and divider borders |
| premium-tier-1 | #460479 | Premium tier accent 1 |
| premium-tier-2 | #92174d | Premium tier accent 2 |

## 4. Typography Roles

| Role | Original | Substitute candidate 1 | Substitute candidate 2 | Use |
|------|------|------------|------------|------|
| display | brand custom VF | Inter | Work Sans | Section headings, card titles |
| body | brand custom VF | Inter | Source Sans 3 | Body text, labels, buttons |
| mono | none | JetBrains Mono | IBM Plex Mono | Code and numeric tables |

- Letter spacing: tight tracking of -0.18px to -0.44px on headings. Intended for a friendly intimacy.
- Weight range: 500 medium, 600 semibold, 700 bold. Do not use weights 300 and 400 on headings.
- The brand-only OpenType `"salt"` feature is excluded when a substitute candidate is used.

## 5. Layout and Spacing Rules

- spacing scale. Based on 2, 3, 4, 6, 8, 10, 11, 12, 15, 16, 22, 24, 32 px.
- base unit. 8px
- border-radius. xs 4, sm 8, md 14, lg 20, xl 32, full 50%
- breakpoint
  - mobile <375px
  - small-mobile 375–550px
  - tablet 550–744px
  - tablet-wide 744–950px
  - desktop 950–1128px
  - desktop-wide 1128–1440px
  - large-desktop 1440–1920px
  - ultra-wide >1920px
- container max-width. 1440px
- listing grid. Responsive 3–5 columns on desktop.
- Vertical spacing between sections. 80–120px. Magazine pacing.

## 6. Component Style Rules

### Buttons

primary-dark

- background: #222222
- text: #ffffff
- padding: 0 24px
- radius: 8px
- focus: `0 0 0 2px` ring plus scale(0.92) shrink animation
- hover: brand-accent tint transition

circular-nav

- background: #f2f2f2
- radius: 50%
- hover: shadow `rgba(0,0,0,0.08) 0px 4px 12px`
- active: 4px white border ring

### Cards

- background: #ffffff
- radius: 20px (card), 14px (badge), 32px (large container)
- shadow: three-layer stack. `rgba(0,0,0,0.02) 0 0 0 1px` plus `rgba(0,0,0,0.04) 0 2px 6px` plus `rgba(0,0,0,0.1) 0 4px 8px`
- layout: top full-width photo, details below

### Inputs

- background: #ffffff
- text: #222222
- focus: primary-accent tint background plus `0 0 0 2px` ring
- radius: 8px. The search bar is a pill shape (high radius).

### Navigation

- Top sticky header. White background.
- Logo left-aligned
- Search bar centered
- category filter pills with horizontal scroll at the bottom
- Carousel controls are circular buttons.

## 7. Prohibitions

- Do not use the brand-only VF font as is. Substitute Inter or Work Sans.
- Do not imitate the brand logo shape. The single logo shape itself is the signature.
- Do not borrow the original slogan. Quoting or reusing a specific slogan phrase itself triggers an identity association.
- Do not use primary-accent #ff385c as a background or on a large surface. Accent only.
- Do not use pure black #000000. Always use #222222 warm near-black.
- Do not use sharp corners of 0–4px on a card. 20px+ rounding is the core identity.
- Do not imitate the original photo style. Handheld, natural-light, travel-magazine framing carries a brand-replication risk.
- Do not trigger immediate brand association through the combination of a single warm coral red, pure white, and circular controls. Dilute the uniqueness by combining with other elements.
- Do not introduce additional brand colors. Expanding the palette to two or more premium tiers collapses the identity.
