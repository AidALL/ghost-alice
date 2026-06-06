# Precision Dark Operator Theme. Normalized Design.
## Contents

- [1. Role and Use](#1-role-and-use)
- [2. Mood Tokens](#2-mood-tokens)
- [3. Color Roles](#3-color-roles)
- [4. Typography Roles](#4-typography-roles)
- [5. Layout and Spacing Rules](#5-layout-and-spacing-rules)
- [6. Component Style Rules](#6-component-style-rules)
  - [Button](#button)
  - [Card](#card)
  - [Input](#input)
  - [Navigation](#navigation)
- [7. Prohibitions](#7-prohibitions)


## 1. Role and Use

A SaaS/developer-tool theme that treats darkness as its native medium. It is characterized by an extremely precise information hierarchy, a single indigo-violet accent, and a translucent white border system.

- Recommended industries: developer tools, AI governance platforms, project-management SaaS, infrastructure monitoring, technical documentation sites
- Not recommended for: consumer commerce (excessively cold tone), children's education (insufficient accessibility), dining and lifestyle (emotional mismatch)
- Tone keywords: cold, precise, technical, high-density, operational

## 2. Mood Tokens

- warmth: cold The blue-indigo surface family lowers emotion.
- density: dense Packs a lot of information onto one screen.
- contrast: stark Emphasis comes only from the single indigo-violet.
- formality: formal There is no playful elements, an operational-tool tone.

## 3. Color Roles

| Role | HEX | Notes |
| --- | --- | --- |
| primary-surface | #08090a | Deepest hero/marketing background |
| elevated-surface-1 | #0f1011 | Sidebar and panel background |
| elevated-surface-2 | #191a1b | Card and dropdown background |
| elevated-surface-3 | #28282c | Hover and topmost surface |
| primary-ink | #f7f8f8 | Primary text |
| secondary-ink | #d0d6e0 | Body and descriptions |
| muted-ink | #8a8f98 | Placeholder and meta |
| subtle-ink | #62666d | Timestamps and inactive |
| primary-accent | #5e6ad2 | CTA background and brand mark |
| secondary-accent | #7170ff | Links and active state |
| accent-hover | #828fff | accent hover |
| accent-muted | #7a7fad | Mouse-over and muted accent |
| positive | #27a644 | Success and in progress |
| positive-alt | #10b981 | Success badge |
| border-default | rgba(255,255,255,0.08) | Default border |
| border-subtle | rgba(255,255,255,0.05) | Minimal divider |

## 4. Typography Roles

| Role | Original | Substitute candidate 1 | Substitute candidate 2 | Use |
| --- | --- | --- | --- | --- |
| display | Inter Variable (OSS) | Plus Jakarta Sans | DM Sans | Page titles, card headlines |
| body | Inter Variable (OSS) | Plus Jakarta Sans | DM Sans | Body text, labels |
| mono | Berkeley Mono (paid) | JetBrains Mono | IBM Plex Mono | Code and numeric tables |

- font-feature-settings: "cv01", "ss03" globally required
- weight: 400 (reading) / 510 (emphasis) / 590 (strong emphasis). 700 forbidden
- display letter-spacing: -1.584px (72px) / -1.056px (48px) / -0.704px (32px)

## 5. Layout and Spacing Rules

- spacing scale: 1, 4, 7, 8, 11, 12, 16, 19, 20, 22, 24, 28, 32, 35 (px)
- border-radius: micro 2px / sm 4px / comfortable 6px / card 8px / panel 12px / lg 22px / pill 9999px / circle 50%
- breakpoint: <600 / 600–640 / 640–768 / 768–1024 / 1024–1280 / >1280
- container max-width: ~1200px, hero single-column, feature 2–3 col grid

## 6. Component Style Rules

### Button
- ghost: rgba(255,255,255,0.02) bg + rgba(255,255,255,0.08) border
- primary: #5e6ad2 bg + #f7f8f8 text
- subtle: elevated-surface-2 bg
- icon: circle shape / pill: 9999px radius

### Card
- bg: rgba(255,255,255,0.02) or elevated-surface-2
- border: rgba(255,255,255,0.08)
- radius: card 8px / panel 12px / lg 22px

### Input
- bg: rgba(255,255,255,0.02)
- border: rgba(255,255,255,0.08) → focus rgba(255,255,255,0.12)
- radius: 6px (comfortable)

### Navigation
- sticky, bg: #0f1011
- link weight: 510
- CTA: primary-accent

## 7. Prohibitions

- No standalone primary-accent branding is Combine it with other colors so that indigo-violet does not become a unique identifier.
- Do not use Inter Variable without font-feature-settings("cv01", "ss03")
- No pure #ffffff primary text. Use primary-ink #f7f8f8.
- No solid-color button background (except the primary CTA): apply the ghost or subtle approach.
- Do not use a solid dark border. Only the rgba(255,255,255,0.08) semi-transparent border is allowed.
- No shadow elevation is Distinguish surfaces only by luminance stepping.
- No weight 700 is 590 is the maximum.
- No positive display letter-spacing
