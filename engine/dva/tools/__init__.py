"""Tools the agent can use. Built-in groups: files, web, browser, python, memory, skills, automation, vision, images, sound, agents. Connector (MCP)
tools are added per chat. Every tool says whether it changes anything; changes ask the user first unless the
chat or scheduled task was set to approve automatically."""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable[..., Awaitable[Any]]
    group: str
    changes: bool = False           # True = needs approval (unless auto-approve)
    label: str = ""                 # short human verb for the UI, e.g. "Read file"
    summary: Callable[[dict], str] | None = None
    source: str = "builtin"         # builtin | connector:<id>
    physical: bool = False          # 2.30.0: it can move, heat, cool or switch a real device: a person approves
    destructive: bool = False       # 2.32.0: it deletes or overwrites (a connector said so): a snapshot comes first

    def schema(self):
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}

    def describe_call(self, args):
        if self.summary:
            try:
                return self.summary(args)
            except Exception:
                pass
        return f"{self.label or self.name}: {json.dumps(args, ensure_ascii=False)[:200]}"


class ToolDenied(Exception):
    pass


class ToolError(Exception):
    pass


@dataclass
class ToolContext:
    app: Any
    chat: dict
    project: dict | None
    workspace: Path
    folders: list            # granted folders (read/write)
    readonly: list           # e.g. skill folders
    emit: Callable           # emit(kind, **data) to the chat's live stream
    approve: Callable        # async approve(tool, args, description) -> bool
    job_id: str | None = None
    model_id: str | None = None
    harness: dict | None = None
    loaded_skills: set = field(default_factory=set)
    notes: dict = field(default_factory=dict)
    turn: Any = None         # the Turn that runs the tools (2.19.0: code reaches its connectors through it)

    def progress(self, text, beat=False):
        """A tool says what it is doing. Shown live inside the chat's step for this tool call and kept with the
        step, so the work can be read afterwards. A beat is a counter that moves: shown, not kept."""
        text = str(text or "")[:400]
        if not text:
            return
        if not beat:
            log = self.notes.setdefault("log", [])
            if not log or log[-1]["text"] != text:
                log.append({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "text": text})
                del log[:-200]
        self.emit("tool_progress", call_id=self.notes.get("call_id"), text=text, beat=bool(beat))
        if self.job_id:
            self.app.jobs.progress(self.job_id, text)

    def reporter(self):
        """The status function a tool hands to long work (pictures, sound, video)."""
        def status(text):
            self.emit("status", text=text)
            self.progress(text)

        def beat(text):
            self.emit("status", text=text)
            self.progress(text, beat=True)
        status.beat = beat
        return status

    @property
    def roots(self):
        return [Path(f).resolve() for f in self.folders] + [self.workspace.resolve()]

    @property
    def primary(self):
        return Path(self.folders[0]).resolve() if self.folders else self.workspace.resolve()

    def resolve(self, path, write=False):
        """Resolve a path inside the granted folders (or read-only skill folders for reading)."""
        p = Path(str(path or ".").strip().strip('"'))
        if not p.is_absolute():
            base = self.primary
            # 2.21.0: an agent that names its own folder ("fizz/fizz.py" in a folder called fizz, "team/x" in the
            # team folder) means that folder, not a new folder of the same name inside it. A sub-folder that really
            # has that name is still used.
            if p.parts and p.parts[0].lower() == base.name.lower() and not (base / p.parts[0]).exists():
                p = Path(*p.parts[1:]) if len(p.parts) > 1 else Path(".")
            p = base / p
        p = p.resolve()
        allowed = self.roots + ([] if write else [Path(r).resolve() for r in self.readonly])
        for root in allowed:
            if p == root or root in p.parents:
                return p
        names = ", ".join(str(r) for r in self.roots)
        raise ToolDenied(f"{p} is outside the folders this chat may use ({names}). "
                         "Add the folder to the chat or project to allow it.")


def builtin_tools():
    from . import apps, browser, devices, files, images, meta, python_exec, setup, sound, video, vision, web
    return (files.TOOLS + web.TOOLS + browser.TOOLS + python_exec.TOOLS + meta.TOOLS + vision.TOOLS + images.TOOLS
            + sound.TOOLS + video.TOOLS + setup.TOOLS + devices.TOOLS + apps.TOOLS)


GROUPS = {
    "files": "Read, search and write files in the chat's folders",
    "web": "Search the web and read pages",
    "browser": "Work in a real browser: open pages, click, type, choose, download and take screenshots",
    "browsing": "A browsing specialist's extra tools: how a page is served and where its data comes from, whole "
                "forms, waiting, going back, tabs, dialogs, saving files with the page's sign-in, uploads",
    "python": "Run Python code and commands (data analysis, tests, git, build tools)",
    "memory": "Remember and recall facts",
    "skills": "Load skills and create new ones",
    "automation": "Create scheduled tasks and harnesses",
    "vision": "Look at images and videos (vision models only)",
    "images": "Make new pictures and change existing ones (needs an image model, see Images)",
    "sound": "Speak texts and scripts, transcribe recordings, make music and mix sound (needs models, see Sound)",
    "video": "Build videos from pictures, narration and music, change video files, and make short clips with a video model (see Video)",
    "agents": "Hand parts of a task to other agents that work in parallel",
    "setup": "Find and install programs the work needs (every agent has these: 2.29.0)",
    "devices": "See the devices attached to this computer (phones, boards, sensors, displays, keyboards, audio, "
               "Bluetooth), what drives them and what can be done with them, and control them",
    "apps": "Save the app (a device's control panel or a game) being built in this chat (2.34.0: the App builder's)",
}
# 2.29.0: groups every agent gets, whatever its harness lists or its step was given
ALWAYS = ("setup",)
# Groups a harness gets only when it lists them: they are a specialist's kit, and every tool in a model's list
# costs reading time and makes the right choice harder (2.16.0).
ON_REQUEST = ("browsing", "apps")
