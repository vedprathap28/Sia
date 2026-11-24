# SIA — frontend

React + Vite UI for SIA: chat, voice orb (wake word + push-to-talk), live task panel, approval cards,
memory and settings. See the [main README](../README.md) for setup.

```bash
npm install
npm run dev      # http://localhost:5173 (proxies /api to the backend on :8000)
npm run build    # production build in dist/
```

Key files: `src/App.jsx` (layout, voice approvals), `src/hooks/useMessages.js` (chat, TTS playback,
wake word, mic), `src/hooks/useAgentEvents.js` (WebSocket: tasks + approvals),
`src/components/MessageBubble.jsx` (safe markdown rendering).

Set `VITE_API_BASE` if the backend is not on `http://localhost:8000`.
