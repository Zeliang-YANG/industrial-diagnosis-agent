# Industrial Diagnosis Agent Frontend

Vue 3 dashboard for equipment KPI monitoring, event inspection and evidence-grounded Agent conversations.

## Development

```bash
npm ci
npm run dev -- --host 127.0.0.1
```

The frontend calls `http://127.0.0.1:5001` by default. Copy `.env.example` to `.env` when a different API endpoint or local bearer token is required.

## Production build

```bash
npm run build
```

Build output is written to `dist/` and is intentionally excluded from Git.
