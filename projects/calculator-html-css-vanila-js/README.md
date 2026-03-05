# Calculator App

A lightweight, responsive web‑based calculator that can perform the four basic arithmetic operations.

## Features
- Addition, subtraction, multiplication, division
- Clear (`C`) and backspace (`←`) functions
- Keyboard support (numbers, `+ - * /`, `Enter`, `Backspace`, `Escape`)
- Responsive layout that works on mobile and desktop
- No external dependencies – pure HTML, CSS, and vanilla JavaScript

## Folder Structure
```
calculator-app/
├─ public/
│  ├─ index.html
│  ├─ styles.css
│  ├─ script.js
│  └─ assets/   (optional)
├─ README.md
└─ .gitignore
```

## Setup & Run

1. **Clone or copy the repository**
   ```bash
   git clone https://github.com/yourname/calculator-app.git
   cd calculator-app
   ```

2. **(Optional) Install a static server**
   ```bash
   npm install -g serve   # one‑time global install
   ```

3. **Start the app**
   - Using the `serve` package (recommended):
     ```bash
     npx serve -s public -l 8000
     ```
   - Or with Python’s built‑in server:
     ```bash
     python -m http.server 8000
     ```

4. Open your browser and navigate to `http://localhost:8000`. The calculator will be ready to use.

## Design Decisions
- **Pure client‑side**: No build step, no backend, making the app instantly deployable to any static host (GitHub Pages, Netlify, Vercel, etc.).
- **State‑machine approach**: A simple mutable `state` object keeps track of the current entry, previous entry, selected operator, and an `overwrite` flag.
- **Accessibility**: Buttons are focusable, the display uses `aria-live`, and full keyboard navigation is supported.
- **Theming**: CSS variables at the top of `styles.css` allow quick colour changes (e.g., dark mode) without touching the markup.

## Contribution Guidelines
1. Fork the repository.
2. Create a feature branch (`git checkout -b feature/awesome‑feature`).
3. Make your changes, ensuring the code remains lint‑free and functional.
4. Submit a pull request with a clear description of what you changed.

## License
This project is MIT licensed – feel free to use, modify, and share.