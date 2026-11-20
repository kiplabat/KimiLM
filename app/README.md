# KimiLM — desktop app (Windows first)

A desktop app for local AI agents: chat, projects, memory, skills, plugins, connectors (MCP), harnesses and
scheduled tasks. Models run **on this computer** (llama.cpp, CPU or GPU) and, from 2.3.0, **optionally online**
(OpenAI, Anthropic, Google, OpenRouter, Moonshot/Kimi, DeepSeek, Mistral, Groq, xAI or any OpenAI-style endpoint,
with the user's own API keys). **Auto** sends planning and hard reasoning to the strongest model and routine work to
a local one; the "Lead and local workers" harness lets an online strategist plan and check while local agents do the
parts in parallel. Online use has a monthly budget (warnings at 50 % and 80 %, then local only) and asks before files,
attachments or memory leave the computer.

From 2.4.0, **teams are run by a planner**: it turns a multi-step request into tasks, starts each agent when the
results it needs are ready, reads every result (accept, send back with feedback, message, pause, add work) and
writes the answer — automatically in chats, projects and scheduled tasks when a request looks multi-step. Agents can
run commands (tests, git, build tools) and fix their own code. **Memory** covers past chats (search by words, and by
meaning with the optional search model), project knowledge files, living project notes, and per-project isolation.
Plugins are optional and none is built in (2.15.0): a service such as Datavera is connected as an MCP connector
in Plugins & connectors.

From 2.9.0 there are **long-running tasks**: work that goes on until a problem is solved. A planner keeps its
understanding of the problem, gives one step at a time to an agent, reads the handoff that agent leaves and decides
the next step from it, changing the plan when the findings call for it. Tasks have limits (hours, steps), pause
between two steps when one is reached or when the planner needs an answer, and continue after a restart. A planner
can also write the set-up of a task or a scheduled task from one sentence (team, limits, what it may do without
asking). Agents can work in a **real browser** (open, click, type, choose, download, screenshot), and the computer is
kept awake while tasks run.

From 2.10.0 **connectors are set up the way other apps do it**: paste the `mcpServers` configuration that Claude
Desktop, Cursor or VS Code use, or take over the servers those apps already have on this computer; remote servers
can sign in with the browser (OAuth) or use keys and headers; MCP bundles (`.mcpb`) install as plugins; and besides
tools, a server's resources and prompts are available. The protocol itself is handled by the bundled client
library, which speaks the 2026-07-28 revision and the earlier ones.

From 2.11.0 KimiLM **makes and changes pictures** (the Images window, and the tools `generate_image` and
`edit_image` for agents). On this computer the pictures come from stable-diffusion.cpp, which the app downloads
like the llama.cpp runtimes (processor, Vulkan and CUDA builds) and runs only while a picture is made, with
recommended models to download (Z-Image Turbo, Stable Diffusion 1.5, SDXL Turbo, FLUX.2 klein) or a model file you
already have; nothing leaves the computer. Online, OpenAI and Google image models work with the keys already
saved for those providers and count against the monthly online budget. Claude reads pictures but does not make
them: a chat on Claude asks for pictures through the same tools. Pictures appear in the chat where they were
made, can be downloaded, and are kept in a library with their prompt, model and seed.

From 2.12.0 KimiLM **speaks, listens and makes music** (the Sound window, and the tools `speak`, `list_voices`,
`transcribe_audio`, `generate_music` and `mix_audio` for agents). On this computer: Kokoro (28 English voices and
Chinese), Pocket TTS (a voice made from a 10 to 30 second recording: your own after you confirm it, another
person's only with a recording of their consent) and Whisper (transcription in 99 languages, with times and
subtitle files), downloaded in the app and run on the processor by the sherpa-onnx library. A script with one line
per speaker (`Host: …`, `Guest: …`) becomes one recording, with a voice for each speaker: a podcast. Online:
OpenAI and Google voices and transcription, and Google's Lyria for music, with the keys already saved, counted
against the monthly budget. Replies can be read aloud and messages can be dictated. After pulling this version,
install the new dependency once: `cd engine` and `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`.

From 2.13.0 a **podcast is made from a document in one request**: in Sound → Podcast choose a PDF, Word file,
slides or text (or attach it in a chat and ask for a podcast), and KimiLM reads it, takes notes part by part,
plans the episode, writes each part as a conversation with the matching pieces of the document in front of the
model, and records it with two voices; the script is saved beside the recording. **Music is made on this
computer** with ACE-Step 1.5 through acestep.cpp: the programs are built from the pinned source by the workflow
`music-engine.yml` of the public repository named in `engine/dva/versions.json` (`music_engine_repo`, since 2.15.0
`kiplabat/kimilm-engines`; run it once: Actions → Build music engine → Run workflow) and installed from that
repository's release; the model (about 3.3 GB) is downloaded in the app.

KimiLM was called **Datavera Agent** up to 2.1.x. Version 2.2.0 renamed it; see "Upgrading from Datavera Agent".

```
app/                Electron shell (window, tray, start with Windows) + React UI (Vite)
  electron/         main.cjs (starts the engine, window, tray), preload.cjs (safe bridge),
                    migrate.cjs (first start: moves the data from Datavera Agent)
  src/              the UI: pages/ (Chat, Projects, Models, Plugins, Skills, Harnesses, Schedules, Activity, Performance,
                    Usage, Memory, Settings); components/Charts.tsx (charts), components/Resizable.tsx (resizable parts)
  scripts/          dev.mjs (development), prepare-resources.mjs (gathers what the installer ships)
  build/            icons, installer.nsh (installer messages; offers to remove Datavera Agent)
engine/             Python engine (FastAPI on 127.0.0.1 + a per-launch token) — see engine/README.md
```

## Run it in development (Windows, PowerShell)

Needs Python 3.12 and Node.js 20+.

```powershell
cd D:\KimiLM\engine
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd ..\app
npm install
npm run dev
```

In development the engine runs from `engine\.venv` and there is no bundled llama.cpp yet. Either run
`node scripts\prepare-resources.mjs --skip-python` once (downloads the CPU build into `app\resources\llama\cpu`),
or install a runtime from **Models → Hardware & GPU** inside the app.

## Build the Windows installer

```powershell
cd D:\KimiLM\app
npm install
npm run dist:win
```

`dist:win` does three things:
1. `vite build` — the UI into `app\dist`.
2. `prepare-resources.mjs --platform win` — copies the engine into
   `app\resources\engine`, downloads the embeddable Python 3.12 (`python.org`) and installs the engine's packages
   into it (`pip --target … --platform win_amd64`), and downloads the llama.cpp CPU build of the pinned release
   (`engine\dva\versions.json`). Downloads are cached in `app\.cache`.
3. `electron-builder --win` — writes `app\release\KimiLM-Setup-<version>.exe` (per-user install, no admin
   rights, desktop + Start-menu shortcuts). The installer shows each step and what it installed, and offers to
   remove Datavera Agent if it finds it (`build\installer.nsh`).

The installer is **not code-signed** yet, so Windows SmartScreen will warn ("More info → Run anyway") until a
signing certificate is added (`win.certificateFile` / Azure Trusted Signing in `electron-builder.yml`).

## Where things are on the user's computer

| What | Where |
|---|---|
| App | `%LOCALAPPDATA%\Programs\KimiLM` (or the folder chosen in the installer) |
| Data (database `kimilm.sqlite3`, chats' workspaces, your skills/harnesses/plugins, logs) | `%APPDATA%\KimiLM` |
| Models | `%APPDATA%\KimiLM\models` by default; change in **Settings → Models folder** (e.g. `D:\models`) |
| Keys and tokens | Windows Credential Manager (service `KimiLM`) |
| A plugin's own data | `%APPDATA%\KimiLM\plugin-data\<plugin>` (the earlier built-in Datavera plugin's data stays in `plugin-data\datavera`) |

## Upgrading from Datavera Agent

- **Installer:** if Datavera Agent is installed, the installer offers to remove it (recommended). Its data is kept.
- **First start** (`electron/migrate.cjs`): `%APPDATA%\Datavera Agent` is moved to `%APPDATA%\KimiLM` — a rename
  on the same drive, so even many GB of models move instantly. If KimiLM's folder already exists, each item it
  doesn't have is moved; a failure puts back what was moved. If KimiLM already has its own database, nothing is
  moved (two databases are never merged). If Datavera Agent is still running, KimiLM asks to close it.
  `migrated-from.json` records the move; `MOVED-TO-KimiLM.txt` is left in the old place.
- **Engine, next start:** the database is renamed to `kimilm.sqlite3` (with its `-wal`/`-shm` files), and paths
  inside it that pointed into the old folder (models, attachments, downloads) are rewritten (`dva/migration.py`).
- **Saved keys:** found under the old Credential Manager name (`DataveraAgent`) and copied to `KimiLM`.
- **Start with Windows:** the old entry is removed; KimiLM's is added if it was on.
- Unchanged on purpose: the Python package `dva`, `DVA_*` variables, the repository name.

## How the pieces talk

- Electron starts `python -m dva serve` with a random token in the environment, waits for `DVA_READY <port>`, and
  opens the window on `http://127.0.0.1:<port>/`. The engine refuses requests without the token and from other hosts.
- Closing the window hides it to the tray: chats, long tasks and schedules continue. **Quit** from the tray stops them.
- Node programs (the Playwright browser helper, `{node}` connectors) run on the app's own Electron
  (`ELECTRON_RUN_AS_NODE`), so users don't need Node.js installed.

## Not done yet (honest list)

- macOS build (the code paths exist; never run on a Mac).
- Code signing and auto-update.
- Vision is tested with stand-in models only; real image quality depends on the model (Qwen3.5 and Gemma 3 have
  vision parts; most others do not). Videos are read as 3–8 sampled frames, not continuous video.
- Agents on the same model take turns unless that model's "Simultaneous replies" is raised (needs more memory).
- Online models: costs are estimates from token counts and catalogue prices (OpenRouter's public list, refreshed
  daily, or prices typed in by the user); the provider's bill is the final word. Tested against stand-in servers
  that speak each provider's API; check each provider with a real key (Models → Online → Test) before relying on it.
  ChatGPT/Claude/Gemini subscriptions cannot be used; providers need API keys with billing.
- Teams: the planner's quality decides the team's quality; small local planners make simpler plans. Tested with
  stand-in models. A team of local agents on one model with "Simultaneous replies" 1 runs its tasks one at a time.
- Knowledge files: scanned PDFs (no text layer) are not read; images inside documents are ignored.
- GPU placement (2.4.1): integrated graphics share the computer's memory and bandwidth, so moving layers there frees
  no memory and is often no faster; "Find the fastest setup" measures it with a short context.
- Speed (2.4.2): each model card has a suggested configuration ("Use suggested configuration"; everything stays
  adjustable). Measurements use a short context and a stand-in prompt; speculative decoding is only suggested when
  it measured faster on this computer. Replies show how much of the prompt came from the model's cache.
