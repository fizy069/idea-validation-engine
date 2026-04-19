# Frontend API Guide

This document is the frontend integration reference for the OASIS backend.

## Base URL

- Local: `http://localhost:8000`
- Production: use your deployed backend URL (set in frontend env/config)

## Global Rules

- Content type: JSON for all requests and responses.
- No auth/credentials required for these endpoints.
- Error shape is always:

```json
{ "error": "<machine_code>", "message": "<human-readable>" }
```

- Use `credentials: "omit"` in frontend fetch calls.
- `POST /simulate/market` is synchronous and can take up to a few minutes.

## Endpoints

### `GET /health`

Fast liveness check. No DB or LLM work.

Response (`200`):

```json
{ "status": "ok" }
```

---

### `POST /simulate/market`

Runs a full market simulation and returns the final aggregate payload.

Request body:

```json
{
  "idea": "A SaaS that turns Slack threads into searchable docs",
  "targetUser": "Engineering managers at 50-500 person companies",
  "subreddit": "r/SaaS",
  "numVocal": 5,
  "turns": 2
}
```

Validation rules:

- `idea`: string, required, trimmed non-empty, max 4000 chars
- `targetUser`: string, required, trimmed non-empty, max 500 chars
- `subreddit`: string, required, regex `^r/[A-Za-z0-9_]{1,32}$`
- `numVocal`: integer, optional, default `5`, range `1..20`
- `turns`: integer, optional, default `2`, range `1..5`
- Unknown fields are rejected with `400`.

Response (`200`):

```json
{
  "slug": "6453f3dd5cebe77b",
  "subreddit": "r/SaaS",
  "post": {
    "title": "A SaaS that turns Slack threads into searchable docs",
    "body": "Engineering managers at 50-500 person companies",
    "likes": 38,
    "dislikes": 6,
    "shares": 9,
    "commentCount": 14,
    "createdAt": "2026-04-19T12:34:56Z"
  },
  "thread": [
    {
      "id": "c1",
      "agentId": 7,
      "agent": "PowerUser_42",
      "personaDescription": "Senior backend engineer, opinionated about tooling",
      "type": "vocal",
      "comment": "Interesting idea...",
      "likes": 47,
      "dislikes": 3,
      "turn": 1,
      "createdAt": "2026-04-19T12:35:12Z",
      "replies": [
        {
          "id": "c1r2",
          "agentId": 12,
          "agent": "Skeptic_9",
          "personaDescription": "Cost-conscious solo founder",
          "comment": "How is this different from...",
          "likes": 8,
          "dislikes": 1,
          "turn": 2,
          "createdAt": "2026-04-19T12:36:01Z"
        }
      ]
    }
  ],
  "tractionScore": 7.2,
  "summary": "Most respondents saw strong pain, with concerns around differentiation..."
}
```

Error statuses:

- `400` `invalid_request`
- `429` `rate_limited` (includes `Retry-After` header in seconds)
- `500` `simulation_failed`

---

### `GET /result/{slug}`

Fetches a previously saved result payload for rendering a share/result page.

Path rule:

- `slug` must match `^[a-f0-9]{8,64}$`

Response (`200`):

```json
{
  "slug": "6453f3dd5cebe77b",
  "createdAt": "2026-04-19T12:34:56Z",
  "idea": "A SaaS that turns Slack threads into searchable docs",
  "targetUser": "Engineering managers at 50-500 person companies",
  "config": {
    "subreddit": "r/SaaS",
    "numVocal": 5,
    "turns": 2
  },
  "result": {
    "slug": "6453f3dd5cebe77b",
    "subreddit": "r/SaaS",
    "post": {},
    "thread": [],
    "tractionScore": 7.2,
    "summary": "..."
  }
}
```

Headers:

- `Cache-Control: no-store`

Error statuses:

- `400` `invalid_slug`
- `404` `not_found`

---

### `GET /result/{slug}/interviews`

Fetches interviews for a completed simulation.

Response (`200`):

```json
{
  "slug": "6453f3dd5cebe77b",
  "interviews": [
    {
      "agentId": 7,
      "agent": "PowerUser_42",
      "personaDescription": "Senior backend engineer, opinionated about tooling",
      "prompt": "You just saw the post above describing an idea...",
      "response": "I would use this if...",
      "createdAt": "2026-04-19T12:38:44Z"
    }
  ]
}
```

Headers:

- `Cache-Control: no-store`

Error statuses:

- `400` `invalid_slug`
- `404` `not_found`

## Frontend Integration Pattern

1. Call `POST /simulate/market` and show loading state.
2. Render directly from that response.
3. Persist slug in frontend route/state.
4. On share/result page load, call `GET /result/{slug}`.
5. Lazy-load interviews using `GET /result/{slug}/interviews`.

## TypeScript Types (starter)

```ts
export type ApiError = { error: string; message: string };

export type MarketReply = {
  id: string;
  agentId: number;
  agent: string;
  personaDescription: string;
  comment: string;
  likes: number;
  dislikes: number;
  turn: number;
  createdAt: string;
};

export type MarketComment = {
  id: string;
  agentId: number;
  agent: string;
  personaDescription: string;
  type: "vocal";
  comment: string;
  likes: number;
  dislikes: number;
  turn: number;
  createdAt: string;
  replies: MarketReply[];
};

export type SimulateMarketResponse = {
  slug: string;
  subreddit: string;
  post: {
    title: string;
    body: string;
    likes: number;
    dislikes: number;
    shares: number;
    commentCount: number;
    createdAt: string;
  };
  thread: MarketComment[];
  tractionScore: number;
  summary: string;
};
```

## Fetch Helper Example

```ts
const API_BASE = process.env.NEXT_PUBLIC_OASIS_BACKEND_URL!;

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    credentials: "omit",
    cache: "no-store",
  });

  if (!res.ok) {
    const err = (await res.json()) as { error: string; message: string };
    throw new Error(`${err.error}: ${err.message}`);
  }

  return (await res.json()) as T;
}

export async function runMarketSimulation(input: {
  idea: string;
  targetUser: string;
  subreddit: string;
  numVocal?: number;
  turns?: number;
}) {
  return apiFetch<SimulateMarketResponse>("/simulate/market", {
    method: "POST",
    body: JSON.stringify(input),
  });
}
```

## Operational Notes for Frontend

- Use a long timeout for `POST /simulate/market` (for example ~240s).
- Handle `429` by reading `Retry-After` and surfacing a retry hint.
- Treat `5xx` as temporary backend failure and allow manual retry.
- There is no endpoint to list all historical runs; retrieval is slug-based.
