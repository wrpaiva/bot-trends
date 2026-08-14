🎯 Como Funciona

┌────────────────────┐
│     Request        │
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│ SlowAPIMiddleware  │  ← Rate Limit Global (100/min)
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│   CORS Middleware  │  ← Origens permitidas
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│   /health          │  ← Público (sem auth)
│   /                │
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│    require_api_key │  ← X-API-Key header
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│    /rankings/*     │  ← Rate limit específico (30/min)
│    /insights/*     │
│    /products/*     │
│    /categories/*   │
└────────────────────┘