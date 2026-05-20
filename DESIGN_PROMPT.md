# Claude Design — Redesign prompt
# Вставь весь текст ниже в поле "Describe what you want to create"

---

Redesign a dark analytics dashboard for WB + Ozon marketplace sellers. 
Keep the dark orange-black color scheme but make it significantly more polished, modern, and visually striking.

## Current layout (keep this structure)

**Left sidebar (240px wide):**
- Logo block at top (icon + title + subtitle)
- Navigation items with emoji icons: Финансы, Товары, Склад, Карточки, WB vs Ozon, Тренды, Сравнение, Топ/Флоп, Категории, Воронка
- Footer with "last updated" timestamp and sync button

**Top bar (full width):**
- Page title (left)
- Period filter buttons: 7д / 14д / 30д / 90д (pill buttons, one active = orange)
- Export CSV button
- Sync data button

**KPI cards row (below top bar, full width, 6 cards in a grid):**
- Выручка 30д — large number + delta badge (▲12.4%)
- Прибыль 30д — large number + delta badge
- Заказы — large number + delta badge
- Маржа % — percentage
- СРОЧНО — count of urgent restock items (red)
- Всего SKU — total product count

**Main content area:** charts + tables depending on active section

---

## Color palette to use

```
Background:     #0d0d0d  (deepest)
Surface 1:      #141414  (sidebar, topbar)
Surface 2:      #1e1e1e  (cards)
Surface 3:      #252525  (table headers, inputs)
Border:         #2a2a2a

Accent orange:  #ff6600  (primary action, active states, highlights)
Orange dim:     rgba(255,102,0, 0.12)  (hover backgrounds)
Orange glow:    rgba(255,102,0, 0.25)  (active card glow)

Text primary:   #f0f0f0
Text muted:     #888888
Text faint:     #555555

Green:          #00c853  (positive delta, profit, OK status)
Red:            #f44336  (negative delta, urgent alerts)
Yellow:         #ffb300  (warning, medium)
Blue:           #42a5f5  (Ozon brand color)
Pink:           #cc0066  (WB brand color)
```

---

## What to redesign / improve

### 1. KPI Cards — make them pop
- Add a subtle left-side colored border (orange for revenue/profit, green for positive, red for urgent)
- Delta badge should be a colored pill: green background + arrow up for positive, red + arrow down for negative
- Add a tiny sparkline or trend arrow under the main number
- Slight glow effect on hover (box-shadow with orange tint)
- Icon in top-right corner of each card (small, muted)

### 2. Sidebar — more premium feel  
- Active nav item: full orange left border (3px) + orange text + orange-tinted background
- Nav item icons: replace emoji with clean SVG-style icon circles (18px)
- Logo icon: orange rounded square with white letter, slight gradient
- Add a thin separator line between nav groups
- Sidebar footer: small pill showing live sync status (green dot = synced)

### 3. Buttons — more clickable
- Period buttons (7д/30д/90д): pill shape, active state = solid orange with black text
- Export button: outlined with orange border, orange text, arrow-down icon
- Sync button: outlined with subtle pulse animation on the icon when syncing
- All buttons: scale(0.97) on active press, smooth transitions

### 4. Tables — more readable
- Alternate row shading (every odd row slightly lighter)
- Sticky header with stronger background (#252525 + bottom border)
- Sort indicators: ↑↓ arrows in orange when active
- Status badges: СРОЧНО = red pill, ДОКУПИТЬ = orange pill, OK = green pill
- Profit/ROI columns: color-coded (green > 50%, yellow 20-50%, red < 20%)
- Row hover: orange-tinted highlight

### 5. Charts (Chart.js) — more visual weight
- Grid lines: very faint (#1e1e1e to #2a2a2a)
- WB line: pink/magenta (#cc0066)
- Ozon line: blue (#42a5f5)
- Total/profit line: orange (#ff6600)
- Area fills: semi-transparent gradient fill under lines
- Tooltips: dark card (#252525) with orange accent border, clean typography
- Bar charts: rounded top corners (borderRadius: 6)

### 6. Cards/panels — glassmorphism touch
- Cards: border: 1px solid #2a2a2a, on hover border transitions to rgba(255,102,0,0.3)
- Card titles: small uppercase, 11px, letter-spacing 0.8px, muted color
- Subtle inner shadow at top of card: inset 0 1px 0 rgba(255,255,255,0.04)

### 7. Overall typography
- Numbers/KPI values: font-size 28px, font-weight 800, tight letter-spacing
- Section titles: 18px, font-weight 700
- Table headers: 11px, uppercase, letter-spacing 0.5px
- Use system font stack: 'Inter', 'Segoe UI', Arial, sans-serif

---

## Specific screens to show in the design

### Screen 1: Финансы (Finance) — main screen
Show full layout with:
- Sidebar (Финансы active)
- KPI cards row (6 cards with example numbers)
- Period selector buttons (30д active)
- Two chart cards side by side: "Выручка и прибыль по дням" (area chart) + "WB vs Ozon" (two lines)

### Screen 2: Склад (Stock) — alerts screen  
Show:
- KPI cards (СРОЧНО = 5 in red, others normal)
- Table with columns: Товар | Площадка | Остаток | Продаж/день | Дней запаса | Статус
- Status badges: СРОЧНО (red), ДОКУПИТЬ (orange), OK (green)
- Red urgent items at the top

### Screen 3: Воронка (Funnel) — conversion screen
Show:
- Horizontal funnel visualization: Показы → Клики → Корзина → Заказ
- Each step: large number + % of previous step
- Color gradient: orange at top, fading down
- CTR table below

---

## UI style reference
- Similar vibe to: Linear app, Vercel dashboard, Grafana dark theme
- NOT: flat material design, not rounded bubbly style
- DO: sharp corners (6-10px radius max), subtle shadows, data-dense layout
- Typography: tight and precise, numbers should feel "financial"

---

## Output format
Show all 3 screens as Hi-Fi desktop mockups (1440px wide).
Dark background, the dashboard fills the screen edge-to-edge.
No browser chrome, no device frame — just the dashboard itself.
