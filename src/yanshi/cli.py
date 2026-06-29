"""YanShi command-line interface for supervising agent CLI mechanisms."""

from __future__ import annotations

import asyncio
import json
import shlex
import shutil
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import typer

from yanshi.config import (
    DEFAULT_CONFIG_FILENAME,
    enabled_adapter_names,
    global_config_path,
    load_config,
    render_default_config_toml,
    resolve_dispatch,
)
from yanshi.contracts import AllowMode, ImproveSpec, PromptMode, RunSpec, WarningRecord
from yanshi.dispatch import cancel as cancel_run
from yanshi.dispatch import config_kernel
from yanshi.dispatch import dispatch_wait as dispatch_wait_run
from yanshi.dispatch import doctor as doctor_run
from yanshi.dispatch import list_agents as list_agents_run
from yanshi.dispatch import status as status_run
from yanshi.dispatch import summary as summary_run
from yanshi.dispatch import wait as wait_run
from yanshi.improve import improve_loop
from yanshi.skill_install import SkillRegistrationError, register_skill
from yanshi.store import StatusStore

app = typer.Typer(
    help=(
        "YanShi (偃师) is a vendor-neutral dispatcher: the parent agent remains "
        "the artisan, enabled adapter CLIs are mechanisms, and status/summary "
        "commands read deterministic control threads."
    )
)

skill_app = typer.Typer(
    help="Register the YanShi skill (SKILL.md) so parent agents can discover it."
)
app.add_typer(skill_app, name="skill")


