"""KimiLM engine: local models, chats, harnesses, plugins, skills, memory, jobs and schedules.

2.37.0: the package was called `dva` and its settings DVA_*. A setting still given by its old name (an older
launcher, a service file, a script) is read as its KIMILM_* name unless that is set too; the old token is never
passed on to the programs the engine starts."""
import os

VERSION = "2.37.0"
OLD_PREFIX, PREFIX = "DVA_", "KIMILM_"


def _adopt_old_names():
    for name in [k for k in os.environ if k.startswith(OLD_PREFIX)]:
        os.environ.setdefault(PREFIX + name[len(OLD_PREFIX):], os.environ[name])
    os.environ.pop(OLD_PREFIX + "TOKEN", None)


def with_old_names(env):
    """What the engine gives a plugin's or a pipeline's script (KIMILM_PLUGIN_SETTINGS, KIMILM_WORKSPACE, …), also
    under the old DVA_* names, so scripts written before 2.37.0 keep working."""
    return {**env, **{OLD_PREFIX + k[len(PREFIX):]: v for k, v in env.items()
                      if k.startswith(PREFIX) and k != PREFIX + "TOKEN"}}


_adopt_old_names()
