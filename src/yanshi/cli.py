"""YanShi command-line interface."""

from __future__ import annotations

import asyncio
import json
import shlex
import shutil
from pathlib import Path
from typing import Annotated, Literal, cast

import typer

from yanshi.contracts import AllowMode, ImproveSpec, PromptMode, RunSpec
from yanshi.dispatch import cancel as cancel_run
from yanshi.dispatch import dispatch_wait as dispatch_wait_run
from yanshi.dispatch import doctor as doctor_run
from yanshi.dispatch import list_agents as list_agents_run
from yanshi.dispatch import status as status_run
from yanshi.dispatch import summary as summary_run
from yanshi.dispatch import wait as wait_run
from yanshi.improve import improve_loop
from yanshi.store import StatusStore

app = typer.Typer(help="YanShi vendor-neutral agent-CLI dispatcher.")


@app.command()
def doctor() -> None:
    """Check registered adapter executables and authentication state."""

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
def dispatch(
    cli: Annotated[str, typer.Option(help="Adapter name, e.g. claude.")] = "claude",
    prompt: Annotated[str, typer.Argument(help="Prompt to send to the agent CLI.")] = "",
    model: Annotated[str | None, typer.Option(help="Model id to pass through.")] = None,
    effort: Annotated[
        str | None,
        typer.Option("--effort", help="Reasoning effort: low, medium, high, xhigh."),
    ] = None,
    allow: Annotated[
        AllowMode,
        typer.Option(help="Permission mode. Defaults to read-only."),
    ] = AllowMode.READ_ONLY,
    workdir: Annotated[str | None, typer.Option(help="Child process working directory.")] = None,
    timeout: Annotated[
        int | None,
        typer.Option("--timeout", help="Wall-clock timeout seconds."),
    ] = None,
    wait: Annotated[
        bool,
        typer.Option("--wait/--no-wait", help="YanShi CLI dispatch is blocking."),
    ] = True,
) -> None:
    """Run a blocking dispatch through the monitor kernel and print RunResult."""

    if not wait:
        typer.echo(
            "CLI dispatch only supports --wait; use library dispatch for background tasks",
            err=True,
        )
        raise typer.Exit(code=2)
    spec = RunSpec(
        cli=cli,
        prompt=prompt,
        prompt_mode=PromptMode.STDIN,
        model=model,
        reasoning_effort=_validate_effort(effort),
        allow=allow,
        workdir=workdir,
        timeout_s=timeout,
    )
    result = asyncio.run(dispatch_wait_run(spec))
    typer.echo(result.model_dump_json())
    if result.is_error:
        raise typer.Exit(code=1)


@app.command()
def improve(
    prompt: Annotated[str, typer.Argument(help="Task prompt to iterate on.")] = "",
    cli: Annotated[str, typer.Option(help="Adapter name, e.g. claude.")] = "claude",
    model: Annotated[str | None, typer.Option(help="Model id to pass through.")] = None,
    effort: Annotated[
        str | None,
        typer.Option("--effort", help="Reasoning effort: low, medium, high, xhigh."),
    ] = None,
    allow: Annotated[
        AllowMode,
        typer.Option(help="Permission mode. Defaults to read-only."),
    ] = AllowMode.READ_ONLY,
    workdir: Annotated[str | None, typer.Option(help="Child process working directory.")] = None,
    timeout: Annotated[
        int | None,
        typer.Option("--timeout", help="Per-dispatch wall-clock timeout seconds."),
    ] = None,
    check: Annotated[
        str | None,
        typer.Option("--check", help="Deterministic gate command (exit 0 = pass)."),
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
    """Run a bounded dispatch->gate->refine loop and print the ImproveResult."""

    if max_iterations < 1:
        typer.echo("max-iterations must be >= 1", err=True)
        raise typer.Exit(code=2)
    check_command = shlex.split(check) if check else None
    spec = RunSpec(
        cli=cli,
        prompt=prompt,
        prompt_mode=PromptMode.STDIN,
        model=model,
        reasoning_effort=_validate_effort(effort),
        allow=allow,
        workdir=workdir,
        timeout_s=timeout,
    )
    plan = ImproveSpec(
        spec=spec,
        check_command=check_command,
        gate_timeout_s=gate_timeout,
        max_iterations=max_iterations,
        use_critic=critic,
    )
    result = asyncio.run(improve_loop(plan))
    typer.echo(result.model_dump_json())
    if not result.succeeded:
        raise typer.Exit(code=1)


@app.command()
def status(agent_id: str) -> None:
    """Read an agent status snapshot from disk."""

    typer.echo(status_run(agent_id).model_dump_json())


@app.command()
def summary(agent_id: str) -> None:
    """Read an agent summary from disk."""

    typer.echo(summary_run(agent_id))


@app.command()
def wait(agent_id: str, timeout: Annotated[float | None, typer.Option("--timeout")] = None) -> None:
    """Wait until an agent reaches a terminal state."""

    typer.echo(asyncio.run(wait_run(agent_id, timeout_s=timeout)).model_dump_json())


@app.command(name="list")
def list_command() -> None:
    """List known agent ids."""

    typer.echo(json.dumps(list_agents_run(), ensure_ascii=False))


@app.command()
def cancel(agent_id: str) -> None:
    """Cancel an agent by id."""

    typer.echo(cancel_run(agent_id).model_dump_json())


@app.command()
def gc(
    older_than: Annotated[
        float,
        typer.Option("--older-than", help="Age in seconds."),
    ] = 86400,
) -> None:
    """Garbage collect terminal runs older than a threshold."""

    typer.echo(json.dumps(StatusStore().gc(older_than_s=older_than), ensure_ascii=False))


@app.command()
def record(
    cli: Annotated[str, typer.Option(help="Adapter name.")] = "claude",
    prompt: Annotated[str, typer.Argument(help="Prompt to record.")] = "hello",
    output: Annotated[Path, typer.Option("--output", help="Fixture output path.")] = Path(
        "tests/fixtures/recorded.ndjson"
    ),
) -> None:
    """Run a CLI once and copy its retained raw stream into a fixture file."""

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