@skill_app.command("register")
def skill_register(
    skills_dir: Annotated[
        str | None,
        typer.Option(
            "--skills-dir",
            help="Skills home to register into; defaults to detected agent homes.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Plan the registration without touching disk."),
    ] = False,
    best_effort: Annotated[
        bool,
        typer.Option(
            "--best-effort",
            help="Do not fail when no agent skills home is found (installer use).",
        ),
    ] = False,
) -> None:
    """Copy SKILL.md into agent skills homes so YanShi is discoverable as a skill."""

    try:
        report = register_skill(skills_dir=skills_dir, dry_run=dry_run)
    except SkillRegistrationError as exc:
        typer.echo(
            json.dumps({"error": str(exc), "category": str(exc.category)}, ensure_ascii=False),
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo(report.model_dump_json())
    if not report.registered:
        # No Silent Failures: surface that nothing was registered and tell the
        # user how to fix it. Best-effort callers (the installer) only warn.
        typer.echo(
            "no agent skills home found; pass --skills-dir to choose one "
            "(searched: ~/.cursor/skills, ~/.claude/skills, ~/.agents/skills)",
            err=True,
        )
        if not best_effort:
            raise typer.Exit(code=1)


@app.command()
def doctor() -> None:
    """Check enabled adapter mechanisms, executables, and auth state."""

    results = doctor_run()
    for result in results:
        status = "ok" if result.ok else "failed"
        typer.echo(
            json.dumps(
                {
                    "cli": result.cli,
                    "status": status,
                    "executable": result.executable,
                    "version": result.version,
                    "errors": result.errors,
                    "warnings": result.warnings,
                },
                ensure_ascii=False,
            )
        )
    if any(not result.ok for result in results):
        raise typer.Exit(code=1)


@app.command()
def init(
    global_: Annotated[
        bool,
        typer.Option(
            "--global/--local",
            help="Write the global config instead of this repo's ./.yanshi.toml.",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite an existing config file."),
    ] = False,
) -> None:
    """Write a starter config for mechanisms, defaults, limits, and summaries."""

    target = global_config_path() if global_ else Path.cwd() / DEFAULT_CONFIG_FILENAME
    if target.exists() and not force:
        typer.echo(
            f"refusing to overwrite existing config: {target} (use --force)",
            err=True,
        )
        raise typer.Exit(code=1)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_default_config_toml(), encoding="utf-8")
    typer.echo(str(target.resolve()))


@app.command()
def config() -> None:
    """Print effective repo/global configuration layering as JSON."""

    loaded = load_config()
    typer.echo(
        json.dumps(
            {
                "config": loaded.config.model_dump(mode="json"),
                "sources": [str(path) for path in loaded.sources],
                "provenance": loaded.provenance,
                "enabled_adapters": enabled_adapter_names(loaded.config),
            },
            ensure_ascii=False,
        )
    )


@app.command()
def dispatch(
    cli: Annotated[
        str | None,
        typer.Option(help="Enabled adapter mechanism name, e.g. claude."),
    ] = None,
    prompt: Annotated[
        str,
        typer.Argument(help="Prompt for the child agent CLI."),
    ] = "",
    model: Annotated[str | None, typer.Option(help="Model id passed through unchanged.")] = None,
    effort: Annotated[
        str | None,
        typer.Option("--effort", help="Reasoning effort: low, medium, high, xhigh."),
    ] = None,
    allow: Annotated[
        AllowMode | None,
        typer.Option(help="Permission mode; defaults stay read-only unless configured."),
    ] = None,
    profile: Annotated[
        str | None,
        typer.Option("--profile", help="Named defaults/limits profile to apply."),
    ] = None,
    workdir: Annotated[str | None, typer.Option(help="Child process working directory.")] = None,
    timeout: Annotated[
        int | None,
        typer.Option("--timeout", help="Wall-clock timeout seconds."),
    ] = None,
    wait: Annotated[
        bool,
        typer.Option("--wait/--no-wait", help="CLI dispatch is blocking by design."),
    ] = True,
) -> None:
    """Run one argv-structured dispatch and print RunResult JSON."""

    if not wait:
        typer.echo(
            "CLI dispatch only supports --wait; use library dispatch for background tasks",
            err=True,
        )
        raise typer.Exit(code=2)
    overrides = _dispatch_overrides(
        cli=cli,
        model=model,
        reasoning_effort=_validate_effort(effort),
        allow=allow,
        workdir=workdir,
        timeout_s=timeout,
    )
    resolved = resolve_dispatch(overrides, config=load_config().config, profile=profile)
    _echo_warnings(resolved.warnings)
    cli_name = resolved.kwargs.pop("cli", None) or "claude"
    spec = RunSpec(cli=cli_name, prompt=prompt, prompt_mode=PromptMode.STDIN, **resolved.kwargs)
    result = asyncio.run(dispatch_wait_run(spec))
    typer.echo(result.model_dump_json())
    if result.is_error:
        raise typer.Exit(code=1)


@app.command()
def improve(
    prompt: Annotated[str, typer.Argument(help="Task prompt to iterate on.")] = "",
    cli: Annotated[
        str | None,
        typer.Option(help="Enabled adapter mechanism name, e.g. claude."),
    ] = None,
    model: Annotated[str | None, typer.Option(help="Model id passed through unchanged.")] = None,
    effort: Annotated[
        str | None,
        typer.Option("--effort", help="Reasoning effort: low, medium, high, xhigh."),
    ] = None,
    allow: Annotated[
        AllowMode | None,
        typer.Option(help="Permission mode; defaults stay read-only unless configured."),
    ] = None,
    profile: Annotated[
        str | None,
        typer.Option("--profile", help="Named defaults/limits profile to apply."),
    ] = None,
    workdir: Annotated[str | None, typer.Option(help="Child process working directory.")] = None,
    timeout: Annotated[
        int | None,
        typer.Option("--timeout", help="Per-dispatch wall-clock timeout seconds."),
    ] = None,
    check: Annotated[
        str | None,
        typer.Option("--check", help="Deterministic gate command; exit 0 passes."),
    ] = None,
    max_iterations: Annotated[
        int,
        typer.Option("--max-iterations", help="Maximum dispatch->gate->refine cycles."),
    ] = 3,
    gate_timeout: Annotated[
        int,
        typer.Option("--gate-timeout", help="Gate command timeout seconds."),
    ] = 300,
    critic: Annotated[
        bool,
        typer.Option("--critic/--no-critic", help="Enable the advisory LLM critic."),
    ] = False,
) -> None:
    """Run bounded dispatch->gate->refine cycles and print ImproveResult JSON."""

    if max_iterations < 1:
        typer.echo("max-iterations must be >= 1", err=True)
        raise typer.Exit(code=2)
    check_command = shlex.split(check) if check else None
    overrides = _dispatch_overrides(
        cli=cli,
        model=model,
        reasoning_effort=_validate_effort(effort),
        allow=allow,
        workdir=workdir,
        timeout_s=timeout,
    )
    resolved = resolve_dispatch(overrides, config=load_config().config, profile=profile)
    _echo_warnings(resolved.warnings)
    cli_name = resolved.kwargs.pop("cli", None) or "claude"
    spec = RunSpec(cli=cli_name, prompt=prompt, prompt_mode=PromptMode.STDIN, **resolved.kwargs)
    plan = ImproveSpec(
        spec=spec,
        check_command=check_command,
        gate_timeout_s=gate_timeout,
        max_iterations=max_iterations,
        use_critic=critic,
    )
    # Route through the same config-driven kernel as dispatch so improve honors
    # [adapters].enabled (G11.3) and the configured summarizer, instead of
    # silently falling back to default_registry() (all adapters).
    result = asyncio.run(improve_loop(plan, kernel=config_kernel(StatusStore())))
    typer.echo(result.model_dump_json())
    if not result.succeeded:
        raise typer.Exit(code=1)


@app.command()
def status(agent_id: str) -> None:
    """Read the deterministic status control thread for an agent."""

    typer.echo(status_run(agent_id).model_dump_json())


@app.command()
def summary(agent_id: str) -> None:
    """Read the optional summary control thread for an agent."""

    typer.echo(summary_run(agent_id))


@app.command()
def wait(agent_id: str, timeout: Annotated[float | None, typer.Option("--timeout")] = None) -> None:
    """Wait on the status control thread until a terminal state."""

    typer.echo(asyncio.run(wait_run(agent_id, timeout_s=timeout)).model_dump_json())


@app.command(name="list")
def list_command() -> None:
    """List agent ids with retained control-thread state."""

    typer.echo(json.dumps(list_agents_run(), ensure_ascii=False))


@app.command()
def cancel(agent_id: str) -> None:
    """Request cancellation for an agent by id."""

    typer.echo(cancel_run(agent_id).model_dump_json())


@app.command()
def gc(
    older_than: Annotated[
        float,
        typer.Option("--older-than", help="Age in seconds."),
    ] = 86400,
) -> None:
    """Garbage collect retained control-thread state older than a threshold."""

    typer.echo(json.dumps(StatusStore().gc(older_than_s=older_than), ensure_ascii=False))


@app.command()
def record(
    cli: Annotated[str, typer.Option(help="Adapter mechanism name.")] = "claude",
    prompt: Annotated[str, typer.Argument(help="Prompt to record.")] = "hello",
    output: Annotated[Path, typer.Option("--output", help="Fixture output path.")] = Path(
        "tests/fixtures/recorded.ndjson"
    ),
) -> None:
    """Run a mechanism once and copy its retained raw stream into a fixture."""

    spec = RunSpec(cli=cli, prompt=prompt, prompt_mode=PromptMode.STDIN)
    result = asyncio.run(dispatch_wait_run(spec))
    stream_path = Path(result.log_dir) / "stream.ndjson"
    if not stream_path.is_file():
        typer.echo(f"recording failed: stream not found at {stream_path}", err=True)
        raise typer.Exit(code=1)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(stream_path, output)
    typer.echo(str(output))


def _validate_effort(value: str | None) -> Literal["low", "medium", "high", "xhigh"] | None:
    if value is None:
        return None
    if value not in {"low", "medium", "high", "xhigh"}:
        typer.echo(f"invalid effort: {value}", err=True)
        raise typer.Exit(code=2)
    return cast(Literal["low", "medium", "high", "xhigh"], value)


def _dispatch_overrides(
    *,
    cli: str | None,
    model: str | None,
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] | None,
    allow: AllowMode | None,
    workdir: str | None,
    timeout_s: int | None,
) -> dict[str, Any]:
    """Collect only the user-supplied (non-None) flags as dispatch overrides.

    Keeping unset flags out of the override map lets config defaults/profiles
    fill them in :func:`resolve_dispatch`; passing them as ``None`` would not.
    """

    candidates: dict[str, Any] = {
        "cli": cli,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "allow": allow,
        "workdir": workdir,
        "timeout_s": timeout_s,
    }
    return {key: value for key, value in candidates.items() if value is not None}


def _echo_warnings(warnings: list[WarningRecord]) -> None:
    """Surface resolution warnings (clamps, unknown profile) to stderr as JSON."""

    for warning in warnings:
        typer.echo(json.dumps(warning.model_dump(mode="json"), ensure_ascii=False), err=True)
