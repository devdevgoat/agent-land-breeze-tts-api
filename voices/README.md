# voices/

Saved voices live here. The server reads them on every request, so new files work without a restart. Nothing in this folder except this README is committed (see `.gitignore`), because voice clips are personal data.

| File | Required | What |
|---|---|---|
| `<name>.wav` | yes | 5–15 s of clean speech (no music or noise), any sample rate |
| `<name>.txt` | yes | The exact transcript of `<name>.wav` |
| `<name>.json` | no | Metadata. Designed voices store `instruction`, `seed`, `cfg_scale` |

- **Designed voices** (`v-<12 hex>`): the server creates these when a request sends an `instruction` and no `voice`. See API.md.
- **Hand-made voices:** use any `<name>` matching `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`, then request them with `voice=<name>`.

Only clone a voice you have the right to use. The Breeze TTS 2 licence also forbids unauthorized voice cloning and impersonation.
